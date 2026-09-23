"""Bring the list and settings of an older Mac install (in ~/Documents) into the new one (#24). Copy only.

    python3 scripts/migrate_install.py --old ~/Documents/OpenLoops --dest ~/Library/Application\\ Support/OpenLoops

Why: macOS will not let the launchd weekday refresh read ~/Documents, so the install now lives elsewhere.
Called by install.sh before it copies the program files. It NEVER moves, renames or deletes anything under the
old folder, and never suggests deleting it: the old copy stays exactly as it was, as a fallback.

  1. Nothing to do without an old install. If the new install already has a list (state.json), nothing is
     copied again: the new copy is the one in use.
  2. The old copy must be idle first, or the copy could catch a half-written file:
     - the weekday job, if its plist (read with plistlib) runs the old folder's script, is unloaded; if it is
       loaded and cannot be unloaded, stop.
     - an Open Loops server on 8765-8784 is asked to quit only if /api/diag says "app": "openloops" AND its
       root is the old folder; a port that does not answer in time is "could not confirm", so stop.
     - lsof must show nothing else working inside the old folder; if lsof is missing or fails, stop.
  3. Copy only the named personal files and folders, file by file (shutil.copy2). Symbolic links are skipped
     and reported, never followed. state.json goes last, so "the new install has a list" only ever means the
     copy finished. Every copied file is then compared byte for byte; any difference stops the install.

Exit 0 = copied or nothing to do, 1 = stopped (one sentence what happened, one what to do; details in the log).
Stdlib only.
"""
import argparse, filecmp, json, os, plistlib, shutil, subprocess, sys, time, traceback
from pathlib import Path

PORTS = range(8765, 8785)   # app.py's pick_port() range: an old copy may be on any of them
LABEL = "com.openloops.refresh"
FILES = ["config.json", "voice.json", "people_suggested.json", "google_oauth_client.json"]   # state.json last
DIRS = ["state", ".grok", "profiles", "private"]
LOG = Path.home() / "Library" / "Logs" / "OpenLoops" / "install.log"   # tracebacks go here, never on screen


class Stop(Exception):
    """A plain-words reason to stop: what happened, and what to do."""
    def __init__(self, what, todo):
        super().__init__(what)
        self.what, self.todo = what, todo


def say(msg):
    print(f"  {msg}", flush=True)


def log(text):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} migrate_install\n{text}\n")
    except OSError:
        pass


def inside(path, folder):
    """True if path is the folder or anything under it (both resolved, so symlinked spellings agree)."""
    p, f = os.path.realpath(path), os.path.realpath(folder)
    return p == f or p.startswith(f + os.sep)


def unload_job(old):
    """The weekday job is a writer too. Unload it if its plist runs the old folder's script; refuse if it is
    loaded and will not unload. install.sh registers it again for the new place afterwards (unless --no-task)."""
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if not plist.exists():
        return
    try:
        args = plistlib.loads(plist.read_bytes()).get("ProgramArguments") or []
    except Exception as e:
        log(f"plist unreadable: {plist}: {e!r}")
        raise Stop("Open Loops couldn't read its morning refresh settings, so it can't tell whether the old copy still uses them.",
                   f"Run the installer again; if this keeps happening, send {LOG} to whoever set Open Loops up.")
    if not any(isinstance(a, str) and a.endswith("run-refresh.sh") and inside(a, old) for a in args):
        return
    target = f"gui/{os.getuid()}/{LABEL}"
    if subprocess.run(["launchctl", "print", target], capture_output=True).returncode != 0:
        return  # not loaded: nothing to stop
    r = subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"launchctl bootout {target} -> {r.returncode}: {r.stdout}{r.stderr}")
        raise Stop("Open Loops couldn't pause the old copy's morning refresh.",
                   "Restart your Mac, then run the installer again.")
    say("Paused the old copy's morning refresh (it is set up again for the new copy in a moment).")


def diag(port):
    """-> ("none", None) nothing listening, ("answer", dict) something answered, ("unknown", None) no clear answer.
    curl, never urllib: the installer test puts a stub curl first on PATH so nothing probes the real app."""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "2", f"http://127.0.0.1:{port}/api/diag"],
                           capture_output=True, text=True, timeout=10)
    except Exception as e:
        log(f"curl {port}: {e!r}")
        return "unknown", None
    if r.returncode == 7:   # connection refused: nothing there
        return "none", None
    if r.returncode != 0:   # 28 = timed out, anything else = could not tell
        log(f"curl {port} -> {r.returncode}")
        return "unknown", None
    try:
        return "answer", json.loads(r.stdout)
    except ValueError:
        return "answer", {}  # some other program on that port


def ours(answer, old):
    return isinstance(answer, dict) and answer.get("app") == "openloops" and isinstance(answer.get("root"), str) \
        and os.path.realpath(answer["root"]) == os.path.realpath(old)


