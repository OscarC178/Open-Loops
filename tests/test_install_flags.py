"""install.sh: the default place outside ~/Documents, moving an older install there, and the test-install flags.

    python3 tests/test_install_flags.py    # macOS/Linux; no Slack/Gmail/Claude. Throwaway $HOME, spare port.

Every run uses a throwaway $HOME and stubs for `claude`, `launchctl` and `curl`, so the real install, the real
~/Library/LaunchAgents, the real Dock and the real app on 8765 are never touched (the stub curl answers for
pretend Open Loops servers listed in a file, and says nothing is running anywhere else). Checks:
  1. --dest DIR --no-app --no-task --no-launch --port N: installs only into DIR, writes the port into its
     config.json, writes no app bundle and no launchd job, and leaves an older ~/Documents install alone.
  2. OPENLOOPS_DEST does the same as --dest.
  3. The app started from that copy, with no --port and no OPENLOOPS_PORT, answers on the config's port.
  4. Default install over an older ~/Documents/OpenLoops, fail-closed (scripts/migrate_install.py):
     a. same disk: renamed; every personal file (state.json, config.json, voice.json, people_suggested.json,
        state/, .grok/, google_oauth_client.json, private/) byte-identical; job unloaded BEFORE the move and
        registered again for the new path at the saved time; old red lines not shown; an update keeps them all.
     b. the old copy's server on 8767 (not 8765) is found and asked to quit; another copy's server is left alone.
     c. copy path (other disk / iCloud): staged, verified, switched; the old folder kept and only then called
        deletable; a leftover "<dest>.migrating" from an interrupted run is discarded and the copy redone.
     d. a new place with no list while the old one has one: refused, nothing changed.
     e. something still working inside the old folder: refused, nothing changed.
     f. ports outside 1024-65535 refused before anything is written.
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

PORT = 8792
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
script(fakebin / "launchctl", f'#!/bin/bash\necho "$*" >> "{launchctl_log}"\n')
# Stub curl: pretend Open Loops servers are the lines "<port> <root>" in servers.txt. GET /api/diag answers with
# that root; POST /api/quit removes the line (the server "quits"); every other port refuses the connection.
servers, curl_log = tmp / "servers.txt", tmp / "curl.log"
servers.write_text("")
script(fakebin / "curl", f"""#!/bin/bash
echo "$*" >> "{curl_log}"
url="${{@: -1}}"
port=$(printf '%s' "$url" | sed -E 's#^https?://[^:/]+:([0-9]+)/.*#\\1#')
line=$(grep "^$port " "{servers}")
[ -z "$line" ] && exit 7
case "$url" in
    */api/quit) grep -v "^$port " "{servers}" > "{servers}.tmp"; mv "{servers}.tmp" "{servers}"; echo '{{"ok": true}}' ;;
    */api/diag) printf '{{"root": "%s"}}' "${{line#* }}" ;;
