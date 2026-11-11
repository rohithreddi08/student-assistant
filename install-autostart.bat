@echo off
rem Makes ULTRON start silently in the background every time you log in to Windows.
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\ULTRON.vbs"
> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%VBS%" echo sh.CurrentDirectory = "%~dp0"
>> "%VBS%" echo sh.Run "pythonw server.py", 0, False
echo ULTRON will now start automatically when you log in.
echo Open http://localhost:8080 (or the installed ULTRON app) any time.
echo To undo: run uninstall-autostart.bat
pause
