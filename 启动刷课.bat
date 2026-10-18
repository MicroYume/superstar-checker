@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "COURSE_ID=%~1"
set "CLASS_ID=%~2"
set "CPI=%~3"

echo ====== Start studying ======
echo Keep this window OPEN. Close it or run the stop bat to pause (progress is kept).
echo.

if "!COURSE_ID!"=="" (
    set /p "COURSE_ID=Enter course_id: "
)
if "!CLASS_ID!"=="" (
    set /p "CLASS_ID=Enter class_id: "
)
if "!CPI!"=="" (
    set /p "CPI=Enter cpi: "
)

if "!COURSE_ID!"=="" (
    echo [ERROR] course_id is required.
    pause
    exit /b 1
)
if "!CLASS_ID!"=="" (
    echo [ERROR] class_id is required.
    pause
    exit /b 1
)
if "!CPI!"=="" (
    echo [ERROR] cpi is required.
    pause
    exit /b 1
)

echo.
echo Starting study task for course !COURSE_ID!, class !CLASS_ID!...
echo.
.venv\Scripts\superstar-checker.exe study-start !COURSE_ID! !CLASS_ID! !CPI!
pause
