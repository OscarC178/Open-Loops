"""The Windows runner of a Claude sign-in step (#27 part 1): `claude mcp login` in a hidden console, its output read
from a file, so the page gets the sign-in link and a failure's reason as it does on the Mac.

    python tests\\test_connect_win.py    # Windows only (SKIP elsewhere); temp install, spare port, fake `claude`.

The fake `claude` is a .cmd shim over a Python script, as the real one (an npm shim) is. Like the real CLI it
refuses `mcp login` when stdin is not a terminal, so a pass proves the runner gives it a real console. BROWSER
points at a .cmd that only writes down the link, so no browser window opens and nothing signs in to anything.
Before #27 the command ran in a console window of its own with nothing captured: no fallback link, a failed
sign-in with nothing to show, and a window whose closing ended the sign-in without a word.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


if sys.platform != "win32":
    say("SKIP: the Windows runner of a sign-in step only exists on Windows (tests/test_connect.py covers the pty one)")
    raise SystemExit(0)

from _helpers import isolated_env, start_app, stop  # noqa: E402

# ---------------------------------------------------------------- the console the CLI gets, from a parent that has none
# The installed app is started by pythonw.exe (the icons, Start-Process), which has no console at all. CREATE_NO_WINDOW
# still gives the child a console of its own, only without a window, so stdin is a terminal there and `claude mcp
# login` does not refuse it (review of #70 asked for CREATE_NEW_CONSOLE + SW_HIDE; this shows it is not needed).
pyw = Path(sys.executable).with_name("pythonw.exe")
if pyw.exists():
    with tempfile.TemporaryDirectory(prefix="openloops-pyw-") as td:
        probe, seen = Path(td) / "probe.py", Path(td) / "seen.txt"
        redirect = f' > "{seen}" 2>&1'  # inserted as a Python literal (!r), so a quote in the temp path cannot break probe.py
        probe.write_text(
            "import subprocess, sys\n"
            "child = subprocess.list2cmdline([sys.executable.replace('pythonw.exe', 'python.exe'), '-c', 'import os; print(os.isatty(0))'])\n"
            f"subprocess.run(child + {redirect!r}, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)\n",
            encoding="utf-8")
        subprocess.run([str(pyw), str(probe)], timeout=60)
        check(seen.exists() and seen.read_text().strip() == "True",
              f"a child of pythonw.exe (no console) launched with CREATE_NO_WINDOW has a terminal on stdin ({seen.read_text().strip() if seen.exists() else 'no output'})")
else:
    say("SKIP the pythonw probe: no pythonw.exe beside this Python")

# ---------------------------------------------------------------- static: the runner itself
src = (REPO / "openloops" / "app.py").read_text(encoding="utf-8")
check("CREATE_NEW_CONSOLE" not in src and "running in its own window" not in src,
      "no sign-in step runs in a console window of its own any more")
check("def _connect_one_win" in src and 'creationflags=subprocess.CREATE_NO_WINDOW' in src.split("def _connect_one_win")[1],
      "the Windows runner gives the CLI a hidden console (a real terminal, no window to close)")
check('"--no-browser"' in (REPO / "openloops" / "agent.py").read_text(encoding="utf-8").split("def login_cmd")[1].split("def ")[0],
      "login_cmd asks the CLI for the link rather than a browser it opens itself")

# ---------------------------------------------------------------- the fakes
FAKE = r'''
# Fake Claude CLI for test_connect_win.py: records each call, behaves like the real one where it matters.
import os, sys, time
a = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.txt"), "a") as f:
    f.write(" | ".join(a) + "\n")
flag = lambda n: os.path.exists(os.path.join(here, n))
if a == ["--version"]:
    print("2.1.280 (Claude Code)"); sys.exit(0)
if a[:3] == ["plugin", "marketplace", "list"]:
    print("Configured marketplaces:\n\n  > claude-plugins-official\n"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    print('{"loggedIn": true, "email": "me@example.com"}'); sys.exit(0)
if a[:2] == ["mcp", "list"]:
    print("Checking MCP server health...\n\nclaude.ai Gmail: https://g - ! Needs authentication"); sys.exit(0)
if a[:2] == ["mcp", "login"]:
    print('Starting authentication for "' + a[2] + '"...', flush=True)
    if not os.isatty(0):  # what the real CLI (2.1.280) does
        print("Couldn't complete authentication: stdin isn't a terminal"); sys.exit(1)
    if flag("fail"):  # a sign-in that ends badly: the reason is one line on stdout, as the real one prints it
        time.sleep(0.5)
        print("Couldn't complete authentication: the server refused the request (invalid_client)"); sys.exit(1)
    with open(os.path.join(here, "login.pid"), "w") as f:
        f.write(str(os.getpid()))
    u = "https://example.invalid/authorize?state=abc.redirect.localhost.51580.callback"
    print("Visit this URL to authorize:\n  \x1b]8;;" + u + "\x1b\\\x1b[94m" + u + "\x1b[39m\x1b]8;;\x1b\\\n", flush=True)
    print("Waiting for authorization... (^C to cancel)", flush=True)
    time.sleep(60 if flag("hang") else 1.5)
    print("Authentication successful. Connected to " + a[2] + "."); sys.exit(0)
print("fake claude: unexpected " + " ".join(a)); sys.exit(9)
'''


def api(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_until(cond, secs=20):
    for _ in range(int(secs * 5)):
        if cond():
            return True
        time.sleep(0.2)
    return False


def wait_step(step, secs=20):
    check(wait_until(lambda: not api(f"/api/connect/{step}")[1]["running"], secs), f"{step} has ended")
    return api(f"/api/connect/{step}")[1]


def alive(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in r.stdout


tmp = Path(tempfile.mkdtemp(prefix="openloops-connect-win-"))
say(f"fresh install in {tmp}")
shutil.copytree(REPO / "openloops", tmp / "openloops")
shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
tpl = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
tpl.update(agent="claude", miro_source="server", slack_source="plugin")
(tmp / "config.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
bin_ = tmp / "bin"
bin_.mkdir()
(bin_ / "fake_claude.py").write_text(FAKE, encoding="utf-8")
# an npm-style shim: what PATH really holds for `claude` on Windows
(bin_ / "claude.cmd").write_text(f'@"{sys.executable}" "%~dp0fake_claude.py" %*\r\n', encoding="utf-8")
opened = tmp / "opened.txt"
# webbrowser hands a BROWSER script the link unquoted, and cmd.exe reads & and % in one, so the fake's link has neither
(bin_ / "browser.cmd").write_text(f'@echo %*>> "{opened}"\r\n', encoding="utf-8")
(tmp / "home").mkdir()
env = isolated_env(tmp, PATH=str(bin_) + os.pathsep + os.environ.get("PATH", ""), BROWSER=str(bin_ / "browser.cmd"))
LINK = "https://example.invalid/authorize?state=abc.redirect.localhost.51580.callback"
log = tmp / "state" / "connect-miro.log"
out = tmp / "state" / "connect-miro.out"
calls = lambda: (bin_ / "calls.txt").read_text().splitlines() if (bin_ / "calls.txt").exists() else []

srv, PORT = start_app(tmp, env)
try:
    # -------------------------------------------------------------- a sign-in that waits in the browser
    (bin_ / "hang").touch()
    code, r = api("/api/connect/miro", {})
    check(code == 200 and r.get("started") is True, "POST /api/connect/miro starts a run")
    rid = r["run_id"]
    check(wait_until(lambda: api("/api/connect/miro")[1].get("url") == LINK, 15),
          "the sign-in link the CLI printed reaches the page as the fallback link (the CLI saw a real terminal)")
    s = api("/api/connect/miro")[1]
    check(s["running"] is True and s["last"] == "Waiting for authorization... (^C to cancel)",
          f"the status carries the CLI's last line while it waits ({s['last']!r})")
    check(wait_until(lambda: opened.exists() and opened.read_text().strip() == LINK, 5),
          "the link was opened in the browser, once, by the app")
    text = log.read_text(encoding="utf-8")
    check(text.startswith("$ claude mcp login ") and "--no-browser" in text.splitlines()[0],
          "the log starts with the command as it was run")
    check("Visit this URL to authorize:" in text and "\x1b" not in text,
          "the CLI's output is in the log without escape codes (no more '(running in its own window)')")
    check("https://example.invalid/authorize?(rest of the link not saved)" in text and "state=abc" not in text,
          "the link is kept in the log without its query")
    check(out.exists(), "the hidden console's output file sits beside the log while the command runs")
    pid = int((bin_ / "login.pid").read_text())
    check(alive(pid), "the fake CLI is waiting")
    # -------------------------------------------------------------- Stop this sign-in (#67) on this runner
    code, r = api("/api/connect/miro/stop", {"run_id": rid})
    check(code == 200 and r.get("ok") is True and r.get("running") is False, f"Stop this sign-in answers once the run has ended ({r})")
    check(wait_until(lambda: not alive(pid), 5), "Stop killed the CLI in its hidden console")
    s = api("/api/connect/miro")[1]
    check(s["running"] is False and s.get("stopped") is True and s["last"] == "stopped: Stop this sign-in was pressed",
          f"the status says why it stopped ({s['last']!r})")
    check(wait_until(lambda: not out.exists(), 5), "the output file is removed once the command has ended")
    (bin_ / "hang").unlink()
    (bin_ / "login.pid").unlink()
    opened.unlink()

    # -------------------------------------------------------------- a sign-in that fails: the reason reaches the page
    (bin_ / "fail").touch()
    code, r = api("/api/connect/miro", {})
    check(code == 200 and r.get("started") is True, "a new run starts after a stopped one")
    s = wait_step("miro")
    check(s["rc"] == 1 and s["last"] == "Couldn't complete authentication: the server refused the request (invalid_client)",
          f"#27 part 1: a failed sign-in's reason is the status's last line, for the Console and the page ({s['last']!r})")
    check(not s.get("url") and not opened.exists(), "no link, so nothing was opened")
    check(not out.exists(), "the output file is gone after a failure too")
    (bin_ / "fail").unlink()

    # -------------------------------------------------------------- a sign-in that finishes
    code, r = api("/api/connect/miro", {})
    check(code == 200 and r.get("started") is True, "a run starts after a failed one")
    s = wait_step("miro")
    check(s["rc"] == 0 and s["last"].startswith("Authentication successful. Connected to ") and s.get("url") == LINK,
          f"a sign-in that finishes: exit code 0, the CLI's last line, the link kept for the page ({s['last']!r})")
    check(opened.read_text().strip() == LINK, "the browser was opened once for it")
    check(not out.exists(), "the output file is gone after a success")
    check(sum(1 for c in calls() if c.startswith("mcp | login | ")) == 3, "the fake CLI was asked to sign in three times, nothing else ran it")
finally:
    stop(srv)
    shutil.rmtree(tmp, ignore_errors=True)
say("all ok")
