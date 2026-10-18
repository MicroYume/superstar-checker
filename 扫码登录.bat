@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====== Chaoxing QR login (no password typed, nothing goes through AI) ======
echo A QR page opens in your browser. Scan it with the Xuexi Tong app,
echo then tap Confirm on the phone.
echo Each code lives about 150s; a fresh one loads automatically (up to 4 codes).
echo.
.venv\Scripts\superstar-checker.exe login --qr
echo.
echo If you see "status": true above, login OK. Go back to the chat and say "continue".
echo The MCP server picks the new session up from disk - no restart needed.
pause
