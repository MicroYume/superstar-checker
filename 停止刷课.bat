@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -c "from superstar_checker.study import FileStopEvent; FileStopEvent().set(); print('Stop signal sent. Study pauses after the current report cycle. Progress is kept and will resume next time.')"
pause
