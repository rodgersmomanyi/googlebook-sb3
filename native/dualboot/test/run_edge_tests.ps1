<#
.SYNOPSIS
  Edge-case / refusal tests of the dual-boot installer in image mode (no admin, never touches a real disk).
  Each refused operation must leave the image byte-identical (gpt_validate.py validate --expect clean).
    E1 not enough space after the shrink              E2 shrink larger than D:
    E3 D: is not the last data partition (E: after it): install + uninstall, E: untouched
    E4 a foreign Googlebook-named partition before D:: install refused, forced remove leaves it alone
    E5 image file too small / not a GPT / D: too small  E6 export image truncated / corrupted
    E7 tampered plan for write and for remove            E8 install stopped after the shrink -> uninstall extends D:
    E9 forced remove anchored at the wrong partition   E10 device path as --image
#>
param(
    [string]$WorkDir = (Join-Path $env:TEMP 'gb-edge'),
    [string]$ExportDir = 'D:\gb-test\export_small',
    [string]$LogDir = (Join-Path $PSScriptRoot 'logs'),
    [switch]$Keep
)
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
# images end up fully allocated (about 9 GiB each); only one exists at a time - each is deleted after its case
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$volLetter = (Split-Path -Qualifier ([IO.Path]::GetFullPath($WorkDir))).TrimEnd(':')
$freeNow = [int64](Get-Volume -DriveLetter $volLetter).SizeRemaining
if ($freeNow -lt 22GB) { Write-Host ("REFUSING: {0}: has {1:N1} GiB free; the edge tests need 22 GiB (one 9 GiB image + an export copy + margin)" -f $volLetter, ($freeNow / 1GB)) -ForegroundColor Red; exit 98 }
$py = (Get-Command python).Source
$val = Join-Path $root 'tools\gpt_validate.py'
$gbd = Join-Path $root 'gbdisk.exe'
New-Item -ItemType Directory -Force -Path $WorkDir, $LogDir | Out-Null
$log = Join-Path $LogDir 'edge_cases.log'
"edge-case tests $(Get-Date -Format o)  work=$WorkDir export=$ExportDir" | Out-File -Encoding utf8 $log
$results = New-Object System.Collections.ArrayList

function Step([string]$Name, [scriptblock]$Body, [int]$Expect = 0) {
    Write-Host "---- $Name" -ForegroundColor Cyan
    "`r`n---- $Name" | Out-File -Append -Encoding utf8 $log
    $out = & $Body 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $out | Out-File -Append -Encoding utf8 $log
    $ok = ($code -eq $Expect) -or ($Expect -ne 0 -and $code -ne 0)
    $line = "{0} {1} (exit {2}, expected {3})" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $Name, $code, $(if ($Expect -eq 0) { '0' } else { 'non-zero' })
    Write-Host $line -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
    $line | Out-File -Append -Encoding utf8 $log
    [void]$results.Add($line)
    return $out
}
function PS1([string]$Script, [string[]]$Args2) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root $Script) @Args2 }
function Fresh([string]$Name, [string[]]$MkArgs) {
    $img = Join-Path $WorkDir "$Name.img"
    foreach ($f in @($img, "$img.gbstate.json", "$img.snap.json")) { if (Test-Path $f) { Remove-Item -Force $f } }
    if (Test-Path "$img.gbbackup") { Remove-Item -Recurse -Force "$img.gbbackup" }
    Step "$Name : mkimage $($MkArgs -join ' ')" { & $py -I -B $val mkimage $img @MkArgs } | Out-Null
    Step "$Name : snapshot" { & $py -I -B $val snapshot $img "$img.snap.json" } | Out-Null
    return $img
}
function Clean([string]$Img, [string]$Snap) { Step "  validate: $([IO.Path]::GetFileName($Img)) byte-identical" { & $py -I -B $val validate $Img --snapshot $Snap --expect clean } | Out-Null }
function PartId([string]$Img, [string]$Name, [int]$Nth = 0) {
    $l = (& $gbd layout --image $Img --json) -join "`n" | ConvertFrom-Json
    return @($l.partitions | Where-Object { $_.name -eq $Name })[$Nth].id
}
function Drop([string]$Img) {   # an image is ~9 GiB allocated: delete it as soon as its case is done
    foreach ($x in @($Img, "$Img.gbbackup")) { if (Test-Path $x) { Remove-Item -Recurse -Force $x } }
}
function StateOf([string]$Img) { $p = Get-Content -Raw "$Img.gbstate.json" | ConvertFrom-Json; return (Get-Content -Raw $p.state | ConvertFrom-Json) }

# ---------------------------------------------------------------- E1 / E2
$img = Fresh 'e1' @('--d-mib', '6144')
Step 'E1 install -ShrinkGB 1 -Apply refused (no room for the Googlebook set after the 1 GiB reserve)' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '1', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
Step 'E2 install -ShrinkGB 7 -Apply refused (D: is only 6 GiB)' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '7', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
Step 'E2b -UserdataGB larger than the freed space refused' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '4', '-UserdataGB', '4', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
Drop $img

