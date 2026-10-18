@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -c "from superstar_checker.client import ChaoxingClient; ChaoxingClient().clear_session(); print('Logged out. Session cleared (memory + disk).')"
echo To switch account, double-click the login bat next.
pause
