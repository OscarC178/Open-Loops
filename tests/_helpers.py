"""Shared by the tests: a throwaway install with its own HOME, and the app started on a port of its own.

    from _helpers import fresh_install, isolated_env, start_app, stop

Nothing here touches the developer's files or ports. The install is a temp folder built from
config.template.json, HOME points at an empty folder inside it (so nothing is read from the real ~), and the
port comes from the OS and is read back from what the app announces ("Open Loops -> http://localhost:N"), so
any number of suites can run side by side, next to the installed copy on 8765.
"""
import json, os, queue, re, shutil, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN = 20  # app.py's pick_port() and `--stop` look at the port they are given and the 19 above it
ANNOUNCE = re.compile(r"Open Loops -> http://localhost:(\d+)")


def listening(port):
    with socket.socket() as sk:
        sk.settimeout(0.3)
        return sk.connect_ex(("127.0.0.1", port)) == 0


def free_port():
    """A port the OS just handed out whose 19 neighbours above are free too, so neither the app's port scan nor
    `--stop` reaches a server some other suite (or the installed copy) is running."""
    for _ in range(50):
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            p = sk.getsockname()[1]
        if p + SCAN <= 65535 and not any(listening(q) for q in range(p, p + SCAN)):
            return p
    return p  # a crowded machine: take the last one; start_app still reads back the port actually bound


def fresh_install(prefix, config=None):
    """A throwaway install: openloops/ and config.template.json copied into a temp folder, config.json built
    from the template plus `config`, and an empty home/ for isolated_env(). -> Path of the temp folder."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    shutil.copytree(REPO / "openloops", tmp / "openloops")
    shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
    cfg = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
    cfg.update(config or {})
    (tmp / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    (tmp / "home").mkdir()
    return tmp


def isolated_env(tmp, **extra):
    """os.environ with HOME (USERPROFILE on Windows) pointed at <tmp>/home and no OPENLOOPS_PORT inherited from
    the developer's shell. `extra` overrides or adds variables."""
    home = str(Path(tmp) / "home")
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    env.pop("OPENLOOPS_PORT", None)
    env.update(extra)
    return env


def start_app(cwd, env=None, extra_args=(), port=None, port_arg=False, tries=5, timeout=30):
    """Start `python -m openloops.app --no-browser` in `cwd` and wait until it says which port it bound (it prints
    that only after binding, and moves up by itself past a port another program holds).

    port=None  -> an OS-chosen free port; if another Open Loops got there first ("already running") or the app
                  does not come up, try again on a fresh port, up to `tries` times.
    port=N     -> that preferred port, once.
    port_arg   -> pass the port as `--port N` instead of OPENLOOPS_PORT (the caller's env is left as it is).
    Returns (Popen, port). The Popen's .lines keeps every line the app printed; its output is drained for its
    whole life, so a chatty server never blocks on a full pipe."""
    base = dict(os.environ if env is None else env, PYTHONUNBUFFERED="1")
    for _ in range(1 if port else tries):
        want = port or free_port()
        args = [sys.executable, "-m", "openloops.app", "--no-browser", *extra_args]
        e = dict(base)
        if port_arg:
            args += ["--port", str(want)]
        else:
            e["OPENLOOPS_PORT"] = str(want)
        p = subprocess.Popen(args, cwd=cwd, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        p.lines = []
        got = queue.Queue()

        def pump(p=p, got=got):
            for line in p.stdout:
                p.lines.append(line.rstrip("\n"))
                got.put(line)
            got.put(None)  # the app exited

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.time() + timeout
        while True:
            try:
                line = got.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                line = None
            if line is None:
                break  # exited, or said nothing in time
            m = ANNOUNCE.match(line)
            if m:
                return p, int(m.group(1))
            if "already running" in line:
                break  # another Open Loops owns that port: not ours
        stop(p)
        print(f"(openloops.app did not come up as ours from port {want}: {p.lines[-3:]}; trying again)", flush=True)
    raise SystemExit("FAIL: openloops.app did not come up on a port of its own")


def stop(p):
    """Terminate a server started by start_app (kill it if it will not go) and wait for it."""
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def wait_until(cond, secs=30, step=0.1):
    """Poll cond() until it is true or `secs` pass. -> the last value of cond(). A deadline, not a fixed sleep:
    fast on a quiet machine, still green on a loaded one."""
    end = time.time() + secs
    while True:
        v = cond()
        if v or time.time() >= end:
            return v
        time.sleep(step)
