"""Stopping the app: closing the tab, the Quit button, and `--stop`.

    python3 tests/test_quit.py    # ~30 s; no Slack/Gmail/Claude. Temp install, spare port.

  1. A page says hello (X-OL-Page header) then goodbye (/api/bye) -> the server exits within a few seconds.
  2. Goodbye followed by a new page's hello (a reload) -> the server stays up.
  3. POST /api/quit (the Settings button) -> exits.
  4. `python -m openloops.app --stop` -> exits; run again -> "not running", exit 1.
  5. `--port N` beats OPENLOOPS_PORT.
  6. With a job running (a stand-in daylog that just sleeps): `--stop` says it will wait and the server stays
     up; `--stop --now` (what `npm run dev` uses) kills the job and the server exits.

Waits for something to happen (exit, a job starting) poll up to a generous deadline rather than a fixed few
seconds, so a loaded machine is slower but still green. Waits that prove something does NOT happen stay fixed,
each longer than the app's 4 s page grace.
"""
import json, os, shutil, subprocess, sys, time, urllib.error, urllib.request

from _helpers import free_port, fresh_install, isolated_env, listening, start_app, stop, wait_until

PORT = 0  # set by start(): the port the app says it bound
EXIT_S = 30  # deadline for "the server exits": ~5 s on a quiet machine (4 s grace + the 1 s reaper tick)
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def api(path, body=None, page=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if page:
        h["X-OL-Page"] = page
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def up():
    return listening(PORT)


tmp = fresh_install("openloops-quit-")
say(f"fresh install in {tmp}")
env = isolated_env(tmp)
procs = []


def start():
    """A server on a port of its own; PORT is what it announced."""
    global PORT
    p, PORT = start_app(tmp, env)
    procs.append(p)
    return p


def stop_cmd(*extra, port=None):
    """`python -m openloops.app --stop ...` aimed at our server (it scans 20 ports up from OPENLOOPS_PORT)."""
    return subprocess.run([sys.executable, "-m", "openloops.app", "--stop", *extra], cwd=tmp,
                          env=dict(env, OPENLOOPS_PORT=str(port or PORT)), capture_output=True, text=True, timeout=60)


def gone(p, secs=EXIT_S):
    """True once the process has exited (within secs) and the port it announced is free (not the global PORT:
    the --port N server in step 5 is on a port of its own)."""
    try:
        p.wait(secs)
    except subprocess.TimeoutExpired:
        return False
    return wait_until(lambda: not listening(p.port), 5)


try:
    # 1. hello + goodbye -> exit
    p = start()
    api("/api/state", page="tab1")
    time.sleep(6)
    check(up() and p.poll() is None, "a page that is open keeps the server up (6 s)")
    out = api("/api/bye", {"page": "tab1"})
    check(out["pages"] == 0, "goodbye drops the page")
    check(gone(p), f"server exits after the last page's goodbye (within {EXIT_S} s)")

    # 2. goodbye then a new hello (reload) -> stays up
    p = start()
    api("/api/state", page="tab2")
    api("/api/bye", {"page": "tab2"})
    api("/api/state", page="tab3")  # the reloaded page, well inside the grace
    time.sleep(7)
    check(up() and p.poll() is None, "a reload (goodbye then hello) does not stop the server")
    api("/api/bye", {"page": "tab3"})
    check(gone(p), "...and the reloaded page's goodbye does")

    # 3. /api/quit
    p = start()
    api("/api/state", page="tab4")
    out = api("/api/quit", {})
    check(out["ok"] is True and out["after_jobs"] == [], "quit accepted with no job running")
    check(gone(p), "server exits after /api/quit")

    # 4. --stop from a terminal
    p = start()
    r = stop_cmd()
    check(r.returncode == 0 and f"port {PORT}: stopping" in r.stdout, f"--stop finds the running instance (out: {r.stdout.strip()!r})")
    check(gone(p), "server exits after --stop")
    r = stop_cmd()
    check(r.returncode == 1 and "not running" in r.stdout, "--stop with nothing running says so and exits 1")

    # 5. --port beats the environment (what `npm run dev` / `npm run stop` use)
    n = free_port()
    p, got = start_app(tmp, dict(env, OPENLOOPS_PORT="1"), port=n, port_arg=True)
    procs.append(p)
    check(got == n and listening(n), f"--port N starts on N even with OPENLOOPS_PORT set (asked {n}, got {got})")
    r = stop_cmd(f"--port={n}", port=1)
    check(r.returncode == 0 and gone(p), "--stop --port=N stops that instance")

    # 6. a running job: plain --stop waits for it, --stop --now cuts it short
    sleeper = tmp / "openloops" / "daylog.py"  # throwaway copy: replace the real job with one that just sleeps
    sleeper.write_text("import os, sys, time" + chr(10) + "open(sys.argv[-1], 'w').write(str(os.getpid()))" + chr(10)
                       + "time.sleep(120)" + chr(10), encoding="utf-8")
    pidfile = tmp / "job.pid"
    p = start()
    api("/api/state", page="tab5")
    # run_job passes ["--digest-only"] as the only extra arg; the stand-in ignores it and writes its pid to argv[-1]
    sleeper.write_text(sleeper.read_text(encoding="utf-8").replace("sys.argv[-1]", repr(str(pidfile))), encoding="utf-8")
    check(api("/api/daylog", {"digest_only": True})["started"] is True, "a job starts")
    check(wait_until(lambda: pidfile.exists() and pidfile.read_text(), EXIT_S), "the job child is running")
    child = int(pidfile.read_text())
    r = stop_cmd()
    check(r.returncode == 0 and "once daylog finishes" in r.stdout, f"--stop with a job running says it will wait (out: {r.stdout.strip()!r})")
    time.sleep(3)
    check(up() and p.poll() is None, "...and the server is still up 3 s later")
    r = stop_cmd("--now")
    check(r.returncode == 0 and "daylog cut short" in r.stdout, f"--stop --now reports the job it cut short (out: {r.stdout.strip()!r} err: {r.stderr.strip()[-400:]!r})")
    check(gone(p), "server exits after --stop --now without waiting for the job")

    def alive(pid):
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
            return str(pid) in out
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    check(wait_until(lambda: not alive(child), 10), "the job child was killed too")

    # page wiring
    html = (tmp / "openloops" / "index.html").read_text(encoding="utf-8")
    for needle in ("'X-OL-Page':PAGE", "sendBeacon('/api/bye'", 'id="quitbtn"', "e.persisted"):
        check(needle in html, f"page has {needle}")
    say("ALL OK")
finally:
    for p in procs:
        stop(p)
    shutil.rmtree(tmp, ignore_errors=True)
