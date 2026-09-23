"""Bring the list and settings of an older Mac install (in ~/Documents) into the new one (#24). Copy only.

    python3 scripts/migrate_install.py --old ~/Documents/OpenLoops --dest ~/Library/Application\\ Support/OpenLoops [--no-task]

Why: macOS will not let the launchd weekday refresh read ~/Documents, so the install now lives elsewhere.
Called by install.sh before it copies the program files. It NEVER moves, renames, deletes or writes anything
under the old folder, and never suggests deleting it: the old copy stays exactly as it was, as a fallback.

  1. Nothing to do without an old install, or when DEST/.migrated-from already names this old folder (written
     only after every file was copied and checked). Otherwise any earlier, interrupted run is resumed: files
     already in place and identical are left alone, missing or different ones are copied again. A new install
     whose state.json differs from the old one (used on its own, or installed fresh) is never overwritten:
     nothing is copied and it says so.
  2. Nothing it writes may lead back into the old folder: DEST inside (or equal to) the old folder, a symbolic
     link or a hard-linked file anywhere on a path it is about to write, or a path resolving outside DEST all
     stop it. The install log is checked before anything else (no link anywhere up to $HOME, no hard link, not
     inside the old folder); until then messages are kept in memory, and it is opened with O_NOFOLLOW.
  3. The old copy must be idle first, or the copy could catch a half-written file:
     - the weekday job, if its plist (read with plistlib) runs the old folder's script, is paused; "Could not
       find service" means not loaded, any other launchctl failure stops it. With --no-task (install.sh will not
       register the job for the new place) the old job is put back afterwards, and it says so.
     - a server on 8765-8784 whose /api/diag root is the old folder is asked to quit only if it also says
       "app": "openloops"; an older version without that field stops the copy (quit it first), and so does a
       port that does not answer clearly.
     - lsof must show no process with any file (or its working folder) inside the old folder; missing or
       failing lsof stops it.
  4. Copy only the named personal files and folders. Each file goes to "<name>.part" (shutil.copy2), is compared
     byte for byte with the original, then os.replace()d into place; state.json last; .migrated-from after all.
     Symbolic links in the old folder are skipped and reported, never followed; an unreadable folder stops it.

Exit 0 = copied or nothing to do, 1 = stopped (one sentence what happened, one what to do; details in the log).
Stdlib only.
"""
import argparse, filecmp, json, os, plistlib, shutil, stat, subprocess, sys, time, traceback
from pathlib import Path

PORTS = range(8765, 8785)   # app.py's pick_port() range: an old copy may be on any of them
LABEL = "com.openloops.refresh"
FILES = ["config.json", "voice.json", "people_suggested.json", "google_oauth_client.json"]   # state.json last
DIRS = ["state", ".grok", "profiles", "private"]
SENTINEL = ".migrated-from"   # in DEST: the resolved old folder, written only once everything is copied and checked
LOG = Path.home() / "Library" / "Logs" / "OpenLoops" / "install.log"   # tracebacks go here, never on screen
PAUSED = []   # the plist whose job unload_job() paused: put back as it was if we stop (the old copy stays in use)
TRY_AGAIN = "Run the installer again; your old copy in Documents is untouched."


class Stop(Exception):
    """A plain-words reason to stop: what happened, and what to do."""
    def __init__(self, what, todo):
        super().__init__(what)
        self.what, self.todo = what, todo


def say(msg):
    print(f"  {msg}", flush=True)


LOG_OK = False   # set by check_log(); until then nothing is written to LOG
LOG_BUFFER = []  # messages from before the log was checked: written once it is, or shown on stderr if it never is


def log(text):
    """Append to LOG - only once check_log() has passed, and never through a link (O_NOFOLLOW where the OS has it)."""
    entry = f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} migrate_install\n{text}\n"
    if not LOG_OK:
        LOG_BUFFER.append(entry)
        return
    try:
        fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass


