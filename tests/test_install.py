"""Install buttons: install_cmd() per agent and platform, prereq(), the doctor row, and /api/connect/install.

    python3 tests/test_install.py    # fast; downloads nothing, installs nothing real. Temp install, temp HOME, spare port.

The API half puts a fake `curl` first on a PATH that has no real `claude`. The app runs the real command line
(`curl -fsSL https://claude.ai/install.sh | bash`); the fake curl answers with a tiny installer script instead of
Anthropic's, which the real bash runs: it drops a fake `claude` into $HOME/.local/bin (HOME is a temp folder),
where the real installer puts it. So a pass proves the shown command is the one that runs, that the app finds a
CLI in a folder that was not on its PATH, and that the re-check goes green. Skipped on Windows (own console window).
"""
import json, os, shlex, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
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
LINES = {  # agent -> (Mac script, what runs it, Windows script, the CLI, vendor)
    "claude": ("https://claude.ai/install.sh", "bash", "https://claude.ai/install.ps1", "claude", "Anthropic"),
    "codex": ("https://chatgpt.com/codex/install.sh", "sh", "https://chatgpt.com/codex/install.ps1", "codex", "OpenAI"),
    "grok": ("https://x.ai/cli/install.sh", "bash", "https://x.ai/cli/install.ps1", "grok", "xAI")}
SHOWN = lambda ag: agent.install_cmd(ag, win=False)["command"]  # what the row shows on a Mac: every command that runs
for ag, (url, shell, ps_url, cli, vendor) in LINES.items():
    mac = agent.install_cmd(ag, win=False)
    sc = mac["script"]
    check(sc == str(agent.ROOT / "state" / "install" / f"{ag}-install.sh") and mac["steps"] == [
              ("download", ["curl", "-fsSL", "-o", sc, url]), ("download", ["test", "-s", sc]),
              ("install", [shell, sc]), ("check", [cli, "--version"])],
          f"{ag} on a Mac: download to a file, check it is not empty, run it, then {cli} --version")
    check(mac["command"] == " &&\n".join(shlex.join(a) for _, a in mac["steps"]) and mac["needs"] == ["curl", "bash"]
          and mac["vendor"] == vendor and mac["source"].startswith("https://"), f"{ag} on a Mac: shown exactly as it runs, joined by &&")
    win = agent.install_cmd(ag, win=True)
    ws = win["script"]
    check([k for k, _ in win["steps"]] == ["download", "install", "check"] and ws.endswith(f"{ag}-install.ps1")
          and win["steps"][0][1][-1] == f"Invoke-WebRequest -UseBasicParsing -Uri '{ps_url}' -OutFile '{ws}'"
          and win["steps"][1][1] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ws]
          and win["command"] == "\n".join(subprocess.list2cmdline(a) for _, a in win["steps"]) and win["needs"] == ["powershell"],
          f"{ag} on Windows: Invoke-WebRequest to a file, then PowerShell -File, shown one per line")
    check(mac["id"] != win["id"] and mac["id"] != agent.install_cmd("grok" if ag != "grok" else "claude", win=False)["id"],
          f"{ag}: the command id differs per AI and platform")
_root = agent.ROOT
agent.ROOT = Path("/Users/o'brien/OpenLoops")
check("-OutFile '/Users/o''brien/OpenLoops/state/install/claude-install.ps1'" in agent.install_cmd("claude", win=True)["steps"][0][1][-1],
      "a quote in the folder name is doubled for PowerShell")
agent.ROOT = _root
check(agent.install_cmd("nope") is None, "an agent with no known installer -> None")
cfg = {"agent": "grok"}
_real_cfg = agent._cfg
agent._cfg = lambda: cfg
check(agent.install_cmd(win=False)["agent"] == "grok", "no agent named -> the selected one")
cfg["agent"] = "claude"
check(agent.install_cmd(win=False)["steps"][0][1][-1] == LINES["claude"][0], "Claude selected -> Claude's installer")

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
check(not row["ok"] and row["connect"] == "install" and row["command"] == SHOWN("claude")
      and "Press Install Claude" in row["fix"] and "Anthropic" in row["fix"], "missing -> Install button, the command, who it comes from")
row = doctor.install_row("Claude", False, found=True)
check(row["connect"] == "install" and "won't start" in row["fix"], "found but won't start -> red, Install again")
have = {"bash"}
row = doctor.install_row("Claude", False)
check("connect" not in row and "curl" in row["fix"] and "Ask IT" in row["fix"], "installer cannot run here -> no button, says what to do")
have = {"curl", "bash"}
cfg["agent"] = "grok"
row = doctor.install_row("Grok", False)
check(row["connect"] == "install" and row["command"] == SHOWN("grok") and "xAI" in row["fix"], "Grok selected -> Grok's installer")
agent.shutil.which, agent._cfg, agent.WIN = _which, _real_cfg, sys.platform == "win32"

