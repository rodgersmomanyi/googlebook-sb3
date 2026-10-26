<#
.SYNOPSIS
  Wave 3 item 7 (6): install_dualboot.ps1's slot decode (gb_common.ps1 Get-GbWantedSlot / Test-GbSlotAmbiguous) against
  the SAME truth table that test_gb_slotsync.sh runs on gb_slotsync's wanted_slot() (its block between the lines
  "# @@wanted_slot-vectors-begin" and "# @@wanted_slot-vectors-end"), plus the hex parser. No disk access.
    -SlotsyncTest  path of test_gb_slotsync.sh (default: ..\..\..\mouse\test, or ..\..\..\guest\test in the repository)
#>
param([string]$SlotsyncTest)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $root 'gb_common.ps1')
if (-not $SlotsyncTest) {
    foreach ($c in @('..\..\..\mouse\test\test_gb_slotsync.sh', '..\..\..\guest\test\test_gb_slotsync.sh')) {
        $p = Join-Path $PSScriptRoot $c
        if (Test-Path -LiteralPath $p) { $SlotsyncTest = $p; break }
    }
}
if (-not $SlotsyncTest -or -not (Test-Path -LiteralPath $SlotsyncTest)) { Write-Host 'FAIL test_gb_slotsync.sh not found (-SlotsyncTest)'; exit 1 }
$fails = 0; $n = 0
$in = $false
foreach ($l in Get-Content -LiteralPath $SlotsyncTest) {
    if ($l -eq '# @@wanted_slot-vectors-begin') { $in = $true; continue }
    if ($l -eq '# @@wanted_slot-vectors-end') { $in = $false; continue }
    if (-not $in -or $l -notmatch '^# ([0-9a-f]{16}) ([0-9a-f]{16}) ([ab-]) ([01])$') { continue }
    $a = ConvertFrom-GbHex $Matches[1]; $b = ConvertFrom-GbHex $Matches[2]; $want = $Matches[3]; $amb = $Matches[4] -eq '1'
    $w = Get-GbWantedSlot $a $b
    $got = if ($w -lt 0) { '-' } else { @('a', 'b')[$w] }
    $gamb = Test-GbSlotAmbiguous $a $b
    $n++
    if ($got -ne $want -or $gamb -ne $amb) { $fails++; Write-Host ("FAIL wanted({0}, {1}) = {2}/{3}, table {4}/{5}" -f $Matches[1], $Matches[2], $got, $gamb, $want, $amb) }
}
if ($n -lt 20) { $fails++; Write-Host "FAIL only $n vectors read from $SlotsyncTest" }
elseif (-not $fails) { Write-Host "PASS $n wanted_slot vectors (shared with test_gb_slotsync.sh S11): Get-GbWantedSlot = gb_slotsync wanted_slot(), ambiguous column as listed" }
foreach ($v in @(@('0x010f000000000000', [UInt64]0x010f000000000000), @('010F000000000000', [UInt64]0x010f000000000000), @('0x0', [UInt64]0))) {
    if ((ConvertFrom-GbHex $v[0]) -ne $v[1]) { $fails++; Write-Host "FAIL ConvertFrom-GbHex $($v[0])" }
}
foreach ($bad in @('', 'zz', '0x11111111111111111')) {
    $threw = $false; try { ConvertFrom-GbHex $bad | Out-Null } catch { $threw = $true }
    if (-not $threw) { $fails++; Write-Host "FAIL ConvertFrom-GbHex '$bad' accepted" }
}
if (-not $fails) { Write-Host 'PASS ConvertFrom-GbHex: 3 good, 3 bad inputs' }
Write-Host "SUMMARY test_wanted_slot: $(if ($fails) { "$fails failure(s)" } else { 'OK' })"
exit $fails
