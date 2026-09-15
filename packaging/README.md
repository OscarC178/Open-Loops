# Packaging — the downloadable installers

Two single-file installers, one per platform. Neither contains the app. When run, each downloads the
source zip of a GitHub ref (a `v*` tag for releases, a branch for test builds), unpacks it, and hands over
to the repo's own installer (`setup.ps1` on Windows, `install.sh` on Mac). That keeps one copy of the
install logic and means the installer rarely needs rebuilding.

| File | Built by | Built from | Runs |
|---|---|---|---|
| `OpenLoops-Setup.exe` | Inno Setup 6.3+ (`ISCC.exe`) | `windows/OpenLoops.iss` | wizard → download → `tar -xf` → `setup.ps1 -Dest "{app}" -Name … -At … -NoLaunch` |
| `OpenLoops.dmg` | `hdiutil` (macOS only) | `macos/build-dmg.sh` + `launcher.sh`, `install-openloops.command`, `Info.plist`, `README.txt` | Finder → Terminal → `curl` → `ditto` → `install.sh` |

## Where things land

- **Windows**: `%LOCALAPPDATA%\Programs\Open Loops` (Inno's `{autopf}` with `PrivilegesRequired=lowest`), i.e. the
  per-user Programs folder. Not `C:\Program Files`: that needs admin rights to write to, and the app keeps
  `config.json`, `state.json` and `state\logs\` next to itself. Desktop and Start-menu shortcuts run
  `pythonw -m openloops.app` in that folder with `docs\AppIcon.ico`. An uninstaller is registered in
  *Apps & features*; it removes the program files, both shortcuts and the scheduled task, and asks before
  deleting personal files.
- **Mac**: unchanged from `install.sh` — code in `~/Documents/OpenLoops`, `Open Loops.app` in `~/Applications`
  and on the Desktop, pinned to the Dock, `launchd` agent for the weekday refresh.

## Releasing

Work lands on `develop`; `main` is the live app. Fast-forward `main` when you want the installed copies and the
installers to pick the new code up, then tag it:

```
git checkout main && git merge --ff-only develop && git push
git tag v0.2
git push origin v0.2
```

Tags must be `vN.N` or `vN.N.N` (the number becomes the Mac bundle version and the Windows AppVersion);
the workflow's first job rejects anything else before the build runners start.

`.github/workflows/release.yml` builds both installers (Inno Setup is preinstalled on GitHub's Windows runners)
with `AppVersion=0.2` and `Ref=<the commit v0.2 pointed to>`, so the installer downloads exactly the code that
was tagged even if the tag is later moved, and attaches them to a GitHub Release. Stable links, always the
newest release:

- https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops-Setup.exe
- https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops.dmg

*Run workflow* on any branch builds both as workflow artifacts that download that branch when run (no release).

## Building and testing locally

Windows (needs Inno Setup: `winget install JRSoftware.InnoSetup`):

```
ISCC packaging\windows\OpenLoops.iss                       # dist\OpenLoops-Setup.exe, downloads main
ISCC /DAppVersion=0.2 /DRef=v0.2 packaging\windows\OpenLoops.iss
```

Run-time switches: `/Ref=<branch-or-tag>` picks another GitHub ref; `/ZipUrl=<url>` downloads any zip in
GitHub-archive shape (one top-level folder) — handy for testing uncommitted work behind `python -m http.server`;
`/Name=` and `/At=` prefill the wizard and are used as-is with `/SILENT` or `/VERYSILENT`; `/DIR="…"` and `/LOG="…"`
are Inno's own. A full silent round trip:

```
dist\OpenLoops-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /ZipUrl=http://127.0.0.1:8011/src.zip /Name=Test /DIR="%TEMP%\ol" /LOG="%TEMP%\ol.log"
"%TEMP%\ol\unins000.exe" /VERYSILENT
```

Note that a test install re-points the real *Claude Open Loops Refresh* scheduled task and the Desktop shortcut
at the test folder, and uninstalling removes them. Export the task first (`Export-ScheduledTask`) if you have a
live install.

Mac (needs macOS):

```
bash packaging/macos/build-dmg.sh --ref main --version 0.1 --out dist/OpenLoops.dmg
```

## Signing

Neither installer is code-signed, so Windows shows a SmartScreen "unknown publisher" warning and macOS an
unidentified-developer warning. On Windows, a code-signing certificate (Inno's `SignTool` setting) names the
publisher in that dialog, but SmartScreen reputation is separate and builds over time from downloads, so the
warning can persist for a while even with an OV or EV certificate. On Mac, an Apple Developer ID plus
notarisation (`codesign` / `notarytool` in `build-dmg.sh`) removes the warning. The Mac app is ad-hoc signed
so macOS does not call it "damaged".
