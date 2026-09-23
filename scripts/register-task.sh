#!/bin/bash
# Register/remove the weekday "com.openloops.refresh" launchd agent - macOS equivalent of
# register-task.ps1 (Windows Task Scheduler).
#
# Usage: scripts/register-task.sh [--at HH:MM] [--dest DIR] [--remove]
#   --dest DIR   the install whose scripts/run-refresh.sh the job runs (default: the copy this script is in).
#                Keep it out of ~/Documents, ~/Desktop and ~/Downloads: macOS will not let the job read them (#24).
set -e

AT="09:15"
REMOVE=0
DEST=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --at) AT="$2"; shift 2 ;;
        --dest) DEST="$2"; shift 2 ;;
        --remove) REMOVE=1; shift ;;
        *) shift ;;
    esac
done

if [ -n "$DEST" ]; then
    ROOT="$(cd "$DEST" && pwd)"
else
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
LABEL="com.openloops.refresh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_GUI="gui/$(id -u)"

if [ "$REMOVE" -eq 1 ]; then
    launchctl bootout "$UID_GUI/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL"
    exit 0
fi

if [[ ! "$AT" =~ ^[0-9]{2}:[0-9]{2}$ ]]; then
    echo "time must be HH:MM" >&2
    exit 1
fi
# force base-10 so "08"/"09" aren't read as invalid octal literals
HOUR=$((10#${AT%%:*}))
MIN=$((10#${AT##*:}))

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/state/logs"

# plistlib, not a text template: it escapes the paths, so a folder named "A & B" still makes a valid plist
python3 - "$PLIST" "$LABEL" "$ROOT" "$HOUR" "$MIN" <<'PY_EOF'
import plistlib, sys
plist, label, root, hour, minute = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
with open(plist, "wb") as f:
    plistlib.dump({
        "Label": label,
        "ProgramArguments": ["/bin/bash", f"{root}/scripts/run-refresh.sh"],
        "StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)],
        "StandardOutPath": f"{root}/state/logs/launchd.out.log",
        "StandardErrorPath": f"{root}/state/logs/launchd.err.log",
        "RunAtLoad": False,
    }, f)
PY_EOF

launchctl bootout "$UID_GUI/$LABEL" 2>/dev/null || true
launchctl bootstrap "$UID_GUI" "$PLIST"
echo "Registered '$LABEL' weekdays at $AT. Test: launchctl kickstart $UID_GUI/$LABEL"
