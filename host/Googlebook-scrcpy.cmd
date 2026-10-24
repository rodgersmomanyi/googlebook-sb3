@echo off
rem Googlebook OS (VMware VM) in a scrcpy window: screen, keyboard/mouse, clipboard and AUDIO over network ADB.
rem The VM must be running; it enables ADB on tcp:5555 itself after boot (gb_adb). While scrcpy runs, the VM's sound
rem plays here instead of through VMware's virtual sound card; it switches back when this window is closed.
rem --no-mouse-hover: scrcpy injects hover as mouse events but left clicks as touch events from the same injected device;
rem Android 16+ rejects that mixed stream, so left clicks would be dropped (scrcpy issues #6895, #7062).
rem   Googlebook-scrcpy.cmd          injected keyboard + mouse (default)
rem   Googlebook-scrcpy.cmd hid      virtual HID keyboard inside Android (uhid), injected mouse
rem   Googlebook-scrcpy.cmd hidall   virtual HID keyboard and mouse (relative mouse: Alt toggles capture)
setlocal
set "D=%~dp0scrcpy-win64-v5.0.1"
set "IP=192.168.115.133:5555"
set "EXTRA=--no-mouse-hover"
if /i "%~1"=="hid" set "EXTRA=--no-mouse-hover --keyboard=uhid"
if /i "%~1"=="hidall" set "EXTRA=--keyboard=uhid --mouse=uhid"
"%D%\adb.exe" connect %IP% >nul 2>&1
for /l %%i in (1,1,45) do (
  "%D%\adb.exe" -s %IP% get-state 2>nul | find "device" >nul && goto run
  timeout /t 2 /nobreak >nul
  "%D%\adb.exe" connect %IP% >nul 2>&1
)
echo Googlebook VM not reachable at %IP% - is the VM running and fully booted?
pause
exit /b 1
:run
start "" "%D%\scrcpy.exe" -s %IP% --audio-codec=opus --max-size=1920 --window-title="Googlebook (scrcpy)" --stay-awake %EXTRA%
