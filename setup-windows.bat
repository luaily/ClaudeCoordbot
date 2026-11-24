@echo off
rem Double-click me to set up coordbot on Windows.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo coordbot needs uv, a small tool that installs Python programs.
  echo.
  echo 1. Open https://docs.astral.sh/uv/getting-started/installation/
  echo 2. Install it, then close and reopen this window.
  echo 3. Double-click setup-windows.bat again.
  pause
  exit /b 1
)
echo Installing coordbot (needs internet)...
uv tool install --force --from . coordbot
if errorlevel 1 (
  echo Install failed. Check your internet connection.
  pause
  exit /b 1
)
uv tool update-shell >nul 2>nul
echo.
uv tool run --from . coordbot setup
echo.
echo When you have saved your settings, open a NEW window and run:  coordbot doctor
pause
