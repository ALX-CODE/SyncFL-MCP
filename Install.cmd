@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Interactive %*
set "SYNCFL_SETUP_EXIT=%ERRORLEVEL%"
if not "%SYNCFL_SETUP_EXIT%"=="0" echo SyncFL setup needs attention. See the message above.
pause
exit /b %SYNCFL_SETUP_EXIT%
