#!/bin/bash
# Open Loops - one-click installer for macOS. macOS equivalent of setup.ps1.
#
#   Double-click "Open Loops.command", or from a terminal:
#       bash install.sh
#
# What it does (all on this computer, nothing sent anywhere):
#   1. Checks for Python 3 and Claude Code, offering to install via Homebrew / the official
#      installer if missing.
#   2. Copies Open Loops to ~/Library/Application Support/OpenLoops (moving an older
#      ~/Documents/OpenLoops install there first).
#   3. Puts Open Loops.app (with the logo) on the Desktop and in ~/Applications.
#   4. Sets it to refresh every weekday morning (default 09:15) via launchd.
#   5. Opens the app - which walks you through connecting Slack and email.
#
# Options: --at HH:MM (refresh time), --name <first name> (skips the question).
# Testing a fresh install beside the one you use, without touching it (INSTALL.md "Testing a fresh install"):
#   bash install.sh --dest ~/OpenLoops-test --no-app --no-task --port 8790 --name "Test"
#   --dest DIR    install there instead (or env OPENLOOPS_DEST); never moves an older ~/Documents install
#   --no-app      do not write Open Loops.app to ~/Applications and the Desktop, or touch the Dock
#   --no-task     do not register the weekday refresh (there is one launchd job per Mac; this keeps yours)
#   --port N      the port this copy answers on, saved in its config.json (default 8765)
#   --no-launch   do not start it at the end
set -e

AT="09:15"
NAME=""
DEST="${OPENLOOPS_DEST:-}"
PORT=""
NO_APP=0
NO_TASK=0
NO_LAUNCH=0
AT_SET=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --at) AT="$2"; AT_SET=1; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --dest) DEST="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --no-app) NO_APP=1; shift ;;
        --no-task) NO_TASK=1; shift ;;
        --no-launch) NO_LAUNCH=1; shift ;;
        *) shift ;;
    esac
done
if [ -n "$PORT" ] && ! [[ "$PORT" =~ ^[0-9]{2,5}$ ]]; then
    echo "  --port must be a number, for example 8790." >&2
    exit 1
fi

say() { echo ""; echo "  $1"; }
ok()  { echo "  [ok] $1"; }

echo ""
echo "  ============================"
echo "   Open Loops - setup"
echo "  ============================"

# ---------- 1. Python ----------
say "Checking Python..."
if ! command -v python3 >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        say "Installing Python (this can take a minute)..."
        brew install python3
    else
        echo "  Python 3 wasn't found and Homebrew isn't installed." >&2
        echo "  Install Python 3 from https://www.python.org/downloads/ and run this again." >&2
        exit 1
    fi
fi
ok "Python $(python3 --version | sed 's/Python //')"

# ---------- 2. Claude Code ----------
say "Checking Claude..."
if ! command -v claude >/dev/null 2>&1; then
    say "Installing Claude Code (this can take a minute)..."
    if ! curl -fsSL https://claude.ai/install.sh | bash; then
        if command -v brew >/dev/null 2>&1; then
            brew install --cask claude-code || true
        fi
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v claude >/dev/null 2>&1; then
    echo "  Couldn't install Claude automatically. Please install it from https://claude.ai/code and run this again." >&2
    exit 1
fi
ok "Claude is installed"

