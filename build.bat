@echo off
rem One-click build of the CodeScope Windows application and installer.
rem Double-click this file, or run "build.bat -Clean", "build.bat -SkipTests", etc.
rem See packaging\build_windows.ps1 for all options.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_windows.ps1" %*
exit /b %ERRORLEVEL%
