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

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$ROOT/scripts/run-refresh.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    </array>
    <key>StandardOutPath</key><string>$ROOT/state/logs/launchd.out.log</string>
    <key>StandardErrorPath</key><string>$ROOT/state/logs/launchd.err.log</string>
    <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST_EOF

launchctl bootout "$UID_GUI/$LABEL" 2>/dev/null || true
launchctl bootstrap "$UID_GUI" "$PLIST"
echo "Registered '$LABEL' weekdays at $AT. Test: launchctl kickstart $UID_GUI/$LABEL"
