#!/bin/bash
# Build OpenLoops.dmg - a disk image holding "Install Open Loops.app". macOS only (hdiutil).
#
# The installer app does NOT contain Open Loops. Double-clicked, it opens Terminal, downloads the
# repo from GitHub, and runs the repo's own install.sh (Python / Claude Code if missing, copy to
# ~/Documents/OpenLoops, Open Loops.app on the Desktop and in the Dock, weekday refresh).
#
#   bash packaging/macos/build-dmg.sh                        -> dist/OpenLoops.dmg, pulls main
#   bash packaging/macos/build-dmg.sh --ref <commit sha> --label v0.2 --version 0.2
#       (release build: the workflow passes the commit the tag pointed to, so a moved tag cannot change
#        what an already-downloaded installer installs; --label is what the Terminal window shows)
set -e

REPO="OscarC178/Open-Loops"
REF="main"
LABEL=""
VERSION="0.1"
OUT="dist/OpenLoops.dmg"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --label) LABEL="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        *) echo "unknown option $1" >&2; exit 1 ;;
    esac
done
[ -n "$LABEL" ] || LABEL="$REF"

# These land in Info.plist, a shell script and a URL via sed. Refuse anything outside the characters a
# GitHub slug, git ref or version number actually uses (& | " $ ; are legal in git ref names but would
# corrupt the output).
for v in REPO REF LABEL VERSION; do
    if [[ ! "${!v}" =~ ^[A-Za-z0-9._/-]+$ ]]; then
        echo "--$(echo "$v" | tr 'A-Z' 'a-z') '${!v}' may only contain letters, digits, . _ / -" >&2
        exit 1
    fi
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
STAGE="$(mktemp -d)"
APP="$STAGE/Install Open Loops.app"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ROOT/docs/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
sed -e "s|@VERSION@|$VERSION|g" "$HERE/Info.plist" > "$APP/Contents/Info.plist"
sed -e "s|@REPO@|$REPO|g" -e "s|@REF@|$REF|g" -e "s|@LABEL@|$LABEL|g" "$HERE/install-openloops.command" \
    > "$APP/Contents/Resources/Install Open Loops.command"
cp "$HERE/launcher.sh" "$APP/Contents/MacOS/installer"
chmod +x "$APP/Contents/MacOS/installer" "$APP/Contents/Resources/Install Open Loops.command"
sed -e "s|@REPO@|$REPO|g" "$HERE/README.txt" > "$STAGE/Read me first.txt"

# Ad-hoc signature: not a Developer ID, but it stops macOS calling the app "damaged".
if command -v codesign >/dev/null 2>&1; then
    codesign --force --deep --sign - "$APP" || true
fi

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
hdiutil create -volname "Open Loops" -srcfolder "$STAGE" -ov -format UDZO "$OUT" >/dev/null
echo "built $OUT"