def stop_servers(old):
    cant = Stop("Open Loops couldn't confirm that the old copy has stopped.",
                "Quit Open Loops (close its browser tab and wait a minute), then run the installer again.")
    running = []
    for p in PORTS:
        kind, answer = diag(p)
        if kind == "unknown":
            raise cant
        if kind == "answer" and ours(answer, old):
            running.append(p)
    for p in running:
        subprocess.run(["curl", "-s", "--max-time", "5", "-X", "POST", "-H", "Content-Type: application/json",
                        "-d", "{}", f"http://127.0.0.1:{p}/api/quit"], capture_output=True, timeout=15)
    deadline = time.time() + 30
    while running and time.time() < deadline:
        time.sleep(1)
        still = []
        for p in running:
            kind, answer = diag(p)
            if kind == "unknown" or (kind == "answer" and ours(answer, old)):
                still.append(p)
        running = still
    if running:
        raise cant


def users_of(old):
    """PIDs (other than this script and install.sh) with their working folder inside the old install."""
    if not shutil.which("lsof"):
        raise Stop("Open Loops can't check whether the old copy is still in use.", "Quit Open Loops and try again.")
    try:
        r = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn"], capture_output=True, text=True, timeout=60)
    except Exception as e:
        log(f"lsof: {e!r}")
        r = None
    if r is None or r.returncode != 0 or not r.stdout.strip():
        if r is not None:
            log(f"lsof -> {r.returncode}: {r.stderr[-500:]}")
        raise Stop("Open Loops can't check whether the old copy is still in use.", "Quit Open Loops and try again.")
    pids, pid = [], None
    for ln in r.stdout.splitlines():
        if ln.startswith("p"):
            pid = int(ln[1:])
        elif ln.startswith("n") and pid not in (None, os.getpid(), os.getppid()) and inside(ln[1:], old):
            pids.append(pid)
    return pids


def plan(old):
    """-> (files to copy as relative paths, state.json last; symlinks skipped). Never follows a link."""
    todo, skipped = [], []
    for name in FILES + DIRS + ["state.json"]:
        src = old / name
        if os.path.islink(src):
            skipped.append(name)
        elif name in DIRS and src.is_dir():
            for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
                for d in list(dirnames):
                    if os.path.islink(os.path.join(dirpath, d)):
                        skipped.append(os.path.relpath(os.path.join(dirpath, d), old))
                        dirnames.remove(d)
                for f in filenames:
                    full = os.path.join(dirpath, f)
                    (skipped if os.path.islink(full) else todo).append(os.path.relpath(full, old))
        elif src.is_file():
            todo.append(name)
    return todo, skipped


def copy_personal(old, dest):
    todo, skipped = plan(old)
    dest.mkdir(parents=True, exist_ok=True)
    for rel in todo:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old / rel, dest / rel, follow_symlinks=False)
    bad = [rel for rel in todo if not filecmp.cmp(old / rel, dest / rel, shallow=False)]
    if bad:
        log("copy differs: " + ", ".join(bad))
        if "state.json" in todo and (dest / "state.json").exists():
            (dest / "state.json").unlink()   # ours, just written: without it the next run copies again
        raise Stop("The copy of your list and settings didn't match the original, so the new Open Loops was not switched on.",
                   "Run the installer again; your old copy in Documents is untouched.")
    return len(todo), skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--dest", required=True)
    a = ap.parse_args()
    old, dest = Path(a.old), Path(a.dest)
    if not (old / "openloops" / "app.py").is_file():
        return 0
    if (dest / "state.json").exists():
        say("Your list is already in the new Open Loops, so nothing was copied again. The old copy in Documents is untouched.")
        return 0
    say("Copying your list and settings from the old Open Loops in Documents ...")
    os.chdir(Path.home())   # this script's own working folder must not count as a user of the old one
    unload_job(old)
    stop_servers(old)
    pids = users_of(old)
    if pids:
        raise Stop(f"Something is still working in the old Open Loops folder (process {', '.join(map(str, pids[:5]))}).",
                   "Quit Open Loops and close any Terminal window opened in that folder, then run the installer again.")
    n, skipped = copy_personal(old, dest)
    say("Your list and settings were copied to the new Open Loops. The old copy in Documents is untouched; "
        "Open Loops no longer uses it.")
    if skipped:
        say(f"Left out {len(skipped)} shortcut(s) that point somewhere else: {', '.join(skipped[:5])}. "
            "Copy what they point to by hand if you need it.")
    log(f"copied {n} file(s) from {old} to {dest}; skipped links: {skipped}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as e:
        print(f"  {e.what}", file=sys.stderr)
        print(f"  {e.todo}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        log(traceback.format_exc())
        print("  Open Loops couldn't copy your list and settings from the old copy.", file=sys.stderr)
        print(f"  Run the installer again; if it happens again, send {LOG} to whoever set Open Loops up.", file=sys.stderr)
        sys.exit(1)
