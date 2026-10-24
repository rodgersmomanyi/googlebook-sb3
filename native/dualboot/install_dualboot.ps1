<#
.SYNOPSIS
  Installs Googlebook OS next to Windows on the Surface Book 3 internal NVMe (Boot Camp style).
  DRY RUN by default: prints every action and changes nothing. -Apply performs the install.

.DESCRIPTION
  Steps (-Apply):
    1. preconditions: elevated, UEFI, GPT disk, BitLocker off on the shrink volume (and C:), enough shrinkable space
       (Get-PartitionSupportedSize), Secure Boot state known and OFF, on AC power, no Googlebook partitions or boot
       entry yet, export images match their manifest (sha256)
    2. backups into <BackupRoot>\<timestamp>: bcdedit /export, bcdedit /enum firmware /v + /enum all /v, the storage
       layout (Get-Disk/Get-Partition/Get-Volume), raw GPT copies (LBA 0-33, last 33 LBAs) + IOCTL layout JSON;
       a second copy of the GPT backups next to the export
    3. shrink D: by -ShrinkGB GiB (Resize-Partition)
    4. create the Googlebook partitions in the freed space after D: (gbdisk create: one IOCTL_DISK_SET_DRIVE_LAYOUT_EX
       with every existing entry verbatim + the new ones, exact GPT names/types/GUIDs, vbmeta slot attributes copied
       from the source image; -ReserveMiB left unallocated before the Recovery partition)
    5. write the partition images raw (gbdisk write: bounds-checked against a fresh layout read, volumes on the target
       locked/dismounted, read-back sha256 check; blank partitions zeroed)
    6. add a UEFI boot entry "Googlebook OS" for \EFI\BOOT\BOOTX64.EFI on the new ESP (bcdedit /copy {bootmgr} ...
       /set device/path, {fwbootmgr} displayorder /addlast) - Windows Boot Manager stays first (the default)
  Boot Googlebook once with Restart-ToGooglebook.ps1; every normal boot goes to Windows.

  -TargetImage <raw disk image>: TEST HARNESS. The same partitioning/naming/attribute/raw-write logic runs against an
  image file (no admin needed); "shrink" is simulated on partition -ImageShrinkPartition, bcdedit is never run.

.EXAMPLE
  .\install_dualboot.ps1                              # dry run against disk 0 (no elevation needed for most checks)
  .\install_dualboot.ps1 -ShrinkGB 40 -Apply           # real install (elevated PowerShell)
  .\install_dualboot.ps1 -TargetImage D:\gb-test\disk.img -ExportDir D:\gb-export -ShrinkGB 28 -Apply
#>
[CmdletBinding()]
param(
    [string]$ExportDir = 'D:\gb-export',
    [int]$DiskNumber = 0,
    [string]$ShrinkDriveLetter = 'D',
    [int]$ShrinkGB = 32,
    [int]$ReserveMiB = 1024,
    [int]$UserdataGB = 0,
    [string]$BackupRoot = 'C:\GB-backup',
    [string]$EntryName = 'Googlebook OS',
    [string]$TargetImage,
    [int]$ImageShrinkPartition = 4,
    [int]$ImageMinUserdataMiB = 0,
    [switch]$ZeroFullUserdata,
    [switch]$SkipBootEntry,
    [switch]$UpdateEspOnly,
    [switch]$RepairBootEntry,
    [switch]$VerifySources,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'gb_common.ps1')
$script:GbApply = [bool]$Apply
$ImageMode = [bool]$TargetImage
$MiB = 1MB
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

Write-Host ('Googlebook OS dual-boot installer - ' + $(if ($Apply) { 'APPLY' } else { 'DRY RUN (nothing will be changed; add -Apply)' })) -ForegroundColor White
if ($ImageMode) {
    $TargetImage = (Resolve-Path $TargetImage).Path
    $target = @('--image', $TargetImage)
    Write-Host "target: IMAGE FILE $TargetImage (test harness)"
    $pointer = "$TargetImage.gbstate.json"
    $runDir = Join-Path "$TargetImage.gbbackup" $stamp
} else {
    $target = @('--disk', "$DiskNumber")
    Write-Host "target: PHYSICAL DISK $DiskNumber"
    $pointer = Join-Path $BackupRoot 'googlebook_install.json'
    $runDir = Join-Path $BackupRoot $stamp
}

