<#
.SYNOPSIS
  Full install + uninstall cycle of the dual-boot installer against a synthetic disk image (no admin, no real disk).
  1 mkimage (host-shaped GPT: ESP/MSR/C:/D:/Recovery, random data blocks)   2 snapshot
  3 install dry run -> image must be byte-identical                          4 install -Apply -> validate installed
  5 negative tests (second install refused, truncated export refused for a disk, overlap plan refused, remove without
    state refused)                                                           6 -UpdateEspOnly -> validate installed
  6b (wave 3, item 7) -UpdateEspOnly refusals, each exit 2 with the image byte-identical: installed slot a active for an
    export of slot b; ambiguous / unbootable slot attributes; one byte of vendor_boot_<slot> changed (content check);
    loader.conf default of another slot. Then an ESP holding gblog\, gbinstall\, gb\gb.cfg, gb\overlay\: the raw backup
    and the extracted copies match (sha256), the new ESP equals the export; the printed rollback restores the saved
    ESP; a dry run changes nothing; the wanted_slot truth table (test_wanted_slot.ps1)
  7 uninstall dry run -> unchanged                                           8 uninstall -Apply -> validate uninstalled
#>
param(
    [string]$Image = 'D:\gb-test\cycle.img',
    [string]$ExportDir = 'D:\gb-test\export_small',
    [int]$CMiB = 1024,
    [int]$DMiB = 6144,
    [int]$ShrinkGB = 4,
    [int]$ImageMinUserdataMiB = 0,
    [string]$LogDir = (Join-Path $PSScriptRoot 'logs'),
    [string]$Tag = 'small',
    [switch]$DeleteImage
)
$ErrorActionPreference = 'Continue'     # PS 5.1: 2>&1 on native commands must not throw
$root = Split-Path $PSScriptRoot -Parent
$py = (Get-Command python).Source
$val = Join-Path $root 'tools\gpt_validate.py'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$log = Join-Path $LogDir "cycle_$Tag.log"
"image-mode cycle '$Tag' $(Get-Date -Format o)  image=$Image export=$ExportDir C=$CMiB MiB D=$DMiB MiB shrink=$ShrinkGB GiB" | Out-File -Encoding utf8 $log
$results = New-Object System.Collections.ArrayList

function Step([string]$Name, [scriptblock]$Body, [int]$Expect = 0, [switch]$Critical) {
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
    if (-not $ok -and $Critical) {
        $msg = "ABORT: critical step failed - see $log"
        $msg | Out-File -Append -Encoding utf8 $log; Write-Host $msg -ForegroundColor Red
        exit 99
    }
    return $out
}
function PS1([string]$Script, [string[]]$Args2) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root $Script) @Args2 }

# free-space guard: the image ends up fully allocated on NTFS despite the sparse flag (writes + zero ranges), so the
# volume must hold its whole logical size plus a margin, or the test can fill the drive
$imgBytes = [int64]($CMiB + $DMiB + 1100) * 1MB
$volLetter = (Split-Path -Qualifier ([IO.Path]::GetFullPath($Image))).TrimEnd(':')
$freeNow = [int64](Get-Volume -DriveLetter $volLetter).SizeRemaining
if (Test-Path $Image) { $freeNow += (Get-Item $Image).Length }
if ($freeNow -lt $imgBytes + 10GB) {
    Write-Host ("REFUSING: {0}: has {1:N1} GiB free, the {2:N1} GiB test image needs {3:N1} GiB (image + 10 GiB margin)" -f $volLetter, ($freeNow / 1GB), ($imgBytes / 1GB), (($imgBytes + 10GB) / 1GB)) -ForegroundColor Red
    exit 98
}
foreach ($f in @($Image, "$Image.gbstate.json")) { if (Test-Path $f) { Remove-Item -Force $f } }
if (Test-Path "$Image.gbbackup") { Remove-Item -Recurse -Force "$Image.gbbackup" }
$snap0 = "$Image.snap0.json"; $snap1 = "$Image.snap1.json"
$common = @('-TargetImage', $Image, '-ExportDir', $ExportDir, '-ShrinkGB', "$ShrinkGB")
if ($ImageMinUserdataMiB) { $common += @('-ImageMinUserdataMiB', "$ImageMinUserdataMiB") }

