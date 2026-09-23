#!/bin/bash
# Open Loops - double-click me. macOS equivalent of "Open Loops.cmd".
#   Not installed yet?  -> runs the installer (install.sh), which puts Open Loops in
#                          ~/Library/Application Support/OpenLoops and a launcher on your Desktop.
#   Already installed?  -> starts it (backgrounded) and opens the page in your browser.
#   Installed in the old place (~/Documents/OpenLoops)? -> runs the installer, which copies the list and
#                          settings across and leaves the old folder as it is (#24).
set -e
# Non-interactive shells don't read the user's profile, so an AI CLI that is already installed (Homebrew or a
# vendor installer) may not be on PATH. Jobs inherit this. None is needed to start: with no AI found, the app's
# checklist offers Install <AI> (#39).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
DEST="$HOME/Library/Application Support/OpenLoops"
APP="$DEST/openloops/app.py"
if [ -f "$APP" ]; then
    cd "$DEST"
    nohup python3 -m openloops.app >/dev/null 2>&1 &
    disown
    sleep 1
    exit 0
fi
echo "Installing Open Loops..."
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$DIR/install.sh"
