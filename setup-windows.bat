@echo off
setlocal
cd /d "%~dp0"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python3"
)

if not defined PY (
  echo Python 3.11 or newer was not found.
  echo.
  echo 1. Download it from https://www.python.org/downloads/ and run the installer.
  echo 2. IMPORTANT: on the first installer screen tick "Add python.exe to PATH".
  echo 3. Then double-click setup-windows.bat again.
  echo.
  pause
  exit /b 1
)
echo Using: %PY%

if not exist ".venv\Scripts\python.exe" (
  echo Creating private Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create the environment.
    pause
    exit /b 1
  )
)

echo Installing the Discord library (needs internet)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo Install failed. Check your internet connection and try again.
  pause
  exit /b 1
)

if not exist config.toml (
  copy config.example.toml config.toml >nul
  echo Created config.toml
)

echo.
echo Setup finished.
echo Now fill in config.toml (it is opening in Notepad):
echo   bot_token, channel_id, agent_name, trusted_user_ids, trusted_webhook_ids
echo The README explains where to find each value. Save the file when done,
echo then double-click start-windows.bat.
start notepad config.toml
echo.
pause
