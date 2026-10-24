# gb_common.ps1 - shared helpers for install_dualboot.ps1 / uninstall_dualboot.ps1 / Restart-ToGooglebook.ps1.
# Windows PowerShell 5.1 compatible. Dot-sourced; defines functions only.
#
# Change discipline: every command that changes the disk, the boot configuration or writes a file goes through
# Invoke-Change, which only runs it when $script:GbApply is $true. Everything else these scripts run is read-only.

$script:GbApply = $false
$script:GbDisk = Join-Path $PSScriptRoot 'gbdisk.exe'
$script:GbNames = @('EFI-SYSTEM','boot_a','boot_b','init_boot_a','init_boot_b','vendor_boot_a','vendor_boot_b',
                    'vbmeta_a','vbmeta_b','pvmfw_a','pvmfw_b','misc','metadata','frp_persist',
                    'desktop_security_persist','desktop_security_storage','super','userdata')
$script:GbFailures = New-Object System.Collections.ArrayList

function Write-Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    [ok]   $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "    [warn] $Text" -ForegroundColor Yellow }
function Write-Bad([string]$Text) { Write-Host "    [FAIL] $Text" -ForegroundColor Red; [void]$script:GbFailures.Add($Text) }
function Write-Info([string]$Text) { Write-Host "           $Text" }

# Runs $Action only with -Apply; otherwise prints what would be done. $Command is the exact command line shown.
function Invoke-Change([string]$Description, [string]$Command, [scriptblock]$Action) {
    if ($script:GbApply) {
        Write-Host "    [do]   $Description" -ForegroundColor Magenta
        if ($Command) { Write-Info $Command }
        & $Action
    } else {
        Write-Host "    [dry]  would: $Description" -ForegroundColor DarkYellow
        if ($Command) { Write-Info $Command }
    }
}

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# gbdisk.exe wrapper: returns stdout lines, echoes them unless -Quiet, throws on a non-zero exit code.
function Invoke-GbDisk {
    param([string[]]$Arguments, [switch]$Quiet, [string]$InputText, [switch]$AllowFail)
    if (-not (Test-Path $script:GbDisk)) { throw "gbdisk.exe missing: run build_gbdisk.ps1 first" }
    if ($InputText) { $out = $InputText | & $script:GbDisk @Arguments } else { $out = & $script:GbDisk @Arguments }
    $code = $LASTEXITCODE
    if (-not $Quiet) { foreach ($l in $out) { Write-Info $l } }
    if ($code -ne 0 -and -not $AllowFail) { throw "gbdisk $($Arguments[0]) failed (exit $code)" }
    $global:GbLastExit = $code
    return $out
}

function Get-GbLayout([string[]]$Target) {
    $json = Invoke-GbDisk -Arguments (@('layout') + $Target + @('--json')) -Quiet
    return (($json -join "`n") | ConvertFrom-Json)
}

# 'off' / 'on' / 'unknown' - registry value readable without elevation; Confirm-SecureBootUEFI when elevated.
function Get-SecureBootState {
    try {
        if (Test-Admin) { if (Confirm-SecureBootUEFI -ErrorAction Stop) { return 'on' } else { return 'off' } }
    } catch { }
    try {
        $v = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State' -ErrorAction Stop).UEFISecureBootEnabled
        if ($v -eq 1) { return 'on' } elseif ($v -eq 0) { return 'off' }
    } catch { }
    return 'unknown'
}

# Windows Fast Startup (hybrid shutdown: "Shut down" hibernates the kernel with the disks' state cached). Readable
# without elevation. 'on' / 'off' / 'unknown'.
function Get-FastStartupState {
    try {
        $hb = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -ErrorAction Stop).HiberbootEnabled
        $he = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -ErrorAction SilentlyContinue).HibernateEnabled
        if ($hb -eq 0 -or $he -eq 0) { return 'off' }
        if ($hb -eq 1) { return 'on' }
    } catch { }
    return 'unknown'
}

# $true on AC, $false on battery, $null unknown
function Get-ACPower {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $s = [System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus
        if ($s -eq 'Online') { return $true } elseif ($s -eq 'Offline') { return $false }
    } catch { }
    try {
        $b = Get-CimInstance Win32_Battery -ErrorAction Stop | Select-Object -First 1
        if ($b) { return ($b.BatteryStatus -eq 2 -or $b.BatteryStatus -eq 6) }
    } catch { }
    return $null
}

