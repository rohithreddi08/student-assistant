@echo off
rem Builds ULTRON.apk (Android 6.0 and newer). Uses the toolchain in C:\Users\%USERNAME%\android-build
setlocal
cd /d "%~dp0"
set "TOOLS=%USERPROFILE%\android-build"
set "JAVA_HOME=%TOOLS%\jdk"
set "ANDROID_HOME=%TOOLS%\sdk"
set "PATH=%JAVA_HOME%\bin;%PATH%"
echo sdk.dir=%ANDROID_HOME:\=/%> local.properties

rem web build tools (Babel/PostCSS/core-js) for old WebViews - installed once
if exist "web\node_modules" goto :webok
pushd web
call npm install --no-audit --no-fund
if errorlevel 1 (popd & goto :fail)
popd
:webok
python prepare.py || goto :fail
call "%TOOLS%\gradle\bin\gradle.bat" --no-daemon assembleRelease || goto :fail
copy /y "app\build\outputs\apk\release\app-release.apk" "..\ULTRON.apk" >nul
echo.
echo Built: %~dp0..\ULTRON.apk
exit /b 0
:fail
echo BUILD FAILED
exit /b 1
