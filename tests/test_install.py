"""Install buttons: install_cmd() per agent and platform, prereq(), the doctor row, and /api/connect/install.

    python3 tests/test_install.py    # fast; downloads nothing, installs nothing real. Temp install, temp HOME, spare port.

The API half puts a fake `curl` first on a PATH that has no real `claude`. The app runs the real command line
(`curl -fsSL https://claude.ai/install.sh | bash`); the fake curl answers with a tiny installer script instead of
Anthropic's, which the real bash runs: it drops a fake `claude` into $HOME/.local/bin (HOME is a temp folder),
where the real installer puts it. So a pass proves the shown command is the one that runs, that the app finds a
CLI in a folder that was not on its PATH, and that the re-check goes green. Skipped on Windows (own console window).
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
with socket.socket() as _s:  # a port nothing else holds
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
t0 = time.time()
sys.path.insert(0, str(REPO))
from openloops import agent, doctor  # noqa: E402


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


# ---------------------------------------------------------------- install_cmd
LINES = {"claude": ("curl -fsSL https://claude.ai/install.sh | bash", "irm https://claude.ai/install.ps1 | iex", "Anthropic"),
         "codex": ("curl -fsSL https://chatgpt.com/codex/install.sh | sh", "irm https://chatgpt.com/codex/install.ps1 | iex", "OpenAI"),
         "grok": ("curl -fsSL https://x.ai/cli/install.sh | bash", "irm https://x.ai/cli/install.ps1 | iex", "xAI")}
for ag, (unix, ps, vendor) in LINES.items():
    mac = agent.install_cmd(ag, win=False)
    check(mac["argv"] == ["bash", "-o", "pipefail", "-c", unix] and mac["command"] == unix and mac["needs"] == ["curl", "bash"]
          and mac["vendor"] == vendor and mac["source"].startswith("https://"), f"{ag} on a Mac: the vendor's script, shown as it runs")
    win = agent.install_cmd(ag, win=True)
    check(win["argv"] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]
          and win["command"] == subprocess.list2cmdline(win["argv"]) and win["needs"] == ["powershell"],
          f"{ag} on Windows: PowerShell's irm | iex, shown as it runs")
check(agent.install_cmd("nope") is None, "an agent with no known installer -> None")
cfg = {"agent": "grok"}
_real_cfg = agent._cfg
agent._cfg = lambda: cfg
check(agent.install_cmd(win=False)["command"] == LINES["grok"][0], "no agent named -> the selected one")
cfg["agent"] = "claude"
check(agent.install_cmd(win=False)["command"] == LINES["claude"][0], "Claude selected -> Claude's installer")

# ---------------------------------------------------------------- prereq
_which = shutil.which
have = {"curl", "bash", "winget"}
agent.shutil.which = lambda t: f"/x/{t}" if t in have else None
check(agent.prereq(win=False) == {"curl": True, "bash": True, "node": False, "npm": False, "brew": False},
      "Mac: curl and bash found, no Node, npm or Homebrew")
check(agent.prereq(win=True) == {"powershell": False, "node": False, "npm": False, "winget": True},
      "Windows: winget found, no PowerShell, no Node")

# ---------------------------------------------------------------- the doctor row
row = doctor.install_row("Claude", True)
check(row["ok"] and "connect" not in row and row["fix"] == "", "installed -> green, no button")
agent.WIN = False
row = doctor.install_row("Claude", False)
check(not row["ok"] and row["connect"] == "install" and row["command"] == LINES["claude"][0]
      and "Press Install Claude" in row["fix"] and "Anthropic" in row["fix"], "missing -> Install button, the command, who it comes from")
have = {"bash"}
row = doctor.install_row("Claude", False)
check("connect" not in row and "curl" in row["fix"] and "Ask IT" in row["fix"], "installer cannot run here -> no button, says what to do")
have = {"curl", "bash"}
cfg["agent"] = "grok"
row = doctor.install_row("Grok", False)
check(row["connect"] == "install" and row["command"] == LINES["grok"][0] and "xAI" in row["fix"], "Grok selected -> Grok's installer")
agent.shutil.which, agent._cfg, agent.WIN = _which, _real_cfg, sys.platform == "win32"

# ---------------------------------------------------------------- /api/connect/install
if sys.platform == "win32":
    say("skip /api/connect/install: Windows runs the installer in its own console window")
    raise SystemExit(0)

# What the fake curl hands to bash in place of the real installer. It writes a fake claude where the real one goes.
INSTALLER = r'''echo "Downloading Claude Code..."
sleep 1
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/claude" <<'EOC'
#!PYTHON
import sys
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print('{"loggedIn": false}'); sys.exit(1)
print("fake claude 0.0.0"); sys.exit(0)
EOC
chmod +x "$HOME/.local/bin/claude"
echo "Docs: https://code.claude.com/docs/en/setup"
echo "Claude Code successfully installed"
'''.replace("PYTHON", sys.executable)
FAKE_CURL = r'''#!PYTHON
# Fake curl for test_install.py: records the call; prints the fake installer, or fails like curl -f on a 404.
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.txt"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
if os.path.exists(os.path.join(here, "fail")):
    sys.stderr.write("curl: (22) The requested URL returned error: 404\n"); sys.exit(22)
sys.stdout.write(open(os.path.join(here, "installer.sh")).read())
'''.replace("PYTHON", sys.executable)


def api(path, body=None, origin=None):
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_install(secs=30):
    for _ in range(secs * 5):
        s = api("/api/connect/install")[1]
        if not s["running"]:
            return s
        time.sleep(0.2)
    raise SystemExit(f"FAIL: install still running after {secs}s")


tmp = Path(tempfile.mkdtemp(prefix="openloops-install-"))
say(f"fresh install in {tmp}")
shutil.copytree(REPO / "openloops", tmp / "openloops")
shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
tpl = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
tpl.update(agent="claude")
(tmp / "config.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
(tmp / "home").mkdir()
(tmp / "bin").mkdir()
(tmp / "bin" / "curl").write_text(FAKE_CURL, encoding="utf-8")
(tmp / "bin" / "installer.sh").write_text(INSTALLER, encoding="utf-8")
os.chmod(tmp / "bin" / "curl", 0o755)
PATH = os.pathsep.join([str(tmp / "bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
if shutil.which("claude", path=PATH):  # a claude in the system folders would make the "missing" half meaningless
    raise SystemExit(f"FAIL: a real claude is on the test PATH ({shutil.which('claude', path=PATH)}); cannot test the install")
calls = lambda: (tmp / "bin" / "calls.txt").read_text().splitlines() if (tmp / "bin" / "calls.txt").exists() else []
env = dict(os.environ, OPENLOOPS_PORT=str(PORT), PATH=PATH, HOME=str(tmp / "home"), BROWSER="true")
srv = subprocess.Popen([sys.executable, "-m", "openloops.app", "--no-browser"], cwd=tmp, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        if socket.socket().connect_ex(("127.0.0.1", PORT)) == 0:
            break
        time.sleep(0.1)
    else:
        raise SystemExit("FAIL: openloops.app did not come up")

    row = lambda r: next(x for x in r["steps"] if x["id"] == "claude")
    code, doc = api("/api/doctor", {"force": True})
    check(code == 200 and not row(doc)["ok"] and row(doc).get("connect") == "install", "the checklist says Claude is missing, offers Install")
    check(row(doc).get("command") == LINES["claude"][0], f"...and shows the exact command first (got {row(doc).get('command')!r})")
    code, s = api("/api/connect/install")
    check(code == 200 and s["running"] is False and s["rc"] is None and s["command"] == LINES["claude"][0],
          "GET /api/connect/install: idle, with the command it would run")
    code, _ = api("/api/connect/install", {}, origin="http://evil.example")
    check(code == 403, "a POST from another site is refused")
    check(calls() == [], "nothing downloaded or run before the button was pressed")

    # a download that fails: pipefail makes the whole line fail, so the row does not pretend
    (tmp / "bin" / "fail").touch()
    code, out = api("/api/connect/install", {})
    check(code == 200 and out == {"started": True}, "POST /api/connect/install starts it")
    s = wait_install()
    check(s["rc"] == 22 and "404" in s["last"], f"a failed download reports curl's exit code and error (got {s['rc']}, {s['last']!r})")
    (tmp / "bin" / "fail").unlink()

    # the real thing, with the fake installer
    code, out = api("/api/connect/install", {})
    check(out == {"started": True}, "pressed again: started")
    code, out = api("/api/connect/install", {})
    check(out.get("started") is False and out.get("error") == "already running", "a second click while it runs starts nothing")
    s = wait_install()
    check(s["rc"] == 0 and s["last"] == "Claude Code successfully installed", f"install finished (got {s['rc']}, {s['last']!r})")
    check(s["url"] == "", "an installer's output is not taken for a sign-in link")
    check(calls()[-1] == "-fsSL https://claude.ai/install.sh", f"curl fetched Anthropic's installer, as shown (calls: {calls()})")
    log = (tmp / "state" / "connect-install.log").read_text(encoding="utf-8")
    check("$ bash -o pipefail -c 'curl -fsSL https://claude.ai/install.sh | bash'" in log and "Downloading Claude Code" in log,
          "state/connect-install.log has the command and what it printed")
    code, doc = api("/api/doctor", {})  # not forced: the finished install must have dropped the cached answer
    check(row(doc)["ok"] and "connect" not in row(doc), "the re-check finds the new claude in ~/.local/bin: green")

    # Grok selected: its own installer, shown the same way (not run: nothing to prove twice)
    api("/api/config", {"agent": "grok"})
    code, s = api("/api/connect/install")
    check(s["command"] == LINES["grok"][0], "with Grok selected the install step runs xAI's script")
    code, out = api("/api/connect/login", {})
    check(code == 400, "the sign-in steps stay Claude-only")

    api("/api/quit", {})
    try:
        srv.wait(10)
    except subprocess.TimeoutExpired:
        raise SystemExit("FAIL: the server did not quit")
    say("PASS")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
