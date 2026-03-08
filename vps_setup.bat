@echo off
:: VPS RDP and Reboot Configuration Script
:: Run as Administrator

echo Setting up VPS optimizations for Headless Chrome...

:: Enable RDP session Keep-Alive when disconnected (prevents Windows from locking UI drawing)
echo Applying tscon patch to keep GUI session alive on RDP disconnect...

:: Create a disconnect shortcut on the desktop
set DESKTOP_PATH=%USERPROFILE%\Desktop
echo @echo off > "%DESKTOP_PATH%\Disconnect_Without_Locking.bat"
echo for /f "skip=1 tokens=3" %%%%s in ('query user %%USERNAME%%') do ( >> "%DESKTOP_PATH%\Disconnect_Without_Locking.bat"
echo    %%windir%%\System32\tscon.exe %%%%s /dest:console >> "%DESKTOP_PATH%\Disconnect_Without_Locking.bat"
echo ) >> "%DESKTOP_PATH%\Disconnect_Without_Locking.bat"

echo Disconnect shortcut created on Desktop. ALWAYS use that to close RDP, never the X button!

:: Add a daily scheduled task to reboot the VPS at 04:00 AM
echo Scheduling daily VPS reboot at 04:00 AM (prevents RAM leaks)...
schtasks /create /tn "Daily_Bot_Reboot" /tr "shutdown.exe /r /t 0 /f" /sc daily /st 04:00 /ru "SYSTEM" /f

:: Add Windows Defender Exclusion for the bot folder
echo Adding Windows Defender exclusion for %cd% ...
powershell -Command "Add-MpPreference -ExclusionPath '%cd%'"

:: Disable Google Chrome Auto-Update via Registry (Prevents undetected-chromedriver crashes)
echo Disabling Google Chrome Auto-Updates...
reg add "HKLM\SOFTWARE\Policies\Google\Update" /v AutoUpdateCheckPeriodMinutes /t REG_DWORD /d 0 /f
reg add "HKLM\SOFTWARE\Policies\Google\Update" /v DisableAutoUpdateChecksCheckboxValue /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Google\Update" /v UpdateDefault /t REG_DWORD /d 0 /f
reg add "HKLM\SOFTWARE\Policies\Google\Update" /v Update{8A69D345-D564-463C-AFF1-A69D9E530F96} /t REG_DWORD /d 0 /f

echo.
echo VPS optimizations applied successfully.
pause
