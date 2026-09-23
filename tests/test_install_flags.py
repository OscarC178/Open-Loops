"""install.sh: the default place outside ~/Documents, copying an older install's list across, and the test flags.

    python3 tests/test_install_flags.py    # macOS/Linux; no Slack/Gmail/Claude. Throwaway $HOME, spare port.

Every run uses a throwaway $HOME and stubs for `claude`, `launchctl` and `curl`, so the real install, the real
~/Library/LaunchAgents, the real Dock and the real app on 8765 are never touched (the stub curl answers for
pretend Open Loops servers listed in a file, and says nothing is running anywhere else). Checks:
  1. --dest DIR --no-app --no-task --no-launch --port N: installs only into DIR, writes the port into its
     config.json, writes no app bundle and no launchd job, and leaves an older ~/Documents install alone.
  2. OPENLOOPS_DEST does the same as --dest.
  3. The app started from that copy, with no --port and no OPENLOOPS_PORT, answers on the config's port.
  4. Default install over an older ~/Documents/OpenLoops - copy only (scripts/migrate_install.py):
     a. every personal file (state.json, config.json, voice.json, people_suggested.json, state/, .grok/,
        google_oauth_client.json, profiles/, private/) byte-equal in the new place; the old folder byte-for-byte
        unchanged; its job (found by parsing the plist) unloaded first, then registered for the new place at
        the saved time; the #25 sentence; no deletion advice, no staging folder, no marker file.
     b. rerun: nothing copied again, even if the old copy changed since; an update keeps every personal file.
     c. symbolic links in the old folder are skipped and listed, never followed.
     d. a job that is loaded and will not unload: refused, nothing copied.
     e. lsof missing, or lsof failing: refused, nothing copied.
     f. servers: only one that says "app": "openloops" with the old root is asked to quit (8767 here, not 8765);
        one without the identity is left alone; a port that times out means "could not confirm": refused.
     g. something still working inside the old folder: refused, then copied once it has stopped.
     h. ports outside 1024-65535 refused before anything is written.
  5. register-task.sh --dest DIR writes a job for DIR.
"""
import json, os, plistlib, shutil, socket, stat, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

if sys.platform == "win32":
    print("SKIP: install.sh is the Mac installer - setup.ps1 is Windows'")
    sys.exit(0)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from openloops import doctor
from _helpers import free_port

