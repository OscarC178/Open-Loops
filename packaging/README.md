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

Nobody tags by hand. `.github/workflows/release.yml` runs on every push to `develop` and `main`, picks the next
version number, creates the tag, builds both installers (Inno Setup is preinstalled on GitHub's Windows runners)
with that version and `Ref=<the pushed commit>`, and publishes a GitHub Release. The installer therefore downloads
exactly the code that was released even if the tag is later moved.

| Merge to | Tag | Release | Who it is for |
|---|---|---|---|
| `develop` | `v0.1.1-dev.<run number>` | **pre-release** "Open Loops v0.1.1-dev.42 (develop test build)" | you and testers: https://github.com/OscarC178/Open-Loops/releases?q=prerelease%3Atrue |
| `main` | `v0.1.1` (patch bump over the newest stable tag) | release "Open Loops v0.1.1" | everyone, via the stable links below |

Pre-releases never count as *latest*, so the links on the website keep pointing at `main`:

- https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops-Setup.exe
- https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops.dmg

Bigger bumps: put the label `release:minor` (v0.1.x → v0.2.0) or `release:major` (→ v1.0.0) on the PR you merge
into `main`. Put `[skip release]` in the merge commit message to merge without tagging or building. A commit
that already carries a `v*` tag is not tagged or built again, so pushing a tag by hand (`v0.2`, `v0.2.1` or
`v0.2.1-dev.3`, nothing else) still works and builds that tag; use plain (lightweight) tags for that, an
annotated tag is not recognised by the already-tagged check.

*Run workflow* on any branch builds both as workflow artifacts that download that branch when run (no tag,
no release).

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