Step 'mkimage (synthetic host-shaped GPT image)' { & $py -I -B $val mkimage $Image --c-mib $CMiB --d-mib $DMiB } -Critical | Out-Null
Step 'snapshot before install' { & $py -I -B $val snapshot $Image $snap0 } | Out-Null
Step 'install DRY RUN (image mode)' { PS1 'install_dualboot.ps1' $common } | Out-Null
Step 'validate: dry run left the image byte-identical' { & $py -I -B $val validate $Image --snapshot $snap0 --expect clean } | Out-Null
Step 'install -Apply (image mode)' { PS1 'install_dualboot.ps1' ($common + '-Apply') } -Critical | Out-Null
$state = Get-Content -Raw "$Image.gbstate.json" | ConvertFrom-Json
$st = Get-Content -Raw $state.state | ConvertFrom-Json
Step 'validate: installed' { & $py -I -B $val validate $Image --snapshot $snap0 --expect installed --plan $st.plan --source-dir $ExportDir } | Out-Null
Step 'snapshot after install' { & $py -I -B $val snapshot $Image $snap1 } | Out-Null

# negative tests: each must be refused and must not change the image
Step 'NEG: second install refused (Googlebook partitions present)' { PS1 'install_dualboot.ps1' ($common + '-Apply') } -Expect 1 | Out-Null
Step 'NEG: plan against the real disk refused (TEST export, or no free space after D:) - query-only handle' { & (Join-Path $root 'gbdisk.exe') plan --manifest (Join-Path $ExportDir 'manifest.json') --disk 0 --anchor 4 --out - } -Expect 1 | Out-Null
$bad = Join-Path (Split-Path $Image) 'neg_overlap_plan.json'
$p = Get-Content -Raw $st.plan | ConvertFrom-Json
$p.partitions[-1].length = [int64]$p.partitions[-1].length + 4GB       # userdata would run into Recovery
$p.partitions[-1].id = [guid]::NewGuid().ToString(); $p.partitions[0].id = [guid]::NewGuid().ToString()
$p.partitions | ForEach-Object { $_.id = [guid]::NewGuid().ToString() }
$p.base = @((Get-Content -Raw $st.plan | ConvertFrom-Json).base)
$p | ConvertTo-Json -Depth 6 | Out-File -Encoding ascii $bad
Step 'NEG: gbdisk create refuses a plan that does not match the disk / overlaps' { & (Join-Path $root 'gbdisk.exe') create --plan $bad --image $Image --apply } -Expect 1 | Out-Null
Step 'NEG: gbdisk remove without install state/protect refused' { & (Join-Path $root 'gbdisk.exe') remove --image $Image --apply } -Expect 1 | Out-Null
Step 'NEG: gbdisk resize-image refuses to grow D: into the Googlebook partitions' { & (Join-Path $root 'gbdisk.exe') resize-image --image $Image --number 4 --length ([int64]$st.anchor.original_length) --apply } -Expect 1 | Out-Null
Step 'validate: refused operations left the image byte-identical' { & $py -I -B $val validate $Image --snapshot $snap1 --expect clean } | Out-Null

Step 'install -UpdateEspOnly -Apply (rewrite EFI-SYSTEM only)' { PS1 'install_dualboot.ps1' @('-TargetImage', $Image, '-ExportDir', $ExportDir, '-UpdateEspOnly', '-Apply') } | Out-Null
Step 'validate: still installed after the ESP update' { & $py -I -B $val validate $Image --snapshot $snap0 --expect installed --plan $st.plan --source-dir $ExportDir } | Out-Null
Step 'gbdisk verify (read-only, layout + content)' { & (Join-Path $root 'gbdisk.exe') verify --plan $st.plan --source-dir $ExportDir --image $Image } | Out-Null

# ---------------------------------------------------------------- wave 3, item 7: -UpdateEspOnly slot safety
$gbd = Join-Path $root 'gbdisk.exe'
$sa = Join-Path $PSScriptRoot 'set_attrs.py'
$fa = Join-Path $PSScriptRoot 'fat_add.py'
$man = Get-Content -Raw (Join-Path $ExportDir 'manifest.json') | ConvertFrom-Json
$sl = [string]$man.slot; $ot = if ($sl -eq 'a') { 'b' } else { 'a' }
$attrOk = @{ a = '0x0000000000000000'; b = '0x0000000000000000' }
foreach ($x in @('a', 'b')) { $attrOk[$x] = ($man.partitions | Where-Object { $_.name -eq "vbmeta_$x" }).attributes }
$upd = @('-TargetImage', $Image, '-ExportDir', $ExportDir, '-UpdateEspOnly')
function Exit2([scriptblock]$Run) {   # the step passes only for exit code 2 exactly (a refusal), not any failure
    & $Run
    $c = $LASTEXITCODE
    if ($c -eq 2) { 'refused with exit 2'; $global:LASTEXITCODE = 0 } else { "exit $c (expected 2)"; $global:LASTEXITCODE = 1 }
}
function Attrs([string]$A, [string]$B) { & $py -I -B $sa $Image "vbmeta_a=$A" "vbmeta_b=$B" }
function Snap { & $py -I -B $val snapshot $Image "$Image.snapw3.json" | Out-Null }
function SameAsSnap([string]$What) { Step "  validate: $What - image byte-identical" { & $py -I -B $val validate $Image --snapshot "$Image.snapw3.json" --expect clean } | Out-Null }
function EspSha { return [string](& $py -I -B $fa $Image sha) }
function LayoutOf([string]$Name) { return (((& $gbd layout --image $Image --json) -join "`n" | ConvertFrom-Json).partitions | Where-Object { $_.name -eq $Name } | Select-Object -First 1) }

