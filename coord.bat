@echo off
rem Agent wrapper: coord.bat status, coord.bat lock file --reason "..." . Works from any directory.
setlocal
set "DIR=%~dp0"
if not exist "%DIR%.venv\Scripts\python.exe" (
  echo coordbot is not set up yet: run setup-windows.bat first. 1>&2
  exit /b 1
)
set "PYTHONPATH=%DIR%"
"%DIR%.venv\Scripts\python.exe" -m coordbot %*
exit /b %errorlevel%
