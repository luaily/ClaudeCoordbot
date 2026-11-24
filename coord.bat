@echo off
rem CLI wrapper: coord.bat status, coord.bat lock file --reason "..."
uv run --quiet --project "%~dp0." coordbot %*