# ---------------------------------------------------------------- /api/connect/install
if sys.platform == "win32":
    say("skip /api/connect/install: Windows runs the installer in its own console window")
    raise SystemExit(0)

# Fake installers: the fake curl saves one of these (picked by the file bin/mode) where -o says, as the real one would.
FAKE_CLAUDE = '''#!PYTHON
import sys
a = sys.argv[1:]
if a == ["--version"]:
    print("fake claude 0.0.0"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    print('{"loggedIn": false}'); sys.exit(1)
sys.exit(0)
'''
PUT_CLAUDE = 'mkdir -p "$HOME/.local/bin"\ncat > "$HOME/.local/bin/claude" <<\'EOC\'\n' + FAKE_CLAUDE + 'EOC\nchmod +x "$HOME/.local/bin/claude"\n'
INSTALLERS = {
    "ok": 'echo "Downloading Claude Code..."\nsleep 1\n' + PUT_CLAUDE +
          'echo "Docs: https://code.claude.com/docs/en/setup"\necho "Claude Code successfully installed"\n',
    # a CLI that lands on PATH but will not start: the row must not go green
    "stub": PUT_CLAUDE,  # what arrives before a download is cut off: would install claude if it were ever run
    "broken": PUT_CLAUDE.replace('sys.exit(0)\nif a[:2]', 'sys.exit(1)\nif a[:2]', 1) + 'echo "Claude Code successfully installed"\n',
}
FAKE_CURL = r'''#!PYTHON
# Fake curl for test_install.py: records the call; saves a fake installer where -o says, or fails like the real one.
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.txt"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
out = sys.argv[sys.argv.index("-o") + 1]
mode = open(os.path.join(here, "mode")).read().strip()
if mode == "fail":  # curl -f on a 404: nothing saved
    sys.stderr.write("curl: (22) The requested URL returned error: 404\n"); sys.exit(22)
if mode == "partial":  # the connection drops after a stub that would install claude arrived: curl fails
    open(out, "w").write(open(os.path.join(here, "installer-stub.sh")).read())
    sys.stderr.write("curl: (18) end of file with 1234 bytes remaining to read\n"); sys.exit(18)
open(out, "w").write(open(os.path.join(here, "installer-" + mode + ".sh")).read())
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


tmp = Path(tempfile.mkdtemp(prefix="openloops-install-")).resolve()
say(f"fresh install in {tmp}")
shutil.copytree(REPO / "openloops", tmp / "openloops")
shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
tpl = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
tpl.update(agent="claude")
(tmp / "config.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
(tmp / "home").mkdir()
(tmp / "bin").mkdir()
(tmp / "bin" / "curl").write_text(FAKE_CURL, encoding="utf-8")
os.chmod(tmp / "bin" / "curl", 0o755)
for mode, body in INSTALLERS.items():
    (tmp / "bin" / f"installer-{mode}.sh").write_text(body.replace("PYTHON", sys.executable), encoding="utf-8")
use = lambda mode: (tmp / "bin" / "mode").write_text(mode)
fake = tmp / "home" / ".local" / "bin" / "claude"
PATH = os.pathsep.join([str(tmp / "bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
if shutil.which("claude", path=PATH):  # a claude in the system folders would make the "missing" half meaningless
    raise SystemExit(f"FAIL: a real claude is on the test PATH ({shutil.which('claude', path=PATH)}); cannot test the install")
calls = lambda: (tmp / "bin" / "calls.txt").read_text().splitlines() if (tmp / "bin" / "calls.txt").exists() else []
AT = lambda ag: SHOWN(ag).replace(str(agent.ROOT), str(tmp))  # the command as the app in tmp shows it
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
    check(row(doc).get("command") == AT("claude") and row(doc)["fix"].startswith("Open Loops couldn't find Claude"),
          f"...and shows every command first (got {row(doc).get('command')!r})")
    shown = {"agent": row(doc)["agent"], "command_id": row(doc)["command_id"]}  # what the page sends back with the press
    check(shown["agent"] == "claude" and len(shown["command_id"]) == 16, "the row names its agent and command id")
    login = next(x for x in doc["steps"] if x["id"] == "login")
    check("connect" not in login and login["fix"].startswith("Install Claude first"), "the sign-in row points at Install, offers no button yet")
    code, s = api("/api/connect/install")
    check(code == 200 and s["running"] is False and s["rc"] is None and s["command"] == AT("claude") and s["command_id"] == shown["command_id"],
          "GET /api/connect/install: idle, with the commands it would run")
    code, _ = api("/api/connect/install", shown, origin="http://evil.example")
    check(code == 403, "a POST from another site is refused")
    code, out = api("/api/connect/install", {})
    check(code == 409 and not out["started"], "a press that names no agent and command is refused")
    api("/api/config", {"agent": "grok"})  # another tab switches to Grok; this tab still shows Install Claude
    code, out = api("/api/connect/install", shown)
    check(code == 409 and out.get("said", "").startswith("The AI chosen in Settings changed"), "a stale tab's press is refused, in plain words")
    code, s = api("/api/connect/install")
    check(s["agent"] == "grok" and s["command"] == AT("grok"), "idle status offers the AI chosen now")
    api("/api/config", {"agent": "claude"})
    check(calls() == [], "nothing downloaded or run before the button was pressed")

    # a download that fails outright
    use("fail")
    code, out = api("/api/connect/install", shown)
    check(code == 200 and out == {"started": True}, "POST /api/connect/install starts it")
    s = wait_install()
    check(s["rc"] == 22 and s["why"] == "download" and "404" in s["last"], f"a failed download stops at the download (got {s['rc']}, {s['why']!r}, {s['last']!r})")
    check(s["said"].startswith("Claude's installer couldn't download") and "curl" not in s["said"],
          f"the page gets a plain sentence, not curl's error (got {s['said']!r})")

    # a download cut off half-way: what arrived is never run, and nothing is added to PATH
    use("partial")
    api("/api/connect/install", shown)
    s = wait_install()
    check(s["rc"] == 18 and s["why"] == "download" and not fake.exists(), "a partial download is never run: no claude appeared")
    check(not (tmp / "state" / "install" / "claude-install.sh").exists(), "and the part that arrived is deleted")
    code, doc = api("/api/doctor", {})
    check(not row(doc)["ok"] and row(doc)["connect"] == "install", "the row stays red with its Install button")

    # the installer puts a claude on PATH that will not start: not green
    use("broken")
    api("/api/connect/install", shown)
    s = wait_install()
    check(fake.exists() and s["rc"] != 0 and s["why"] == "check" and "won't start" in s["said"], f"a CLI that won't start fails the check step (got {s['why']!r})")
    code, doc = api("/api/doctor", {})
    check(not row(doc)["ok"] and "won't start" in row(doc)["fix"] and row(doc)["connect"] == "install", "the row says it won't start, offers Install again")
    fake.unlink()

    # the real thing, with the fake installer
    use("ok")
    code, out = api("/api/connect/install", shown)
    check(out == {"started": True}, "pressed again: started")
    code, out = api("/api/connect/install", shown)
    check(out.get("started") is False and out.get("error") == "already running", "a second click while it runs starts nothing")
    api("/api/config", {"agent": "grok"})  # settings change mid-install: the status still reports the run's own command
    code, s = api("/api/connect/install")
    check(s["running"] and s["agent"] == "claude" and s["command"] == AT("claude"), "a running install reports its own command, not a recomputed one")
    api("/api/config", {"agent": "claude"})
    s = wait_install()
    check(s["rc"] == 0 and s["last"] == "fake claude 0.0.0" and not s.get("why"), f"install finished and claude --version answered (got {s['rc']}, {s['last']!r})")
    check(s["url"] == "", "an installer's output is not taken for a sign-in link")
    check(calls()[-1] == f"-fsSL -o {tmp}/state/install/claude-install.sh https://claude.ai/install.sh", f"curl saved Anthropic's installer, as shown (calls: {calls()[-1:]})")
    log = (tmp / "state" / "connect-install.log").read_text(encoding="utf-8")
    check(all("$ " + line.rstrip(" &") in log for line in AT("claude").splitlines()) and "Downloading Claude Code" in log,
          "state/connect-install.log has every command and what it printed")
    code, doc = api("/api/doctor", {})  # not forced: the finished install must have dropped the cached answer
    check(row(doc)["ok"] and "connect" not in row(doc), "the re-check finds the new claude in ~/.local/bin: green")

    # Grok selected: its own installer, shown the same way (not run: nothing to prove twice)
    api("/api/config", {"agent": "grok"})
    code, s = api("/api/connect/install")
    check(s["command"] == AT("grok"), "with Grok selected the install step runs xAI's script")
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
