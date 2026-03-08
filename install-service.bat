@echo off
:: NSSM Service Installation Script for VizeBot
:: Make sure you have downloaded nssm.exe and placed it in this folder or in SYSTEM PATH
:: Run as Administrator

set SERVICE_NAME="VizeBotServer"
set PYTHON_PATH="python.exe"
:: If you use a virtual environment, set the full path to it below:
:: set PYTHON_PATH="C:\path\to\your\venv\Scripts\python.exe"

set SCRIPT_PATH="%cd%\main.py"
set WORKING_DIR="%cd%"

echo Checking for nssm...
where nssm >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] nssm.exe is not found in your system PATH.
    echo Please download NSSM from nssm.cc, extract it, and place nssm.exe in this folder.
    pause
    exit /b
)

echo Installing %SERVICE_NAME% service...
nssm install %SERVICE_NAME% %PYTHON_PATH% %SCRIPT_PATH%
nssm set %SERVICE_NAME% AppDirectory %WORKING_DIR%
nssm set %SERVICE_NAME% Description "Headless VizeBot FastAPI Background Server"
nssm set %SERVICE_NAME% Start SERVICE_AUTO_START
nssm set %SERVICE_NAME% AppStdout "%cd%\backend.log"
nssm set %SERVICE_NAME% AppStderr "%cd%\backend_error.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateOnline 1
nssm set %SERVICE_NAME% AppRotateSeconds 86400
nssm set %SERVICE_NAME% AppRotateBytes 10485760

echo Starting %SERVICE_NAME% service...
nssm start %SERVICE_NAME%

echo.
echo Service installed and started successfully!
pause
