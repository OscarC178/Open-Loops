"""Shared by the tests: a throwaway install with its own HOME, and the app started on a port of its own.

    from _helpers import fresh_install, isolated_env, run_node, start_app, stop

Nothing here touches the developer's files or ports. The install is a temp folder built from
config.template.json, HOME points at an empty folder inside it (so nothing is read from the real ~), and the
port comes from a reserved block (see reserve()) and is read back from what the app announces ("Open Loops -> http://localhost:N"), so
any number of suites can run side by side, next to the installed copy on 8765.
"""
import atexit, json, os, queue, random, re, shutil, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN = 20  # app.py's pick_port() and `--stop` look at the port they are given and the 19 above it
ANNOUNCE = re.compile(r"Open Loops -> http://localhost:(\d+)")


def listening(port):
    with socket.socket() as sk:
        sk.settimeout(0.3)
        return sk.connect_ex(("127.0.0.1", port)) == 0


class Reservation:
    """A block of SCAN ports, port .. port + SCAN - 1, held by plain bound (not listening) sockets. Anything else that
    tries to bind one of them fails (a second test suite's reserve(), and an app, which binds with SO_REUSEADDR only),
    while a connect to one is refused, so this block's own app and `--stop` see the guards as free ports.

    Two suites' blocks can therefore never overlap, which is what keeps one suite's `--stop` (it scans 20 ports up
    from the port it is given) from reaching another suite's server. Held until release() or the process exits."""

    def __init__(self, port, socks):
        self.port = port
        self._socks = dict(zip(range(port, port + SCAN), socks))

    def free(self, *ports):
        """Close the guards on `ports` (default: the first port) so a server can bind there."""
        for q in ports or (self.port,):
            sk = self._socks.pop(q, None)
            if sk:
                sk.close()

    def release(self):
        self.free(*list(self._socks))


_held = []  # every reservation, so the guards live as long as the test process unless released earlier


# Blocks are drawn at random from below the OSes' ephemeral ranges (macOS 49152-65535, Linux 32768-60999). Every
# HTTP request a test makes leaves its client port in TIME_WAIT for a while, and a plain bind (a guard) fails on one,
# so under a busy suite whole blocks of 20 are rarely free up there; down here only listening servers get in the way.
BLOCKS = (20000, 32000)


def _candidate():
    return random.randrange(BLOCKS[0], BLOCKS[1] - SCAN)


def _try_reserve(p):
    """Bind guards on p .. p + SCAN - 1. -> Reservation, or None if any of them is taken (all are let go again)."""
    if p < 1024 or p + SCAN > 65536:
        return None
    socks = []
    try:
        for q in range(p, p + SCAN):
            sk = socket.socket()
            socks.append(sk)
            sk.bind(("127.0.0.1", q))
    except OSError:
        for sk in socks:
            sk.close()
        return None
    return Reservation(p, socks)


def reserve(candidates=(), tries=200):
    """A Reservation of SCAN ports: the `candidates` first (tests use this to aim at a taken block), then random
    blocks in BLOCKS. Raises when no whole block is free, rather than hand back a port whose neighbours were not held."""
    todo = list(candidates)
    for _ in range(tries + len(todo)):
        r = _try_reserve(todo.pop(0) if todo else _candidate())
        if r:
            _held.append(r)
            return r
    raise SystemExit(f"FAIL: no block of {SCAN} free ports after {tries} tries")


def free_port():
    """A port for a server, whose 19 neighbours above stay reserved (guarded) for the rest of this test, so neither
    the app's port scan nor `--stop` can reach a server another suite (or the installed copy) is running."""
    r = reserve()
    r.free()
    return r.port


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


def isolate_this_process(prefix):
    """For a test that imports openloops in its own process: call before the first `from openloops import ...`.
    The package is then imported from a throwaway install, so the config.json, state.json and state/ that importing
    app.py creates land there and not in the checkout, and this process's HOME (USERPROFILE) is an empty folder in
    it, so nothing the modules read under ~ is the developer's. -> Path of the copy (removed at exit)."""
    tmp = fresh_install(prefix)
    home = str(tmp / "home")
    os.environ["HOME"] = os.environ["USERPROFILE"] = home
    sys.path.insert(0, str(tmp))
    atexit.register(shutil.rmtree, tmp, True)
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

    port=None  -> the first port of a fresh reserve() block; if the app does not come up, says "already running",
                  or announces any other port than that one, stop it and try a new block, up to `tries` times.
    port=N     -> that preferred port, once; the announced port is returned as it is (the caller checks it).
    port_arg   -> pass the port as `--port N` instead of OPENLOOPS_PORT (the caller's env is left as it is).
    Returns (Popen, port); the Popen also carries .port (announced) and .lines (every line the app printed). Its
    output is drained for its whole life, so a chatty server never blocks on a full pipe. stop(p) lets go of the
    port block."""
    base = dict(os.environ if env is None else env, PYTHONUNBUFFERED="1")
    for _ in range(1 if port else tries):
        res = None
        if port:
            want = port
        else:
            res = reserve()
            res.free()  # only the first port: the app binds it; the other 19 stay guarded
            want = res.port
        args = [sys.executable, "-m", "openloops.app", "--no-browser", *extra_args]
        e = dict(base)
        if port_arg:
            args += ["--port", str(want)]
        else:
            e["OPENLOOPS_PORT"] = str(want)
        p = subprocess.Popen(args, cwd=cwd, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        p.lines, p.port, p.reservation = [], None, res
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
                p.port = int(m.group(1))
                if port or p.port == want:
                    return p, p.port
                break  # moved off its block (something took the port meanwhile): its --stop range is not ours
            if "already running" in line:
                break  # another Open Loops owns that port: not ours
        stop(p)
        print(f"(openloops.app did not come up as ours from port {want}: {p.lines[-3:]}; trying again)", flush=True)
    raise SystemExit("FAIL: openloops.app did not come up on a port of its own")


def run_node(js, timeout=60, check=False):
    """Run JavaScript in node -> CompletedProcess (stdout and stderr as text). The code goes in a script file, never
    `node -e`: the page's code is far longer than a Windows command line may be (WinError 206). utf-8 both ways,
    whatever the console's code page (a piped stdout is cp1252 on Windows, and node writes utf-8)."""
    node = shutil.which("node")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    try:
        return subprocess.run([node, f.name], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=check)
    finally:
        os.unlink(f.name)


def stop(p):
    """Terminate a server started by start_app (kill it if it will not go), wait for it, and let go of its ports."""
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    if p is not None and getattr(p, "reservation", None):
        p.reservation.release()


def wait_until(cond, secs=30, step=0.1):
    """Poll cond() until it is true or `secs` pass. -> the last value of cond(). A deadline, not a fixed sleep:
    fast on a quiet machine, still green on a loaded one."""
    end = time.time() + secs
    while True:
        v = cond()
        if v or time.time() >= end:
            return v
        time.sleep(step)