$prio15 = '0x010f000000000000'
Step "W3 (1) set slot $ot active+successful, $sl not bootable (export is for $sl)" { if ($ot -eq 'a') { Attrs $prio15 '0' } else { Attrs '0' $prio15 } } | Out-Null
Snap
Step "W3 (1) -UpdateEspOnly -Apply refused: installed active slot $ot != export slot $sl" { Exit2 { PS1 'install_dualboot.ps1' ($upd + '-Apply') } } | Out-Null
SameAsSnap 'slot mismatch refused'
Step 'W3 (3) set both slots bootable with the same priority' { Attrs $prio15 $prio15 } | Out-Null
Snap
Step 'W3 (3) -UpdateEspOnly -Apply refused: ambiguous active slot' { Exit2 { PS1 'install_dualboot.ps1' ($upd + '-Apply') } } | Out-Null
SameAsSnap 'ambiguous refused'
Step 'W3 (3b) set no slot bootable (priority 15, no tries, not successful)' { Attrs '0x000f000000000000' '0x000f000000000000' } | Out-Null
Snap
Step 'W3 (3b) -UpdateEspOnly -Apply refused: no bootable slot (fail closed)' { Exit2 { PS1 'install_dualboot.ps1' ($upd + '-Apply') } } | Out-Null
SameAsSnap 'unbootable refused'
Step 'W3 restore the installed slot attributes' { Attrs $attrOk['a'] $attrOk['b'] } | Out-Null

$vb = LayoutOf "vendor_boot_$sl"
Step "W3 (2) change one byte of vendor_boot_$sl (offset +4096)" {
    $fs = [IO.File]::Open($Image, 'Open', 'ReadWrite'); $fs.Position = [int64]$vb.offset + 4096; $b0 = $fs.ReadByte()
    $fs.Position = [int64]$vb.offset + 4096; $fs.WriteByte(($b0 -bxor 0x5A)); $fs.Close(); "byte $b0 -> $($b0 -bxor 0x5A)"; $global:LASTEXITCODE = 0 } | Out-Null
Snap
Step "W3 (2) -UpdateEspOnly -Apply refused: vendor_boot_$sl is not the export's build (content check)" { Exit2 { PS1 'install_dualboot.ps1' ($upd + '-Apply') } } | Out-Null
SameAsSnap 'content mismatch refused'
Step "W3 (2) restore vendor_boot_$sl" {
    $fs = [IO.File]::Open($Image, 'Open', 'ReadWrite'); $fs.Position = [int64]$vb.offset + 4096; $b1 = $fs.ReadByte()
    $fs.Position = [int64]$vb.offset + 4096; $fs.WriteByte(($b1 -bxor 0x5A)); $fs.Close(); $global:LASTEXITCODE = 0 } | Out-Null

# (wave 8 final step) an export written since wave 8 says 'default gb-<slot>.conf'; one written before (the 2026-10-24
# fixture D:\gb-test\export_small the ISO harness's T8 uses) says 'default gb-<slot>', and -UpdateEspOnly accepts both:
# edit whichever form the installed ESP (a copy of the export's) has
$cf = if ([string](& $py -I -B $fa $Image count "default gb-$sl.conf\ntimeout") -eq '1') { '.conf' } else { '' }
Step "W3 (4c) loader.conf default -> gb-$ot$cf on the installed ESP" { & $py -I -B $fa $Image replace "default gb-$sl$cf\ntimeout" "default gb-$ot$cf\ntimeout" } | Out-Null
Snap
Step "W3 (4c) -UpdateEspOnly -Apply refused: the installed loader.conf default is not gb-$sl" { Exit2 { PS1 'install_dualboot.ps1' ($upd + '-Apply') } } | Out-Null
$gbb = "$Image.gbbackup"
Step '  the refusal came after the read-only backup (raw ESP saved, nothing written to the image)' {
    $r = @(Get-ChildItem -Recurse -Filter 'esp-before-*.img' $gbb); if ($r.Count -ge 1) { "$($r.Count) raw backup(s)"; $global:LASTEXITCODE = 0 } else { 'no raw backup'; $global:LASTEXITCODE = 1 } } | Out-Null
