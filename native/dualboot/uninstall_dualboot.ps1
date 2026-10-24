<#
.SYNOPSIS
  Removes the Googlebook OS dual-boot install: the firmware boot entry, the Googlebook partitions, and gives the
  space back to D:. DRY RUN by default; -Apply performs it.

.DESCRIPTION
  Only partitions that (a) carry a Googlebook GPT name, (b) have a Googlebook type GUID (ESP / Linux data / the
  vbmeta type), (c) are not in the pre-install layout backup and (d) - when the install state is available - have
  one of the installed GUIDs, exactly at the offset/length the install plan gave them, are deleted - and only if
  they lie after D: (the shrunk region) with no other partition between D: and them. Every other partition entry
  must come out of the layout change unchanged (gbdisk verifies). D: is then extended back to its original size
  (also when the install stopped after the shrink and no Googlebook partition exists).
  Without an install state (lost C:\GB-backup) it refuses unless -Force (name + type + "after D:" + contiguity only).

.EXAMPLE
  .\uninstall_dualboot.ps1                 # dry run
  .\uninstall_dualboot.ps1 -Apply          # elevated PowerShell
  .\uninstall_dualboot.ps1 -TargetImage D:\gb-test\disk.img -Apply
#>
[CmdletBinding()]
param(
    [int]$DiskNumber = 0,
    [string]$StateFile,
    [string]$BackupRoot = 'C:\GB-backup',
    [string]$EntryName = 'Googlebook OS',
    [string]$ExtendDriveLetter = 'D',
    [string]$TargetImage,
    [switch]$KeepBootEntry,
    [switch]$NoExtend,
    [switch]$Force,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'gb_common.ps1')
$script:GbApply = [bool]$Apply
$ImageMode = [bool]$TargetImage
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

Write-Host ('Googlebook OS dual-boot UNINSTALL - ' + $(if ($Apply) { 'APPLY' } else { 'DRY RUN (nothing will be changed; add -Apply)' })) -ForegroundColor White
if ($ImageMode) {
    $TargetImage = (Resolve-Path $TargetImage).Path
    $target = @('--image', $TargetImage); $pointer = "$TargetImage.gbstate.json"
    $runDir = Join-Path "$TargetImage.gbbackup" "uninstall-$stamp"
    Write-Host "target: IMAGE FILE $TargetImage (test harness)"
} else {
    $target = @('--disk', "$DiskNumber"); $pointer = Join-Path $BackupRoot 'googlebook_install.json'
    $runDir = Join-Path $BackupRoot "uninstall-$stamp"
    Write-Host "target: PHYSICAL DISK $DiskNumber"
}

Write-Step 'Install state'
$state = $null
if (-not $StateFile -and (Test-Path $pointer)) { $StateFile = (Read-Json $pointer).state }
if ($StateFile -and (Test-Path $StateFile)) {
    $state = Read-Json $StateFile
    Write-Ok "state $StateFile (phase $($state.phase), installed $($state.time))"
    if ($state.disk_id) { Write-Info "disk $($state.disk_id); anchor #$($state.anchor.number) $($state.anchor.id) original length $($state.anchor.original_length)" }
} elseif ($Force) {
    Write-Warn2 'no install state: -Force given, removing by GPT name + type + contiguity only; D: is extended to its maximum'
} else {
    Write-Bad "no install state found ($pointer). Pass -StateFile <...\install_state.json>, or -Force to remove by name and type only."
    exit 2
}

Write-Step 'Target layout (read-only query)'
$layout = Get-GbLayout $target
Invoke-GbDisk -Arguments (@('layout') + $target) | Out-Null
if ($state -and $state.disk_id -and $state.disk_id -ne $layout.disk_id) { Write-Bad "state is for disk $($state.disk_id), target is $($layout.disk_id)"; exit 2 }

Write-Step 'Preconditions'
if (-not $ImageMode) {
    if (Test-Admin) { Write-Ok 'elevated' } elseif ($Apply) { Write-Bad 'not elevated: run from an elevated PowerShell' } else { Write-Warn2 'not elevated: firmware boot entries cannot be listed in this dry run' }
    $ac = Get-ACPower
    if ($ac -eq $true) { Write-Ok 'on AC power' } elseif ($Apply) { Write-Bad 'not on AC power' } else { Write-Warn2 'not on AC power' }
} else { Write-Info 'image mode: elevation, AC power and bcdedit do not apply' }

