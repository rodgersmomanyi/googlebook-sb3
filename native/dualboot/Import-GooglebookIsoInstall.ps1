<#
.SYNOPSIS
  Imports the state of a dual-boot install made by the Googlebook SB3 installer ISO (option 2) into C:\GB-backup,
  so that uninstall_dualboot.ps1, install_dualboot.ps1 -UpdateEspOnly and -RepairBootEntry work as for an install
  made by install_dualboot.ps1. DRY RUN by default; -Apply writes the run folder and the pointer.

.DESCRIPTION
  Exactly one source:
    -EspId <GUID>   the Googlebook ESP's partition GUID (the installer shows it). Elevated PowerShell. The files of
                    \gbinstall\ are read from the partition's volume path (\\?\Volume{...}\) into memory: no drive
                    letter, no mountvol, nothing is written to the ESP.
    -From <folder>  a folder holding the gbinstall files (the copy that the installer's tools 'w' writes to a USB
                    stick: <stick>:\gbsb3-logs\<...>\gbinstall), or the test harness's fixture.
  Checks (any failure: exit 2, nothing written): SHA256SUMS; install_state.json is gb-dualboot-state/1 with mode iso;
  plan.json is gb-dualboot-plan/1; the target disk (gbdisk layout, read-only) has the state's disk GUID and every
  planned partition with identical offset/length/type/GUID/name (attributes may differ only in the vbmeta slot bits
  48-56); gbdisk verify --layout-only accepts the plan; an existing pointer is refused unless -Force (then it is kept
  as <pointer>.bak-<stamp>).
  -Apply: <BackupRoot>\<yyyyMMdd-HHmmss>-iso\ gets every file (the ISO's own install_state.json is kept as
  install_state.iso.json), install_state.json is rewritten with absolute paths and the target, and the pointer
  <BackupRoot>\googlebook_install.json is written the same way install_dualboot.ps1 writes it.
  -TargetImage <file> (with -From only): test harness, same conventions as the other scripts (pointer
  <image>.gbstate.json, run folder <image>.gbbackup\<stamp>-iso).

.EXAMPLE
  .\Import-GooglebookIsoInstall.ps1 -EspId <ESP-GUID>            # dry run
  .\Import-GooglebookIsoInstall.ps1 -EspId <ESP-GUID> -Apply     # elevated
  .\Import-GooglebookIsoInstall.ps1 -From E:\gbsb3-logs\20261025-153000-1a2b3c4d-20261026-101500\gbinstall -Apply
#>
[CmdletBinding()]
param(
    [string]$EspId,
    [string]$From,
    [int]$DiskNumber = 0,
    [string]$TargetImage,
    [string]$BackupRoot = 'C:\GB-backup',
    [switch]$Force,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'gb_common.ps1')
$script:GbApply = [bool]$Apply
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$EspType = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
$VbmetaMask = [Convert]::ToUInt64('FE00FFFFFFFFFFFF', 16)      # every attribute bit except 48..56

