#!/bin/bash
# Double-click me to set up coordbot on macOS (or run it on Linux).
cd "$(dirname "$0")" || exit 1
pause() { echo; read -n 1 -s -r -p "Press any key to close this window..."; echo; }

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "coordbot needs uv, a small tool that installs Python programs."
  echo
  echo "1. Open https://docs.astral.sh/uv/getting-started/installation/"
  echo "2. Install it (on a Mac with Homebrew: brew install uv)."
  echo "3. Then double-click setup-mac.command again."
  pause; exit 1
fi

echo "Installing coordbot (needs internet)..."
"$UV" tool install --force --from . coordbot || { echo "Install failed. Check your internet connection."; pause; exit 1; }
"$UV" tool update-shell >/dev/null 2>&1
chmod +x start-mac.command coord 2>/dev/null

echo
"$UV" tool run --from . coordbot setup
echo
echo "When you have saved your settings, check them with:  coordbot doctor"
echo "(open a NEW Terminal window first so the coordbot command is found)."
pause
