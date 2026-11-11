@echo off
rem Start ULTRON on Windows. Double-click this file, then open http://localhost:8080
cd /d "%~dp0"
start "" http://localhost:8080
:loop
python server.py
echo ULTRON stopped, restarting in 5 s...
timeout /t 5 >nul
goto loop
