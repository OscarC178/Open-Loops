#!/bin/bash
# Open Loops - one-click installer for macOS. macOS equivalent of setup.ps1.
#
#   Double-click "Open Loops.command", or from a terminal:
#       bash install.sh
#
# What it does (all on this computer, nothing sent anywhere):
#   1. Checks for Python 3, installing it with Homebrew if Homebrew is there (else it says where to get it).
#      It never installs an AI CLI (Claude, Codex, Grok): the app's checklist offers Install <AI> for the AI
#      the person picks, downloads the vendor's installer to a file, checks it and runs it (#16, #39).
#   2. Copies Open Loops to ~/Library/Application Support/OpenLoops (copying the list and settings of
#      an older ~/Documents/OpenLoops install across first; the old folder is left as it is).
#   3. Puts Open Loops.app (with the logo) on the Desktop and in ~/Applications.
#   4. Sets it to refresh every weekday morning (default 09:15) via launchd.
#   5. Opens the app - which walks you through connecting Slack and email.
#
# Options: --at HH:MM (refresh time), --name <first name> (skips the question), --help (this list, installs nothing).
# Testing a fresh install beside the one you use, without touching it (INSTALL.md "Testing a fresh install"):
#   bash install.sh --dest ~/OpenLoops-test --no-app --no-task --port 8790 --name "Test"
#   --dest DIR    install there instead (or env OPENLOOPS_DEST); never reads an older ~/Documents install
#   --no-app      do not write Open Loops.app to ~/Applications and the Desktop, or touch the Dock
#   --no-task     do not register the weekday refresh (there is one launchd job per Mac; this keeps yours)
#   --port N      the port this copy answers on, saved in its config.json (default 8765)
#   --no-launch   do not start it at the end
#   --isolated    a test copy that stays away from your own files and starts no scan by itself (#36): implies
#                 --no-app and --no-task, and writes "isolated": true (and "test_copy": true) into its config.json.
#                 It reads no to-do file, its page waits for Start the first scan every time it is opened, and the
#                 old-install port probe is skipped. Once that button is pressed it still uses the AI you are signed
#                 in to, so your real accounts (read-only: a scan sends and drafts nothing).
#                 OPENLOOPS_ISOLATED=1 in the environment does the same for any copy at run time.
set -e

AT="09:15"
NAME=""
DEST="${OPENLOOPS_DEST:-}"
PORT=""
NO_APP=0
NO_TASK=0
NO_LAUNCH=0
ISOLATED=0
AT_SET=0
USAGE="usage: bash install.sh [--at HH:MM] [--name NAME] [--dest DIR] [--port N] [--no-app] [--no-task] [--no-launch] [--isolated] [--help]"