# ------------------------------------------------------------------------------------------------ manifest
Write-Step "Export $ExportDir"
$manifestPath = Join-Path $ExportDir 'manifest.json'
if (-not (Test-Path $manifestPath)) { Write-Bad "no manifest.json in $ExportDir (run build_internal.py --out $ExportDir)"; exit 2 }
$manifest = Read-Json $manifestPath
if ($manifest.format -ne 'gb-dualboot-manifest/1') { Write-Bad "$manifestPath is not a gb-dualboot manifest"; exit 2 }
if ($manifest.test_truncated) {
    if ($ImageMode) { Write-Warn2 'TEST export (truncated super) - accepted in image mode only' }
    else { Write-Bad 'this is a TEST export (truncated super): refusing on a real disk'; exit 2 }
}
Write-Info "created $($manifest.created) from $($manifest.source), system slot $($manifest.slot), disk selector $($manifest.disk_selector)"
Write-Info "boot entry options: $($manifest.cmdline)"
$exportBytes = 0
foreach ($p in $manifest.partitions) {
    if ($p.file) {
        $f = Join-Path $ExportDir $p.file
        if (-not (Test-Path $f)) { Write-Bad "missing $f" ; continue }
        $len = (Get-Item $f).Length
        if ($len -ne [int64]$p.file_size) { Write-Bad "$f is $len bytes, manifest says $($p.file_size)" } else { $exportBytes += $len }
    }
}
$espM = $manifest.partitions | Where-Object { $_.name -eq 'EFI-SYSTEM' }
$superM = $manifest.partitions | Where-Object { $_.name -eq 'super' }
Write-Ok ("{0} partitions, {1} of images present; ESP {2}, super {3}" -f @($manifest.partitions).Count, (Format-MiB $exportBytes), $espM.guid, $superM.guid)
if ($script:GbFailures.Count) { exit 2 }

# ------------------------------------------------------------------------------------------------ target layout
Write-Step 'Target layout (read-only query)'
$layout = Get-GbLayout $target
Invoke-GbDisk -Arguments (@('layout') + $target) | Out-Null
$gbParts = @($layout.partitions | Where-Object { $script:GbNames -contains $_.name })

# ------------------------------------------------------------------------------------------------ special modes
function Get-InstallState {
    if (-not (Test-Path $pointer)) { return $null }
    $ptr = Read-Json $pointer
    if (-not (Test-Path $ptr.state)) { return $null }
    return (Read-Json $ptr.state)
}

