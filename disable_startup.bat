@echo off
setlocal

set "APP_NAME=Desktop Pet"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_DIR%\%APP_NAME%.lnk"

if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    if errorlevel 1 (
        echo Failed to remove the startup shortcut:
        echo   "%SHORTCUT%"
        pause
        exit /b 1
    )

    echo %APP_NAME% has been removed from Windows startup for this user.
) else (
    echo No %APP_NAME% startup shortcut was found for this user.
)

echo.
echo Startup shortcut checked:
echo   "%SHORTCUT%"
pause