# --help: the header comment above is the flag list, so print it rather than keep a second copy that drifts (#55).
# Everything from line 2 up to (not including) `set -e`, with the leading "# " taken off.
show_help() {
    echo "$USAGE"
    echo ""
    if [ -f "${BASH_SOURCE[0]}" ]; then
        sed -n '2,/^set -e$/p' "${BASH_SOURCE[0]}" | sed '$d' | sed -e 's/^# \{0,1\}//'
    fi
}
# A bad option stops here, before anything is written, paused or started (#55): an unknown flag used to be dropped
# silently and the installer carried on with a full default install.
bad_option() {
    echo "  $1" >&2
    echo "  $USAGE" >&2
    echo "  (bash install.sh --help lists what each option does)" >&2
    exit 1
}
# A value-taking flag needs a value that is not itself a flag: `--dest --isolated` must not install into a folder
# called "--isolated", and `--dest` given last must not fall back to the default place.
# An explicitly empty value is refused too (an unset variable in `--dest "$X"` must not mean "the copy you use"),
# except for --name, where `--name ""` has always meant "ask for the name" (the prompt further down).
need_value() {   # need_value <flag> <number of arguments left> <the next argument>
    if [ "$2" -lt 2 ] || { [ -z "$3" ] && [ "$1" != "--name" ]; } || [[ "$3" == -* ]]; then
        local example
        case "$1" in
            --at) example="--at 09:15" ;;
            --name) example="--name Sam" ;;
            --dest) example="--dest ~/OpenLoops-test" ;;
            *) example="--port 8790" ;;
        esac
        bad_option "$1 needs a value, for example: $example"
    fi
}
# All option checks happen in this loop and the --at / --port checks just below it: nothing before them writes anything.
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) show_help; exit 0 ;;
        --at) need_value "$1" $# "${2-}"; AT="$2"; AT_SET=1; shift 2 ;;
        --name) need_value "$1" $# "${2-}"; NAME="$2"; shift 2 ;;
        --dest) need_value "$1" $# "${2-}"; DEST="$2"; shift 2 ;;
        --port) need_value "$1" $# "${2-}"; PORT="$2"; shift 2 ;;
        --no-app) NO_APP=1; shift ;;
        --no-task) NO_TASK=1; shift ;;
        --no-launch) NO_LAUNCH=1; shift ;;
        --isolated) ISOLATED=1; NO_APP=1; NO_TASK=1; shift ;;   # a test copy touches no app icon and no weekday job
        *) bad_option "unknown option: $1" ;;   # a typo (--isolatd, --Dest) or a stray word: never ignored
    esac