def ancestors(path, stop_at):
    """path, then each folder above it, up to (not including) stop_at - or up to the root if stop_at is not above it."""
    out, p = [], Path(path)
    while True:
        out.append(p)
        if p.parent == p or p.parent == stop_at:
            return out
        p = p.parent


def check_log(old):
    """Before anything else: the log must not be able to write into the old folder (or anywhere unexpected).
    LOG and every folder above it up to $HOME: no symbolic links. LOG itself, if there: a regular file with one
    link. Neither LOG nor its folder may resolve into the old folder."""
    global LOG_OK
    unsafe = Stop("Open Loops found a shortcut where its install log should be, so it stopped to be safe.",
                  f"Remove {LOG.parent}, then run the installer again.")
    home = Path.home()
    for p in ancestors(LOG, home):
        if os.path.islink(p):
            raise unsafe
    if os.path.lexists(LOG):
        st = os.lstat(LOG)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise unsafe
    if inside(LOG, old) or inside(LOG.parent, old):
        raise unsafe
    LOG.parent.mkdir(parents=True, exist_ok=True)
    for p in ancestors(LOG.parent, home):   # again: a folder just created must not have been swapped for a link
        if os.path.islink(p):
            raise unsafe
    LOG_OK = True
    for entry in LOG_BUFFER:
        log(entry.split("\n", 1)[1].rstrip("\n"))
    LOG_BUFFER.clear()


def inside(path, folder):
    """True if path is the folder or anything under it (both resolved, so symlinked spellings agree)."""
    p, f = os.path.realpath(path), os.path.realpath(folder)
    return p == f or p.startswith(f + os.sep)


# ---------- 1. the old copy must be idle ----------

def unload_job(old):
    """The weekday job is a writer too. Pause it if its plist runs the old folder's script."""
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
    r = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
    if r.returncode != 0:
        if "could not find service" in (r.stdout + r.stderr).lower():
            return  # not loaded: nothing to pause
        log(f"launchctl print {target} -> {r.returncode}: {r.stdout}{r.stderr}")
        raise Stop("Open Loops couldn't check the old copy's morning refresh.", "Restart your Mac, then run the installer again.")
    r = subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"launchctl bootout {target} -> {r.returncode}: {r.stdout}{r.stderr}")
        raise Stop("Open Loops couldn't pause the old copy's morning refresh.",
                   "Restart your Mac, then run the installer again.")
    PAUSED.append(plist)
    say("Paused the old copy's morning refresh while copying.")


def resume():
    """Put a paused job back exactly as it was (its plist was never changed)."""
    while PAUSED:
        plist = PAUSED.pop()
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
        if r.returncode != 0:
            log(f"launchctl bootstrap {plist} -> {r.returncode}: {r.stdout}{r.stderr}")
            return False
    return True


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
        answer = json.loads(r.stdout)
    except ValueError:
        answer = {}
    return "answer", answer if isinstance(answer, dict) else {}


def old_server(answer, old, port):
    """True = the old copy's server (says "app": "openloops" and its root is the old folder): ask it to quit.
    False = not the old copy. An older version of Open Loops (no "app" field) with the old root stops the copy:
    nothing is sent to a server that cannot say what it is."""
    root = answer.get("root")
    if not isinstance(root, str) or os.path.realpath(root) != os.path.realpath(old):
        return False
    if answer.get("app") == "openloops":
        return True
    raise Stop(f"An older copy of Open Loops is still running on port {port}.",
               "Quit it (close its tab and wait a few seconds), then run the installer again.")


def stop_servers(old):
    cant = Stop("Open Loops couldn't confirm that the old copy has stopped.",
                "Quit Open Loops (close its browser tab and wait a minute), then run the installer again.")
    running = []
    for p in PORTS:
        kind, answer = diag(p)
        if kind == "unknown":
            raise cant
        if kind == "answer" and old_server(answer, old, p):
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
            if kind == "unknown" or (kind == "answer" and old_server(answer, old, p)):
                still.append(p)
        running = still
    if running:
        raise cant


