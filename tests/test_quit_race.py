"""A "Quit now" that lands while a job's process is starting: the process is stopped and the server exits (#43 review).

    python3 tests/test_quit_race.py    # fast; no Slack/Gmail/Claude. Temp install, spare port. Skipped on Windows.

run_job claims a job (running=True) before it starts the process and registers it in procs after. A `--stop --now`
arriving in that gap used to find a busy job with no process to stop, and the server quit while the child lived on.
Here the app runs under a harness that makes the gap as wide as we like: the job's Popen really starts a child (a long
sleep, its pid written to a file), then waits for a "release" file before returning. The test sends /api/quit
{"now": true} during that wait, releases, and checks the child is gone and the server has exited.
"""
import json, os, signal, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path

from _helpers import fresh_install, isolated_env, reserve, wait_until

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


if sys.platform == "win32":
    say("SKIP: the harness relies on process groups (POSIX)")
    raise SystemExit(0)

# Runs the real app (runpy, as `python -m openloops.app`) with subprocess.Popen wrapped for job processes only.
HARNESS = r'''
import os, runpy, subprocess, sys, time
from pathlib import Path
here = Path(os.environ["RACE_DIR"])
real = subprocess.Popen

class Gated(real):
    def __init__(self, args, *a, **kw):
        job = isinstance(args, list) and len(args) > 2 and args[1] == "-m" and args[2] == "openloops.daylog"
        if job:  # a child that would outlive the server if nobody stopped it
            args = [sys.executable, "-c", "import time; time.sleep(120)"]
        super().__init__(args, *a, **kw)
        if job:
            (here / "child.pid").write_text(str(self.pid))
            while not (here / "release").exists():  # the gap: started, not yet registered
                time.sleep(0.05)

subprocess.Popen = Gated
sys.argv = ["openloops.app", "--no-browser"]
runpy.run_module("openloops.app", run_name="__main__", alter_sys=True)
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie (exited, not yet reaped by its parent) is not running: ps says Z
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


tmp = fresh_install("openloops-quitrace-")
race = Path(tempfile.mkdtemp(prefix="openloops-quitrace-ctl-"))
(tmp / "harness.py").write_text(HARNESS, encoding="utf-8")
res = reserve()
res.free()
port = res.port
env = isolated_env(tmp, OPENLOOPS_PORT=str(port), RACE_DIR=str(race), PYTHONUNBUFFERED="1")
srv = subprocess.Popen([sys.executable, str(tmp / "harness.py")], cwd=tmp, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
child = None
try:
    line = srv.stdout.readline()
    check(f"localhost:{port}" in line, f"app came up under the harness ({line.strip()!r})")
    threading.Thread(target=lambda: [None for _ in srv.stdout], daemon=True).start()   # keep draining

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    started = {}
    threading.Thread(target=lambda: started.update(post("/api/daylog", {})), daemon=True).start()   # blocks in the gap
    check(wait_until(lambda: (race / "child.pid").exists(), 15), "the job's child process started (and Popen is held open)")
    child = int((race / "child.pid").read_text())
    check(alive(child), f"child {child} is running, not yet registered")

    out = post("/api/quit", {"now": True})
    check(out.get("ok") and out.get("cut_short") == ["daylog"], f"Quit now answered, cutting the starting job short ({out})")
    time.sleep(1.5)   # longer than a reaper tick: with the old code the server would already be gone here
    check(srv.poll() is None, "the server waits: a job is still being started")

    (race / "release").touch()
    check(wait_until(lambda: not alive(child), 10), "released: the child is stopped")
    check(wait_until(lambda: srv.poll() is not None, 10), "and the server exits")
    say("PASS")
finally:
    (race / "release").touch()
    if srv.poll() is None:
        srv.kill()
        srv.wait()
    if child and alive(child):
        os.kill(child, signal.SIGKILL)
    res.release()