done
# --at too: register-task.sh would reject a bad time only after config.json had already saved it
if [ "$AT_SET" -eq 1 ] && ! [[ "$AT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
    echo "  --at must be HH:MM, 24-hour (00:00 to 23:59), for example 09:15." >&2
    exit 1
fi
# checked before anything is written: a bad port saved in config.json would stop the app from starting
if [ -n "$PORT" ] && { ! [[ "$PORT" =~ ^[0-9]{1,5}$ ]] || [ "$((10#$PORT))" -lt 1024 ] || [ "$((10#$PORT))" -gt 65535 ]; }; then
    echo "  --port must be a number from 1024 to 65535, for example 8790." >&2
    exit 1
fi
[ -n "$PORT" ] && PORT="$((10#$PORT))"   # "08790" -> 8790

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

# ---------- 2. AI ----------
# Nothing is installed here, and nothing here can fail the install: the app's first screen offers
# Install <AI> for whichever AI the person chooses (Claude, Codex or Grok). This only says so when none is
# found. The folders are where the vendors' installers put them (agent.install_dirs), often not yet on PATH.
AI_FOUND=0
for cli in claude codex grok; do
    if PATH="$PATH:$HOME/.local/bin:$HOME/.grok/bin" command -v "$cli" >/dev/null 2>&1; then AI_FOUND=1; fi
done
if [ "$AI_FOUND" -eq 0 ]; then
    say "No AI is installed yet. Open Loops will offer to install one on its first screen."
fi

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
# A test copy is one made with --dest, --no-app and --no-task together (INSTALL.md "Testing a fresh install"). It is
# recorded as "test_copy": true in its config.json, so the app never tells it to fix the other copy's morning refresh.
TEST_COPY=0
if [ "$DEST" != "$DEFAULT_DEST" ] && [ "$NO_APP" -eq 1 ] && [ "$NO_TASK" -eq 1 ]; then TEST_COPY=1; fi
if [ "$ISOLATED" -eq 1 ]; then TEST_COPY=1; fi   # --isolated is a test copy wherever it is

# ---------- 3a. Bring over an older ~/Documents install's list and settings (copy only) ----------
# Only for the default place: a --dest test install never reads the copy you use. scripts/migrate_install.py
# copies the named personal files into the new place after making sure the old copy is idle; it never moves,
# renames or deletes anything in the old folder. If it cannot be sure, it stops the installer (exit 1).
if [ "$DEST" = "$DEFAULT_DEST" ] && [ -f "$OLD/openloops/app.py" ]; then
    MIGRATE_FLAGS=()
    [ "$NO_TASK" -eq 1 ] && MIGRATE_FLAGS+=(--no-task)   # no new job will be registered: put the old one back
    [ "$ISOLATED" -eq 1 ] && MIGRATE_FLAGS+=(--isolated)   # send nothing to the ports an Open Loops may answer on
    python3 "$SRC/scripts/migrate_install.py" --old "$OLD" --dest "$DEST" "${MIGRATE_FLAGS[@]}"
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
    # The first scan reads as far back as Settings > History says (history_days, as app.py's fresh_state does), which
    # is what the page tells the person before it starts (#38): from the config.json already here, else the template.
    CURSOR=$(python3 - "$DEST/config.json" "$SRC/config.template.json" <<'PYEOF'
import json, sys
from datetime import datetime, timedelta
days = 30
for f in sys.argv[1:]:
    try:
        days = min(int(json.load(open(f, encoding="utf-8-sig")).get("history_days") or 30), 365)
        break
    except (OSError, ValueError, TypeError, AttributeError):
        continue
print((datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="minutes"))
PYEOF
)
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
    python3 - "$SRC/config.template.json" "$CFG_FILE" "$NAME" "$AT" "$PORT" "$TEST_COPY" "$ISOLATED" <<'PYEOF'
import json, sys
tpl_path, cfg_path, name, at, port, test_copy, isolated = sys.argv[1:8]
cfg = json.load(open(tpl_path, encoding="utf-8-sig"))
cfg["owner_name"] = name
cfg["refresh_time"] = at
if port:
    cfg["port"] = int(port)  # app.py: --port beats OPENLOOPS_PORT beats this beats 8765
if test_copy == "1":
    cfg["test_copy"] = True  # doctor.is_test_copy
if isolated == "1":
    cfg["isolated"] = True   # store.isolated: no to-do file, no scan by itself (#36)
cfg["first_scan"] = "later"  # the weekday task reads nothing until Start the first scan (store.scheduled_skip)
json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=2)
PYEOF
else
    # Updating (or just moved): the person's own refresh time wins unless --at was given, as in setup.ps1 -
    # otherwise re-registering the job below would quietly put it back to 09:15. An explicit --at or --port is
    # saved; everything else in config.json stays as it is.
    AT=$(python3 - "$CFG_FILE" "$AT" "$AT_SET" "$PORT" "$TEST_COPY" "$ISOLATED" <<'PYEOF'
import json, re, sys
cfg_path, at, at_set, port, test_copy, isolated = sys.argv[1:7]
try:
    cfg = json.load(open(cfg_path, encoding="utf-8-sig"))
except (OSError, ValueError):  # unreadable config.json: leave it alone, the app will say so
    print(at)
    sys.exit(0)
saved = str(cfg.get("refresh_time") or "")
if at_set == "0" and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", saved):
    at = saved  # a hand-edited bad value falls back to the default rather than breaking the job
changed = (at_set == "1" or bool(port) or (test_copy == "1") != (cfg.get("test_copy") is True)
           or (isolated == "1") != (cfg.get("isolated") is True))
if test_copy == "1":
    cfg["test_copy"] = True   # doctor.is_test_copy; this run says what the copy is
else:
    cfg.pop("test_copy", None)
if isolated == "1":
    cfg["isolated"] = True    # store.isolated; likewise, a run without --isolated takes the mark off
else:
    cfg.pop("isolated", None)
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
# --name only names a new copy (config.json "owner_name"); say which name this copy uses either way (#56)
OWNER=$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1], encoding="utf-8-sig")).get("owner_name") or "")
except Exception: print("")' "$CFG_FILE")
if [ -n "$OWNER" ]; then
    if [ -n "$NAME" ] && [ "$NAME" != "$OWNER" ]; then
        ok "Kept the name this copy already has: $OWNER (--name only names a new copy)"
    else
        ok "Your first name: $OWNER (used so messages sound like you)"
    fi
