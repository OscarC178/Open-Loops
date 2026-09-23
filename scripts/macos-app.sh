#!/bin/bash
# Build Open Loops.app — a real Mac app with the logo, so it can sit in the Dock.
# Called by install.sh. Not double-clicked by the user.
#
#   bash scripts/macos-app.sh --app-dir "$HOME/Library/Application Support/OpenLoops" --out ~/Applications/Open\ Loops.app
#   bash scripts/macos-app.sh ... --dock     # also pin to the Dock (idempotent)
set -e

APP_DIR=""
OUT=""
DOCK=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app-dir) APP_DIR="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --dock) DOCK=1; shift ;;
        *) shift ;;
    esac
done
if [ -z "$APP_DIR" ] || [ -z "$OUT" ]; then
    echo "usage: macos-app.sh --app-dir DIR --out FILE.app [--dock]" >&2
    exit 1
fi

CONTENTS="$OUT/Contents"
MACOS="$CONTENTS/MacOS"
RES="$CONTENTS/Resources"
rm -rf "$OUT"
mkdir -p "$MACOS" "$RES"

# Icon: committed icns, else build from docs/logo-512.png
if [ -f "$APP_DIR/docs/AppIcon.icns" ]; then
    cp "$APP_DIR/docs/AppIcon.icns" "$RES/AppIcon.icns"
elif [ -f "$APP_DIR/docs/logo-512.png" ] && command -v iconutil >/dev/null && command -v sips >/dev/null; then
    ICONSET="$(mktemp -d)/AppIcon.iconset"
    mkdir -p "$ICONSET"
    for s in 16 32 128 256 512; do
        sips -z $s $s "$APP_DIR/docs/logo-512.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
        sips -z $((s*2)) $((s*2)) "$APP_DIR/docs/logo-512.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"
    rm -rf "$(dirname "$ICONSET")"
fi

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Open Loops</string>
  <key>CFBundleDisplayName</key>
  <string>Open Loops</string>
  <key>CFBundleIdentifier</key>
  <string>uk.openloops.app</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleExecutable</key>
  <string>openloops</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

# Stay in the foreground so the Dock icon is this app, not a Terminal window.
# PATH matches Open Loops.command — Finder-launched apps get a thin PATH.
# The install path goes into the launcher shell-quoted (printf %q): a folder name with $, ` or quotes in it
# must stay a literal path, not be run as shell when the app is opened.
Q_APP=$(printf '%q' "$APP_DIR/openloops/app.py")
Q_DIR=$(printf '%q' "$APP_DIR")
cat > "$MACOS/openloops" <<LAUNCH
#!/bin/bash
export PATH="\$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH"
APP=$Q_APP
if [ ! -f "\$APP" ]; then
    osascript -e 'display alert "Open Loops" message "Open Loops is not installed yet. Double-click Open Loops.command in the folder you downloaded, once."' >/dev/null 2>&1 || true
    exit 1
fi
cd $Q_DIR
# Don't exec: replacing the process with python3 makes the Dock show Python's icon.
python3 -m openloops.app
LAUNCH
chmod +x "$MACOS/openloops"
xattr -d com.apple.quarantine "$OUT" 2>/dev/null || true
xattr -cr "$OUT" 2>/dev/null || true

if [ "$DOCK" -eq 1 ]; then
    python3 - "$OUT" <<'PY'
import os, pathlib, plistlib, subprocess, sys
app = pathlib.Path(sys.argv[1]).resolve()
uri = app.as_uri() + "/"
raw = subprocess.check_output(["defaults", "export", "com.apple.dock", "-"])
dock = plistlib.loads(raw)
apps = dock.get("persistent-apps") or []
already = False
for tile in apps:
    data = ((tile.get("tile-data") or {}).get("file-data") or {})
    s = data.get("_CFURLString") or ""
    if "Open%20Loops.app" in s or s.rstrip("/").endswith("Open Loops.app"):
        already = True
        break
if not already:
    apps.append({
        "tile-type": "file-tile",
        "tile-data": {
            "file-data": {
                "_CFURLString": uri,
                "_CFURLStringType": 15,
            },
            "file-label": "Open Loops",
        },
    })
    dock["persistent-apps"] = apps
    tmp = pathlib.Path("/tmp/openloops-dock.plist")
    tmp.write_bytes(plistlib.dumps(dock, fmt=plistlib.FMT_BINARY))
    subprocess.check_call(["defaults", "import", "com.apple.dock", str(tmp)])
    tmp.unlink(missing_ok=True)
    subprocess.Popen(["killall", "Dock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY
fi
