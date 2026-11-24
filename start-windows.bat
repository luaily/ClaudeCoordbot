@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto notsetup
if not exist config.toml goto notsetup
".venv\Scripts\python.exe" -m coordbot run
echo.
echo The bot stopped. If you see an error above, see Troubleshooting in README.md.
pause
exit /b 0
:notsetup
echo Setup has not been done yet. Double-click setup-windows.bat first.
pause
exit /b 1
