@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo =======================================================
echo  Instagram Pro Downloader - Diagnostic Launcher
echo =======================================================
echo.
echo Select Language / เลือกภาษา:
echo  [1] English (Default)
echo  [2] ภาษาไทย (Thai)
echo.
set /p "LANG_CHOICE=Choice [1-2] (Press Enter for English): "

if "%LANG_CHOICE%"=="2" (
    set "LANG_ARG=th"
    set "PROMPT_USER=กรุณาระบุ Instagram username ที่ต้องการทดสอบ: "
    set "MSG_START=[*] กำลังเริ่มดึงและตรวจสอบข้อมูลสำหรับ @"
    set "MSG_VERIFY=[*] กำลังตรวจสอบความถูกต้องกับ Instagram API..."
    set "MSG_DONE=การทดสอบและการตรวจสอบความถูกต้องเสร็จสิ้น"
) else (
    set "LANG_ARG=en"
    set "PROMPT_USER=Enter target Instagram username: "
    set "MSG_START=[*] Starting inspection and payload extraction for @"
    set "MSG_VERIFY=[*] Cross-checking with Instagram Web Profile API..."
    set "MSG_DONE=Diagnostic and verification pipeline complete."
)

echo.
if "%~1"=="" (
    set /p "TARGET_USER=%PROMPT_USER%"
) else (
    set "TARGET_USER=%~1"
)

if "%TARGET_USER%"=="" (
    echo [!] Error: No username provided.
    echo.
    pause
    exit /b 1
)

echo %MSG_START%%TARGET_USER%...
echo.
python -m tests.live_user_test %TARGET_USER% --lang %LANG_ARG%

echo.
echo %MSG_VERIFY%
python verify_dump.py %TARGET_USER% --lang %LANG_ARG%

echo.
echo =======================================================
echo  %MSG_DONE%
echo =======================================================
pause