# the partition the Googlebook set follows (D:): it bounds what may be removed, and it is extended back afterwards
$anchor = $null
if ($state) { $anchor = $layout.partitions | Where-Object { $_.id -eq $state.anchor.id } | Select-Object -First 1 }
elseif (-not $ImageMode) {
    $p = Get-Partition -DriveLetter $ExtendDriveLetter
    if ($p.DiskNumber -ne $DiskNumber) { Write-Bad "$($ExtendDriveLetter): is on disk $($p.DiskNumber), not $DiskNumber"; exit 2 }
    $anchor = $layout.partitions | Where-Object { [int64]$_.offset -eq [int64]$p.Offset -and [int64]$_.length -eq [int64]$p.Size } | Select-Object -First 1
}
if (-not $anchor) { Write-Bad 'cannot identify the partition the Googlebook set follows (D:) - nothing is removed without it'; exit 2 }
Write-Ok "anchor: #$($anchor.number) '$($anchor.name)' $($anchor.id), $(Format-MiB $anchor.length); only partitions after it are candidates"
$targetLen = $null
if ($state) { $targetLen = [int64]$state.anchor.original_length }

# which partitions (gbdisk decides; dry run here)
$removeArgs = @('remove') + $target + @('--anchor-id', "$($anchor.id)")
if ($state) {
    if (Test-Path $state.plan) { $removeArgs += @('--plan', $state.plan) } else { Write-Warn2 "plan $($state.plan) missing" }
    if (Test-Path $state.pre_shrink_layout) { $removeArgs += @('--protect', $state.pre_shrink_layout) } else { Write-Warn2 "pre-install layout $($state.pre_shrink_layout) missing" }
}
if ($Force) { $removeArgs += '--force' }
Write-Step 'Partitions to remove (gbdisk remove, dry run)'
Invoke-GbDisk -Arguments $removeArgs | Out-Null
$gbLeft = @($layout.partitions | Where-Object { $script:GbNames -contains $_.name -and [int64]$_.offset -ge ([int64]$anchor.offset + [int64]$anchor.length) })
# D: still smaller than before the install (e.g. the install stopped after the shrink): extend it even if no
# Googlebook partitions are left
$stillShrunk = [bool]($targetLen -and [int64]$anchor.length -lt $targetLen)

$entry = $null
if (-not $ImageMode -and -not $KeepBootEntry -and (Test-Admin)) {
    $entry = Find-GooglebookEntry $EntryName $(if ($state) { $state.entry_id } else { $null })
    if ($entry) { Write-Ok "firmware boot entry: $($entry.Id) '$($entry.Description)' $($entry.Device) $($entry.Path)" } else { Write-Info "no firmware boot entry '$EntryName' found" }
}

if ($script:GbFailures.Count) {
    Write-Host ("REFUSING: {0} check(s) failed" -f $script:GbFailures.Count) -ForegroundColor Red
    if ($Apply) { exit 1 }
}

trap {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host ('Backups of this run: ' + $runDir + '  - see README_DUALBOOT.md, section Recovery') -ForegroundColor Red
    if ($script:GbApply) { try { Stop-Transcript | Out-Null } catch { } }
    exit 1
}

Write-Step "1. Backups -> $runDir"
Invoke-Change "create $runDir and log this run there" '' {
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    Start-Transcript -Path (Join-Path $runDir 'uninstall.log') | Out-Null
}
if (-not $ImageMode) {
    Invoke-Change 'export the BCD store' "bcdedit /export $runDir\bcd.bak" { Invoke-Native 'bcdedit.exe' @('/export', (Join-Path $runDir 'bcd.bak')) | Out-Null }
}
Invoke-Change 'save the raw GPT and the layout' "gbdisk backup ... --out $runDir --tag pre_uninstall" {
    Invoke-GbDisk -Arguments (@('backup', '--out', $runDir, '--tag', 'pre_uninstall') + $target) | Out-Null
}

Write-Step '2. Firmware boot entry'
if ($ImageMode) { Write-Info 'image mode: firmware boot configuration not touched (real disk: bcdedit /delete {ID})' }
elseif ($KeepBootEntry) { Write-Info 'kept (-KeepBootEntry)' }
else {
    $eid = if ($entry) { $entry.Id } elseif ($state -and $state.entry_id) { $state.entry_id } else { $null }
    if ($eid) {
        # never Windows Boot Manager / the firmware boot manager / anything that starts Windows
        Assert-SafeEntryId $eid
        if ($entry -and ($entry.Description -eq 'Windows Boot Manager' -or $entry.Path -match '\\EFI\\Microsoft\\')) { Write-Bad "entry $eid looks like Windows Boot Manager ($($entry.Description), $($entry.Path)) - not deleting it"; exit 1 }
        Invoke-Change "delete the firmware boot entry $eid" "bcdedit /delete $eid" {
            Invoke-Native 'bcdedit.exe' @('/delete', $eid) | Out-Null
            if (Find-GooglebookEntry $EntryName $eid) { throw "entry $eid still present" }
            Write-Ok 'boot entry removed (Windows Boot Manager untouched)'
        }
    } else { Write-Info 'none to delete' }
}