PORT = free_port()  # the test copy's config.json port: chosen per run so it never meets another server
PORT2 = free_port()  # a second valid port for the re-install check, chosen on its own (PORT + 100 could pass 65535)
LABEL = "com.openloops.refresh"
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def script(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


tmp = Path(tempfile.mkdtemp(prefix="openloops-install-"))
fakebin = tmp / "bin"
fakebin.mkdir()
launchctl_log = tmp / "launchctl.log"
script(fakebin / "claude", "#!/bin/bash\nexit 0\n")
# launchctl: log the call; `print` (is it loaded?) and `bootout` answer with LAUNCHCTL_PRINT_RC / LAUNCHCTL_BOOTOUT_RC
script(fakebin / "launchctl", f"""#!/bin/bash
echo "$*" >> "{launchctl_log}"
case "$1" in
    print) rc=${{LAUNCHCTL_PRINT_RC:-0}}
           [ "$rc" = 113 ] && echo 'Could not find service "com.openloops.refresh" in domain for user gui: 501' >&2
           exit $rc ;;
    bootout) exit ${{LAUNCHCTL_BOOTOUT_RC:-0}} ;;
esac
""")
# Stub curl: pretend servers are the lines "<port> <mode> <root>" in servers.txt, mode = openloops (answers with
# "app": "openloops"), noapp (answers without it) or timeout (curl exit 28). POST /api/quit removes the line (the
# server "quits"); every other port refuses the connection (exit 7).
servers, curl_log = tmp / "servers.txt", tmp / "curl.log"
servers.write_text("")
script(fakebin / "curl", f"""#!/bin/bash
echo "$*" >> "{curl_log}"
url="${{@: -1}}"
port=$(printf '%s' "$url" | sed -E 's#^https?://[^:/]+:([0-9]+)/.*#\\1#')
line=$(grep "^$port " "{servers}")
[ -z "$line" ] && exit 7
rest="${{line#* }}"; mode="${{rest%% *}}"; root="${{rest#* }}"
[ "$mode" = timeout ] && exit 28
case "$url" in
    */api/quit) grep -v "^$port " "{servers}" > "{servers}.tmp"; mv "{servers}.tmp" "{servers}"; echo '{{"ok": true}}' ;;
    */api/diag)
        if [ "$mode" = openloops ]; then printf '{{"app": "openloops", "root": "%s"}}' "$root"
        else printf '{{"root": "%s"}}' "$root"; fi ;;
esac
""")
PERSONAL = ["state.json", "config.json", "voice.json", "people_suggested.json", "state/logs/launchd.err.log",
            "state/logs/runner-2026-09-10.log", "state/google_oauth.json", ".grok/config.toml",
            "google_oauth_client.json", "private/notes.md", "profiles/work.json"]


def tree(root):
    """Every file and link under root -> bytes (or link target): to prove the old folder was not touched."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                out[rel] = "link->" + os.readlink(full)
            elif os.path.isfile(full):
                out[rel] = Path(full).read_bytes()
            else:
                out[rel] = "dir"
    return out


def job_for(home, old):
    """The weekday job as #24 left it: a real plist (plistlib) running the old folder's script."""
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": ["/bin/bash", f"{old}/scripts/run-refresh.sh"]}))
    return plist


def snapshot(root):
    return {f: (root / f).read_bytes() for f in PERSONAL}


def install(home, *args, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in ("OPENLOOPS_PORT", "OPENLOOPS_DEST")}
    env.update(HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}", BROWSER="/usr/bin/true")
    env.update(extra_env or {})
    return subprocess.run(["bash", str(REPO / "install.sh"), *args], env=env, capture_output=True, text=True,
                          timeout=180, stdin=subprocess.DEVNULL)


def old_install(home, owner):
    """A pre-#24 install in ~/Documents/OpenLoops, with personal files and the log the bug left behind."""
    old = home / "Documents" / "OpenLoops"
    shutil.copytree(REPO / "openloops", old / "openloops")
    shutil.copytree(REPO / "scripts", old / "scripts")
    (old / "config.json").write_text(json.dumps({"owner_name": owner, "refresh_time": "08:30"}), encoding="utf-8")
    (old / "state.json").write_text(json.dumps({"cursor": "2026-09-01T00:00", "last_refresh": None,
                                                "loops": [{"id": "keep-me"}]}), encoding="utf-8")
    (old / "state" / "logs").mkdir(parents=True)
    (old / "state" / "logs" / "launchd.err.log").write_text(
        f"/bin/bash: {old}/scripts/run-refresh.sh: Operation not permitted\n" * 6, encoding="utf-8")
    # every other personal file, with content an update must not replace
    (old / "voice.json").write_text('{"people": {"Alex": {"style": "brief, ✓"}}}', encoding="utf-8")
    (old / "people_suggested.json").write_text('[{"name": "Alex"}]', encoding="utf-8")
    (old / "state" / "logs" / "runner-2026-09-10.log").write_text("=== refresh 09:15:01\n", encoding="utf-8")
    (old / "state" / "google_oauth.json").write_text('{"refresh_token": "x"}', encoding="utf-8")
    (old / ".grok").mkdir()
    (old / ".grok" / "config.toml").write_text("# edited by the person\n[mcp]\n", encoding="utf-8")
    (old / "google_oauth_client.json").write_text('{"installed": {}}', encoding="utf-8")
    (old / "private").mkdir()
    (old / "private" / "notes.md").write_text("mine\n", encoding="utf-8")
    (old / "profiles").mkdir()
    (old / "profiles" / "work.json").write_text('{"p": 1}', encoding="utf-8")
    return old


srv = None
try:
    say("1. --dest --no-app --no-task --no-launch --port: a test copy that touches nothing else")
    home = tmp / "home1"
    (home / "Desktop").mkdir(parents=True)
    old = old_install(home, "Real")
    dest = tmp / "OpenLoops-test"
    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", str(PORT), "--name", "Test")
    check(r.returncode == 0, f"install.sh finished ({(r.stdout + r.stderr)[-300:].strip() if r.returncode else 'ok'})")
    check((dest / "openloops" / "app.py").exists() and (dest / "scripts" / "run-refresh.sh").exists(), "code copied to --dest")
    cfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    check(cfg.get("owner_name") == "Test" and cfg.get("port") == PORT, f"fresh config.json has the name and port {PORT}")
    check(cfg.get("test_copy") is True, "--dest with --no-app and --no-task records the copy as a test copy (#25 review)")
    check(not (home / "Applications" / "Open Loops.app").exists() and not (home / "Desktop" / "Open Loops.app").exists(),
          "--no-app: no Open Loops.app in ~/Applications or on the Desktop")
    check(not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists() and not launchctl_log.exists(),
          "--no-task: no launchd job written, launchctl never called")
    check((old / "openloops" / "app.py").exists() and json.loads((old / "config.json").read_text())["owner_name"] == "Real",
          "the older ~/Documents install was left exactly where it was")
    check(not (home / "Library" / "Application Support" / "OpenLoops").exists(), "nothing written to the default place")
    check("--no-launch" in r.stdout, "--no-launch: says it did not start")

    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", str(PORT2))
    cfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    check(r.returncode == 0 and cfg.get("port") == PORT2 and cfg.get("owner_name") == "Test",
          "re-run with a new --port: port updated, the rest of config.json kept")
    for bad_at in ("9:15", "24:00", "09:60", "noon"):
        before = (dest / "config.json").read_bytes()
        r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--at", bad_at)
        check(r.returncode != 0 and "--at must be HH:MM" in r.stderr and (dest / "config.json").read_bytes() == before,
              f"--at {bad_at} refused before config.json is touched")
    for bad in ("80a", "80", "1023", "65536", "99999"):
        before = (dest / "config.json").read_bytes()
        r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", bad)
        check(r.returncode != 0 and "--port must be a number from 1024 to 65535" in r.stderr
              and (dest / "config.json").read_bytes() == before, f"--port {bad} refused before anything is written")
    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", str(PORT))
    r = install(home, "--dest", str(dest), "--no-app", "--no-launch", "--port", str(PORT))   # --no-task dropped: now a real install
    check(r.returncode == 0 and "test_copy" not in json.loads((dest / "config.json").read_text(encoding="utf-8")),
          "re-run on that folder without --no-task: no longer marked as a test copy")
    for f in (home / "Library" / "LaunchAgents" / f"{LABEL}.plist",):
        f.unlink(missing_ok=True)   # that run registered the (fake) job; the steps below expect none
    launchctl_log.unlink(missing_ok=True)
    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", str(PORT))
    check(json.loads((dest / "config.json").read_text(encoding="utf-8")).get("test_copy") is True, "...and marked again with the flags")

    say("2. OPENLOOPS_DEST instead of --dest")
    dest2 = tmp / "via env"
    r = install(home, "--no-app", "--no-task", "--no-launch", "--name", "Env", extra_env={"OPENLOOPS_DEST": str(dest2)})
    check(r.returncode == 0 and (dest2 / "openloops" / "app.py").exists(), "installed into OPENLOOPS_DEST (a path with a space)")
    check(json.loads((dest2 / "config.json").read_text())["owner_name"] == "Env" and "port" not in json.loads((dest2 / "config.json").read_text()),
          "no --port: config.json has no port key (8765 as before)")

    say("3. the test copy answers on its config.json port")
    with socket.socket() as sk:
        check(sk.connect_ex(("127.0.0.1", PORT)) != 0, f"spare port {PORT} free")
    env = {k: v for k, v in os.environ.items() if k != "OPENLOOPS_PORT"}
    env.update(HOME=str(tmp / "home1"), USERPROFILE=str(tmp / "home1"))  # the throwaway HOME it was installed from
    srv = subprocess.Popen([sys.executable, "-m", "openloops.app", "--no-browser"], cwd=dest, env=dict(env, BROWSER="/usr/bin/true"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    d = None
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/diag", timeout=2) as resp:
                d = json.loads(resp.read())
            break
        except Exception:
            time.sleep(0.2)
    check(d is not None and d["port"] == PORT and Path(d["root"]).resolve() == dest.resolve(),
          f"app.py with no --port / OPENLOOPS_PORT listens on {PORT}, from the test copy")
    srv.kill()
    srv.wait()
    srv = None

    COPIED = "Your list and settings were copied to the new Open Loops. The old copy in Documents is untouched; Open Loops no longer uses it."

    say("4a. default install over an older ~/Documents/OpenLoops: personal files copied, old folder untouched")
    home = tmp / "home2"
    (home / "Desktop").mkdir(parents=True)
    old = old_install(home, "Mover")
    plist = job_for(home, old)
    before, old_tree = snapshot(old), tree(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    launchctl_log.unlink(missing_ok=True)
    r = install(home, "--no-app", "--no-launch")
    check(r.returncode == 0, f"install.sh finished ({(r.stdout + r.stderr)[-300:].strip() if r.returncode else 'ok'})")
    check(snapshot(new) == before, f"all {len(PERSONAL)} personal files byte-equal in the new place")
    check(tree(old) == old_tree, "the old folder is byte-for-byte as it was (nothing moved, renamed or deleted)")
    check(COPIED in r.stdout and "delete" not in r.stdout.lower(), "the #25 sentence, and no deletion advice")
    check(not new.with_name("OpenLoops.migrating").exists() and not list(new.rglob("*.part")),
          "no staging folder, no .part file left behind")
    check((new / ".migrated-from").read_text().strip() == os.path.realpath(old),
          "the .migrated-from sentinel names the resolved old folder (written last)")
    calls = [c.split()[0] for c in launchctl_log.read_text().splitlines()]
    check(calls[:2] == ["print", "bootout"] and calls[-1] == "bootstrap",
          "the old job (found by parsing its plist) was unloaded before copying, then registered for the new place")
    pl = plistlib.loads(plist.read_bytes())
    check(pl["ProgramArguments"] == ["/bin/bash", f"{new}/scripts/run-refresh.sh"], "... running the script at the new path")
    check({(d["Hour"], d["Minute"]) for d in pl["StartCalendarInterval"]} == {(8, 30)},
          "... at the person's own 08:30 from the copied config.json")
    s = doctor.schedule_step(new / "state" / "logs", new)
    check(s is None or s["ok"], "the old install's 'Operation not permitted' lines, copied along, are not a red row")

    say("4b. rerun: nothing copied again; an update keeps every personal file")
    (old / "config.json").write_text('{"owner_name": "Changed later"}', encoding="utf-8")
    old_tree = tree(old)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and "nothing was copied again" in r.stdout and COPIED not in r.stdout,
          "says the list is already there and copies nothing")
    check(snapshot(new) == before, "every personal file in the new place still byte-equal to the first copy (.grok not replaced)")
    check(tree(old) == old_tree, "the old folder still untouched")

    say("4c. symbolic links in the old folder are skipped and listed, never followed")
    home = tmp / "home3b"
    old = old_install(home, "Linker")
    os.symlink("/etc/hosts", old / "private" / "hosts-link")
    os.symlink("/etc", old / ".grok" / "etc-link")
    os.symlink(old / "config.json", old / "voice-link.json")   # not a named file: never looked at
    # the links a Grok job keeps in state/grok-home (agent.grok_job_env): named, with the easy fix, not "copy by hand" (#55)
    (old / "state" / "grok-home").mkdir(parents=True, exist_ok=True)
    for f in ("auth.json", "trusted_folders.toml", "trusted_folders.toml.lock"):
        os.symlink(home / ".grok" / f, old / "state" / "grok-home" / f)
    new = home / "Library" / "Application Support" / "OpenLoops"
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and "Left out 2 shortcut(s)" in r.stdout and "private/hosts-link" in r.stdout
          and ".grok/etc-link" in r.stdout and "grok-home" not in r.stdout, "both links reported")
    check("Left out 3 link(s) to your Grok sign-in (auth.json, trusted_folders.toml, trusted_folders.toml.lock)" in r.stdout
          and "signing in again inside the app" in r.stdout, "the grok-home links named, with the easy fix")
    check(not os.path.lexists(new / "private" / "hosts-link") and not os.path.lexists(new / ".grok" / "etc-link")
          and not (new / ".grok" / "etc-link" / "hosts").exists(), "neither copied nor followed")
    check((new / "private" / "notes.md").read_text() == "mine\n", "the real files next to them were copied")

    say("4d. the old job is loaded and will not unload: refused")
    home = tmp / "home6"
    old = old_install(home, "Stuck")
    job_for(home, old)
    old_tree = tree(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    r = install(home, "--no-app", "--no-launch", extra_env={"LAUNCHCTL_BOOTOUT_RC": "5"})
    check(r.returncode == 1 and "couldn't pause the old copy's morning refresh" in r.stderr and "Traceback" not in r.stderr,
          "exit 1, one plain sentence of what happened and one of what to do, no traceback")
    check(not (new / "state.json").exists() and not (new / "config.json").exists() and tree(old) == old_tree,
          "nothing copied, the old folder untouched")
    r = install(home, "--no-app", "--no-launch", "--no-task", extra_env={"LAUNCHCTL_PRINT_RC": "5"})
    check(r.returncode == 1 and "couldn't check the old copy's morning refresh" in r.stderr
          and not (new / "state.json").exists(), "launchctl print failing for another reason: refused, nothing copied")
    r = install(home, "--no-app", "--no-launch", "--no-task", extra_env={"LAUNCHCTL_PRINT_RC": "113"})
    check(r.returncode == 0 and (new / "state.json").exists(), "a job that is not loaded needs no unloading: copied")

    say("4e. lsof missing, or lsof failing: refused")
    py_dir = os.path.dirname(shutil.which("python3"))
    for label, extra in (("missing", {"PATH": f"{fakebin}:{py_dir}:/usr/bin:/bin"}),   # lsof lives in /usr/sbin
                         ("failing", {"PATH": f"{tmp / 'badlsof'}:{fakebin}:{os.environ['PATH']}"})):
        if label == "missing" and shutil.which("lsof", path=extra["PATH"]):
            # Linux keeps lsof in /usr/bin, which install.sh needs for everything else: no PATH hides only lsof there
            say(f"SKIP lsof missing: lsof is in {os.path.dirname(shutil.which('lsof', path=extra['PATH']))} on this system")
            continue
        home = tmp / f"home-lsof-{label}"
        old = old_install(home, "L")
        (tmp / "badlsof").mkdir(exist_ok=True)
        script(tmp / "badlsof" / "lsof", "#!/bin/bash\necho 'lsof: WARNING' >&2\nexit 1\n")
        check(label == "failing" or shutil.which("lsof", path=extra["PATH"]) is None, "sanity: lsof not on that PATH")
        r = install(home, "--no-app", "--no-launch", "--no-task", extra_env=extra)
        check(r.returncode == 1 and "can't check whether the old copy is still in use" in r.stderr
              and "Quit Open Loops and try again." in r.stderr, f"lsof {label}: refused in plain words")
        check(not (home / "Library" / "Application Support" / "OpenLoops" / "state.json").exists(), "... nothing copied")

    say("4e2. stopping after the old job was paused puts the job back as it was")
    home = tmp / "home-resume"
    old = old_install(home, "Resume")
    plist = job_for(home, old)
    launchctl_log.unlink(missing_ok=True)
    r = install(home, "--no-app", "--no-launch", extra_env={"PATH": f"{tmp / 'badlsof'}:{fakebin}:{os.environ['PATH']}"})
    calls = launchctl_log.read_text().splitlines()
    check(r.returncode == 1 and [c.split()[0] for c in calls] == ["print", "bootout", "bootstrap"]
          and calls[-1].endswith(str(plist)), "paused, then lsof failed: the old job's own plist bootstrapped again")
    check("while copying" in r.stdout and "set up again" not in r.stdout, "... and no promise that it is set up again")
    check("put back as it was" in r.stdout, "... but it does say the paused job was put back (#25)")

    say("4f. servers: quit only the old copy's (identity AND root); an older one without the identity stops the copy")
    home = tmp / "home4"
    old = old_install(home, "Busy")
    servers.write_text(f"8765 openloops /somewhere/else/OpenLoops\n8767 openloops {old}\n8768 noapp /another/OpenLoops\n")
    curl_log.unlink(missing_ok=True)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    log = curl_log.read_text()
    check(r.returncode == 0 and COPIED in r.stdout, "copied once the old server had quit")
    check("8767/api/quit" in log and "8765/api/quit" not in log and "8768/api/quit" not in log,
          "quit sent to 8767 only: not another copy (8765), not an older server running elsewhere (8768)")
    check(all(f"127.0.0.1:{p}/api/diag" in log for p in (8765, 8770, 8784)), "the whole 8765-8784 range was checked")

    home = tmp / "home4c"
    old = old_install(home, "Legacy")
    servers.write_text(f"8769 noapp {old}\n")   # an older version (no "app" field) running the old folder
    curl_log.unlink(missing_ok=True)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and "An older copy of Open Loops is still running on port 8769." in r.stderr
          and "Quit it (close its tab and wait a few seconds), then run the installer again." in r.stderr
          and "8769/api/quit" not in curl_log.read_text(),
          "an older server without the identity: nothing sent to it, stopped with the plain two-sentence message")
    check(not (home / "Library" / "Application Support" / "OpenLoops" / "state.json").exists(), "... nothing copied")

    home = tmp / "home4b"
    old = old_install(home, "Slow")
    servers.write_text("8770 timeout -\n")
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and "couldn't confirm that the old copy has stopped" in r.stderr, "a port that times out: refused")
    check(not (home / "Library" / "Application Support" / "OpenLoops" / "state.json").exists(), "... nothing copied")
    servers.write_text("")

    say("4h. --no-task: the paused old job is put back afterwards, and it says so")
    home = tmp / "home-notask"
    old = old_install(home, "NoTask")
    plist = job_for(home, old)
    launchctl_log.unlink(missing_ok=True)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    calls = launchctl_log.read_text().splitlines()
    check(r.returncode == 0 and [c.split()[0] for c in calls] == ["print", "bootout", "bootstrap"]
          and calls[-1].endswith(str(plist)) and "put back as it was" in r.stdout,
          "paused for the copy, then its own plist bootstrapped again; nothing registered for the new place")

    say("4i. the new place must not lead back into the old one")
    MIG = REPO / "scripts" / "migrate_install.py"

    def migrate(home, old, dest, *extra):
        env = dict(os.environ, HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}")
        return subprocess.run([sys.executable, str(MIG), "--old", str(old), "--dest", str(dest), *extra],
                              env=env, capture_output=True, text=True, timeout=180)

    ALIAS = "points back into the old one"
    home = tmp / "home-alias1"
    old = old_install(home, "Alias")
    old_tree = tree(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    new.mkdir(parents=True)
    os.symlink(old / "voice.json", new / "config.json")   # copying config would overwrite the OLD voice.json
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and ALIAS in r.stderr and "Traceback" not in r.stderr, "a symlinked file in the new place: refused")
    check(tree(old) == old_tree, "... and the old folder is byte-identical afterwards")

    home = tmp / "home-alias2"
    old = old_install(home, "Alias2")
    old_tree = tree(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    new.mkdir(parents=True)
    os.link(old / "voice.json", new / "voice.json")        # a hard link: same file, different name
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and ALIAS in r.stderr and tree(old) == old_tree, "a hard-linked file in the new place: refused, old untouched")

    home = tmp / "home-alias3"
    old = old_install(home, "Alias3")
    old_tree = tree(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    new.mkdir(parents=True)
    os.symlink(old / "private", new / "private")           # a symlinked folder on the way to a file
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and ALIAS in r.stderr and tree(old) == old_tree, "a symlinked folder in the new place: refused, old untouched")

    home = tmp / "home-alias4"
    old = old_install(home, "Alias4")
    old_tree = tree(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    new.parent.mkdir(parents=True)
    os.symlink(old, new)                                   # the whole new place is the old folder
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and ALIAS in r.stderr and tree(old) == old_tree, "the new place itself a link to the old folder: refused, old untouched")

    home = tmp / "home-alias5"
    old = old_install(home, "Alias5")
    old_tree = tree(old)
    r = migrate(home, old, old / "nested")
    check(r.returncode == 1 and ALIAS in r.stderr and tree(old) == old_tree, "a new place inside the old folder: refused, old untouched")

    home = tmp / "home-alias6"
    old = old_install(home, "Alias6")
    (home / "Library" / "Logs").mkdir(parents=True)
    os.symlink(old, home / "Library" / "Logs" / "OpenLoops")   # the log would be written into the old folder
    old_tree = tree(old)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and "where its install log should be" in r.stderr and tree(old) == old_tree,
          "a symlinked log folder: refused, old untouched")

    home = tmp / "home-alias7"
    old = old_install(home, "Alias7")
    (home / "Library" / "Logs" / "OpenLoops").mkdir(parents=True)
    os.link(old / "voice.json", home / "Library" / "Logs" / "OpenLoops" / "install.log")   # the log IS an old file
    old_tree = tree(old)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and "where its install log should be" in r.stderr and tree(old) == old_tree,
          "a hard-linked install.log: refused before anything is written, old untouched")

    home = tmp / "home-alias8"
    old = old_install(home, "Alias8")
    (home / "Library").mkdir(parents=True)
    os.symlink(old, home / "Library" / "Logs")               # a HIGHER ancestor of the log is a link into the old folder
    old_tree = tree(old)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 1 and "where its install log should be" in r.stderr and tree(old) == old_tree
          and not list(old.rglob("install.log")), "a symlinked ancestor of the log folder: refused, no log written into the old folder")

    home = tmp / "home-alias9"
    old = old_install(home, "Alias9")
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, home / "Linked")   # DEST three levels under a symlinked ancestor (pointing outside the old folder)
    old_tree = tree(old)
    r = migrate(home, old, home / "Linked" / "a" / "b" / "OpenLoops")
    check(r.returncode == 1 and ALIAS in r.stderr and tree(old) == old_tree and not any(elsewhere.iterdir()),
          "a new place three levels under a symlinked folder: refused, nothing written anywhere, old untouched")

    say("4j. an interrupted copy is finished by the next run; the sentinel makes later runs a no-op")
    home = tmp / "home-resume2"
    old = old_install(home, "Interrupted")
    before = snapshot(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    (new / "state").mkdir(parents=True)
    (new / "config.json").write_bytes((old / "config.json").read_bytes())       # copied before the interruption
    (new / "voice.json").write_text('{"half', encoding="utf-8")                  # cut off mid-copy
    (new / "state.json.part").write_text('{"cursor": "20', encoding="utf-8")     # state.json was being written
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and COPIED in r.stdout, "rerun after the interruption: finishes")
    check(snapshot(new) == before and not list(new.rglob("*.part")) and (new / ".migrated-from").exists(),
          "every personal file byte-equal, the leftover .part gone, the sentinel written")
    (old / "voice.json").write_text('{"changed": "later"}', encoding="utf-8")
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and "nothing was copied again" in r.stdout and snapshot(new) == before,
          "with the sentinel naming this old folder: a no-op, even though the old copy changed")

    home = tmp / "home-ownlist"
    old = old_install(home, "Own")
    new = home / "Library" / "Application Support" / "OpenLoops"
    new.mkdir(parents=True)
    (new / "state.json").write_text('{"loops": [{"id": "made-here"}]}', encoding="utf-8")
    r = install(home, "--no-app", "--no-launch", "--no-task", "--name", "Own")   # its own settings are asked for as usual
    check(r.returncode == 0 and "already has a list of its own" in r.stdout
          and "made-here" in (new / "state.json").read_text(), "a new place with its own, different list: never overwritten")

    say("4k. a folder in the old copy that cannot be read: stopped in plain words")
    home = tmp / "home-walk"
    old = old_install(home, "Locked")
    locked = old / "private" / "locked"
    locked.mkdir()
    (locked / "x.txt").write_text("x")
    locked.chmod(0)
    try:
        r = install(home, "--no-app", "--no-launch", "--no-task")
    finally:
        locked.chmod(0o755)
    check(r.returncode == 1 and "couldn't read part of the old copy's folder" in r.stderr and "Traceback" not in r.stderr,
          "an unreadable folder: refused (os.walk onerror), no traceback on screen")
    check(not (home / "Library" / "Application Support" / "OpenLoops" / ".migrated-from").exists(), "... not marked as done")

    say("4g. something still working inside the old folder: refused, copied once it stops")
    home = tmp / "home7"
    old = old_install(home, "Held")
    before = snapshot(old)
    holder = subprocess.Popen(["/bin/sleep", "60"], cwd=old)
    try:
        r = install(home, "--no-app", "--no-launch", "--no-task")
    finally:
        holder.kill()
        holder.wait()
    ilog = home / "Library" / "Logs" / "OpenLoops" / "install.log"
    check(r.returncode == 1 and "still working in the old Open Loops folder" in r.stderr and str(holder.pid) not in r.stderr
          and "process" not in r.stderr and str(holder.pid) in ilog.read_text(encoding="utf-8"),
          "refused in plain words; the process number is in the install log, not on screen (#25)")
    check(not (home / "Library" / "Application Support" / "OpenLoops" / "state.json").exists(), "... nothing copied")
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and snapshot(home / "Library" / "Application Support" / "OpenLoops") == before and old.exists(),
          "run again once it has stopped: copied, old folder still there")

    say("5. register-task.sh --dest")
    home = tmp / "home3"
    target = tmp / "some install"
    (target / "state").mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}")
    r = subprocess.run(["bash", str(REPO / "scripts" / "register-task.sh"), "--at", "07:05", "--dest", str(target)],
                       env=env, capture_output=True, text=True, timeout=30)
    pl = plistlib.loads((home / "Library" / "LaunchAgents" / f"{LABEL}.plist").read_bytes())
    check(r.returncode == 0 and pl["ProgramArguments"][1] == f"{target}/scripts/run-refresh.sh",
          "the job runs --dest's run-refresh.sh, not the copy the script sits in")
    check(pl["StartCalendarInterval"][0]["Hour"] == 7 and pl["StartCalendarInterval"][0]["Minute"] == 5, "... at --at")
    say("PASS - installs outside ~/Documents, copies an old install's list across without touching it, and the test flags keep the real install untouched")
finally:
    if srv and srv.poll() is None:
        srv.kill()
        srv.wait()
    shutil.rmtree(tmp, ignore_errors=True)
