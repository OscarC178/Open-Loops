#!/bin/bash
# Runs inside Terminal (opened by launcher.sh). Downloads Open Loops from GitHub and runs the
# repo's own installer, install.sh. @REPO@ / @REF@ / @LABEL@ are filled in by build-dmg.sh
# (REF is what is downloaded - a commit sha for release builds; LABEL is the tag or branch name shown).
set -e
REPO="@REPO@"
REF="@REF@"
LABEL="@LABEL@"
URL="https://github.com/$REPO/archive/$REF.zip"

echo ""
echo "  ============================"
echo "   Open Loops - installer"
echo "  ============================"
echo ""
echo "  Downloading from https://github.com/$REPO ($LABEL) ..."
TMP="$(mktemp -d /tmp/openloops-src.XXXXXX)"
if ! curl -fL --progress-bar -o "$TMP/src.zip" "$URL"; then
    echo ""
    echo "  Could not download $URL" >&2
    echo "  Check your internet connection and try again." >&2
    read -n 1 -s -r -p "  Press any key to close this window."
    exit 1
fi
mkdir -p "$TMP/src"
if command -v ditto >/dev/null 2>&1; then
    ditto -x -k "$TMP/src.zip" "$TMP/src"
else
    unzip -q "$TMP/src.zip" -d "$TMP/src"
fi
# GitHub puts everything inside one top-level folder (Open-Loops-main, Open-Loops-0.2, ...)
SRC="$(find "$TMP/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$SRC" ] || [ ! -f "$SRC/install.sh" ]; then
    echo "  The download did not look like Open Loops (no install.sh inside)." >&2
    read -n 1 -s -r -p "  Press any key to close this window."
    exit 1
fi

bash "$SRC/install.sh" "$@"
rm -rf "$TMP"
echo ""
read -n 1 -s -r -p "  Press any key to close this window."
echo ""