def users_of(old):
    """PIDs (other than this script and install.sh) with ANY open file, or their working folder, inside the old
    install - so a writer using absolute paths counts too."""
    cant = Stop("Open Loops can't check whether the old copy is still in use.", "Quit Open Loops and try again.")
    if not shutil.which("lsof"):
        raise cant
    try:
        r = subprocess.run(["lsof", "-Fpn"], capture_output=True, text=True, timeout=120)
    except Exception as e:
        log(f"lsof: {e!r}")
        raise cant
    if r.returncode != 0 or not r.stdout.strip():
        log(f"lsof -> {r.returncode}: {r.stderr[-500:]}")
        raise cant
    pids, pid, old_s = set(), None, os.path.realpath(old)
    for ln in r.stdout.splitlines():
        if ln.startswith("p"):
            pid = int(ln[1:])
        elif ln.startswith("n") and pid not in (None, os.getpid(), os.getppid()):
            n = ln[1:]
            if any(n == o or n.startswith(o + "/") for o in (old_s, str(old))):
                pids.add(pid)
    return sorted(pids)


# ---------- 2. what to copy, and where it may be written ----------

def plan(old):
    """-> (files to copy as relative paths, state.json last; symlinks skipped). Never follows a link."""
    def unreadable(err):
        log(f"walk: {err!r}")
        raise Stop("Open Loops couldn't read part of the old copy's folder, so it copied nothing more.",
                   "Check that the old Open Loops folder in Documents opens in Finder, then run the installer again.")
    todo, skipped = [], []
    for name in FILES + DIRS + ["state.json"]:
        src = old / name
        if os.path.islink(src):
            skipped.append(name)
        elif name in DIRS and src.is_dir():
            for dirpath, dirnames, filenames in os.walk(src, followlinks=False, onerror=unreadable):
                for d in list(dirnames):
                    if os.path.islink(os.path.join(dirpath, d)):
                        skipped.append(os.path.relpath(os.path.join(dirpath, d), old))
                        dirnames.remove(d)
                for f in sorted(filenames):
                    full = os.path.join(dirpath, f)
                    (skipped if os.path.islink(full) else todo).append(os.path.relpath(full, old))
        elif src.is_file():
            todo.append(name)
    return todo, skipped


def check_dest(old, dest, rels):
    """Stop before writing anything if a write under DEST could land in the old folder or anywhere else outside DEST."""
    alias = Stop("The new Open Loops folder points back into the old one, so copying could change your old copy.",
                 "Remove the new Open Loops folder in Library/Application Support, then run the installer again.")
    if inside(dest, old) or inside(old, dest):
        raise alias
    real_dest = os.path.realpath(dest)
    # DEST and every folder above it, up to $HOME, must not be links
    for p in ancestors(dest, Path.home()):
        if os.path.islink(p):
            raise alias
    for rel in rels + [SENTINEL]:
        target = dest / rel
        chain = [target, Path(str(target) + ".part")] + [dest / q for q in Path(rel).parents if str(q) != "."]
        for p in chain:
            if not os.path.lexists(p):
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode) or (stat.S_ISREG(st.st_mode) and st.st_nlink > 1) or not inside(p, real_dest):
                log(f"alias: {p}")
                raise alias


