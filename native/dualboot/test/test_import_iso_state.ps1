<#
.SYNOPSIS
  Test of Import-GooglebookIsoInstall.ps1 and of uninstall_dualboot.ps1's guards for installs made by the installer
  ISO - image mode only (no admin, never touches a real disk; every image is a sparse file below -WorkDir).
    1 mkimage (C 1024 / D 26624 MiB) + gbdisk resize-image #4 -> 2 GiB (stands in for "Shrink D:" in Windows)
    2 an ISO-style install: gbdisk backup --tag pre_install into a gbinstall fixture, gbdisk plan, gbdisk create,
      an ISO-style install_state.json (mode iso) + SHA256SUMS
    3 negative imports (exit 2, image unchanged): -EspId with -TargetImage, wrong disk id, tampered SHA256SUMS,
      missing partition
    4 import dry run (image unchanged, no pointer), -Apply, a second import without -Force refused
    5 uninstall_dualboot.ps1 -TargetImage -Apply -> exit 0 (guard A: nothing to extend) -> validate uninstalled
    6 stale pointer: a fresh ISO-style install, the pointer names a state whose plan lists other GUIDs ->
      uninstall -Apply exit 1 ('are not from'), image byte-identical, no uninstall-* run folder
#>
param(
    [string]$WorkDir = (Join-Path $env:TEMP 'gb-import-iso'),
    [string]$ExportDir = 'D:\gb-test\export_small',
    [string]$LogDir = (Join-Path $PSScriptRoot 'logs'),
    [switch]$Keep
)
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path $WorkDir, $LogDir | Out-Null
$volLetter = (Split-Path -Qualifier ([IO.Path]::GetFullPath($WorkDir))).TrimEnd(':')
$freeNow = [int64](Get-Volume -DriveLetter $volLetter).SizeRemaining
if ($freeNow -lt 4GB) { Write-Host ("REFUSING: {0}: has {1:N1} GiB free; this test needs 4 GiB (sparse images, only partition tables are written)" -f $volLetter, ($freeNow / 1GB)) -ForegroundColor Red; exit 98 }
$py = (Get-Command python).Source
$val = Join-Path $root 'tools\gpt_validate.py'
$gbd = Join-Path $root 'gbdisk.exe'
$manifest = Join-Path $ExportDir 'manifest.json'
$log = Join-Path $LogDir 'import_iso.log'
"import-iso tests $(Get-Date -Format o)  work=$WorkDir export=$ExportDir" | Out-File -Encoding utf8 $log
$results = New-Object System.Collections.ArrayList

function Step([string]$Name, [scriptblock]$Body, [int]$Expect = 0, [string]$MustContain) {
    Write-Host "---- $Name" -ForegroundColor Cyan
    "`r`n---- $Name" | Out-File -Append -Encoding utf8 $log
    $out = & $Body 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $out | Out-File -Append -Encoding utf8 $log
    $ok = ($code -eq $Expect)
    if ($ok -and $MustContain) { $ok = [bool](@($out | Where-Object { $_ -like "*$MustContain*" }).Count) }
    $line = "{0} {1} (exit {2}, expected {3}{4})" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $Name, $code, $Expect, $(if ($MustContain) { ", output contains '$MustContain'" } else { '' })
    Write-Host $line -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
    $line | Out-File -Append -Encoding utf8 $log
    [void]$results.Add($line)
    return $out
}
function Assert([string]$Name, [bool]$Ok) {
    $line = "{0} {1}" -f $(if ($Ok) { 'PASS' } else { 'FAIL' }), $Name
    Write-Host $line -ForegroundColor $(if ($Ok) { 'Green' } else { 'Red' })
    $line | Out-File -Append -Encoding utf8 $log
    [void]$results.Add($line)
}
function PS1([string]$Script, [string[]]$Args2) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root $Script) @Args2 }
function Sha256Hex([string]$Path) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
function Write-Utf8([string]$Path, [string]$Text) { [IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false))) }
function Write-Sums([string]$Dir) {
    $lines = foreach ($f in @(Get-ChildItem -LiteralPath $Dir -File | Where-Object { $_.Name -ne 'SHA256SUMS' } | Sort-Object Name)) { "$(Sha256Hex $f.FullName)  $($f.Name)" }
    Write-Utf8 (Join-Path $Dir 'SHA256SUMS') (($lines -join "`n") + "`n")
}
function Copy-Fixture([string]$From, [string]$To) {
    if (Test-Path $To) { Remove-Item -Recurse -Force $To }
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Copy-Item (Join-Path $From '*') $To
}
function Clean([string]$Name, [string]$Img, [string]$Snap) { Step "  validate: $Name - image byte-identical" { & $py -I -B $val validate $Img --snapshot $Snap --expect clean } | Out-Null }

