"""Port-clash regression test.

    python3 tests/test_port_clash.py    # fast; no Slack/Gmail/Claude needed.

Guards the bug where a stray `python -m http.server 8765` (or any other local server) on Open Loops'
port made the launcher think Open Loops was already running, so double-clicking the icon opened a
"Directory listing for /" page instead of the app.

  1. Something else on the port -> Open Loops must start on the next free port, not exit.
  2. Stray still there and Open Loops on the next port -> relaunching must find the running instance
     (Server: OpenLoops) and exit 0 instead of starting a third server.
Builds a fresh install in a temp folder and cleans up. The clashing pair of ports is chosen per run from ones
the OS says are free, so it never meets the installed copy or another suite. Exit code 0 = both hold.
"""
import http.server, shutil, subprocess, sys, threading, time, urllib.request

from _helpers import fresh_install, free_port, isolated_env, listening, start_app, stop

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def wait_for(port, up=True, tries=50):
    for _ in range(tries):
        if listening(port) == up:
            return True
        time.sleep(0.1)
    return False


def server_header(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
        return r.headers.get("Server", "")


tmp = fresh_install("openloops-portclash-")
say(f"fresh install in {tmp}")
env = isolated_env(tmp)

app = None
app2 = None
stray = None
try:
    # 1. A foreign server (plain directory listing) squats on the preferred port. PORT and PORT + 1 come from
    #    free_port(); if some other program takes PORT + 1 in between, start again on a new pair.
    for attempt in range(3):
        PORT = free_port()
        stray = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), http.server.SimpleHTTPRequestHandler)
        threading.Thread(target=stray.serve_forever, daemon=True).start()
        check(wait_for(PORT) and not server_header(PORT).startswith("OpenLoops"), f"stray http.server is on port {PORT}")
        app, got = start_app(tmp, env, port=PORT)
        if got == PORT + 1 or attempt == 2:
            break
        say(f"port {PORT + 1} was taken meanwhile (app came up on {got}); trying a new pair")
        stop(app)
        stray.shutdown(); stray.server_close()
    check(got == PORT + 1 and wait_for(PORT + 1), "Open Loops moved to the next free port instead of quitting")
    check(server_header(PORT + 1).startswith("OpenLoops"), "Server: OpenLoops header identifies the app")
    check(app.poll() is None, "Open Loops is still serving (did not mistake the stray for itself)")

    # 2. Stray still on the preferred port, Open Loops on the next one: relaunching with the same
    #    preferred port must find the running instance and exit 0, not start a third server.
    app2 = subprocess.Popen([sys.executable, "-m", "openloops.app", "--no-browser"], cwd=tmp, env=dict(env, OPENLOOPS_PORT=str(PORT)),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        out, _ = app2.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        app2.kill()
        raise SystemExit("FAIL: second launch did not exit when Open Loops was already running")
    check(app2.returncode == 0, "second launch exits 0 when Open Loops already owns the port")
    check(not listening(PORT + 2), "second launch did not start a duplicate server on another port")
    check("localhost" in out, "second launch printed the page address")
    say("PASS")
finally:
    for p in (app, app2):
        stop(p)
    try:
        stray.shutdown(); stray.server_close()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
