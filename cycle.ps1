param([int]$wait = 45, [switch]$NoBuild)
$gb = if ($env:GB_HOME) { $env:GB_HOME } else { $PSScriptRoot }   # workspace (README: GB_HOME)
$py = if ($env:GB_PYTHON) { $env:GB_PYTHON } else { "python" }
$ErrorActionPreference = "SilentlyContinue"
$vmrun = "C:\Program Files\VMware\VMware Workstation\vmrun.exe"
$vmx = "$gb\lapis-boot.vmx"
$log = "$gb\serial0.log"

& $vmrun -T ws stop $vmx hard 2>&1 | Out-Null
Start-Sleep -Seconds 3
if (-not $NoBuild) {
  & $py -I "$gb\build_esp.py" 2>&1 | Select-Object -First 1
}
$del = @("$gb\lapis-boot.nvram", $log)
$del += (Get-ChildItem "$gb" -Filter "*.REDO_*").FullName
foreach ($f in $del) { try { [System.IO.File]::Delete($f) } catch {} }
& $vmrun -T ws start $vmx gui 2>&1 | Out-Null
Write-Host "booted; waiting $wait s..."
Start-Sleep -Seconds $wait
$sz = (Get-Item $log).Length
Write-Host "serial0.log = $sz bytes"
$lines = Get-Content $log
$seen = $null
$dedup = foreach ($l in $lines) { if ($l -ne $seen) { $l; $seen = $l } }
Write-Host "deduped lines: $($dedup.Count)"
Write-Host "=== key progress (tail) ==="
$pat = 'by-name|/super|dm-|verity|erofs|first stage mount|first stage mounted|second stage|Switching root|SELinux: (policy|Permissive|Disabled|Loaded)|avb|realpath|Failed to|Kernel panic|mounted filesystem|ueventd|init: Service|keystore|zygote|SurfaceFlinger|boot_completed|servicemanager'
$dedup | Select-String -Pattern $pat | Select-Object -Last 40 -ExpandProperty Line