function Add-GooglebookBootEntry([string]$EspId) {
    $before = Get-FirmwareEntries
    $orderBefore = @()
    if ($before -eq $null) {
        if ($script:GbApply) { throw 'bcdedit /enum firmware failed (elevated prompt?)' }
        Write-Warn2 'firmware boot entries not readable here (bcdedit needs elevation)'
    } else {
        $fwb = $before | Where-Object { $_.Id -eq '{fwbootmgr}' } | Select-Object -First 1
        if ($fwb) { $orderBefore = @($fwb.DisplayOrder | Where-Object { $_ }) }
        # without the current boot order the "Windows Boot Manager stays first" check cannot work: stop BEFORE any change
        if ($orderBefore.Count -eq 0) {
            if ($script:GbApply) { throw 'the firmware boot order ({fwbootmgr} displayorder) could not be read - not touching the boot configuration' }
            Write-Warn2 'the firmware boot order could not be read'
        } else { Write-Info ("firmware boot order now: " + ($orderBefore -join ' ')) }
    }
    $script:EspDev = $null; $script:EspVol = $null; $script:NewId = $null
    Invoke-Change 'look up the Windows device of the new ESP' "gbdisk voldev --disk $DiskNumber --id $EspId" {
        for ($i = 0; $i -lt 15 -and -not $script:EspDev; $i++) {
            $o = Invoke-GbDisk -Arguments @('voldev', '--disk', "$DiskNumber", '--id', $EspId) -Quiet -AllowFail
            if ($global:GbLastExit -eq 0) { $script:EspDev = [string]$o[0]; $script:EspVol = [string]$o[1] } else { Start-Sleep -Seconds 2 }
        }
        if (-not $script:EspDev) { throw "no volume for the new ESP $EspId" }
        Write-Info "ESP = $script:EspVol = $script:EspDev"
    }
    Invoke-Change "copy the Windows Boot Manager firmware entry as '$EntryName'" "bcdedit /copy {bootmgr} /d `"$EntryName`"" {
        $o = Invoke-Native 'bcdedit.exe' @('/copy', '{bootmgr}', '/d', $EntryName)
        $id = Get-GuidFromText $o
        Assert-SafeEntryId $id                 # a real new GUID, never empty: '/set' without an id would edit {current}
        $script:NewId = $id
    }
    try {
        Invoke-Change 'point the entry at the Googlebook ESP' 'bcdedit /set {NEW} device partition=\Device\HarddiskVolumeN' {
            Assert-SafeEntryId $script:NewId
            try { Invoke-Native 'bcdedit.exe' @('/set', $script:NewId, 'device', "partition=$script:EspDev") | Out-Null }
            catch {
                # fallback: give the ESP a temporary drive letter, reference it, remove the letter again
                $free = [char[]]([int][char]'S'..[int][char]'Z') | Where-Object { -not (Test-Path "$($_):\") } | Select-Object -First 1
                if (-not $free) { throw }
                Write-Info "retrying with a temporary drive letter $($free):"
                Invoke-Native 'mountvol.exe' @("$($free):", $script:EspVol) | Out-Null
                try { Invoke-Native 'bcdedit.exe' @('/set', $script:NewId, 'device', "partition=$($free):") | Out-Null }
                finally { Invoke-Native 'mountvol.exe' @("$($free):", '/D') | Out-Null }
            }
        }
        Invoke-Change 'set the loader path (systemd-boot)' 'bcdedit /set {NEW} path \EFI\BOOT\BOOTX64.EFI' {
            Assert-SafeEntryId $script:NewId
            Invoke-Native 'bcdedit.exe' @('/set', $script:NewId, 'path', '\EFI\BOOT\BOOTX64.EFI') | Out-Null
        }
        Invoke-Change 'append it to the firmware boot order (Windows Boot Manager stays first = default)' 'bcdedit /set {fwbootmgr} displayorder {NEW} /addlast' {
            Assert-SafeEntryId $script:NewId
            Invoke-Native 'bcdedit.exe' @('/set', '{fwbootmgr}', 'displayorder', $script:NewId, '/addlast') | Out-Null
        }
        Invoke-Change 'verify the firmware boot entries' 'bcdedit /enum firmware /v' {
            $after = Get-FirmwareEntries
            if ($after -eq $null) { throw 'bcdedit /enum firmware failed after the change' }
            $e = $after | Where-Object { $_.Id -eq $script:NewId } | Select-Object -First 1
            $fwa = $after | Where-Object { $_.Id -eq '{fwbootmgr}' } | Select-Object -First 1
            if (-not $e) { throw "new entry $script:NewId not listed" }
            if (-not $fwa) { throw 'firmware boot manager ({fwbootmgr}) not listed' }
            if ($e.Path -ne '\EFI\BOOT\BOOTX64.EFI') { throw "entry path is '$($e.Path)'" }
            $orderAfter = @($fwa.DisplayOrder | Where-Object { $_ })
            if ($orderAfter.Count -eq 0 -or $orderAfter[0] -ne $orderBefore[0]) { throw "first boot entry changed: $($orderAfter[0]) (was $($orderBefore[0]))" }
            if ($orderAfter -notcontains $script:NewId) { throw 'new entry not in the firmware boot order' }
            if ($orderAfter[-1] -ne $script:NewId) { throw 'new entry is not last in the firmware boot order' }
            Write-Ok "entry $script:NewId '$($e.Description)' device $($e.Device) path $($e.Path); first in boot order still $($orderAfter[0])"
        }
    } catch {
        $err = $_
        if ($script:NewId -and $script:GbApply) {
            try { Assert-SafeEntryId $script:NewId; Write-Warn2 "rolling back: bcdedit /delete $script:NewId"; & bcdedit.exe /delete $script:NewId | Out-Null }
            catch { Write-Warn2 "no rollback: $($_.Exception.Message)" }
        }
        throw $err
    }
    return $script:NewId
}

if ($UpdateEspOnly -or $RepairBootEntry) {
    $state = Get-InstallState
    if (-not $state) { Write-Bad "no install state at $pointer"; exit 2 }
    Write-Info "installed set: $($state.run_dir) (phase $($state.phase))"
    if ($state.disk_id -and $state.disk_id -ne $layout.disk_id) { Write-Bad "the install state is for disk $($state.disk_id), the target is $($layout.disk_id)"; exit 2 }
    if ($UpdateEspOnly) {
        if (-not $ImageMode -and $Apply -and -not (Test-Admin)) { Write-Bad 'run from an elevated PowerShell'; exit 2 }
        if ($espM.guid -ne $state.esp_id -or $superM.guid -ne $state.super_id) {
            Write-Bad "export GUIDs (ESP $($espM.guid), super $($superM.guid)) differ from the installed set (ESP $($state.esp_id), super $($state.super_id)): rebuild with build_internal.py --reuse-guids <installed manifest>"
            exit 2
        }
        Write-Step 'Rewrite only EFI-SYSTEM (kernel/initrd update; userdata and system untouched)'
        $planEsp = if ($Apply) { Join-Path $state.run_dir "plan_esp_$stamp.json" } else { '-' }
        $planArgs = @('plan', '--manifest', $manifestPath, '--existing', '--out', $planEsp) + $target
        if ($ImageMode) { $planArgs += '--allow-truncated' }
        if ($Apply) { Invoke-GbDisk -Arguments $planArgs | Out-Null } else { Invoke-GbDisk -Arguments $planArgs -Quiet | Out-Null }
        Invoke-Change 'write the new EFI-SYSTEM image and verify it' "gbdisk write --plan $planEsp --only EFI-SYSTEM --source-dir $ExportDir --apply" {
            Invoke-GbDisk -Arguments (@('write', '--plan', $planEsp, '--only', 'EFI-SYSTEM', '--source-dir', $ExportDir, '--apply') + $target) | Out-Null
        }
        exit 0
    }
    if ($RepairBootEntry) {
        if ($ImageMode) { Write-Info 'image mode: no firmware boot entries'; exit 0 }
        if (-not (Test-Admin)) { Write-Bad 'run from an elevated PowerShell'; exit 2 }
        $e = Find-GooglebookEntry $EntryName $state.entry_id
        if ($e) { Write-Ok "boot entry present: $($e.Id) '$($e.Description)' $($e.Device) $($e.Path)"; exit 0 }
        Write-Step "Re-create the firmware boot entry '$EntryName'"
        $id = Add-GooglebookBootEntry $state.esp_id
        if ($Apply) { $state.entry_id = $id; Save-Json $state (Join-Path $state.run_dir 'install_state.json') }
        exit 0
    }
}

# ------------------------------------------------------------------------------------------------ preconditions
Write-Step 'Preconditions'
$isAdmin = Test-Admin
if ($gbParts.Count) { Write-Bad ("Googlebook partitions already on the target: " + (($gbParts | ForEach-Object { $_.name }) -join ', ') + ' - run uninstall_dualboot.ps1 first') }
else { Write-Ok 'no Googlebook partitions on the target yet' }
$conflict = @($layout.partitions | Where-Object { $id = $_.id; @($manifest.partitions | Where-Object { $_.guid -eq $id }).Count -gt 0 })
if ($conflict.Count) { Write-Bad 'the export GUIDs are already in use on the target' }

if ($ImageMode) {
    $anchor = $layout.partitions | Where-Object { $_.number -eq $ImageShrinkPartition } | Select-Object -First 1
    if (-not $anchor) { Write-Bad "image has no partition #$ImageShrinkPartition"; exit 2 }
    Write-Ok "image mode: partition #$($anchor.number) '$($anchor.name)' stands in for $($ShrinkDriveLetter): ($(Format-MiB $anchor.length))"
    Write-Info 'image mode: elevation, BitLocker, shrink limit, Secure Boot, AC power and bcdedit do not apply'
    $shrinkMin = $null
} else {
    if ($isAdmin) { Write-Ok 'elevated' } elseif ($Apply) { Write-Bad 'not elevated: run from an elevated PowerShell' }
    else { Write-Warn2 'not elevated: the shrink limit, BitLocker (cmdlet) and firmware boot entries cannot be queried in this dry run' }
    if ($env:firmware_type -eq 'UEFI') { Write-Ok 'UEFI firmware' } else { Write-Bad "firmware type '$($env:firmware_type)' (UEFI required)" }
    $disk = Get-Disk -Number $DiskNumber
    if ($disk.PartitionStyle -ne 'GPT') { Write-Bad "disk $DiskNumber is $($disk.PartitionStyle), not GPT" }
    if ($disk.Guid -ne ('{' + $layout.disk_id + '}')) { Write-Bad "disk GUID mismatch: Get-Disk $($disk.Guid) vs IOCTL $($layout.disk_id)" }
    else { Write-Ok "disk $DiskNumber $($disk.FriendlyName) $($disk.Guid), $([math]::Round($disk.Size / 1GB, 1)) GiB, $($disk.LogicalSectorSize)/$($disk.PhysicalSectorSize) B sectors" }
    if ($disk.LogicalSectorSize -ne 512) { Write-Bad 'logical sector size is not 512' }
    $sp = Get-Partition -DriveLetter $ShrinkDriveLetter
    if ($sp.DiskNumber -ne $DiskNumber) { Write-Bad "$($ShrinkDriveLetter): is on disk $($sp.DiskNumber), not $DiskNumber" }
    $anchor = $layout.partitions | Where-Object { [int64]$_.offset -eq [int64]$sp.Offset } | Select-Object -First 1
    if (-not $anchor -or [int64]$anchor.length -ne [int64]$sp.Size) { Write-Bad "$($ShrinkDriveLetter): (offset $($sp.Offset)) not found in the IOCTL layout"; exit 2 }
    Write-Ok "$($ShrinkDriveLetter): = partition #$($anchor.number) '$($anchor.name)' $($anchor.id), $(Format-MiB $anchor.length)"
    foreach ($L in @($ShrinkDriveLetter, 'C')) {
        $bl = Get-BitLockerState $L
        if ($bl[0] -eq 'off') { Write-Ok "BitLocker off on $($L): ($($bl[1]))" }
        elseif ($bl[0] -eq 'on') { Write-Bad "BitLocker is ON on $($L): ($($bl[1])) - decrypt it first (or keep the recovery key and suspend it)" }
        else { if ($Apply) { Write-Bad "BitLocker state of $($L): unknown" } else { Write-Warn2 "BitLocker state of $($L): unknown ($($bl[1]))" } }
    }
    $shrinkMin = $null
    try {
        $ss = Get-PartitionSupportedSize -DiskNumber $DiskNumber -PartitionNumber $sp.PartitionNumber -ErrorAction Stop
        $shrinkMin = [int64]$ss.SizeMin
        Write-Ok "$($ShrinkDriveLetter): can shrink to $(Format-MiB $shrinkMin) (max shrink $(Format-MiB ($sp.Size - $shrinkMin)))"
    } catch { if ($Apply) { Write-Bad "Get-PartitionSupportedSize failed: $($_.Exception.Message)" } else { Write-Warn2 'shrink limit unknown (Get-PartitionSupportedSize needs elevation)' } }
    $vol = Get-Volume -DriveLetter $ShrinkDriveLetter
    Write-Info ("{0}: {1}, {2} free of {3}" -f $ShrinkDriveLetter, $vol.FileSystem, (Format-MiB $vol.SizeRemaining), (Format-MiB $vol.Size))
    # an NTFS shrink can never take more than the free space (and needs some slack); checkable without elevation
    if ([int64]$ShrinkGB * 1GB -gt [int64]$vol.SizeRemaining - 2GB) {
        Write-Bad ("{0}: has only {1} free: it cannot give up {2} GiB (keep >= 2 GiB free) - free space on {0}: or lower -ShrinkGB" -f $ShrinkDriveLetter, (Format-MiB $vol.SizeRemaining), $ShrinkGB)
    }
    if ((Split-Path -Qualifier (Resolve-Path $ExportDir).Path).TrimEnd(':') -eq $ShrinkDriveLetter.TrimEnd(':')) {
        Write-Warn2 "the export lies on $($ShrinkDriveLetter): itself ($(Format-MiB $exportBytes)): it takes free space the shrink needs - consider moving it to C: or an external drive"
    }
    $sb = Get-SecureBootState
    if ($sb -eq 'off') { Write-Ok 'Secure Boot off (systemd-boot and the kernel are not signed)' }
    elseif ($sb -eq 'on') { Write-Bad 'Secure Boot is ON: the unsigned systemd-boot/kernel would not start - disable it in Surface UEFI (Security > Secure Boot) first' }
    else { Write-Bad 'Secure Boot state unknown' }
    $ac = Get-ACPower
    if ($ac -eq $true) { Write-Ok 'on AC power' } elseif ($Apply) { Write-Bad 'not on AC power (plug in the charger)' } else { Write-Warn2 'not on AC power' }
    $fs = Get-FastStartupState
    if ($fs -eq 'off') { Write-Ok 'Fast Startup off' }
    else { Write-Warn2 "Fast Startup is $($fs) - after 'Shut down' Windows is hibernated with the disk layout cached. Boot Googlebook only via Restart-ToGooglebook.ps1 (a restart), or turn Fast Startup off (README)" }
    if ($isAdmin -and -not $SkipBootEntry) {
        $fw = Get-FirmwareEntries
        if ($fw -eq $null) { Write-Bad 'bcdedit /enum firmware failed' }
        else {
            $dupe = $fw | Where-Object { $_.Description -eq $EntryName }
            if ($dupe) { Write-Bad "a firmware boot entry '$EntryName' already exists ($($dupe.Id))" } else { Write-Ok "$(@($fw).Count) firmware boot entries, none named '$EntryName'" }
            $fwb0 = $fw | Where-Object { $_.Id -eq '{fwbootmgr}' } | Select-Object -First 1
            $first = if ($fwb0) { @($fwb0.DisplayOrder | Where-Object { $_ })[0] } else { $null }
            if (-not $first) { Write-Bad 'the firmware boot order ({fwbootmgr} displayorder) could not be read' }
            elseif ($first -ne '{bootmgr}') { Write-Warn2 "the first firmware boot entry is $first, not Windows Boot Manager; the installer leaves the first entry as it is" }
            else { Write-Ok 'Windows Boot Manager is first in the firmware boot order (it stays first)' }
        }
    }
}

# shrink size and placement
$shrinkBytes = [int64]$ShrinkGB * 1GB
$newLen = [int64]([math]::Floor(([int64]$anchor.length - $shrinkBytes) / $MiB)) * $MiB
if ($newLen -le 0) { Write-Bad "cannot shrink a $(Format-MiB $anchor.length) partition by $ShrinkGB GiB"; exit 2 }
if ($shrinkMin -ne $null -and $newLen -lt $shrinkMin) { Write-Bad "$($ShrinkDriveLetter): cannot shrink by $ShrinkGB GiB: new size $(Format-MiB $newLen) < minimum $(Format-MiB $shrinkMin) (free space, or run 'defrag $($ShrinkDriveLetter): /X')" }
$next = $layout.partitions | Where-Object { [int64]$_.offset -gt [int64]$anchor.offset } | Sort-Object { [int64]$_.offset } | Select-Object -First 1
if ($next) { Write-Info "next partition after it: #$($next.number) '$($next.name)' type $($next.type) at $($next.offset) (stays in place)" }

Write-Step ("Plan: shrink #{0} by {1} GiB to {2}, Googlebook partitions after it, {3} MiB kept free before the next partition" -f $anchor.number, $ShrinkGB, (Format-MiB $newLen), $ReserveMiB)
$planArgs = @('plan', '--manifest', $manifestPath, '--anchor', "$($anchor.number)", '--anchor-id', "$($anchor.id)", '--anchor-new-length', "$newLen",
              '--reserve', "$([int64]$ReserveMiB * $MiB)") + $target
if ($UserdataGB -gt 0) { $planArgs += @('--userdata', "$([int64]$UserdataGB * 1GB)") }
if ($ImageMode) { $planArgs += '--allow-truncated'; if ($ImageMinUserdataMiB -gt 0) { $planArgs += @('--min-userdata', "$([int64]$ImageMinUserdataMiB * $MiB)") } }
$previewJson = Invoke-GbDisk -Arguments ($planArgs + @('--out', '-')) -Quiet -AllowFail
if ($global:GbLastExit -ne 0) { Write-Bad 'planning failed (see the gbdisk message above)'; $preview = $null }
else {
    $preview = (($previewJson -join "`n") | ConvertFrom-Json)
    $ud = $preview.partitions | Where-Object { $_.name -eq 'userdata' }
    $last = $preview.partitions | Select-Object -Last 1
    Write-Ok ("{0} partitions from {1} to {2} ({3}); userdata {4}" -f @($preview.partitions).Count, $preview.region.start,
              ([int64]$last.offset + [int64]$last.length), (Format-MiB ([int64]$last.offset + [int64]$last.length - [int64]$preview.region.start)), (Format-MiB $ud.length))
}

