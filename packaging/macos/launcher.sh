#!/bin/bash
# Contents/MacOS/installer - what Finder runs when "Install Open Loops.app" is double-clicked.
# Finder gives us no terminal, so hand the real work to Terminal, where the person can watch the
# download and type their first name. The script is copied out of the (read-only, soon ejected)
# disk image first so Terminal can keep running it.
RES="$(cd "$(dirname "${BASH_SOURCE[0]}")/../Resources" && pwd)"
TMP="$(mktemp -d /tmp/openloops-installer.XXXXXX)"
cp "$RES/Install Open Loops.command" "$TMP/Install Open Loops.command"
chmod +x "$TMP/Install Open Loops.command"
open -a Terminal "$TMP/Install Open Loops.command"
