"""The Mac morning refresh that could not start (#24): doctor.py's red row and /api/diag's log tail.

    python3 tests/test_schedule.py    # fast; no Slack/Gmail/Claude. Temp folders, spare port.

launchd writes state/logs/launchd.err.log when it cannot start scripts/run-refresh.sh at all; the case that hid
for a week was "/bin/bash: .../run-refresh.sh: Operation not permitted" (macOS privacy protection refusing a
background job a script under ~/Documents). Checks, all against temp folders, never the real install:
  1. doctor.schedule_step: missing log, empty log, the blocked line, a later run ("started", green), an
     older run, another failure, the LATEST failure deciding, and lines about any other install ignored.
  2. the user-facing sentences follow #25's plain-words rules.
  3. doctor.main() adds the row on a Mac (optional, so setup is not sent back to step 1) and only there.
  4. /api/diag carries the tail of launchd.err.log, and "" when there is none; /api/schedule/status answers
     the same check without the CLI.
  5. the page shows the row once set up, re-checks it, puts the download button in the checklist too, and
     does not promise a morning refresh while the row is red.
"""
import contextlib, inspect, io, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from openloops import doctor

PORT = 8791
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def touch(path, text="", ago=0):
    """Write a file and set its modified time `ago` seconds in the past (the check compares log ages)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    t = time.time() - ago
    os.utime(path, (t, t))


tmp = Path(tempfile.mkdtemp(prefix="openloops-schedule-"))
srv = None
try:
    say("1. doctor.schedule_step against temp logs")
    root = tmp / "install"
    logs = root / "state" / "logs"
    logs.mkdir(parents=True)
    err = logs / "launchd.err.log"
    blocked = f"/bin/bash: {root}/scripts/run-refresh.sh: Operation not permitted\n"

    check(doctor.schedule_step(logs, root) is None, "no launchd.err.log (never due yet): no row")
    touch(err, "")
    check(doctor.schedule_step(logs, root) is None, "empty launchd.err.log: no row")
    touch(err, "\n  \n")
    check(doctor.schedule_step(logs, root) is None, "blank lines only: no row")

    touch(err, blocked * 6, ago=60)   # six weekday mornings, like the live install
    s = doctor.schedule_step(logs, root)
    check(s is not None and s["id"] == "schedule" and s["ok"] is False, "Operation not permitted: red 'schedule' row")
    check(s["kind"] == "blocked" and s["alert"] is True, "... recognised as the privacy block")
    check(s.get("optional") is True, "... optional, so a set-up user is never sent back to the connection steps")
    check(s["title"] == doctor.SCHEDULE_MSG["blocked"]["title"] and s["fix"] == doctor.SCHEDULE_MSG["blocked"]["fix"],
          "... wording comes from the one SCHEDULE_MSG table")
    check(s["link"].startswith("https://github.com/") and "releases" in s["link"], "... links to the download page")
    check("Operation not permitted" in s["detail"], "... the raw line is kept as developer detail")

    touch(logs / "runner-2026-09-22.log", "=== refresh 09:15:01\n", ago=3600)
    check(doctor.schedule_step(logs, root)["ok"] is False, "a runner log OLDER than the error: still red")
    touch(logs / "runner-2026-09-23.log", "=== refresh 09:15:01\nrefresh exit 1\n", ago=0)
    s = doctor.schedule_step(logs, root)
    check(s["ok"] is True and s["kind"] == "started" and "started" in s["title"] and "fix" in s and not s["fix"],
          "a runner log NEWER than the error: green, and it says 'started' (not 'worked': that run exited 1)")
    for f in logs.glob("runner-*.log"):
        f.unlink()
    err.unlink()
    touch(logs / "runner-2026-09-23.log", "weekend - skipped\n")
    check(doctor.schedule_step(logs, root)["kind"] == "started", "runner log and no error log at all: green 'started'")
    for f in logs.glob("runner-*.log"):
        f.unlink()

    touch(err, f"/bin/bash: {root}/scripts/run-refresh.sh: No such file or directory\n")
    s = doctor.schedule_step(logs, root)
    check(s is not None and s["kind"] == "failed" and s["title"] == doctor.SCHEDULE_MSG["failed"]["title"],
          "another start failure: red row, the general wording")
    touch(err, blocked * 3 + f"/bin/bash: {root}/scripts/run-refresh.sh: No such file or directory\n")
    s = doctor.schedule_step(logs, root)
    check(s["kind"] == "failed" and "No such file" in s["detail"], "older privacy lines, newer other failure: the LATEST decides")
    touch(err, f"/bin/bash: {root}/scripts/run-refresh.sh: No such file or directory\n" + blocked)
    check(doctor.schedule_step(logs, root)["kind"] == "blocked", "... and the other way round")
    touch(err, "some other launchd complaint without a script path\n")
    check(doctor.schedule_step(logs, root) is None, "a line that names no run-refresh.sh: not counted")

    gone = tmp / "Documents" / "OpenLoops"   # an install that install.sh has since moved: the path no longer exists
    touch(err, f"/bin/bash: {gone}/scripts/run-refresh.sh: Operation not permitted\n" * 3)
    check(doctor.schedule_step(logs, root) is None, "lines about a moved-away install (path gone): no row")

    other = tmp / "other"                    # another install that exists: still not ours, so not reported here
    touch(other / "scripts" / "run-refresh.sh", "#!/bin/bash\n")
    touch(err, f"/bin/bash: {other}/scripts/run-refresh.sh: Operation not permitted\n")
    check(doctor.schedule_step(logs, root) is None, "lines about ANOTHER install's script (even one on disk): no row")

    spaced = tmp / "Application Support" / "OpenLoops"   # the new default has a space in it
    touch(spaced / "state" / "logs" / "launchd.err.log", f"/bin/bash: {spaced}/scripts/run-refresh.sh: Operation not permitted\n")
    check(doctor.schedule_step(spaced / "state" / "logs", spaced) is not None, "a path with a space is matched to its own install")

    say("2. plain-words rules (#25) for every sentence the page shows")
    for kind, m in doctor.SCHEDULE_MSG.items():
        for part in ("title", "fix"):
            text = m[part]
            if not text:
                continue
            low = text.lower()
            check(not any(w in low for w in ("launchd", "tcc", "rc=", "exit code", "bash", "/", ".log", "plist")),
                  f"{kind}.{part}: no jargon, no paths")
            check(text.count(". ") == 0 and text.endswith("."), f"{kind}.{part}: one sentence")
    check("privacy settings" in doctor.SCHEDULE_MSG["blocked"]["title"], "blocked: says who stopped it (your Mac's privacy settings)")
    check(all("page is open" not in m["title"] for m in doctor.SCHEDULE_MSG.values()),
          "no claim that an open page refreshes by itself (it does not)")
    check(all(m["fix"].count(",") == 0 and m["fix"].count(";") == 0 for m in doctor.SCHEDULE_MSG.values()),
          "each fix is one action, not a chain")

    say("3. doctor.main() adds the row on a Mac only, and all_ok ignores it")
    touch(err, blocked)
    saved = (doctor.claude_steps, doctor.LOGS, doctor.ROOT, doctor.CONFIG, doctor.agent.name)
    # how many values main() unpacks from claude_steps (email, slack, gmail, routes, ...): read from main's own
    # source, so this stub keeps fitting when that list grows
    lhs = inspect.getsource(doctor.main).split("= (grok_steps")[0].splitlines()[-1]
    arity = lhs.count(",") + 1
    try:
        def fake_steps(steps):   # connections all fine (Gmail only), no CLI called
            steps.append({"id": "claude", "ok": True, "title": "Claude is installed", "fix": ""})
            steps.append({"id": "login", "ok": True, "title": "Signed in to Claude", "fix": ""})
            return ("", False, True, "", False, "", *([{}] * (arity - 6)))
        doctor.claude_steps, doctor.LOGS, doctor.ROOT = fake_steps, logs, root
        doctor.CONFIG, doctor.agent.name = root / "config.json", (lambda: "claude")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            doctor.main()
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
    finally:
        doctor.claude_steps, doctor.LOGS, doctor.ROOT, doctor.CONFIG, doctor.agent.name = saved
    rows = [x for x in out["steps"] if x["id"] == "schedule"]
    if sys.platform == "darwin":
        check(len(rows) == 1 and rows[0]["ok"] is False, "Mac: the red 'schedule' row is in the checklist")
        check(out["all_ok"] is True, "... and all_ok stays true (the rest is fine)")
    else:
        check(not rows, f"{sys.platform}: no 'schedule' row (the launchd check is Mac-only)")

    say("4. /api/diag includes the tail of launchd.err.log")
    app = tmp / "app"
    app.mkdir()
    shutil.copytree(REPO / "openloops", app / "openloops")
    shutil.copy(REPO / "config.template.json", app / "config.template.json")
    tpl = json.loads((app / "config.template.json").read_text(encoding="utf-8-sig"))
    tpl["owner_name"] = "Testuser"
    (app / "config.json").write_text(json.dumps(tpl), encoding="utf-8")
    (app / "state.json").write_text(json.dumps({"cursor": "2026-01-01T00:00", "last_refresh": None, "loops": []}), encoding="utf-8")
    with socket.socket() as sk:
        check(sk.connect_ex(("127.0.0.1", PORT)) != 0, f"spare port {PORT} free")
    srv = subprocess.Popen([sys.executable, "-m", "openloops.app", "--no-browser"], cwd=app,
                           env=dict(os.environ, OPENLOOPS_PORT=str(PORT), BROWSER="/usr/bin/true"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def diag():
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/diag", timeout=5) as r:
            return json.loads(r.read())

    for _ in range(100):
        try:
            d = diag()
            break
        except Exception:
            time.sleep(0.2)
    else:
        raise SystemExit("FAIL: app did not come up")
    check(d.get("launchd_err_log") == "", "no launchd.err.log: launchd_err_log is \"\"")
    alog = app / "state" / "logs" / "launchd.err.log"
    touch(alog, "old noise line\n" * 300 + f"/bin/bash: {app}/scripts/run-refresh.sh: Operation not permitted\n")
    d = diag()
    tail = d.get("launchd_err_log", "")
    check("Operation not permitted" in tail and tail.endswith("Operation not permitted\n"), "the latest line is in the diag")
    check(len(tail) == 2000, f"only the tail is sent (2000 chars of {alog.stat().st_size})")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/schedule/status", timeout=5) as r:
        st = json.loads(r.read())
    if sys.platform == "darwin":
        check(st["step"] and st["step"]["kind"] == "blocked", "/api/schedule/status: the same red row, no CLI needed")
    else:
        check(st["step"] is None, f"/api/schedule/status on {sys.platform}: nothing (Mac-only check)")
finally:
    if srv and srv.poll() is None:
        srv.kill()
        srv.wait()
    shutil.rmtree(tmp, ignore_errors=True)

say("5. the page shows the row once set up")
html = (REPO / "openloops" / "index.html").read_text(encoding="utf-8")
check('id="sched_warn"' in html and "function paintSchedule(" in html and "paintSchedule(st)" in html,
      "index.html: #sched_warn box, painted from tick()")
body = html.split("function paintSchedule(", 1)[1].split("\n\n", 1)[0]
check("style.display='block'" in body, "... shown with display:block ('' would fall back to the stylesheet's display:none)")
check("setInterval(checkSchedule" in html and "/api/schedule/status" in html, "... re-checked every minute once set up")
check("paintConnect=function(){paintConnectBase()" in html and "s.link" in body, "... the checklist row gets the download button too")
check("st==='ready'&&!schedBad())toast(" in html, "... no 'refreshes itself every morning' toast while the row is red")
say("PASS - the morning refresh failure is detected, worded plainly, and in /api/diag")