# ---------------------------------------------------------------- E3 D: not the last data partition
$img = Fresh 'e3' @('--d-mib', '6144', '--extra-after-d-mib', '512')
Step 'E3 install -Apply with E: between D: and Recovery' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '4', '-Apply') } | Out-Null
$st = StateOf $img
Step '  validate: installed between D: and E:, E: and Recovery byte-identical' { & $py -I -B $val validate $img --snapshot "$img.snap.json" --expect installed --plan $st.plan --source-dir $ExportDir } | Out-Null
$p = Get-Content -Raw $st.plan | ConvertFrom-Json
$eId = PartId $img 'Basic data partition' 2
Step '  plan region ends at E: (not at Recovery)' { $l = (& $gbd layout --image $img --json) -join "`n" | ConvertFrom-Json; $e = $l.partitions | Where-Object { $_.id -eq $eId }; if ([int64]$p.region.end -eq [int64]$e.offset) { 'region end = E: offset'; $global:LASTEXITCODE = 0 } else { "region end $($p.region.end) != E: $($e.offset)"; $global:LASTEXITCODE = 1 } } | Out-Null
Step 'E3 uninstall -Apply' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $img, '-Apply') } | Out-Null
Step '  validate: uninstalled, GPT byte-identical to before' { & $py -I -B $val validate $img --snapshot "$img.snap.json" --expect uninstalled --plan $st.plan } | Out-Null
Drop $img

# ---------------------------------------------------------------- E4 foreign Googlebook-named partition before D:
$img = Fresh 'e4' @('--d-mib', '6144', '--foreign-before-d', 'userdata:64')
Step 'E4 install -Apply refused (a partition named userdata exists)' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '4', '-ImageShrinkPartition', '5', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
$dId = PartId $img 'Basic data partition' 1
Step 'E4 gbdisk remove --force without an anchor refused' { & $gbd remove --image $img --force --apply } -Expect 1 | Out-Null
Step 'E4 gbdisk remove --force --anchor-id D: --apply keeps the foreign userdata (it lies before D:)' { & $gbd remove --image $img --force --anchor-id $dId --apply } | Out-Null
Clean $img "$img.snap.json"
Step 'E4 uninstall -Force without state refused in image mode (D: unknown)' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $img, '-Force', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
Drop $img

# ---------------------------------------------------------------- E5 image too small / no GPT / D: too small
$tiny = Join-Path $WorkDir 'e5_tiny.img'; [IO.File]::WriteAllBytes($tiny, (New-Object byte[] (1MB)))
$zero = Join-Path $WorkDir 'e5_zero.img'; [IO.File]::WriteAllBytes($zero, (New-Object byte[] (8MB)))
$h1 = (Get-FileHash $tiny).Hash; $h2 = (Get-FileHash $zero).Hash
Step 'E5 install on a 1 MiB file refused' { PS1 'install_dualboot.ps1' @('-TargetImage', $tiny, '-ExportDir', $ExportDir, '-ShrinkGB', '1', '-Apply') } -Expect 1 | Out-Null
Step 'E5 install on an 8 MiB file without a GPT refused' { PS1 'install_dualboot.ps1' @('-TargetImage', $zero, '-ExportDir', $ExportDir, '-ShrinkGB', '1', '-Apply') } -Expect 1 | Out-Null
Step '  both files unchanged' { if ((Get-FileHash $tiny).Hash -eq $h1 -and (Get-FileHash $zero).Hash -eq $h2) { 'unchanged'; $global:LASTEXITCODE = 0 } else { 'CHANGED'; $global:LASTEXITCODE = 1 } } | Out-Null
$img = Fresh 'e5' @('--c-mib', '8', '--d-mib', '64')
Step 'E5 install on an image whose D: is 64 MiB refused' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '1', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
Drop $img; Drop $tiny; Drop $zero

# ---------------------------------------------------------------- E6 export images truncated / corrupted
$bad = Join-Path $WorkDir 'export_bad'
if (Test-Path $bad) { Remove-Item -Recurse -Force $bad }
Copy-Item -Recurse $ExportDir $bad
$img = Fresh 'e6' @('--d-mib', '6144')
$fs = [IO.File]::Open((Join-Path $bad 'boot_a.img'), 'Open', 'ReadWrite'); $fs.SetLength($fs.Length - 1MB); $fs.Close()
Step 'E6 install with a truncated boot_a.img refused' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $bad, '-ShrinkGB', '4', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
Copy-Item -Force (Join-Path $ExportDir 'boot_a.img') (Join-Path $bad 'boot_a.img')
$fs = [IO.File]::Open((Join-Path $bad 'vbmeta_a.img'), 'Open', 'ReadWrite'); $fs.Position = 100; $b = $fs.ReadByte(); $fs.Position = 100; $fs.WriteByte(($b -bxor 0xFF)); $fs.Close()
Step 'E6 install with a corrupted vbmeta_a.img (same size) refused at the sha256 check, before the shrink' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $bad, '-ShrinkGB', '4', '-Apply') } -Expect 1 | Out-Null
Clean $img "$img.snap.json"
Drop $img; Remove-Item -Recurse -Force $bad

