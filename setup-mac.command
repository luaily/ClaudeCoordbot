#!/bin/bash
# Double-click me to set up coordbot on macOS.
cd "$(dirname "$0")" || exit 1

pause() { echo; read -n 1 -s -r -p "Press any key to close this window..."; echo; }

PY=""
for c in python3 python /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.11 or newer was not found on this Mac."
  echo
  echo "1. Download it from https://www.python.org/downloads/ and install it."
  echo "2. Then double-click setup-mac.command again."
  pause; exit 1
fi
echo "Using $("$PY" --version) ($PY)"

if [ ! -x .venv/bin/python ]; then
  echo "Creating private Python environment (.venv)..."
  "$PY" -m venv .venv || { echo "Could not create the environment."; pause; exit 1; }
fi

echo "Installing the Discord library (needs internet)..."
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || { echo "Install failed. Check your internet connection and try again."; pause; exit 1; }

if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  echo "Created config.toml"
fi
chmod +x start-mac.command coord 2>/dev/null

echo
echo "Setup finished."
echo "Now fill in config.toml (it is opening in your text editor):"
echo "  bot_token, channel_id, agent_name, trusted_user_ids, trusted_webhook_ids"
echo "The README explains where to find each value. Save the file when done,"
echo "then double-click start-mac.command."
open -t config.toml 2>/dev/null || open config.toml
pause