function Stop-Import([string]$Text) {
    Write-Bad $Text
    Write-Host 'REFUSING: nothing was written.' -ForegroundColor Red
    exit 2
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $h = [System.Security.Cryptography.SHA256]::Create()
    try { return (-join ($h.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}

function ConvertFrom-JsonBytes([byte[]]$Bytes, [string]$Name) {
    try {
        $t = [System.Text.Encoding]::UTF8.GetString($Bytes)
        if ($t.Length -and [int][char]$t[0] -eq 0xFEFF) { $t = $t.Substring(1) }
        return ($t | ConvertFrom-Json)
    } catch { Stop-Import "$Name is not valid JSON: $($_.Exception.Message)" }
}

Write-Host ('Googlebook OS - import an install made by the installer ISO - ' + $(if ($Apply) { 'APPLY' } else { 'DRY RUN (nothing will be changed; add -Apply)' })) -ForegroundColor White

# ------------------------------------------------------------------------------------------------ source
if ([bool]$EspId -eq [bool]$From) { Stop-Import 'give exactly one source: -EspId <ESP partition GUID> or -From <folder>' }
if ($EspId -and $TargetImage) { Stop-Import '-TargetImage works with -From only (test harness); -EspId reads a real disk' }
$ImageMode = [bool]$TargetImage
if ($ImageMode) {
    $TargetImage = (Resolve-Path $TargetImage).Path
    $target = @('--image', $TargetImage)
    $pointer = "$TargetImage.gbstate.json"
    $runDir = Join-Path "$TargetImage.gbbackup" "$stamp-iso"
    Write-Host "target: IMAGE FILE $TargetImage (test harness)"
} else {
    $target = @('--disk', "$DiskNumber")
    $pointer = Join-Path $BackupRoot 'googlebook_install.json'
    $runDir = Join-Path $BackupRoot "$stamp-iso"
    Write-Host "target: PHYSICAL DISK $DiskNumber"
}

Write-Step 'Target layout (read-only query)'
$layout = Get-GbLayout $target
Write-Info "disk $($layout.disk_id): $(@($layout.partitions).Count) partitions"

$files = @{}
if ($EspId) {
    $g = $EspId.Trim().Trim('{', '}').ToLowerInvariant()
    if ($g -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') { Stop-Import "-EspId '$EspId' is not a GUID" }
    if (-not (Test-Admin)) { Write-Bad '-EspId needs an elevated PowerShell (the ESP has no drive letter)'; exit 2 }
    Write-Step "Googlebook ESP $g on disk $DiskNumber (read-only)"
    $esp = @($layout.partitions | Where-Object { $_.id -eq $g -and $_.type -eq $EspType -and $_.name -eq 'EFI-SYSTEM' })
    if ($esp.Count -ne 1) { Stop-Import "disk $DiskNumber has no partition $g of type EFI system named EFI-SYSTEM" }
    $p = @(Get-Partition -DiskNumber $DiskNumber | Where-Object { $_.Guid -eq "{$g}" })
    if ($p.Count -ne 1) { Stop-Import "Get-Partition does not list exactly one partition {$g} on disk $DiskNumber" }
    $vp = @(@($p[0].AccessPaths) | Where-Object { $_ -like '\\?\Volume{*}\' })
    if ($vp.Count -lt 1) {
        Stop-Import "Windows assigned no volume path to this partition; boot the installer, use tools w, then -From <stick>\gbsb3-logs\...\gbinstall"
    }
    $dir = $vp[0] + 'gbinstall\'
    try {
        foreach ($f in [System.IO.Directory]::GetFiles($dir)) { $files[[System.IO.Path]::GetFileName($f)] = [System.IO.File]::ReadAllBytes($f) }
    } catch { Stop-Import "cannot read \gbinstall\ on the Googlebook ESP: $($_.Exception.Message)" }
    Write-Ok "read $($files.Count) files from \gbinstall\ on the Googlebook ESP (into memory)"
} else {
    if (-not (Test-Path -LiteralPath $From -PathType Container)) { Stop-Import "-From $From is not a folder" }
    $dir = (Resolve-Path -LiteralPath $From).Path
    Write-Step "Folder $dir"
    foreach ($f in @(Get-ChildItem -LiteralPath $dir -File)) { $files[$f.Name] = [System.IO.File]::ReadAllBytes($f.FullName) }
    Write-Ok "read $($files.Count) files"
}

# ------------------------------------------------------------------------------------------------ checks
Write-Step 'Checks'
foreach ($need in @('SHA256SUMS', 'install_state.json', 'plan.json', 'layout_pre_install.json', 'manifest_installed.json')) {
    if (-not $files.ContainsKey($need)) { Stop-Import "$need is missing" }
}
$sums = @{}
foreach ($line in ([System.Text.Encoding]::ASCII.GetString($files['SHA256SUMS']) -split "`n")) {
    $l = $line.TrimEnd("`r")
    if (-not $l.Trim()) { continue }
    if ($l -notmatch '^([0-9a-fA-F]{64}) [ *](.+)$') { Stop-Import "SHA256SUMS: unexpected line" }
    $sums[$Matches[2]] = $Matches[1].ToLowerInvariant()
}
foreach ($n in @($files.Keys)) {
    if ($n -eq 'SHA256SUMS') { continue }
    if (-not $sums.ContainsKey($n)) { Stop-Import "SHA256SUMS does not list $n" }
    if ((Get-Sha256Hex $files[$n]) -ne $sums[$n]) { Stop-Import "SHA256SUMS: $n does not match" }
}
foreach ($n in @($sums.Keys)) { if (-not $files.ContainsKey($n)) { Stop-Import "SHA256SUMS lists $n, which is missing" } }
Write-Ok "SHA256SUMS: $($sums.Count) files match"

$st = ConvertFrom-JsonBytes $files['install_state.json'] 'install_state.json'
if ($st.format -ne 'gb-dualboot-state/1' -or $st.mode -ne 'iso') { Stop-Import "install_state.json is not an ISO install state (format '$($st.format)', mode '$($st.mode)')" }
$plan = ConvertFrom-JsonBytes $files['plan.json'] 'plan.json'
if ($plan.format -ne 'gb-dualboot-plan/1') { Stop-Import "plan.json is not a gb-dualboot plan" }
Write-Ok "state: installer build $($st.iso.build_id), phase $($st.phase), ESP $($st.esp_id), anchor #$($st.anchor.number) $($st.anchor.id)"
if ($st.disk_id -ne $layout.disk_id) { Stop-Import "the state is for disk $($st.disk_id), the target is $($layout.disk_id)" }
if ($plan.disk_id -ne $layout.disk_id) { Stop-Import "plan.json is for disk $($plan.disk_id), the target is $($layout.disk_id)" }
Write-Ok "target disk GUID $($layout.disk_id) matches"
$bad = 0
foreach ($pp in @($plan.partitions)) {
    $q = @($layout.partitions | Where-Object { $_.id -eq $pp.id })
    if ($q.Count -ne 1) { Write-Bad "planned partition $($pp.name) $($pp.id) is not on the disk"; $bad++; continue }
    $q = $q[0]
    if ([int64]$q.offset -ne [int64]$pp.offset -or [int64]$q.length -ne [int64]$pp.length -or $q.type -ne $pp.type -or $q.name -ne $pp.name) {
        Write-Bad "partition $($pp.name) $($pp.id) differs from plan.json (offset/length/type/name)"; $bad++; continue
    }
    $x = [Convert]::ToUInt64(([string]$q.attributes).Substring(2), 16) -bxor [Convert]::ToUInt64(([string]$pp.attributes).Substring(2), 16)
    if (($x -band $VbmetaMask) -ne 0) { Write-Bad "partition $($pp.name): attributes $($q.attributes) (plan $($pp.attributes))"; $bad++ }
}
if ($bad) { Stop-Import "$bad planned partition(s) do not match the disk" }
Write-Ok "all $(@($plan.partitions).Count) planned partitions are on the disk as planned"
$planText = [System.Text.Encoding]::UTF8.GetString($files['plan.json'])
Invoke-GbDisk -Arguments (@('verify', '--plan', '-', '--layout-only') + $target) -InputText $planText -Quiet -AllowFail | Out-Null
if ($global:GbLastExit -ne 0) { Stop-Import "gbdisk verify --layout-only refused the plan (exit $global:GbLastExit)" }
Write-Ok 'gbdisk verify --layout-only: OK'
if (Test-Path -LiteralPath $pointer) {
    if (-not $Force) { Stop-Import "an install pointer already exists ($pointer): uninstall/clean up first, or -Force (the old pointer is kept as .bak-$stamp)" }
    Write-Warn2 "an install pointer exists ($pointer): -Force given, it is renamed to $pointer.bak-$stamp"
}

# ------------------------------------------------------------------------------------------------ apply
Write-Step "Import -> $runDir"
$stateFile = Join-Path $runDir 'install_state.json'
Invoke-Change "create $runDir and write the $($files.Count) files (the ISO's state as install_state.iso.json)" '' {
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    foreach ($n in @($files.Keys)) { [System.IO.File]::WriteAllBytes((Join-Path $runDir $n), $files[$n]) }
    [System.IO.File]::WriteAllBytes((Join-Path $runDir 'install_state.iso.json'), $files['install_state.json'])
    foreach ($n in @($files.Keys)) {
        if ((Get-Sha256Hex ([System.IO.File]::ReadAllBytes((Join-Path $runDir $n)))) -ne (Get-Sha256Hex $files[$n])) { throw "read-back of $n differs" }
    }
}
$st.run_dir = $runDir
$st.plan = Join-Path $runDir 'plan.json'
$st.pre_shrink_layout = Join-Path $runDir 'layout_pre_install.json'
$st.manifest = Join-Path $runDir 'manifest_installed.json'
$st.target = ($target -join ' ')
$st.disk_number = $DiskNumber
if (-not $ImageMode -and (Test-Admin)) {
    $e = Find-GooglebookEntry $st.entry_name $null
    if ($e) { $st.entry_id = $e.Id; Write-Ok "firmware boot entry: $($e.Id) '$($e.Description)'" } else { Write-Info "no firmware boot entry '$($st.entry_name)' found (entry_id stays empty)" }
}
Invoke-Change 'write install_state.json with absolute paths and the target' "-> $stateFile" { Save-Json $st $stateFile }
Invoke-Change "write the pointer $pointer" '' {
    if (Test-Path -LiteralPath $pointer) { Rename-Item -LiteralPath $pointer -NewName ((Split-Path -Leaf $pointer) + ".bak-$stamp") }
    if (-not $ImageMode) { New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null }
    Save-Json ([ordered]@{ state = $stateFile; updated = (Get-Date).ToString('o') }) $pointer
}
if ($Apply) {
    Write-Ok "imported: $stateFile"
    Write-Info 'Now uninstall_dualboot.ps1, install_dualboot.ps1 -UpdateEspOnly and -RepairBootEntry use this state.'
} else {
    Write-Host 'DRY RUN complete: nothing was changed.' -ForegroundColor White
}
exit 0
