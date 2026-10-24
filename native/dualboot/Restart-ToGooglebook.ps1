<#
.SYNOPSIS
  Boot Camp style "restart into Googlebook OS" - for the next boot only. Windows stays the default: the boot after
  that (or any power cycle) goes back to Windows Boot Manager.

.DESCRIPTION
  Sets the UEFI BootNext variable through the Windows boot configuration:
      bcdedit /set {fwbootmgr} bootsequence {GOOGLEBOOK-ENTRY}
      shutdown /r /t 0
  The entry is the one install_dualboot.ps1 created (its GUID is in the install state; otherwise the firmware entry
  whose description is -EntryName). Needs an elevated PowerShell.

.EXAMPLE
  .\Restart-ToGooglebook.ps1              # set BootNext and restart now
  .\Restart-ToGooglebook.ps1 -NoRestart   # set BootNext only (restart later yourself)
  .\Restart-ToGooglebook.ps1 -WhatIf      # show what would be done
#>
[CmdletBinding()]
param(
    [string]$EntryId,
    [string]$EntryName = 'Googlebook OS',
    [string]$BackupRoot = 'C:\GB-backup',
    [switch]$NoRestart,
    [switch]$WhatIf
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'gb_common.ps1')

if (-not (Test-Admin)) { Write-Host 'Run this from an elevated PowerShell (bcdedit needs it).' -ForegroundColor Red; exit 1 }
if (-not $EntryId) {
    $ptr = Join-Path $BackupRoot 'googlebook_install.json'
    if (Test-Path $ptr) { $st = Read-Json (Read-Json $ptr).state; if ($st.entry_id) { $EntryId = $st.entry_id } }
}
$e = Find-GooglebookEntry $EntryName $EntryId
if (-not $e) { Write-Host "No firmware boot entry '$EntryName' found - run install_dualboot.ps1 -RepairBootEntry -Apply" -ForegroundColor Red; exit 1 }
Write-Host "Googlebook boot entry: $($e.Id) '$($e.Description)' $($e.Device) $($e.Path)"
Assert-SafeEntryId $e.Id      # a plain GUID, never {bootmgr}/{fwbootmgr}
if ($e.Path -ne '\EFI\BOOT\BOOTX64.EFI') { Write-Host "entry path is '$($e.Path)', not \EFI\BOOT\BOOTX64.EFI - run install_dualboot.ps1 -RepairBootEntry" -ForegroundColor Red; exit 1 }
if ($WhatIf) {
    Write-Host "would run: bcdedit /set {fwbootmgr} bootsequence $($e.Id)"
    if (-not $NoRestart) { Write-Host 'would run: shutdown /r /t 0' }
    exit 0
}
Invoke-Native 'bcdedit.exe' @('/set', '{fwbootmgr}', 'bootsequence', $e.Id) | Out-Null
$chk = & bcdedit.exe /enum '{fwbootmgr}' /v
if (-not (($chk -join "`n") -match [regex]::Escape($e.Id))) { Write-Host 'bootsequence not visible in {fwbootmgr} - not restarting' -ForegroundColor Red; exit 1 }
Write-Host 'Next boot: Googlebook OS (once). Windows remains the default.' -ForegroundColor Green
if ($NoRestart) { exit 0 }
Write-Host 'Restarting now...'
& shutdown.exe /r /t 0