$img = Join-Path $WorkDir 'iso_install.img'
$fx = Join-Path $WorkDir 'gbinstall'
foreach ($f in @($img, "$img.gbstate.json")) { if (Test-Path $f) { Remove-Item -Force $f } }
foreach ($d in @("$img.gbbackup", $fx)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
$snap0 = "$img.snap0.json"; $snap1 = "$img.snap1.json"; $snap2 = "$img.snap2.json"

# ---------------------------------------------------------------- 1 + 2 an ISO-style install on an image
Step 'mkimage (C 1024 / D 26624 MiB)' { & $py -I -B $val mkimage $img --c-mib 1024 --d-mib 26624 } | Out-Null
Step 'gbdisk resize-image #4 -> 2 GiB (the user shrinks D: in Windows)' { & $gbd resize-image --image $img --number 4 --length 2147483648 --apply } | Out-Null
Step 'snapshot before the install' { & $py -I -B $val snapshot $img $snap0 } | Out-Null
New-Item -ItemType Directory -Force -Path $fx | Out-Null
Step 'gbdisk backup --tag pre_install into the gbinstall fixture' { & $gbd backup --image $img --out $fx --tag pre_install } | Out-Null
Step 'gbdisk plan (export_small, anchor 4)' { & $gbd plan --manifest $manifest --anchor 4 --allow-truncated --image $img --out (Join-Path $fx 'plan.json') } | Out-Null
Step 'gbdisk create (the installer writes the same table)' { & $gbd create --plan (Join-Path $fx 'plan.json') --image $img --apply } | Out-Null
$plan = Get-Content -Raw (Join-Path $fx 'plan.json') | ConvertFrom-Json
$em = Get-Content -Raw $manifest | ConvertFrom-Json
$anchorBase = @($plan.base | Where-Object { $_.id -eq $plan.anchor.id })[0]
Copy-Item $manifest (Join-Path $fx 'manifest_installed.json')
$state = [ordered]@{
    format = 'gb-dualboot-state/1'; mode = 'iso'; target = $null; disk_number = $null; disk_id = $plan.disk_id; disk_serial = $null
    run_dir = 'gbinstall'; export_dir = $null; manifest = 'manifest_installed.json'; phase = 'complete'; time = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    anchor = [ordered]@{ number = $plan.anchor.number; id = $plan.anchor.id; offset = [int64]$anchorBase.offset; original_length = [int64]$plan.anchor.length; new_length = [int64]$plan.anchor.length }
    plan = 'plan.json'; pre_shrink_layout = 'layout_pre_install.json'
    esp_id = @($em.partitions | Where-Object { $_.name -eq 'EFI-SYSTEM' })[0].guid; super_id = @($em.partitions | Where-Object { $_.name -eq 'super' })[0].guid
    entry_id = $null; entry_name = 'Googlebook OS'
    iso = [ordered]@{ build_id = '20261025-000000-00000000'; payload_manifest_sha256 = ('0' * 64); efi_boot_number = '0005'; efi_partition_number = 6; disk_kname = 'nvme0n1'; gpt_written = $true }
}
Write-Utf8 (Join-Path $fx 'install_state.json') ($state | ConvertTo-Json -Depth 6)
Write-Sums $fx
Step 'snapshot after the ISO-style install' { & $py -I -B $val snapshot $img $snap1 } | Out-Null

# ---------------------------------------------------------------- 3 negative imports
Step 'NEG: -EspId together with -TargetImage refused' { PS1 'Import-GooglebookIsoInstall.ps1' @('-EspId', $state.esp_id, '-TargetImage', $img, '-Apply') } -Expect 2 -MustContain '-TargetImage works with -From only' | Out-Null
Step 'NEG: -EspId together with -From refused' { PS1 'Import-GooglebookIsoInstall.ps1' @('-EspId', $state.esp_id, '-From', $fx) } -Expect 2 -MustContain 'exactly one source' | Out-Null
$neg = Join-Path $WorkDir 'neg'
Copy-Fixture $fx $neg
$s = Get-Content -Raw (Join-Path $neg 'install_state.json') | ConvertFrom-Json
$s.disk_id = [guid]::NewGuid().ToString()
Write-Utf8 (Join-Path $neg 'install_state.json') ($s | ConvertTo-Json -Depth 6); Write-Sums $neg
Step 'NEG: state for another disk refused' { PS1 'Import-GooglebookIsoInstall.ps1' @('-TargetImage', $img, '-From', $neg, '-Apply') } -Expect 2 | Out-Null
Copy-Fixture $fx $neg
$t = Get-Content -Raw (Join-Path $neg 'SHA256SUMS')
Write-Utf8 (Join-Path $neg 'SHA256SUMS') ($t.Substring(0, 5) + $(if ($t[5] -eq 'a') { 'b' } else { 'a' }) + $t.Substring(6))
Step 'NEG: tampered SHA256SUMS refused' { PS1 'Import-GooglebookIsoInstall.ps1' @('-TargetImage', $img, '-From', $neg, '-Apply') } -Expect 2 | Out-Null
Copy-Fixture $fx $neg
$p2 = Get-Content -Raw (Join-Path $neg 'plan.json') | ConvertFrom-Json
$p2.partitions[3].id = [guid]::NewGuid().ToString()
Write-Utf8 (Join-Path $neg 'plan.json') ($p2 | ConvertTo-Json -Depth 6 -Compress); Write-Sums $neg
Step 'NEG: a planned partition missing on the disk refused' { PS1 'Import-GooglebookIsoInstall.ps1' @('-TargetImage', $img, '-From', $neg, '-Apply') } -Expect 2 | Out-Null
Assert 'NEG imports: no pointer and no run folder written' (-not (Test-Path "$img.gbstate.json") -and -not (Test-Path "$img.gbbackup"))
Clean 'negative imports' $img $snap1

# ---------------------------------------------------------------- 4 import
Step 'import DRY RUN' { PS1 'Import-GooglebookIsoInstall.ps1' @('-TargetImage', $img, '-From', $fx) } | Out-Null
Assert 'dry run: no pointer and no run folder written' (-not (Test-Path "$img.gbstate.json") -and -not (Test-Path "$img.gbbackup"))
Clean 'import dry run' $img $snap1
Step 'import -Apply' { PS1 'Import-GooglebookIsoInstall.ps1' @('-TargetImage', $img, '-From', $fx, '-Apply') } | Out-Null
$ptr = $null; $imp = $null
if (Test-Path "$img.gbstate.json") { $ptr = Get-Content -Raw "$img.gbstate.json" | ConvertFrom-Json; $imp = Get-Content -Raw $ptr.state | ConvertFrom-Json }
Assert 'import: pointer written, state has absolute paths and the image target' ($imp -and $imp.mode -eq 'iso' -and (Test-Path $imp.plan) -and (Test-Path $imp.pre_shrink_layout) -and (Test-Path $imp.manifest) -and $imp.target -eq "--image $img" -and (Test-Path (Join-Path $imp.run_dir 'install_state.iso.json')))
Clean 'import -Apply' $img $snap1
Step 'NEG: a second import without -Force refused (pointer exists)' { PS1 'Import-GooglebookIsoInstall.ps1' @('-TargetImage', $img, '-From', $fx, '-Apply') } -Expect 2 | Out-Null

# ---------------------------------------------------------------- 5 uninstall with the imported state
Step 'uninstall_dualboot.ps1 -TargetImage -Apply (guard A: D: already has its original size)' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $img, '-Apply') } -MustContain 'already has its original size' | Out-Null
Step '  validate: uninstalled (GPT byte-identical to before the install)' { & $py -I -B $val validate $img --snapshot $snap0 --expect uninstalled --plan (Join-Path $fx 'plan.json') } | Out-Null