SameAsSnap 'loader.conf mismatch refused'
Step "W3 (4c) restore loader.conf default gb-$sl$cf" { & $py -I -B $fa $Image replace "default gb-$ot$cf\ntimeout" "default gb-$sl$cf\ntimeout" } | Out-Null

$w3 = Join-Path (Split-Path $Image) 'w3_esp_files'
if (Test-Path $w3) { Remove-Item -Recurse -Force $w3 }
New-Item -ItemType Directory -Force -Path $w3 | Out-Null
[IO.File]::WriteAllText((Join-Path $w3 'x.txt'), "boot 7 log`n")
[IO.File]::WriteAllText((Join-Path $w3 'gb.cfg'), "# gb.cfg v1`nGB_PROP_1=debug.gb.bringup_test=1`n")
[IO.File]::WriteAllText((Join-Path $w3 'x.idc'), "touch.deviceType = pointer`n")
$rnd = New-Object byte[] 70001; (New-Object Random 7).NextBytes($rnd); [IO.File]::WriteAllBytes((Join-Path $w3 'install.log'), $rnd)
$addMap = [ordered]@{ 'gblog/boot7-t40.txt' = 'x.txt'; 'gb/gb.cfg' = 'gb.cfg'; 'gb/overlay/usr/idc/GB_Test.idc' = 'x.idc'; 'gbinstall/install-20261026-120000.log' = 'install.log' }
Step 'W3 (4) put gblog\, gb\gb.cfg, gb\overlay\ and gbinstall\ files on the installed ESP' {
    foreach ($k in $addMap.Keys) { & $py -I -B $fa $Image add $k (Join-Path $w3 $addMap[$k]); if ($LASTEXITCODE) { return } } } | Out-Null
$espPre = EspSha
Snap
Step 'W3 (4) dry run: exit 0, lists the files it would copy, image byte-identical' {
    $o = PS1 'install_dualboot.ps1' $upd; $c = $LASTEXITCODE; $o
    if ($c -eq 0 -and ($o -join "`n") -match '4 file\(s\) would be copied out') { $global:LASTEXITCODE = 0 } else { $global:LASTEXITCODE = 1 } } | Out-Null
SameAsSnap 'dry run'
$before = @(Get-ChildItem -Recurse -Filter 'esp-before-*.img' $gbb | ForEach-Object { $_.FullName })
$updOut = Step 'W3 (4) -UpdateEspOnly -Apply with those files: passes all checks, saves, rewrites' { PS1 'install_dualboot.ps1' ($upd + '-Apply') }
$raw = @(Get-ChildItem -Recurse -Filter 'esp-before-*.img' $gbb | Where-Object { $before -notcontains $_.FullName })
Step '  the raw backup = the installed ESP before the update (sha256), and its .sha256 file agrees' {
    if ($raw.Count -ne 1) { "raw backups new: $($raw.Count)"; $global:LASTEXITCODE = 1; return }
    $h = (Get-FileHash -Algorithm SHA256 $raw[0].FullName).Hash.ToLowerInvariant()
    $side = (Get-Content -Raw "$($raw[0].FullName).sha256").Split(' ')[0]
    "raw $h side $side pre $espPre"
    $global:LASTEXITCODE = $(if ($h -eq $espPre.Trim() -and $side -eq $h -and $raw[0].Length -eq [int64](LayoutOf 'EFI-SYSTEM').length) { 0 } else { 1 }) } | Out-Null
