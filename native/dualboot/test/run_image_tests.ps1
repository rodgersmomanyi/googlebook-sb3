<#
.SYNOPSIS
  Full install + uninstall cycle of the dual-boot installer against a synthetic disk image (no admin, no real disk).
  1 mkimage (host-shaped GPT: ESP/MSR/C:/D:/Recovery, random data blocks)   2 snapshot
  3 install dry run -> image must be byte-identical                          4 install -Apply -> validate installed
  5 negative tests (second install refused, truncated export refused for a disk, overlap plan refused, remove without
    state refused)                                                           6 -UpdateEspOnly -> validate installed
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
