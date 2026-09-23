"""The test helpers' port blocks: two suites can never share a 20-port range, so one's `--stop` never quits the other.

    python3 tests/test_helpers.py    # fast; no Slack/Gmail/Claude. Temp installs, reserved ports.

  1. A second reserve() aimed inside (or just below) a held block moves to a block that does not overlap it.
  2. A guarded port refuses the SO_REUSEADDR bind an app's HTTP server makes.
  3. Two apps from start_app(): `--stop` aimed at the second stops only the second; the first stays up.
"""
import shutil, socket, subprocess, sys, time

from _helpers import SCAN, _try_reserve, fresh_install, isolated_env, listening, reserve, start_app, stop, wait_until

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def overlaps(a, b):
    return abs(a - b) < SCAN


# 1. overlapping ranges are refused; the allocation moves
a = reserve()
for aim in (a.port, a.port + 5, a.port + SCAN - 1, a.port - 10, a.port - (SCAN - 1)):
    check(_try_reserve(aim) is None, f"a block starting at {aim} overlaps {a.port}..{a.port + SCAN - 1}: refused")
b = reserve(candidates=[a.port + 5, a.port - 10])
check(not overlaps(a.port, b.port), f"aimed inside the held block, the second allocation moved ({a.port} vs {b.port})")
b.release()
check(_try_reserve(b.port) is not None, "a released block can be reserved again")

# 2. a guard keeps out the bind an app makes (http.server sets SO_REUSEADDR)
with socket.socket() as sk:
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sk.bind(("127.0.0.1", a.port + 3))
        took = True
    except OSError:
        took = False
check(not took, "a SO_REUSEADDR bind on a guarded port fails")
check(not listening(a.port + 3), "...while a connect to it is refused (the app's own scan sees it as free)")
a.release()

# 3. end to end: --stop from one block never reaches another block's server
tmp = fresh_install("openloops-helpers-")
env = isolated_env(tmp)
one = two = None
try:
    one, p1 = start_app(tmp, env)
    two, p2 = start_app(tmp, env)
    check(not overlaps(p1, p2), f"two apps got non-overlapping blocks ({p1}, {p2})")
    r = subprocess.run([sys.executable, "-m", "openloops.app", "--stop"], cwd=tmp, env=dict(env, OPENLOOPS_PORT=str(p2)),
                       capture_output=True, text=True, timeout=60)
    check(r.returncode == 0 and f"port {p2}: stopping" in r.stdout, f"--stop from the second block stops the second app ({r.stdout.strip()!r})")
    check(wait_until(lambda: two.poll() is not None, 30), "...which exits")
    check(one.poll() is None and listening(p1), "...and the first app is still up")
    say("PASS")
finally:
    stop(one)
    stop(two)
    shutil.rmtree(tmp, ignore_errors=True)