if ($gbLeft.Count -eq 0 -and -not $stillShrunk) {
    Write-Step '3-5. Partitions'
    Write-Ok 'no Googlebook partitions on the target: the partition table and D: are left alone'
    if ($Apply) { Write-Ok 'done'; Stop-Transcript | Out-Null } else { Write-Host 'DRY RUN complete: nothing was changed.' -ForegroundColor White }
    exit 0
}

Write-Step '3. Delete the Googlebook partitions'
if ($gbLeft.Count -eq 0) { Write-Info "none left; $($ExtendDriveLetter): is still $(Format-MiB $anchor.length) of $(Format-MiB $targetLen): giving the space back" }
else {
    Invoke-Change 'remove them from the GPT (one SET_DRIVE_LAYOUT_EX, every other entry verbatim)' ("gbdisk " + (($removeArgs + '--apply') -join ' ')) {
        Invoke-GbDisk -Arguments ($removeArgs + '--apply') | Out-Null
    }
}

Write-Step '4. Give the space back'
if ($NoExtend -or -not $anchor) { Write-Info 'skipped' }
elseif ($ImageMode) {
    if (-not $targetLen) { Write-Bad 'original length unknown in image mode'; exit 1 }
    Invoke-Change "simulate extending #$($anchor.number) back to $(Format-MiB $targetLen)" "gbdisk resize-image --number $($anchor.number) --length $targetLen --apply" {
        Invoke-GbDisk -Arguments @('resize-image', '--image', $TargetImage, '--number', "$($anchor.number)", '--length', "$targetLen", '--apply') | Out-Null
    }
} else {
    $sizeText = if ($targetLen) { "$targetLen (the original size)" } else { '(Get-PartitionSupportedSize).SizeMax' }
    Invoke-Change "extend $($ExtendDriveLetter): (partition #$($anchor.number)) to $sizeText" "Resize-Partition -DiskNumber $DiskNumber -PartitionNumber <D:> -Size $sizeText" {
        $p = Get-Partition -DiskNumber $DiskNumber | Where-Object { [int64]$_.Offset -eq [int64]$anchor.offset }
        $sz = $targetLen
        $max = [int64](Get-PartitionSupportedSize -DiskNumber $DiskNumber -PartitionNumber $p.PartitionNumber).SizeMax
        if (-not $sz -or $sz -gt $max) { $sz = $max }
        Resize-Partition -DiskNumber $DiskNumber -PartitionNumber $p.PartitionNumber -Size $sz
        Write-Ok ("{0}: is now {1}" -f $ExtendDriveLetter, (Format-MiB (Get-Partition -DiskNumber $DiskNumber -PartitionNumber $p.PartitionNumber).Size))
    }
}

Write-Step '5. Verify against the pre-install layout'
Invoke-Change 'compare every pre-install partition entry with the disk now' '' {
    $now = Get-GbLayout $target
    $left = @($now.partitions | Where-Object { $script:GbNames -contains $_.name -and [int64]$_.offset -gt [int64]$anchor.offset })
    if ($left.Count) { Write-Bad ('Googlebook partitions still present: ' + (($left | ForEach-Object { $_.name }) -join ', ')) } else { Write-Ok 'no Googlebook partitions left' }
    if ($state -and (Test-Path $state.pre_shrink_layout)) {
        $pre = Read-Json $state.pre_shrink_layout
        foreach ($p in $pre.partitions) {
            $q = $now.partitions | Where-Object { $_.id -eq $p.id } | Select-Object -First 1
            $same = $q -and ([int64]$q.offset -eq [int64]$p.offset) -and ([int64]$q.length -eq [int64]$p.length) -and ($q.type -eq $p.type) -and ($q.name -eq $p.name) -and ($q.attributes -eq $p.attributes)
            if ($same) { Write-Ok "#$($p.number) '$($p.name)' $($p.id) as before the install" } else { Write-Bad "#$($p.number) '$($p.name)' differs from before the install" }
        }
        if (@($now.partitions).Count -ne @($pre.partitions).Count) { Write-Bad "partition count $(@($now.partitions).Count), before the install $(@($pre.partitions).Count)" }
    }
    if ($script:GbFailures.Count) { throw 'post-uninstall verification failed' }
    if ($state) { $state.phase = 'uninstalled'; Save-Json $state $StateFile }
}
if ($Apply) { Write-Ok 'uninstalled'; Stop-Transcript | Out-Null } else { Write-Host 'DRY RUN complete: nothing was changed.' -ForegroundColor White }
