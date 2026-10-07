"""`python3 -m openloops screenshot` (openloops/screenshot.py, headless_chrome.js, #76).

    python3 tests/test_screenshot.py    # throwaway install and $HOME, a spare port; no Slack/Gmail/AI

Checks:
  1. without Chrome: refused in one line on stderr, exit 1, no traceback, before any server is looked for; a bad
     --width likewise; without a server (Chrome present): need_server's one line.
  2. with Chrome and Node (else SKIP): the app on a spare port, `screenshot --width 900 --out <file>` writes a PNG
     900 px wide and prints its path; the default lands in state/logs/; and the server is still up afterwards
     (the page's goodbye on unload, which would make a tab-less server quit, is never sent).
"""
import contextlib, io, json, os, re, shutil, struct, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import fresh_install, isolate_this_process, isolated_env, listening, start_app, stop  # noqa: E402

REAL_PROFILE = os.environ.get("USERPROFILE", "")
TMP = isolate_this_process("openloops-shot-parent-")
from openloops import cli, screenshot  # noqa: E402

t0 = time.time()


def show(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    show(f"ok   {what}")


def real_home():
    if hasattr(os, "getuid"):
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    return Path(REAL_PROFILE) if REAL_PROFILE else Path.home()


def console(argv):
    """cli.main(argv) in this process -> (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- 1. refusals
show("1. one-line refusals")
chrome = screenshot.find_chrome(real_home())
had_bin = os.environ.pop("CHROME_BIN", None)
real_find = screenshot.find_chrome
screenshot.find_chrome = lambda home=None: ""
rc, out, err = console(["screenshot"])
check(rc == 1 and not out and err.count("\n") <= 3 and "no Chrome or Chromium found" in err and "CHROME_BIN" in err and "Traceback" not in err,
      f"no Chrome: refused in one line (plus the usage lines), exit 1 ({err.strip()!r})")
screenshot.find_chrome = real_find
rc, out, err = console(["screenshot", "--width", "99"])
check(rc == 1 and "--width needs" in err, "a width under 320 px is refused before anything runs")
rc, out, err = console(["screenshot", "now"])
check(rc == 1 and "takes no words" in err, "a stray word is refused")
if chrome and shutil.which("node"):
    os.environ["CHROME_BIN"] = chrome
    rc, out, err = console(["screenshot", "--port", "20001"])
    check(rc == 1 and "not running" in err and "app start" in err, "Chrome found, no server on the port: need_server's line")

# ---------------------------------------------------------------- 2. a real capture
if not chrome or not shutil.which("node"):
    show("SKIP the capture: no Chrome / Chromium or no node (set CHROME_BIN)")
    raise SystemExit(0)
show("2. a capture of the running page")
tmp = fresh_install("openloops-shot-", {"owner_name": "Oscar"})
(tmp / "state.json").write_text(json.dumps({"cursor": None, "last_refresh": None, "loops": [
    {"id": "sam-budget", "owner": "Sam", "ask": "the budget", "channel": "email", "asked_at": "2026-01-01T10:00",
     "status": "waiting", "chases": 0, "snooze_until": None, "notes": ""}]}), encoding="utf-8")
env = isolated_env(tmp, CHROME_BIN=chrome)
if REAL_PROFILE:  # Chrome on Windows needs the real profile to open its DevTools port
    env["USERPROFILE"] = REAL_PROFILE
p, port = start_app(tmp, env=env)
try:
    run = lambda *a: subprocess.run([sys.executable, "-m", "openloops", *a], cwd=tmp, env=dict(env, OPENLOOPS_PORT=str(port)),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    out_png = tmp / "shots" / "page.png"
    r = run("screenshot", "--width", "900", "--out", str(out_png))
    check(r.returncode == 0 and str(out_png) in r.stdout and "Traceback" not in r.stderr,
          f"screenshot --width 900 --out: exit 0, the path printed ({r.stdout.strip()[-200:]} {r.stderr.strip()[-300:]})")
    data = out_png.read_bytes() if out_png.exists() else b""
    check(data[:8] == b"\x89PNG\r\n\x1a\n", "the file is a PNG")
    w, h = struct.unpack(">II", data[16:24])
    check(w == 900 and h >= 600, f"...{w}x{h} px: the width asked for, the page's own height")
    check("had not loaded" not in r.stdout, "the page had loaded its state before the capture")
    r = run("screenshot")
    m = re.search(r"(\S+page-[0-9_-]+\.png)", r.stdout)
    check(r.returncode == 0 and m and Path(m.group(1)).is_file() and Path(m.group(1)).parent == (tmp / "state" / "logs").resolve(),
          f"no --out: a PNG in state/logs/ ({r.stdout.strip()[-200:]})")
    time.sleep(6)   # past app.PAGE_GRACE_S: a goodbye from the headless page would have stopped the server by now
    check(p.poll() is None and listening(port), "the server is still up: the headless page never said goodbye")
    show("ALL OK")
finally:
    stop(p)
    shutil.rmtree(tmp, ignore_errors=True)
