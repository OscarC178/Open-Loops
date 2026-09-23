"""Move an older Mac install out of ~/Documents (#24). Called by install.sh before it copies anything:

    python3 scripts/migrate_install.py --old ~/Documents/OpenLoops --dest ~/Library/Application\ Support/OpenLoops

Why: macOS will not let the launchd weekday refresh read ~/Documents, so the install has to live elsewhere.
The move is fail-closed - it either finishes with every file checked, or changes nothing and says why:

  1. Nothing to do when there is no old install, or when the new place already holds a list (state.json).
     A new place WITHOUT a list while the old one has one is refused: something half-made is in the way,
     and treating it as a fresh install would bury the real list.
  2. Stop every writer first: unload the weekday job if it points at the old folder, ask any Open Loops on
     ports 8765-8784 whose /api/diag root is the old folder to quit (curl, so tests can stub it), then
     refuse if any process still has its working folder inside the old one (lsof) - that catches older
     versions without /api/diag, a running job, or a Terminal opened there.
  3. Same disk and nothing left in iCloud only: one rename(2), which is all-or-nothing.
     Otherwise: copy to "<dest>.migrating", compare every file byte for byte, then rename into place and
     leave the old folder alone, recording that it was verified. A leftover "<dest>.migrating" from an
     interrupted run is ours and is started again from scratch.

Exit 0 = done or nothing to do (stdout says which), 1 = refused, nothing moved. Stdlib only.
"""
import argparse, filecmp, json, os, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

PORTS = range(8765, 8785)          # app.py's pick_port() range: an old copy may be on any of them
LABEL = "com.openloops.refresh"
MARKER = "state/migrated-from.txt"  # written into the new place: where it came from and that it was checked
SF_DATALESS = 0x40000000           # st_flags bit for an iCloud file whose contents are not on this Mac


def say(msg):
    print(f"  {msg}", flush=True)


def refuse(msg):
    print(f"  {msg}", file=sys.stderr, flush=True)
    print("  Nothing was moved. Your list and settings are still in the old folder.", file=sys.stderr, flush=True)
    sys.exit(1)


def curl(*args):
    """curl, never urllib: the installer test puts a stub curl first on PATH so nothing probes the real app."""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "1", *args], capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def root_on(port):
    rc, out = curl(f"http://127.0.0.1:{port}/api/diag")
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out).get("root")
    except ValueError:
        return None


def same(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def unload_job(old):
    """Unload the weekday job BEFORE moving anything, if it runs the old folder's script (it would be a writer).
    install.sh registers it again for the new place afterwards, unless --no-task."""
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    try:
        points_old = str(old) in plist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if points_old:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        say("Paused the weekday refresh while Open Loops moves (it is set up again in a moment).")


def stop_servers(old):
    running = [p for p in PORTS if same(root_on(p) or "/nonexistent", old)]
    for p in running:
        curl("-X", "POST", "-H", "Content-Type: application/json", "-d", "{}", f"http://127.0.0.1:{p}/api/quit")
    deadline = time.time() + 30
    while running and time.time() < deadline:
        running = [p for p in running if root_on(p) is not None]
        if running:
            time.sleep(1)
    if running:
        refuse("Open Loops is still finishing a job. Close its browser tab, wait a minute, then run the installer again.")


def users_of(old):
    """PIDs (other than ours) whose working folder is inside the old install."""
    if not shutil.which("lsof"):
        return []
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn"], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return []
    old_s, pids, pid = str(Path(old).resolve()), [], None
    for ln in out.splitlines():
        if ln.startswith("p"):
            pid = int(ln[1:])
        elif ln.startswith("n") and pid not in (None, os.getpid(), os.getppid()):  # us and install.sh
            n = ln[1:]
            if n == old_s or n.startswith(old_s + "/"):
                pids.append(pid)
    return pids


def has_dataless(root):
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            try:
                if getattr(os.lstat(os.path.join(dirpath, name)), "st_flags", 0) & SF_DATALESS:
                    return True
            except OSError:
                return True
    return False


def mismatches(a, b):
    """Every file under a must exist under b with the same bytes (and every symlink with the same target)."""
    bad = []
    for dirpath, _dirs, files in os.walk(a):
        for name in files:
            src = os.path.join(dirpath, name)
            dst = os.path.join(b, os.path.relpath(src, a))
            if os.path.islink(src):
                ok = os.path.islink(dst) and os.readlink(src) == os.readlink(dst)
            else:
                ok = os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False)
            if not ok:
                bad.append(os.path.relpath(src, a))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--dest", required=True)
    a = ap.parse_args()
    old, dest = Path(a.old), Path(a.dest)
    stage = dest.with_name(dest.name + ".migrating")

    if not (old / "openloops" / "app.py").is_file():
        return  # no old install: nothing to do
    if (dest / "state.json").exists():
        marker = dest / MARKER
        if marker.exists() and "verified" in marker.read_text(encoding="utf-8", errors="replace"):
            say(f"Your old copy in {old} was copied and checked earlier. Open Loops no longer uses it; you can delete it.")
        else:
            say(f"An older copy is still in {old}. Open Loops no longer uses it; nothing there was changed.")
        return
    if dest.exists() and (old / "state.json").exists():
        refuse(f"{dest} already exists but has no list in it, while the old folder does. "
               "Rename or remove that folder, then run the installer again.")
    if dest.exists():
        return  # neither has a list: an ordinary (re)install into dest, the old copy is left alone

    say(f"Moving Open Loops out of Documents to {dest} ...")
    unload_job(old)
    stop_servers(old)
    os.chdir(Path.home())  # our own working folder must not count as a user of the old one
    pids = users_of(old)
    if pids:
        refuse("Something is still using the old Open Loops folder (Open Loops itself, or a Terminal window opened "
               f"in it: process {', '.join(map(str, pids[:5]))}). Close it, then run the installer again.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    one_disk = old.stat().st_dev == dest.parent.stat().st_dev and not os.environ.get("OPENLOOPS_MIGRATE_COPY")
    if one_disk and not has_dataless(old):
        if stage.exists():
            shutil.rmtree(stage)  # left over from an interrupted copy; the old folder is still the real one
        os.rename(old, dest)      # one step: either all of it moved or none of it did
        if not (dest / "openloops" / "app.py").is_file() or old.exists():
            refuse("The move did not complete as expected.")  # rename(2) cannot half-happen; belt and braces
        say("Moved (your list and settings came with it).")
        return

    # Different disk, or files still in iCloud: copy (which downloads them), check every byte, then switch.
    if stage.exists():
        shutil.rmtree(stage)  # an interrupted earlier copy: start again, the old folder is untouched
    try:
        shutil.copytree(old, stage, symlinks=True)
    except Exception as e:  # noqa - disk full, iCloud download failed, permission
        shutil.rmtree(stage, ignore_errors=True)
        refuse(f"Could not copy Open Loops to the new place ({type(e).__name__}: {e}).")
    bad = mismatches(old, stage)
    if bad:
        shutil.rmtree(stage, ignore_errors=True)
        refuse(f"The copy did not match the original ({len(bad)} file(s), e.g. {bad[0]}).")
    (stage / MARKER).parent.mkdir(parents=True, exist_ok=True)
    (stage / MARKER).write_text(f"copied from {old} and verified byte for byte on "
                                f"{datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
    os.rename(stage, dest)  # same folder, so all-or-nothing
    say("Copied and checked (your list and settings came with it).")
    say(f"Your old copy in {old} is no longer used; you can delete it.")


if __name__ == "__main__":
    main()