# ---------------------------------------------------------------- E7 tampered plans
$img = Fresh 'e7' @('--d-mib', '6144')
Step 'E7 install -Apply' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '4', '-Apply') } | Out-Null
$st = StateOf $img
& $py -I -B $val snapshot $img "$img.snap1.json" | Out-Null
$dId = PartId $img 'Basic data partition' 1
$pl = Get-Content -Raw $st.plan | ConvertFrom-Json
$dBase = $pl.base | Where-Object { $_.id -eq $dId }
# a) a "Googlebook" partition that is really D: (its GUID, offset and length)
$t = Get-Content -Raw $st.plan | ConvertFrom-Json
$t.partitions[1].id = $dId; $t.partitions[1].offset = [int64]$dBase.offset; $t.partitions[1].length = [int64]$dBase.length
$fa = Join-Path $WorkDir 'e7_write_d.json'; $t | ConvertTo-Json -Depth 6 | Out-File -Encoding ascii $fa
Step 'E7a gbdisk write with a plan entry pointing at D: refused' { & $gbd write --plan $fa --source-dir $ExportDir --image $img --apply } -Expect 1 | Out-Null
# b) a plan entry renamed to a Windows name
$t = Get-Content -Raw $st.plan | ConvertFrom-Json
$t.partitions[1].name = 'Basic data partition'
$fb = Join-Path $WorkDir 'e7_write_name.json'; $t | ConvertTo-Json -Depth 6 | Out-File -Encoding ascii $fb
Step 'E7b gbdisk write with a non-Googlebook plan entry refused' { & $gbd write --plan $fb --source-dir $ExportDir --image $img --apply } -Expect 1 | Out-Null
# c) remove with a plan whose super moved
$t = Get-Content -Raw $st.plan | ConvertFrom-Json
$sp = $t.partitions | Where-Object { $_.name -eq 'super' }; $sp.offset = [int64]$sp.offset + 1MB
$fc = Join-Path $WorkDir 'e7_remove_moved.json'; $t | ConvertTo-Json -Depth 6 | Out-File -Encoding ascii $fc
Step 'E7c gbdisk remove with a plan that does not match the disk refused' { & $gbd remove --plan $fc --protect $st.pre_shrink_layout --image $img --apply } -Expect 1 | Out-Null
Clean $img "$img.snap1.json"

# ---------------------------------------------------------------- E8 install stopped after the shrink
Step 'E8 (simulate) remove the Googlebook partitions only, D: stays shrunk' { & $gbd remove --plan $st.plan --protect $st.pre_shrink_layout --anchor-id $dId --image $img --apply } | Out-Null
Step 'E8 uninstall -Apply gives the space back although no Googlebook partition is left' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $img, '-Apply') } | Out-Null
Step '  validate: GPT byte-identical to before the install' { & $py -I -B $val validate $img --snapshot "$img.snap.json" --expect uninstalled --plan $st.plan } | Out-Null
Drop $img

# ---------------------------------------------------------------- E9 forced remove anchored at C:
$img = Fresh 'e9' @('--d-mib', '6144')
Step 'E9 install -Apply' { PS1 'install_dualboot.ps1' @('-TargetImage', $img, '-ExportDir', $ExportDir, '-ShrinkGB', '4', '-Apply') } | Out-Null
& $py -I -B $val snapshot $img "$img.snap1.json" | Out-Null
$cId = PartId $img 'Basic data partition' 0
Step 'E9 gbdisk remove --force --anchor-id C: refused (D: lies between C: and the Googlebook set)' { & $gbd remove --image $img --force --anchor-id $cId --apply } -Expect 1 | Out-Null
Clean $img "$img.snap1.json"
Step 'E9 uninstall -Apply' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $img, '-Apply') } | Out-Null
$st = StateOf $img
Step '  validate: uninstalled' { & $py -I -B $val validate $img --snapshot "$img.snap.json" --expect uninstalled --plan $st.plan } | Out-Null
Drop $img

# ---------------------------------------------------------------- E10 device paths are never images (no --apply: read-only even if opened)
Step 'E10 gbdisk layout --image \\.\PhysicalDrive0 refused' { & $gbd layout --image '\\.\PhysicalDrive0' } -Expect 1 | Out-Null
Step 'E10 gbdisk layout --image \\?\GLOBALROOT\Device\Harddisk0\DR0 refused' { & $gbd layout --image '\\?\GLOBALROOT\Device\Harddisk0\DR0' } -Expect 1 | Out-Null

$fails = @($results | Where-Object { $_ -like 'FAIL*' }).Count
$sum = "SUMMARY edge cases: $($results.Count - $fails) passed, $fails failed"
$sum | Out-File -Append -Encoding utf8 $log
Write-Host $sum -ForegroundColor $(if ($fails) { 'Red' } else { 'Green' })
if (-not $Keep) { Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue }
exit $fails