def same(a, b):
    try:
        return os.path.isfile(b) and not os.path.islink(b) and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def put(src, target):
    """Copy to <target>.part, check it byte for byte, then replace the target in one step."""
    part = Path(str(target) + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(part):
        os.unlink(part)   # a leftover from an interrupted run; check_dest() already refused links and hard links
    shutil.copy2(src, part, follow_symlinks=False)
    if not filecmp.cmp(src, part, shallow=False):
        os.unlink(part)
        raise Stop("The copy of your list and settings didn't match the original, so the new Open Loops was not switched on.",
                   TRY_AGAIN)
    os.replace(part, target)


def copy_personal(old, dest, todo):
    copied = 0
    for rel in todo:   # state.json is last in todo
        if same(old / rel, dest / rel):
            continue       # already copied by an interrupted earlier run
        put(old / rel, dest / rel)
        copied += 1
    bad = [rel for rel in todo if not same(old / rel, dest / rel)]
    if bad:
        log("copy differs: " + ", ".join(bad))
        raise Stop("The copy of your list and settings didn't match the original, so the new Open Loops was not switched on.",
                   TRY_AGAIN)
    tmp = Path(str(dest / SENTINEL) + ".part")
    tmp.write_text(os.path.realpath(old) + "\n", encoding="utf-8")
    os.replace(tmp, dest / SENTINEL)   # only now does a rerun skip
    return copied


def done_before(old, dest):
    try:
        return (dest / SENTINEL).read_text(encoding="utf-8").strip() == os.path.realpath(old)
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--no-task", action="store_true", help="install.sh will not register the job: put the old one back")
    a = ap.parse_args()
    old, dest = Path(a.old), Path(a.dest)
    if not (old / "openloops" / "app.py").is_file():
        return 0
    check_log(old)   # first: nothing may be logged (or done) before the log is known to be safe
    if not os.path.islink(dest) and done_before(old, dest):
        say("Your list is already in the new Open Loops, so nothing was copied again. The old copy in Documents is untouched.")
        return 0
    todo, skipped = plan(old)
    check_dest(old, dest, todo)
    if (dest / "state.json").exists() and not same(old / "state.json", dest / "state.json"):
        # the new copy has been used on its own (or was installed fresh): its list is never overwritten
        say("The new Open Loops already has a list of its own, so nothing was copied from the old one. "
            "The old copy in Documents is untouched.")
        return 0
    say("Copying your list and settings from the old Open Loops in Documents ...")
    os.chdir(Path.home())   # this script's own working folder must not count as a user of the old one
    unload_job(old)
    stop_servers(old)
    pids = users_of(old)
    if pids:
        log(f"still in use by process {', '.join(map(str, pids[:20]))}")   # the numbers are for the log, not the screen (#25)
        raise Stop("Something is still working in the old Open Loops folder.",
                   "Quit Open Loops and close any Terminal window opened in that folder, then run the installer again.")
    dest.mkdir(parents=True, exist_ok=True)
    check_dest(old, dest, todo)   # again, now that DEST exists: nothing may have appeared in between
    copied = copy_personal(old, dest, todo)
    say("Your list and settings were copied to the new Open Loops. The old copy in Documents is untouched; "
        "Open Loops no longer uses it.")
    if skipped:
        say(f"Left out {len(skipped)} shortcut(s) that point somewhere else: {', '.join(skipped[:5])}. "
            "Copy what they point to by hand if you need it.")
    if a.no_task and PAUSED:
        if resume():
            say("The old copy's morning refresh was put back as it was, because this install does not set one up.")
        else:
            say("The old copy's morning refresh could not be put back; it will be again the next time you log in.")
    log(f"copied {copied} of {len(todo)} file(s) from {old} to {dest}; skipped links: {skipped}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as e:
        paused = bool(PAUSED)
        back = resume()
        if not LOG_OK and LOG_BUFFER:   # the log could not be used: the details go to the screen instead
            print("".join(LOG_BUFFER), file=sys.stderr, end="")
        print(f"  {e.what}", file=sys.stderr)
        print(f"  {e.todo}", file=sys.stderr)
        if paused:  # "Paused the old copy's morning refresh" was said: say what became of it (#25, from the #31 test)
            print("  The old copy's morning refresh was put back as it was." if back else
                  "  The old copy's morning refresh could not be put back; it will be again the next time you log in.")
        sys.exit(1)
    except Exception:
        resume()
        log(traceback.format_exc())
        if not LOG_OK:
            print("".join(LOG_BUFFER), file=sys.stderr, end="")
        print("  Open Loops couldn't copy your list and settings from the old copy.", file=sys.stderr)
        print(f"  Run the installer again; if it happens again, send {LOG} to whoever set Open Loops up.", file=sys.stderr)
        sys.exit(1)