# BitLocker state of a volume: 'off' / 'on' / 'unknown' (+ detail). Get-BitLockerVolume needs elevation; the shell
# property System.Volume.BitLockerProtection does not (1 on, 2 off, 3 encrypting, 4 decrypting, 5 suspended, 6 locked).
function Get-BitLockerState([string]$Letter) {
    $mp = $Letter.TrimEnd(':') + ':'
    try {
        $v = Get-BitLockerVolume -MountPoint $mp -ErrorAction Stop
        if ($v.VolumeStatus -eq 'FullyDecrypted' -and $v.ProtectionStatus -eq 'Off') { return @('off', "FullyDecrypted, protection Off") }
        return @('on', "VolumeStatus $($v.VolumeStatus), protection $($v.ProtectionStatus), $($v.EncryptionPercentage)%")
    } catch { }
    try {
        $sh = New-Object -ComObject Shell.Application
        $val = $sh.NameSpace($mp + '\').Self.ExtendedProperty('System.Volume.BitLockerProtection')
        if ($val -eq 2) { return @('off', 'shell property: protection off') }
        # no value (or 0) does NOT prove the volume is unencrypted: report unknown (fails the check with -Apply)
        if ($val -eq $null -or $val -eq 0) { return @('unknown', "shell property empty ($val)") }
        return @('on', "shell property value $val")
    } catch { }
    return @('unknown', 'no BitLocker query worked')
}

# bcdedit /v prints well-known objects by GUID, not by alias; mapped back so callers can compare with '{fwbootmgr}'
# and '{bootmgr}' (Windows Boot Manager).
$script:BcdWellKnown = @{ '{a5a30fa2-3d06-4e9f-b5f4-a01df9d1fcba}' = '{fwbootmgr}'; '{9dea862c-5cdd-4e70-acc1-f32b344d4795}' = '{bootmgr}' }
function ConvertTo-BcdAlias([string]$Id) {
    $k = $Id.Trim().ToLowerInvariant()
    if ($script:BcdWellKnown.ContainsKey($k)) { return $script:BcdWellKnown[$k] }
    return $Id.Trim()
}

# A BCD id that bcdedit /set or /delete may be given for the Googlebook entry: a plain GUID, never empty (bcdedit
# /set without an id changes {current}, the running Windows loader) and never a well-known Windows object.
function Assert-SafeEntryId([string]$Id) {
    if (-not $Id -or $Id -notmatch '^\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}$') { throw "refusing: '$Id' is not a BCD entry GUID" }
    if ((ConvertTo-BcdAlias $Id) -ne $Id.Trim()) { throw "refusing: $Id is a well-known Windows boot object" }
}

# bcdedit /enum firmware /v parsed into objects (Id, Description, Device, Path, DisplayOrder). Requires elevation.
# The ids of the firmware boot manager and Windows Boot Manager come back as '{fwbootmgr}' / '{bootmgr}'.
function Get-FirmwareEntries {
    $ErrorActionPreference = 'Continue'      # PS 5.1: redirected native stderr must not become a terminating error
    $out = & bcdedit.exe /enum firmware /v 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    return (ConvertFrom-BcdEnum $out)
}

# Parser for bcdedit /enum output (separate from the bcdedit call so it can be tested offline).
function ConvertFrom-BcdEnum([object[]]$Lines) {
    $entries = @(); $cur = $null; $lastKey = ''
    foreach ($line in $Lines) {
        $l = [string]$line
        if ($l -match '^\s*$') { if ($cur) { $entries += $cur; $cur = $null }; $lastKey = ''; continue }
        if ($l -match '^-+$') { continue }
        if ($l -cmatch '^([a-z]+)\s+(.*)$') {          # element lines (lower-case names); headers start upper-case
            if (-not $cur) { $cur = [pscustomobject]@{ Id = ''; Description = ''; Device = ''; Path = ''; DisplayOrder = @() } }
            $k = $Matches[1]; $v = $Matches[2].Trim(); $lastKey = $k
            switch ($k) {
                'identifier' { $cur.Id = ConvertTo-BcdAlias $v }
                'description' { $cur.Description = $v }
                'device' { $cur.Device = $v }
                'path' { $cur.Path = $v }
                'displayorder' { $cur.DisplayOrder = @(ConvertTo-BcdAlias $v) }
            }
        } elseif ($cur -and $l -match '^\s+(\{[0-9a-fA-F-]{36}\})\s*$') {
            # continuation line of a list element: only displayorder is collected
            if ($lastKey -eq 'displayorder') { $cur.DisplayOrder += (ConvertTo-BcdAlias $Matches[1]) }
        } elseif (-not $cur -and $l -notmatch '^\s') {
            $cur = [pscustomobject]@{ Id = ''; Description = ''; Device = ''; Path = ''; DisplayOrder = @() }
        }
    }
    if ($cur) { $entries += $cur }
    return $entries
}

function Find-GooglebookEntry([string]$Name, [string]$KnownId) {
    $fw = Get-FirmwareEntries
    if ($fw -eq $null) { return $null }
    if ($KnownId) { $e = $fw | Where-Object { $_.Id -eq $KnownId } | Select-Object -First 1; if ($e) { return $e } }
    return ($fw | Where-Object { $_.Description -eq $Name } | Select-Object -First 1)
}

function Get-GuidFromText([string[]]$Text) {
    foreach ($l in $Text) { if ([string]$l -match '(\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\})') { return $Matches[1] } }
    return $null
}

# Native command runner that throws on failure (for bcdedit/shutdown inside Invoke-Change actions).
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    $out = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    foreach ($l in $out) { Write-Info ([string]$l) }
    if ($code -ne 0) { throw "$Exe $($Arguments -join ' ') failed (exit $code)" }
    return $out
}

function Save-Json($Object, [string]$Path) {
    $Object | ConvertTo-Json -Depth 8 | Out-File -FilePath $Path -Encoding utf8
}

function Read-Json([string]$Path) { return ((Get-Content -Raw -Path $Path) | ConvertFrom-Json) }

function Format-MiB([double]$Bytes) { return ('{0:N0} MiB' -f ($Bytes / 1MB)) }
