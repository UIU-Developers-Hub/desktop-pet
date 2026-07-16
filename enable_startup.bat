@echo off
setlocal

set "APP_NAME=Desktop Pet"
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

set "ENTRY=%APP_DIR%\main.py"
set "RUNNER=%APP_DIR%\.venv\Scripts\pythonw.exe"
set "ICON=%APP_DIR%\assets\pet_icon.png"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_DIR%\%APP_NAME%.lnk"

if not exist "%ENTRY%" (
    echo Could not find "%ENTRY%".
    echo Move this script back into the Desktop Pet app folder and try again.
    pause
    exit /b 1
)

if not exist "%RUNNER%" (
    echo Could not find "%RUNNER%".
    echo Create the virtual environment and install dependencies first:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%STARTUP_DIR%" mkdir "%STARTUP_DIR%"

set "DESKTOP_PET_APP_NAME=%APP_NAME%"
set "DESKTOP_PET_APP_DIR=%APP_DIR%"
set "DESKTOP_PET_ENTRY=%ENTRY%"
set "DESKTOP_PET_RUNNER=%RUNNER%"
set "DESKTOP_PET_ICON=%ICON%"
set "DESKTOP_PET_SHORTCUT=%SHORTCUT%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $shell = New-Object -ComObject WScript.Shell; $shortcut = $shell.CreateShortcut($env:DESKTOP_PET_SHORTCUT); $shortcut.TargetPath = $env:DESKTOP_PET_RUNNER; $shortcut.Arguments = [char]34 + $env:DESKTOP_PET_ENTRY + [char]34; $shortcut.WorkingDirectory = $env:DESKTOP_PET_APP_DIR; if (Test-Path $env:DESKTOP_PET_ICON) { $shortcut.IconLocation = $env:DESKTOP_PET_ICON }; $shortcut.Description = 'Starts Desktop Pet in the background when Windows signs in.'; $shortcut.Save()"
if errorlevel 1 (
    echo Failed to register %APP_NAME% as a startup app.
    pause
    exit /b 1
)

echo %APP_NAME% is now registered as a startup app for this Windows user.
echo It will start automatically the next time you sign in.
echo.
echo Startup shortcut:
echo   "%SHORTCUT%"
pause