Step '  the extracted copies match the files put on the ESP (sha256) and SHA256SUMS.txt checks' {
    $dir = Join-Path $raw[0].DirectoryName ($raw[0].BaseName -replace '^esp-before-', 'gblog-preserved-')
    $bad = 0
    foreach ($k in $addMap.Keys) {
        $c = Join-Path $dir ($k -replace '/', '\')
        if (-not (Test-Path -LiteralPath $c)) { "missing $k"; $bad++; continue }
        if ((Get-FileHash $c).Hash -ne (Get-FileHash (Join-Path $w3 $addMap[$k])).Hash) { "differs $k"; $bad++ }
    }
    foreach ($l in Get-Content (Join-Path $dir 'SHA256SUMS.txt')) {
        $h, $pth = $l -split '  ', 2
        if ((Get-FileHash -LiteralPath (Join-Path $dir ($pth -replace '/', '\'))).Hash.ToLowerInvariant() -ne $h) { "sum mismatch $pth"; $bad++ }
    }
    "$($addMap.Count) copies checked in $dir"; $global:LASTEXITCODE = $bad } | Out-Null
Step '  the update said that it removes gb\gb.cfg / gb\overlay\ and where the copies are' {
    $global:LASTEXITCODE = $(if (($updOut -join "`n") -match 'removes the bring-up files') { 0 } else { 1 }) } | Out-Null
Step '  the new ESP = the export image (gbdisk verify --only EFI-SYSTEM)' { & $gbd verify --plan $st.plan --only EFI-SYSTEM --source-dir $ExportDir --image $Image } | Out-Null
Step 'W3 (4b) rollback with the printed commands: the saved ESP is written back' {
    $rbm = @(Get-ChildItem -Path $raw[0].DirectoryName -Filter 'manifest_esp_rollback_*.json')[-1].FullName
    $rbp = Join-Path $raw[0].DirectoryName 'plan_esp_rollback_test.json'
    & $gbd plan --manifest $rbm --existing --image $Image --allow-truncated --out $rbp; if ($LASTEXITCODE) { return }
    & $gbd write --plan $rbp --only EFI-SYSTEM --source-dir $raw[0].DirectoryName --image $Image --apply } | Out-Null
Step '  after the rollback the ESP = the saved one (sha256)' {
    $now = EspSha; "now $now pre $espPre"; $global:LASTEXITCODE = $(if ($now.Trim() -eq $espPre.Trim()) { 0 } else { 1 }) } | Out-Null
Step 'W3 (5) -UpdateEspOnly -Apply again (normal path): passes, ESP = export' { PS1 'install_dualboot.ps1' ($upd + '-Apply') } | Out-Null
Step '  validate: still installed after the ESP updates' { & $py -I -B $val validate $Image --snapshot $snap0 --expect installed --plan $st.plan --source-dir $ExportDir } | Out-Null
Step 'W3 (6) wanted_slot truth table shared with test_gb_slotsync.sh' { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'test_wanted_slot.ps1') } | Out-Null
Remove-Item -Recurse -Force $w3, "$Image.snapw3.json" -ErrorAction SilentlyContinue

Step 'uninstall DRY RUN (image mode)' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $Image) } | Out-Null
Step 'validate: uninstall dry run left the image byte-identical' { & $py -I -B $val validate $Image --snapshot $snap1 --expect clean } | Out-Null
Step 'uninstall -Apply (image mode)' { PS1 'uninstall_dualboot.ps1' @('-TargetImage', $Image, '-Apply') } -Critical | Out-Null
Step 'validate: uninstalled (GPT byte-identical to before the install)' { & $py -I -B $val validate $Image --snapshot $snap0 --expect uninstalled --plan $st.plan } | Out-Null

Step 'NEG: plan with more userdata than the freed space refused' { & (Join-Path $root 'gbdisk.exe') plan --manifest (Join-Path $ExportDir 'manifest.json') --image $Image --anchor 4 --anchor-new-length ([int64]$st.anchor.new_length) --userdata ([int64]$ShrinkGB * 1GB) --allow-truncated --out - } -Expect 1 | Out-Null
Step 'validate: still byte-identical to before the install' { & $py -I -B $val validate $Image --snapshot $snap0 --expect uninstalled --plan $st.plan } | Out-Null

# restore-gpt path (emergency restore of the pre-install GPT from the backup files), on a re-installed image
Step 'reinstall -Apply for the restore-gpt test' { PS1 'install_dualboot.ps1' ($common + '-Apply') } -Critical | Out-Null
$state2 = Get-Content -Raw "$Image.gbstate.json" | ConvertFrom-Json
$st2 = Get-Content -Raw $state2.state | ConvertFrom-Json
Step 'gbdisk restore-gpt --tag pre_shrink --apply (emergency path)' { & (Join-Path $root 'gbdisk.exe') restore-gpt --image $Image --from $st2.run_dir --tag pre_shrink --apply } | Out-Null
Step 'validate: GPT back to the pre-install state' { & $py -I -B $val validate $Image --snapshot $snap0 --expect uninstalled --plan $st2.plan } | Out-Null

$fails = @($results | Where-Object { $_ -like 'FAIL*' }).Count
$sum = "SUMMARY '$Tag': $($results.Count - $fails) passed, $fails failed"
$sum | Out-File -Append -Encoding utf8 $log
Write-Host $sum -ForegroundColor $(if ($fails) { 'Red' } else { 'Green' })
if ($DeleteImage) { Remove-Item -Force $Image; Write-Host "deleted $Image" }
exit $fails
