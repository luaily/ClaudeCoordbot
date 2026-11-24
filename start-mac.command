#!/bin/bash
# Double-click me to start the coordbot listener on macOS.
cd "$(dirname "$0")" || exit 1
pause() { echo; read -n 1 -s -r -p "Press any key to close this window..."; echo; }

if [ ! -x .venv/bin/python ] || [ ! -f config.toml ]; then
  echo "Setup has not been done yet. Double-click setup-mac.command first."
  pause; exit 1
fi
.venv/bin/python -m coordbot run
echo
echo "The bot stopped. If you see an error above, see Troubleshooting in README.md."
pause