# ---------------------------------------------------------------- 6 stale pointer
Step 'a fresh ISO-style install (gbdisk create again)' { & $gbd create --plan (Join-Path $fx 'plan.json') --image $img --apply } | Out-Null
Step 'snapshot of the fresh install' { & $py -I -B $val snapshot $img $snap2 } | Out-Null
$stale = Join-Path $WorkDir 'stale'
Copy-Fixture $fx $stale
$p3 = Get-Content -Raw (Join-Path $stale 'plan.json') | ConvertFrom-Json
foreach ($pp in $p3.partitions) { $pp.id = [guid]::NewGuid().ToString() }
Write-Utf8 (Join-Path $stale 'plan.json') ($p3 | ConvertTo-Json -Depth 6 -Compress)
$s3 = Get-Content -Raw (Join-Path $stale 'install_state.json') | ConvertFrom-Json
$s3.run_dir = $stale; $s3.plan = Join-Path $stale 'plan.json'; $s3.pre_shrink_layout = Join-Path $stale 'layout_pre_install.json'; $s3.manifest = Join-Path $stale 'manifest_installed.json'
Write-Utf8 (Join-Path $stale 'install_state.json') ($s3 | ConvertTo-Json -Depth 6)
Write-Utf8 "$img.gbstate.json" (([ordered]@{ state = (Join-Path $stale 'install_state.json'); updated = (Get-Date).ToString('o') }) | ConvertTo-Json)
$runsBefore = @(Get-ChildItem -Directory -LiteralPath "$img.gbbackup" -Filter 'uninstall-*' -ErrorAction SilentlyContinue).Count
Step 'stale pointer: uninstall -Apply refused by guard B' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $img, '-Apply') } -Expect 1 -MustContain 'are not from' | Out-Null
Clean 'stale pointer' $img $snap2
$runsAfter = @(Get-ChildItem -Directory -LiteralPath "$img.gbbackup" -Filter 'uninstall-*' -ErrorAction SilentlyContinue).Count
Assert "stale pointer: no uninstall-* run folder created ($runsBefore before, $runsAfter after)" ($runsAfter -eq $runsBefore)

$fails = @($results | Where-Object { $_ -like 'FAIL*' }).Count
$sum = "SUMMARY import-iso: $($results.Count - $fails) passed, $fails failed"
$sum | Out-File -Append -Encoding utf8 $log
Write-Host $sum -ForegroundColor $(if ($fails) { 'Red' } else { 'Green' })
if (-not $Keep) { Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue }
exit $fails
