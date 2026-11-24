@echo off
rem Double-click me to keep the bot online and recording (CLI users; MCP users do not need this).
cd /d "%~dp0"
uv run --quiet --project . coordbot run
echo.
echo The bot stopped. If you see an error above, see Troubleshooting in README.md.
pause
