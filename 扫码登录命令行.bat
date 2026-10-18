@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====== Chaoxing QR login in terminal ======
echo A native square-pixel QR window will open. Scan it with the Xuexi Tong app.
echo Then tap Confirm on the phone.
echo Each code lives about 150s; a fresh one loads automatically.
echo.
.venv\Scripts\superstar-checker.exe login --qr --terminal
echo.
echo If you see "status": true above, login OK.
pause