esac
""")
PERSONAL = ["state.json", "config.json", "voice.json", "people_suggested.json", "state/logs/launchd.err.log",
            "state/logs/runner-2026-09-10.log", "state/google_oauth.json", ".grok/config.toml",
            "google_oauth_client.json", "private/notes.md"]


def snapshot(root):
    return {f: (root / f).read_bytes() for f in PERSONAL}


def install(home, *args, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in ("OPENLOOPS_PORT", "OPENLOOPS_DEST")}
    env.update(HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}", BROWSER="/usr/bin/true", **(extra_env or {}))
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
    check(not (home / "Applications" / "Open Loops.app").exists() and not (home / "Desktop" / "Open Loops.app").exists(),
          "--no-app: no Open Loops.app in ~/Applications or on the Desktop")
    check(not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists() and not launchctl_log.exists(),
          "--no-task: no launchd job written, launchctl never called")
    check((old / "openloops" / "app.py").exists() and json.loads((old / "config.json").read_text())["owner_name"] == "Real",
          "the older ~/Documents install was left exactly where it was")
    check(not (home / "Library" / "Application Support" / "OpenLoops").exists(), "nothing written to the default place")
    check("--no-launch" in r.stdout, "--no-launch: says it did not start")

    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", str(PORT + 100))
    cfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    check(r.returncode == 0 and cfg.get("port") == PORT + 100 and cfg.get("owner_name") == "Test",
          "re-run with a new --port: port updated, the rest of config.json kept")
    for bad in ("80a", "80", "1023", "65536", "99999"):
        before = (dest / "config.json").read_bytes()
        r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", bad)
        check(r.returncode != 0 and "--port must be a number from 1024 to 65535" in r.stderr
              and (dest / "config.json").read_bytes() == before, f"--port {bad} refused before anything is written")
    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", str(PORT))

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

    say("4a. default install over an older ~/Documents/OpenLoops, same disk: renamed, every personal file kept")
    home = tmp / "home2"
    (home / "Desktop").mkdir(parents=True)
    old = old_install(home, "Mover")
    before = snapshot(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text(f"<plist><string>{old}/scripts/run-refresh.sh</string></plist>")   # the job as #24 left it
    launchctl_log.unlink(missing_ok=True)
    r = install(home, "--no-app", "--no-launch")
    check(r.returncode == 0, f"install.sh finished ({(r.stdout + r.stderr)[-300:].strip() if r.returncode else 'ok'})")
    check(not old.exists() and (new / "openloops" / "app.py").exists(), "renamed: the app is in Application Support, one copy only")
    check(snapshot(new) == before, f"all {len(PERSONAL)} personal files byte-identical (incl. .grok, OAuth, private/)")
    calls = launchctl_log.read_text().splitlines()
    check([c.split()[0] for c in calls] == ["bootout", "bootout", "bootstrap"],
          "the old job was unloaded FIRST (before the move), then registered again for the new place")
    pl = plistlib.loads(plist.read_bytes())
    check(pl["ProgramArguments"] == ["/bin/bash", f"{new}/scripts/run-refresh.sh"], "... running the script at the new path")
    check(pl["StandardErrorPath"] == f"{new}/state/logs/launchd.err.log", "... logging into the new folder")
    check({(d["Hour"], d["Minute"]) for d in pl["StartCalendarInterval"]} == {(8, 30)},
          "... at the person's own 08:30 from config.json, not the installer's default 09:15")
    check("Documents" not in plist.read_text(), "... with no ~/Documents path left in it")
    s = doctor.schedule_step(new / "state" / "logs", new)
    check(s is None or s["ok"], "the six old 'Operation not permitted' lines (old path) are not a red row after the move")
    check("delete" not in r.stdout, "a rename leaves nothing to delete, and says nothing about deleting")

    r = install(home, "--no-app", "--no-launch", "--at", "07:45")   # an update from the repo over the moved copy
    pl = plistlib.loads(plist.read_bytes())
    check(r.returncode == 0 and "older copy" not in r.stdout, "second run: nothing left to move, no note")
    check({(d["Hour"], d["Minute"]) for d in pl["StartCalendarInterval"]} == {(7, 45)}
          and json.loads((new / "config.json").read_text())["refresh_time"] == "07:45",
          "an explicit --at wins, in the job and in config.json")
    after = snapshot(new)
    after.pop("config.json")   # --at rightly changed refresh_time in it
    check(after == {k: v for k, v in before.items() if k != "config.json"},
          "the update kept every other personal file byte-identical (.grok not replaced by the shipped one)")
    old_install(home, "Stray")   # both exist now: the new one wins, the old one is only mentioned
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and "older copy is still in" in r.stdout and "delete" not in r.stdout and old.exists()
          and json.loads((new / "config.json").read_text())["owner_name"] == "Mover",
          "both exist: nothing moved or overwritten, the old one pointed out, no 'delete' (it was never verified)")

    say("4b. the old copy's server on another port of the range is found and stopped; other copies are left alone")
    home = tmp / "home4"
    old = old_install(home, "Busy")
    servers.write_text(f"8765 /somewhere/else/OpenLoops\n8767 {old}\n")
    curl_log.unlink(missing_ok=True)
    r = install(home, "--no-app", "--no-launch", "--no-task")
    log = curl_log.read_text()
    check(r.returncode == 0 and not old.exists(), "moved once the old server had quit")
    check("8767/api/quit" in log and "8765/api/quit" not in log, "quit sent to 8767 (the old copy), not to 8765 (another copy)")
    check(all(f"127.0.0.1:{p}/api/diag" in log for p in (8765, 8770, 8784)), "the whole 8765-8784 range was checked")
    servers.write_text("")

    say("4c. copy path (another disk, or files still in iCloud): staged, checked byte for byte, then switched")
    home = tmp / "home5"
    old = old_install(home, "Copier")
    before = snapshot(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    stage = new.with_name("OpenLoops.migrating")
    stage.mkdir(parents=True)                       # an interrupted earlier copy: half a tree, no list
    (stage / "config.json").write_text("{half", encoding="utf-8")
    r = install(home, "--no-app", "--no-launch", "--no-task", extra_env={"OPENLOOPS_MIGRATE_COPY": "1"})
    check(r.returncode == 0, f"install.sh finished ({(r.stdout + r.stderr)[-300:].strip() if r.returncode else 'ok'})")
    check(not stage.exists(), "the leftover staging folder from the interrupted run is gone")
    check(snapshot(new) == before, f"all {len(PERSONAL)} personal files byte-identical in the new place")
    check(old.exists() and snapshot(old) == before, "the old folder is kept, unchanged")
    check("verified" in (new / "state" / "migrated-from.txt").read_text() and "you can delete it" in r.stdout,
          "only after the check passed: recorded, and the old copy called deletable")
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and "copied and checked earlier" in r.stdout and snapshot(new) == before,
          "repeat run: nothing copied again, the verified old copy still named deletable")

    say("4d. a new place with no list while the old one has one: refused")
    home = tmp / "home6"
    old = old_install(home, "Blocked")
    before = snapshot(old)
    new = home / "Library" / "Application Support" / "OpenLoops"
    (new / "openloops").mkdir(parents=True)          # half-made, no state.json
    r = install(home, "--no-app", "--no-launch", "--name", "X")
    check(r.returncode == 1 and "has no list in it" in r.stderr and "Nothing was moved" in r.stderr,
          "refused with a plain message, exit 1")
    check(snapshot(old) == before and not (new / "state.json").exists() and not (new / "config.json").exists()
          and not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists(),
          "nothing changed: no fresh list or settings made, no job registered, the old folder intact")

    say("4e. something still working inside the old folder: refused")
    home = tmp / "home7"
    old = old_install(home, "Held")
    before = snapshot(old)
    holder = subprocess.Popen(["/bin/sleep", "60"], cwd=old)
    try:
        r = install(home, "--no-app", "--no-launch", "--no-task")
    finally:
        holder.kill()
        holder.wait()
    check(r.returncode == 1 and "still using the old Open Loops folder" in r.stderr and str(holder.pid) in r.stderr,
          "refused, naming the process")
    check(old.exists() and snapshot(old) == before and not (home / "Library" / "Application Support" / "OpenLoops").exists(),
          "nothing changed")
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and not old.exists(), "run again once it has stopped: moved")

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
    say("PASS - installs outside ~/Documents, moves old installs, and the test flags keep the real install untouched")
finally:
    if srv and srv.poll() is None:
        srv.kill()
        srv.wait()
    shutil.rmtree(tmp, ignore_errors=True)
