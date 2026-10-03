@echo off
setlocal
cd /d "%~dp0"

:: หา Path ของ Python และ main.py
set "PYTHON_EXE=%~dp0venv\Scripts\pythonw.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=pythonw.exe"
)
set "APP_PATH=%~dp0main.py"

echo ===================================================
echo  Registering igdownloader:// Custom URI Scheme
echo ===================================================

reg add "HKCU\Software\Classes\igdownloader" /ve /d "URL:Instagram Pro Downloader Protocol" /f
reg add "HKCU\Software\Classes\igdownloader" /v "URL Protocol" /d "" /f
reg add "HKCU\Software\Classes\igdownloader\shell\open\command" /ve /d "\"%PYTHON_EXE%\" \"%APP_PATH%\" \"%%1\"" /f

echo.
echo [SUCCESS] Protocol registered successfully!
pause