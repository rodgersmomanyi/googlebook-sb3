# Reliably change lapis-boot.vmx settings: clean guest power-off, close the Workstation GUI (it caches the VM config and
# rewrites the .vmx at power-on otherwise), then edit the file. Start the VM afterwards with boot_live.ps1.
# Usage: vmx_set.ps1 'mouse.vusb.useBasicMouse=FALSE' 'priority.grabbed=high'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Pairs)
$gb = if ($env:GB_HOME) { $env:GB_HOME } else { $PSScriptRoot }   # workspace (README: GB_HOME)
$py = if ($env:GB_PYTHON) { $env:GB_PYTHON } else { "python" }
$vmrun = "C:\Program Files\VMware\VMware Workstation\vmrun.exe"
$vmx = "$gb\lapis-boot.vmx"
if ((& $vmrun list) -match 'lapis-boot') {
  $sp = Start-Process -FilePath $py -ArgumentList @('-I', "$gb\gbsh.py", '-t', '15', '/system/bin/reboot -p >/dev/null 2>&1 &') -WindowStyle Hidden -PassThru
  $null = $sp.WaitForExit(30000); if (-not $sp.HasExited) { $sp.Kill() }
  for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Seconds 3; if (-not ((& $vmrun list) -match 'lapis-boot')) { break } }
  if ((& $vmrun list) -match 'lapis-boot') { Write-Host "guest did not power off; aborting"; exit 1 }
  Write-Host "guest powered off cleanly"
}
foreach ($p in Get-Process vmware -ErrorAction SilentlyContinue) { $null = $p.CloseMainWindow() }
Start-Sleep -Seconds 6
Copy-Item $vmx "$vmx.bak" -Force
$t = [IO.File]::ReadAllText($vmx)
foreach ($pair in $Pairs) {
  $k, $v = $pair.Split('=', 2)
  $re = '(?m)^' + [regex]::Escape($k) + ' = .*$'
  $line = "$k = `"$v`""
  if ($t -match $re) { $t = [regex]::Replace($t, $re, $line) } else { $t = $t.TrimEnd() + "`r`n$line`r`n" }
  Write-Host "set $line"
}
[IO.File]::WriteAllText($vmx, $t)