# ---------- 3. Copy files ----------
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Install into ~/Library/Application Support, NOT ~/Documents: macOS privacy protection (TCC) stops the
# launchd weekday refresh (a background /bin/bash) from even reading a script under ~/Documents, Desktop
# or Downloads - "Operation not permitted" every morning, silently (#24). Application Support is not
# gated. The Desktop launcher points here, so the downloaded folder can be deleted afterwards.
DEFAULT_DEST="$HOME/Library/Application Support/OpenLoops"
DEST="${DEST:-$DEFAULT_DEST}"
case "$DEST" in /*) ;; *) DEST="$PWD/$DEST" ;; esac   # a relative --dest is relative to where this was run
DEST="${DEST%/}"
OLD="$HOME/Documents/OpenLoops"   # where installs before #24 went

# ---------- 3a. Move an older ~/Documents install here (keeps config, list, tone and logs) ----------
# Only for the default place: a --dest test install must never move the copy you use. The work is in
# scripts/migrate_install.py: it stops every writer first (the weekday job, any Open Loops server on
# 8765-8784 running from the old folder, anything else with its working folder there), then either renames
# in one step or copies, checks every byte and switches over. On any doubt it changes nothing and exits 1.
if [ "$DEST" = "$DEFAULT_DEST" ] && [ -f "$OLD/openloops/app.py" ]; then
    python3 "$SRC/scripts/migrate_install.py" --old "$OLD" --dest "$DEST"
    if [ "$SRC" = "$OLD" ] && [ ! -e "$OLD" ]; then
        SRC="$DEST"   # was running from the old copy itself: it is now at DEST
    fi
fi

if [ "$SRC" = "$DEST" ]; then
    say "Already installed here - updating."
else
    say "Installing Open Loops to $DEST ..."
    mkdir -p "$DEST"
    # Personal files are never copied over: the list, settings, tone, logs, the Grok project config the
    # person may have edited (.grok), a Google OAuth client they downloaded, and anything private.
    rsync -a --exclude 'state' --exclude 'voice.json' --exclude 'state.json' --exclude 'config.json' \
        --exclude 'people_suggested.json' --exclude '.git' --exclude '.grok' \
        --exclude 'google_oauth_client.json' --exclude 'profiles' --exclude 'private' "$SRC"/ "$DEST"/
fi
# The shipped Grok project config (bundled Gmail server) only when the install has none of its own yet.
if [ -f "$SRC/.grok/config.toml" ] && [ ! -e "$DEST/.grok/config.toml" ]; then
    mkdir -p "$DEST/.grok"
    cp "$SRC/.grok/config.toml" "$DEST/.grok/config.toml"
fi
mkdir -p "$DEST/state/logs"

# fresh state + config unless the person already has them
STATE_FILE="$DEST/state.json"
if [ ! -f "$STATE_FILE" ]; then
    CURSOR=$(python3 -c "from datetime import datetime, timedelta; print((datetime.now().astimezone()-timedelta(days=7)).isoformat(timespec='minutes'))")
    cat > "$STATE_FILE" <<EOF
{
  "cursor": "$CURSOR",
  "last_refresh": null,
  "loops": []
}
EOF
fi
CFG_FILE="$DEST/config.json"
if [ ! -f "$CFG_FILE" ]; then
    while [ -z "$NAME" ]; do
        read -r -p "  Your first name (used so messages sound like you): " NAME
    done
    python3 - "$SRC/config.template.json" "$CFG_FILE" "$NAME" "$AT" "$PORT" <<'PYEOF'
import json, sys
tpl_path, cfg_path, name, at, port = sys.argv[1:6]
cfg = json.load(open(tpl_path, encoding="utf-8-sig"))
cfg["owner_name"] = name
cfg["refresh_time"] = at
if port:
    cfg["port"] = int(port)  # app.py: --port beats OPENLOOPS_PORT beats this beats 8765
json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=2)
PYEOF
else
    # Updating (or just moved): the person's own refresh time wins unless --at was given, as in setup.ps1 -
    # otherwise re-registering the job below would quietly put it back to 09:15. An explicit --at or --port is
    # saved; everything else in config.json stays as it is.
    AT=$(python3 - "$CFG_FILE" "$AT" "$AT_SET" "$PORT" <<'PYEOF'
import json, re, sys
cfg_path, at, at_set, port = sys.argv[1:5]
try:
    cfg = json.load(open(cfg_path, encoding="utf-8-sig"))
except (OSError, ValueError):  # unreadable config.json: leave it alone, the app will say so
    print(at)
    sys.exit(0)
saved = str(cfg.get("refresh_time") or "")
if at_set == "0" and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", saved):
    at = saved  # a hand-edited bad value falls back to the default rather than breaking the job
changed = at_set == "1" or bool(port)
if at_set == "1":
    cfg["refresh_time"] = at
if port:
    cfg["port"] = int(port)
if changed:
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(at)
PYEOF
)
fi
ok "Files in place"

# ---------- 4. App with logo (Dock + Desktop) ----------
# Real .app so it can sit in the Dock. The zip's Open Loops.command is only first-run install.
if [ "$NO_APP" -eq 1 ]; then
    ok "Skipped Open Loops.app (--no-app). Start this copy with: cd \"$DEST\" && python3 -m openloops.app"
else
    mkdir -p "$HOME/Applications" "$HOME/Desktop"
    DOCK_FLAG=""
    # Don't pin to the Dock from a throwaway $HOME (tests) — killall Dock would hit the real Dock.
    if [ "$HOME" = "/Users/$(whoami)" ]; then
        DOCK_FLAG="--dock"
    fi
    bash "$DEST/scripts/macos-app.sh" --app-dir "$DEST" --out "$HOME/Applications/Open Loops.app" $DOCK_FLAG
    rm -f "$HOME/Desktop/Open Loops.command"
    rm -rf "$HOME/Desktop/Open Loops.app"
    cp -R "$HOME/Applications/Open Loops.app" "$HOME/Desktop/Open Loops.app"
    ok "Open Loops.app on the Desktop (drag it to the Dock if it isn't there)"
fi

# ---------- 5. Morning refresh ----------
if [ "$NO_TASK" -eq 1 ]; then
    ok "Skipped the weekday refresh (--no-task): whatever this Mac already had registered is unchanged"
else
    bash "$DEST/scripts/register-task.sh" --at "$AT" --dest "$DEST"
    ok "Will refresh itself weekdays at $AT"
fi

# ---------- 6. Open it ----------
if [ "$NO_LAUNCH" -eq 1 ]; then
    ok "Installed in $DEST (--no-launch: not started)"
else
    say "Opening Open Loops - it will guide you through connecting Slack and email."
    cd "$DEST"
    nohup python3 -m openloops.app ${PORT:+--port "$PORT"} >/dev/null 2>&1 &
    disown
fi
echo ""
echo "  Done. You can close this window."
echo ""