fi
# The port the printed start command answers on, as app.py picks it for a run without --port: OPENLOOPS_PORT in the
# environment, else config.json "port" (where --port was just saved), else 8765 (review of #59)
SHOW_PORT=$(python3 -c 'import json,sys
try: print(int(json.load(open(sys.argv[1], encoding="utf-8-sig")).get("port") or 8765))
except Exception: print(8765)' "$CFG_FILE")
# app.py takes --port first, then OPENLOOPS_PORT, then config.json: the launch below passes --port when it was given,
# and the printed command does the same, so the address matches both (review of #59)
PORT_ARG=""
if [ -n "$PORT" ]; then SHOW_PORT="$PORT"; PORT_ARG=" --port $PORT"; PORT_FROM="--port you gave"
elif [[ "${OPENLOOPS_PORT:-}" =~ ^[0-9]{1,5}$ ]]; then SHOW_PORT="$((10#$OPENLOOPS_PORT))"; PORT_FROM="OPENLOOPS_PORT in your environment"
else PORT_FROM="port in its config.json"; fi
# Why a step was skipped, in the words the person typed: --isolated is "test copy", not the flags it implies (#56)
if [ "$ISOLATED" -eq 1 ]; then SKIP_APP="test copy"; SKIP_TASK="test copy"; else SKIP_APP="--no-app"; SKIP_TASK="--no-task"; fi
if [ "$ISOLATED" -eq 1 ]; then
    ok "Isolated test copy: it reads no to-do file and starts no scan until you press Start the first scan"
fi

# ---------- 4. App with logo (Dock + Desktop) ----------
# Real .app so it can sit in the Dock. The zip's Open Loops.command is only first-run install.
if [ "$NO_APP" -eq 1 ]; then
    ok "Skipped Open Loops.app ($SKIP_APP): start this copy from Terminal, as shown at the end"
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
# --isolated over a copy that has the weekday job (there is one per Mac): take it away, so "no automatic scans" is
# true. Only a job whose plist runs THIS copy's script; one for another copy is left alone. (refresh.py skips on an
# isolated copy anyway: this is the second line.)
PLIST="$HOME/Library/LaunchAgents/com.openloops.refresh.plist"
if [ "$ISOLATED" -eq 1 ] && [ -f "$PLIST" ] && python3 - "$PLIST" "$DEST" <<'PYEOF'
import os, plistlib, sys
try:
    args = plistlib.load(open(sys.argv[1], "rb")).get("ProgramArguments") or []
except Exception:
    sys.exit(1)   # unreadable: not provably ours, left alone
mine = os.path.realpath(os.path.join(sys.argv[2], "scripts", "run-refresh.sh"))
sys.exit(0 if any(isinstance(a, str) and os.path.realpath(a) == mine for a in args) else 1)
PYEOF
then
    bash "$DEST/scripts/register-task.sh" --remove >/dev/null
    ok "Removed this copy's weekday refresh (--isolated: it starts no scan by itself)"
    TASK_REMOVED=1
fi
if [ "$NO_TASK" -eq 1 ] && [ "${TASK_REMOVED:-0}" -eq 1 ]; then
    ok "No new weekday refresh registered ($SKIP_TASK)"   # the line above said what changed: not "unchanged" (review of #59)
elif [ "$NO_TASK" -eq 1 ]; then
    ok "Skipped the weekday refresh ($SKIP_TASK): whatever this Mac already had registered is unchanged"
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
if [ "$NO_APP" -eq 1 ]; then
    # no icon to start it from: the command, and the address it answers on (#56)
    echo ""
    echo "  Start this copy with:"
    echo "    cd \"$DEST\" && python3 -m openloops.app$PORT_ARG"
    echo "  It opens at http://localhost:$SHOW_PORT (the $PORT_FROM; the next free port if that one is taken)"
fi
echo ""
echo "  Done. You can close this window."
echo ""
