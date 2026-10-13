@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_dev.ps1" %*
set "SYNCFL_DEV_EXIT=%ERRORLEVEL%"
if not "%SYNCFL_DEV_EXIT%"=="0" echo SyncFL development setup needs attention. See the message above.
pause
exit /b %SYNCFL_DEV_EXIT%
