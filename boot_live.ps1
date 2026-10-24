param([int]$Wait=190,[string]$Mode="gui")
$gb = if ($env:GB_HOME) { $env:GB_HOME } else { $PSScriptRoot }   # workspace (README: GB_HOME)
$py = if ($env:GB_PYTHON) { $env:GB_PYTHON } else { "python" }
$vmrun="C:\Program Files\VMware\VMware Workstation\vmrun.exe"
$vmx="$gb\lapis-boot.vmx"
$log="$gb\serial0.log"
function clean($s){ ($s -replace '^\<\d+\>\[[^\]]+\]\s*','').Trim() }

# /data is on a PERSISTENT disk now: power Android off cleanly via the serial console (gbsh.py) first, so ext4 is
# unmounted cleanly; fall back to a hard stop only if the guest doesn't power off within ~2 min.
if ((& $vmrun list) -match 'lapis-boot') {
  & $py -I "$gb\gbsh.py" -t 15 "/system/bin/reboot -p >/dev/null 2>&1 &" 2>&1 | Out-Null
  for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Seconds 3; if (-not ((& $vmrun list) -match 'lapis-boot')) { Write-Host "guest powered off cleanly"; break } }
}
& $vmrun -T ws stop $vmx hard 2>&1 | Out-Null
Start-Sleep -Seconds 3
foreach($f in (Get-ChildItem "$gb" -Filter "*.lck" -Recurse -ErrorAction SilentlyContinue).FullName){try{[System.IO.File]::Delete($f)}catch{}}
foreach($f in (Get-ChildItem "$gb" -Filter "*.REDO_*" -ErrorAction SilentlyContinue).FullName){try{[System.IO.File]::Delete($f)}catch{}}
try{[System.IO.File]::Delete("$gb\lapis-boot.nvram")}catch{}
try{[System.IO.File]::Delete($log)}catch{}
$b = & $py -I "$gb\build_esp.py" 2>&1 | Select-Object -Last 1
Write-Host "build: $b"
# Start-Process (not `& vmrun | Out-Null`): if vmrun has to launch the Workstation GUI, the GUI inherits a piped stdout
# and Out-Null would wait for the GUI to exit.
$sp = Start-Process -FilePath $vmrun -ArgumentList @('-T','ws','start',"`"$vmx`"",$Mode) -WindowStyle Hidden -PassThru
$null = $sp.WaitForExit(180000)   # waits for vmrun only, not for the GUI it may have launched
Write-Host "started ($Mode, left running); waiting $Wait s"
Start-Sleep -Seconds $Wait

# Report WITHOUT stopping the VM (snapshot read of the growing serial)
if(Test-Path $log){
  Write-Host ("reboots: " + (Select-String -Path $log -Pattern 'Linux version 6' -SimpleMatch).Count)
  Write-Host "=== vmwgfx / card0 / display ==="
  Select-String -Path $log -Pattern 'vmwgfx.*(Initialized|Screen Target|Unresolved|protected)|/dev/dri/card|hwc3|Cannot use protected symbol ttm' | ForEach-Object { clean $_.Line } | Select-Object -Unique | Select-Object -First 8
  Write-Host "=== progression ==="
  foreach($m in @('invalid opcode|Kernel panic','starting service .zygote','SurfaceFlinger','bootanim','boot_completed|sys.boot_completed','Launcher|launcher','updatable_crashing')){
    $c=(Select-String -Path $log -Pattern $m).Count; Write-Host ("  [{0,-30}] {1}" -f $m,$c)
  }
}
Write-Host "LIVE_DONE (VM still running)"
