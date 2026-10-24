<#
.SYNOPSIS
  Offline test of the bcdedit /enum firmware /v parser and the BCD id guard in gb_common.ps1 (runs no bcdedit).
  bcdedit /v prints well-known objects by GUID ({fwbootmgr} = {a5a30fa2-...}, {bootmgr} = {9dea862c-...});
  the parser must map them back, otherwise the "Windows Boot Manager stays first" check can never pass.
#>
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'gb_common.ps1')
$fails = 0
function Check([bool]$Ok, [string]$Msg) { if ($Ok) { Write-Host "PASS $Msg" } else { Write-Host "FAIL $Msg" -ForegroundColor Red; $script:fails++ } }

# shape of 'bcdedit /enum firmware /v' (Windows 11, English), with a Googlebook entry appended last
$sample = @'

Firmware Boot Manager
---------------------
identifier              {a5a30fa2-3d06-4e9f-b5f4-a01df9d1fcba}
displayorder            {9dea862c-5cdd-4e70-acc1-f32b344d4795}
                        {6e3a2b10-8a1c-11ef-b864-0242ac120002}
                        {6e3a2b11-8a1c-11ef-b864-0242ac120002}
                        {0f1e2d3c-4b5a-4968-8776-655443322110}
timeout                 0

Windows Boot Manager
--------------------
identifier              {9dea862c-5cdd-4e70-acc1-f32b344d4795}
device                  partition=\Device\HarddiskVolume1
path                    \EFI\Microsoft\Boot\bootmgfw.efi
description             Windows Boot Manager
locale                  en-US
inherit                 {7ea2e1ac-2e61-4728-aaa3-896d9d0a9f0e}
default                 {b4c1f5a0-1111-11ef-a2b2-9e2e4f0a1b2c}
resumeobject            {b4c1f5a1-1111-11ef-a2b2-9e2e4f0a1b2c}
displayorder            {b4c1f5a0-1111-11ef-a2b2-9e2e4f0a1b2c}
toolsdisplayorder       {b2721d73-1db4-4c62-bf78-c548a880142d}
timeout                 30

Firmware Application (101fffff)
-------------------------------
identifier              {6e3a2b10-8a1c-11ef-b864-0242ac120002}
description             UEFI: PXE IPv4 Intel(R) Wi-Fi 6 AX201 160MHz

Firmware Application (101fffff)
-------------------------------
identifier              {6e3a2b11-8a1c-11ef-b864-0242ac120002}
description             USB Storage

Windows Boot Manager
--------------------
identifier              {0f1e2d3c-4b5a-4968-8776-655443322110}
device                  partition=\Device\HarddiskVolume7
path                    \EFI\BOOT\BOOTX64.EFI
description             Googlebook OS
locale                  en-US
inherit                 {7ea2e1ac-2e61-4728-aaa3-896d9d0a9f0e}
bootsequence            {b4c1f5a0-1111-11ef-a2b2-9e2e4f0a1b2c}
                        {6e3a2b10-8a1c-11ef-b864-0242ac120002}
'@ -split "`r?`n"

$e = ConvertFrom-BcdEnum $sample
Check (@($e).Count -eq 5) "5 entries parsed (got $(@($e).Count))"
$fw = $e | Where-Object { $_.Id -eq '{fwbootmgr}' } | Select-Object -First 1
Check ([bool]$fw) '{a5a30fa2-...} mapped to {fwbootmgr}'
Check ($fw -and @($fw.DisplayOrder).Count -eq 4) "fwbootmgr displayorder has 4 entries (got $(@($fw.DisplayOrder) -join ' '))"
Check ($fw -and $fw.DisplayOrder[0] -eq '{bootmgr}') 'first firmware boot entry is {bootmgr} (mapped from {9dea862c-...})'
Check ($fw -and $fw.DisplayOrder[-1] -eq '{0f1e2d3c-4b5a-4968-8776-655443322110}') 'Googlebook entry last'
$wbm = $e | Where-Object { $_.Id -eq '{bootmgr}' } | Select-Object -First 1
Check ($wbm -and $wbm.Path -eq '\EFI\Microsoft\Boot\bootmgfw.efi' -and $wbm.Description -eq 'Windows Boot Manager') 'Windows Boot Manager entry parsed'
Check ($wbm -and @($wbm.DisplayOrder).Count -eq 1) 'toolsdisplayorder not merged into displayorder'
$gb = $e | Where-Object { $_.Description -eq 'Googlebook OS' } | Select-Object -First 1
Check ($gb -and $gb.Id -eq '{0f1e2d3c-4b5a-4968-8776-655443322110}' -and $gb.Path -eq '\EFI\BOOT\BOOTX64.EFI') 'Googlebook entry parsed'
Check ($gb -and @($gb.DisplayOrder).Count -eq 0) 'bootsequence continuation lines not taken as displayorder'

foreach ($bad in @('', $null, '{bootmgr}', '{fwbootmgr}', '{current}', '{9DEA862C-5CDD-4E70-ACC1-F32B344D4795}', '{a5a30fa2-3d06-4e9f-b5f4-a01df9d1fcba}', 'device', '{1234}')) {
    $threw = $false; try { Assert-SafeEntryId $bad } catch { $threw = $true }
    Check $threw "Assert-SafeEntryId refuses '$bad'"
}
$threw = $false; try { Assert-SafeEntryId '{0f1e2d3c-4b5a-4968-8776-655443322110}' } catch { $threw = $true }
Check (-not $threw) 'Assert-SafeEntryId accepts a fresh GUID'

Write-Host "SUMMARY bcd-parse: $fails failed"
exit $fails
