@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\app\sync-website.ps1" %*
if errorlevel 1 (
  echo Website sync was not completed. Read the error above before retrying.
)
pause
