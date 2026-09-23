"""install.sh: the default place outside ~/Documents, moving an older install there, and the test-install flags.

    python3 tests/test_install_flags.py    # macOS/Linux; no Slack/Gmail/Claude. Throwaway $HOME, spare port.

Every run uses a throwaway $HOME, a stub `claude` and a stub `launchctl`, so the real install, the real
~/Library/LaunchAgents and the real Dock are never touched. Checks:
  1. --dest DIR --no-app --no-task --no-launch --port N: installs only into DIR, writes the port into its
     config.json, writes no app bundle and no launchd job, and leaves an older ~/Documents install alone.
  2. OPENLOOPS_DEST does the same as --dest.
  3. The app started from that copy, with no --port and no OPENLOOPS_PORT, answers on the config's port.
  4. Default install over an older ~/Documents/OpenLoops: it is moved (list and settings kept), the old folder is
     gone, and the weekday job is re-registered for the new path - and the old "Operation not permitted" lines it
     carried no longer show as a red row.
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
    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--port", "80a")
    check(r.returncode != 0 and "--port must be a number" in r.stderr, "a bad --port is refused")
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

    say("4. default install over an older ~/Documents/OpenLoops: moved, not copied")
    home = tmp / "home2"
    (home / "Desktop").mkdir(parents=True)
    old = old_install(home, "Mover")
    new = home / "Library" / "Application Support" / "OpenLoops"
    r = install(home, "--no-app", "--no-launch")
    check(r.returncode == 0, f"install.sh finished ({(r.stdout + r.stderr)[-300:].strip() if r.returncode else 'ok'})")
    check(not old.exists(), "~/Documents/OpenLoops is gone (one copy only)")
    check((new / "openloops" / "app.py").exists(), "the app is in ~/Library/Application Support/OpenLoops")
    cfg = json.loads((new / "config.json").read_text(encoding="utf-8"))
    st = json.loads((new / "state.json").read_text(encoding="utf-8"))
    check(cfg.get("owner_name") == "Mover" and cfg.get("refresh_time") == "08:30", "settings came along (no name asked)")
    check(any(l.get("id") == "keep-me" for l in st.get("loops", [])), "the list came along")
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    check(plist.exists(), "weekday job re-registered")
    pl = plistlib.loads(plist.read_bytes())
    check(pl["ProgramArguments"] == ["/bin/bash", f"{new}/scripts/run-refresh.sh"], "... and it runs the script at the new path")
    check(pl["StandardErrorPath"] == f"{new}/state/logs/launchd.err.log", "... logging into the new folder")
    hours = {(d["Hour"], d["Minute"]) for d in pl["StartCalendarInterval"]}
    check(hours == {(8, 30)}, "... at the person's own 08:30 from config.json, not the installer's default 09:15")
    check("Documents" not in plist.read_text(), "... with no ~/Documents path left in it")
    check("bootstrap" in launchctl_log.read_text(), "... through the launchctl stub (real launchd untouched)")
    check(doctor.schedule_step(new / "state" / "logs", new) is None,
          "the six old 'Operation not permitted' lines (old path) do not show as a red row after the move")

    r = install(home, "--no-app", "--no-launch", "--at", "07:45")
    pl = plistlib.loads(plist.read_bytes())
    check(r.returncode == 0 and "older copy" not in r.stdout, "second run: nothing left to move, no note")
    check({(d["Hour"], d["Minute"]) for d in pl["StartCalendarInterval"]} == {(7, 45)}
          and json.loads((new / "config.json").read_text())["refresh_time"] == "07:45",
          "an explicit --at wins, in the job and in config.json")
    old_install(home, "Stray")   # both exist now: the new one wins, the old one is only mentioned
    r = install(home, "--no-app", "--no-launch", "--no-task")
    check(r.returncode == 0 and "older copy is still in" in r.stdout and old.exists()
          and json.loads((new / "config.json").read_text())["owner_name"] == "Mover",
          "both exist: nothing moved or overwritten, the old one is pointed out")

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