if ($script:GbFailures.Count) {
    Write-Host ''
    Write-Host ("REFUSING: {0} precondition(s) failed:" -f $script:GbFailures.Count) -ForegroundColor Red
    foreach ($f in $script:GbFailures) { Write-Host "  - $f" -ForegroundColor Red }
    if (-not $Apply) { Write-Host '(dry run: the actions below are what -Apply would do once these are fixed)' -ForegroundColor DarkYellow }
    else { exit 1 }
}
if ($Apply -and -not $preview) { exit 1 }

# ------------------------------------------------------------------------------------------------ actions
trap {
    Write-Host ''
    Write-Host ("FAILED in phase '{0}': {1}" -f $(if ($state) { $state.phase } else { 'preparation' }), $_.Exception.Message) -ForegroundColor Red
    Write-Host ('The steps after this point were not run. State and backups: ' + $runDir) -ForegroundColor Red
    Write-Host 'See README_DUALBOOT.md, section Recovery. uninstall_dualboot.ps1 removes whatever was created.' -ForegroundColor Red
    if ($script:GbApply) { try { Stop-Transcript | Out-Null } catch { } }
    exit 1
}
$stateFile = Join-Path $runDir 'install_state.json'
$state = [ordered]@{
    format = 'gb-dualboot-state/1'; mode = $(if ($ImageMode) { 'image' } else { 'disk' }); target = ($target -join ' ')
    disk_number = $DiskNumber; disk_id = $layout.disk_id; disk_serial = $(if ($ImageMode) { $null } else { [string]$disk.SerialNumber })
    run_dir = $runDir; export_dir = (Resolve-Path $ExportDir).Path
    manifest = (Resolve-Path $manifestPath).Path; phase = 'start'; time = (Get-Date).ToString('o')
    anchor = [ordered]@{ number = $anchor.number; id = $anchor.id; offset = [int64]$anchor.offset; original_length = [int64]$anchor.length; new_length = $newLen }
    plan = (Join-Path $runDir 'plan.json'); pre_shrink_layout = (Join-Path $runDir 'layout_pre_shrink.json')
    esp_id = $espM.guid; super_id = $superM.guid; entry_id = $null; entry_name = $EntryName
}
function Set-Phase([string]$Phase) {
    $state.phase = $Phase; $state.time = (Get-Date).ToString('o')
    if ($script:GbApply) {
        Save-Json $state $stateFile
        Save-Json ([ordered]@{ state = $stateFile; updated = $state.time }) $pointer
    }
}

