@echo off
cd /d "%~dp0"
echo ====== Chaoxing Login ======
echo Password is masked with *, it never leaves this window.
.venv\Scripts\superstar-checker.exe login
echo.
echo If you see "status": true above, login OK. Go back to the chat and say "continue".
pause
