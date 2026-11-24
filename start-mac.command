#!/bin/bash
# Double-click me to keep the bot online and recording (CLI users; MCP users don't need this).
cd "$(dirname "$0")" || exit 1
pause() { echo; read -n 1 -s -r -p "Press any key to close this window..."; echo; }
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
[ -x "$UV" ] || { echo "Run setup-mac.command first."; pause; exit 1; }
"$UV" run --quiet --project . coordbot run
echo
echo "The bot stopped. If you see an error above, see Troubleshooting in README.md."
pause