Write-Step "1. Backups -> $runDir"
Invoke-Change "create $runDir and log this run there" "New-Item -ItemType Directory $runDir; Start-Transcript $runDir\install.log" {
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    if (-not $ImageMode) { New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null }
    Start-Transcript -Path (Join-Path $runDir 'install.log') | Out-Null
}
if (-not $ImageMode) {
    Invoke-Change 'export the BCD store' "bcdedit /export $runDir\bcd.bak" { Invoke-Native 'bcdedit.exe' @('/export', (Join-Path $runDir 'bcd.bak')) | Out-Null }
    Invoke-Change 'save the firmware boot entries and the whole BCD' "bcdedit /enum firmware /v > fw_entries.txt; bcdedit /enum all /v > bcd_all.txt" {
        & bcdedit.exe /enum firmware /v | Out-File -Encoding utf8 (Join-Path $runDir 'fw_entries.txt'); if ($LASTEXITCODE) { throw 'bcdedit /enum firmware failed' }
        & bcdedit.exe /enum all /v | Out-File -Encoding utf8 (Join-Path $runDir 'bcd_all.txt'); if ($LASTEXITCODE) { throw 'bcdedit /enum all failed' }
    }
    Invoke-Change 'save the storage layout (Get-Disk/Get-Partition/Get-Volume)' "-> storage.json" {
        $st = [ordered]@{
            disk = (Get-Disk -Number $DiskNumber | Select-Object Number, FriendlyName, SerialNumber, Size, Guid, PartitionStyle, LogicalSectorSize, PhysicalSectorSize, LargestFreeExtent)
            partitions = @(Get-Partition -DiskNumber $DiskNumber | Select-Object PartitionNumber, DriveLetter, Offset, Size, GptType, Guid, Type, IsSystem, IsBoot, IsHidden, @{ n = 'AccessPaths'; e = { @($_.AccessPaths) } })
            volumes = @(Get-Partition -DiskNumber $DiskNumber | Get-Volume -ErrorAction SilentlyContinue | Select-Object DriveLetter, FileSystem, FileSystemLabel, Size, SizeRemaining, UniqueId, HealthStatus) }
        $st | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $runDir 'storage.json')
    }
}
Invoke-Change 'save the raw GPT (LBA 0-33 + last 33 LBAs) and the GPT layout with names/GUIDs/attributes' "gbdisk backup $($target -join ' ') --out $runDir --tag pre_shrink" {
    Invoke-GbDisk -Arguments (@('backup', '--out', $runDir, '--tag', 'pre_shrink') + $target) | Out-Null
}
Invoke-Change 'keep a copy of the export manifest (partition GUIDs, needed for later ESP-only updates)' "copy $manifestPath -> $runDir\manifest_installed.json" {
    Copy-Item $manifestPath (Join-Path $runDir 'manifest_installed.json')
}
$second = if ($ImageMode) { Join-Path "$TargetImage.gbbackup" ("second-copy\" + $stamp) } else { Join-Path $ExportDir ("gpt-backups\" + $stamp) }
Invoke-Change "second copy of the GPT backups$(if (-not $ImageMode) { ' on the export volume' })" "copy gpt_*.bin layout_*.json -> $second" {
    New-Item -ItemType Directory -Force -Path $second | Out-Null
    Copy-Item (Join-Path $runDir 'gpt_pre_shrink_*.bin'), (Join-Path $runDir 'layout_pre_shrink.json') $second
}
Invoke-Change 'check that the backups exist and are not empty' '' {
    $need = @('gpt_pre_shrink_primary.bin', 'gpt_pre_shrink_backup.bin', 'layout_pre_shrink.json')
    if (-not $ImageMode) { $need += @('bcd.bak', 'fw_entries.txt', 'bcd_all.txt', 'storage.json') }
    foreach ($n in $need) { $f = Join-Path $runDir $n; if (-not (Test-Path $f) -or (Get-Item $f).Length -eq 0) { throw "backup $f missing or empty" } }
    Write-Ok "backups written: $($need -join ', ')"
}
Set-Phase 'backups'

Write-Step '2. Verify the export images (sha256 against the manifest)'
if ($Apply -or $VerifySources) {
    Invoke-GbDisk -Arguments @('checksrc', '--manifest', $manifestPath, '--source-dir', $ExportDir) | Out-Null
} else { Write-Host "    [dry]  would: gbdisk checksrc --manifest $manifestPath --source-dir $ExportDir   (add -VerifySources to run it now; read-only)" -ForegroundColor DarkYellow }

Write-Step ("3. Shrink partition #{0} ({1}) from {2} to {3}" -f $anchor.number, $(if ($ImageMode) { 'image' } else { "$($ShrinkDriveLetter):" }), (Format-MiB $anchor.length), (Format-MiB $newLen))
if ($ImageMode) {
    Invoke-Change 'simulate the shrink (GPT entry only)' "gbdisk resize-image --image $TargetImage --number $($anchor.number) --length $newLen --apply" {
        Invoke-GbDisk -Arguments @('resize-image', '--image', $TargetImage, '--number', "$($anchor.number)", '--length', "$newLen", '--apply') | Out-Null
    }
} else {
    Invoke-Change "shrink $($ShrinkDriveLetter): (NTFS + partition)" "Resize-Partition -DiskNumber $DiskNumber -PartitionNumber $($sp.PartitionNumber) -Size $newLen" {
        # identity re-check right before the first change to the disk: same disk (GUID + serial) and the same D:
        $d2 = Get-Disk -Number $DiskNumber
        if ($d2.Guid -ne $disk.Guid -or [string]$d2.SerialNumber -ne [string]$disk.SerialNumber -or [int64]$d2.Size -ne [int64]$disk.Size) {
            throw "disk $DiskNumber is no longer $($disk.Guid) / serial $($disk.SerialNumber) - not shrinking"
        }
        $p2 = Get-Partition -DiskNumber $DiskNumber -PartitionNumber $sp.PartitionNumber
        if ($p2.Guid -ne ('{' + $anchor.id + '}') -or [int64]$p2.Offset -ne [int64]$anchor.offset -or [int64]$p2.Size -ne [int64]$anchor.length -or [string]$p2.DriveLetter -ne $ShrinkDriveLetter.TrimEnd(':')) {
            throw "partition $($sp.PartitionNumber) is no longer $($ShrinkDriveLetter): $($anchor.id) at $($anchor.offset) - not shrinking"
        }
        $l2 = Get-GbLayout $target
        if ($l2.disk_id -ne $layout.disk_id) { throw 'IOCTL disk GUID changed - not shrinking' }
        Resize-Partition -DiskNumber $DiskNumber -PartitionNumber $sp.PartitionNumber -Size $newLen
        $now = [int64](Get-Partition -DiskNumber $DiskNumber -PartitionNumber $sp.PartitionNumber).Size
        if ($now -gt $newLen) { throw "partition size is $now after the shrink, expected at most $newLen" }
        if ($now -ne $newLen) { Write-Warn2 "size is $now, requested ${newLen}: the placement is re-planned from the actual layout" }
        $state.anchor.new_length = $now
        Write-Ok "$($ShrinkDriveLetter): is now $(Format-MiB $now)"
    }
}
Set-Phase 'shrunk'
Invoke-Change 'GPT backup after the shrink' "gbdisk backup ... --tag post_shrink" {
    Invoke-GbDisk -Arguments (@('backup', '--out', $runDir, '--tag', 'post_shrink') + $target) | Out-Null
}

Write-Step '4. Create the Googlebook partitions'
$planFile = Join-Path $runDir 'plan.json'
$realPlanArgs = @('plan', '--manifest', $manifestPath, '--anchor', "$($anchor.number)", '--anchor-id', "$($anchor.id)", '--reserve', "$([int64]$ReserveMiB * $MiB)", '--out', $planFile) + $target
if ($UserdataGB -gt 0) { $realPlanArgs += @('--userdata', "$([int64]$UserdataGB * 1GB)") }
if ($ImageMode) { $realPlanArgs += '--allow-truncated'; if ($ImageMinUserdataMiB -gt 0) { $realPlanArgs += @('--min-userdata', "$([int64]$ImageMinUserdataMiB * $MiB)") } }
Invoke-Change 'plan on the shrunk layout (same placement as the preview)' ("gbdisk " + ($realPlanArgs -join ' ')) {
    Invoke-GbDisk -Arguments $realPlanArgs | Out-Null
    $real = Read-Json $planFile
    $a = @($real.partitions | ForEach-Object { "$($_.name)@$($_.offset)+$($_.length)" }) -join ';'
    $b = @($preview.partitions | ForEach-Object { "$($_.name)@$($_.offset)+$($_.length)" }) -join ';'
    if ($a -ne $b) { Write-Warn2 'placement differs from the dry-run preview (the shrink came out differently) - using the new plan' }
}
Invoke-Change "create $(@($manifest.partitions).Count) partitions with GPT names, type GUIDs, partition GUIDs, vbmeta slot attributes" "gbdisk create --plan $planFile $($target -join ' ') --apply" {
    Invoke-GbDisk -Arguments (@('create', '--plan', $planFile, '--apply') + $target) | Out-Null
}
Set-Phase 'created'

Write-Step '5. Write the partition images (raw, bounds-checked, read-back verified)'
$writeArgs = @('write', '--plan', $planFile, '--source-dir', $ExportDir, '--apply') + $target
if ($ZeroFullUserdata) { $writeArgs += '--zero-full-userdata' }
Invoke-Change 'write every partition: images from the export, zeros for the blank ones; sha256 read-back' ("gbdisk " + ($writeArgs -join ' ')) {
    Invoke-GbDisk -Arguments $writeArgs | Out-Null
}
Set-Phase 'written'
Invoke-Change 'GPT backup after the install' "gbdisk backup ... --tag post_install" {
    Invoke-GbDisk -Arguments (@('backup', '--out', $runDir, '--tag', 'post_install') + $target) | Out-Null
}

Write-Step "6. UEFI boot entry '$EntryName' -> \EFI\BOOT\BOOTX64.EFI on the Googlebook ESP"
if ($SkipBootEntry) { Write-Info 'skipped (-SkipBootEntry)' }
elseif ($ImageMode) {
    Write-Info 'image mode: the firmware boot configuration is NOT touched; on a real disk this runs:'
    Write-Info "  gbdisk voldev --disk N --id $($espM.guid)            -> \Device\HarddiskVolumeX"
    Write-Info "  bcdedit /copy {bootmgr} /d `"$EntryName`"            -> {NEW}"
    Write-Info '  bcdedit /set {NEW} device partition=\Device\HarddiskVolumeX'
    Write-Info '  bcdedit /set {NEW} path \EFI\BOOT\BOOTX64.EFI'
    Write-Info '  bcdedit /set {fwbootmgr} displayorder {NEW} /addlast'
} else {
    if ($Apply) { $state.entry_id = Add-GooglebookBootEntry $espM.guid } else { Add-GooglebookBootEntry $espM.guid | Out-Null }
}
Set-Phase 'complete'

Write-Step 'Done'
if ($Apply) {
    Write-Ok "installed. State: $stateFile"
    if (-not $ImageMode) {
        Write-Info 'Boot Googlebook once:   .\Restart-ToGooglebook.ps1      (Windows stays the default for every other boot)'
        Write-Info 'First boot formats metadata/userdata (slow). Logs: EFI-SYSTEM\gblog\ (see README_DUALBOOT.md)'
    }
    Stop-Transcript | Out-Null
} else {
    Write-Host 'DRY RUN complete: nothing was changed.' -ForegroundColor White
}
