"""Install buttons: install_cmd() per agent and platform, prereq(), the doctor row, and /api/connect/install.

    python3 tests/test_install.py    # fast; downloads nothing, installs nothing real. Temp install, temp HOME, spare port.

The API half puts a fake `curl` first on a PATH that has no real `claude`. The app runs the real commands the row
shows (curl -o <script>, test -s, bash <script>, claude --version); the fake curl saves a tiny installer in place of
Anthropic's, which the real bash runs. Fixtures: a failed download, a download cut off after a stub (never run), a
CLI that installs but won't start, an installer that asks a question (fails at once), one that never finishes
(stopped at a 6-second deadline), Quit during an install, and the good case, which drops a fake `claude` into
$HOME/.local/bin (HOME is a temp folder) and turns the row green. The runner's Windows branch is checked in-process.
The API half is skipped on Windows (the fake installers are shell scripts).
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
              ("download", ["mkdir", "-p", str(agent.ROOT / "state" / "install")]),
              ("download", ["curl", "-fsSL", "-o", sc, url]), ("download", ["test", "-s", sc]),
              ("install", [shell, sc]), ("check", [cli, "--version"])],
          f"{ag} on a Mac: make the folder, download to a file, check it is not empty, run it, then {cli} --version")
    check(mac["command"] == " &&\n".join(shlex.join(a) for _, a in mac["steps"]) and mac["needs"] == ["curl", "bash"]
          and mac["vendor"] == vendor and mac["source"].startswith("https://"), f"{ag} on a Mac: shown exactly as it runs, joined by &&")
    win = agent.install_cmd(ag, win=True)
    ws = win["script"]
    dl = win["steps"][0][1][-1]
    check([k for k, _ in win["steps"]] == ["download", "install", "check"] and ws.endswith(f"{ag}-install.ps1")
          and dl.startswith("$ErrorActionPreference = 'Stop'; New-Item -ItemType Directory -Force")
          and f"Invoke-WebRequest -UseBasicParsing -Uri '{ps_url}' -OutFile '{ws}'" in dl
          and dl.endswith(f"if ((Get-Item -LiteralPath '{ws}').Length -eq 0) {{ throw 'The download was empty.' }}")
          and win["steps"][1][1] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ws]
          and win["needs"] == ["powershell"], f"{ag} on Windows: a download that fails as a whole (empty too), then PowerShell -File")
    lines = win["command"].splitlines()
    check(lines[0] == "& {" and lines[-1] == "}" and [l.strip() for l in lines[1:5]] == dl.split("; ")
          and lines[5].strip() == f"powershell -NoProfile -ExecutionPolicy Bypass -File '{ws}'"
          and lines[6].strip().startswith("if ($LASTEXITCODE -ne 0) { throw") and lines[7].strip() == f"{cli} --version",
          f"{ag} on Windows: the fallback is one block with the app's gates (stops on a failed or empty download, or a failed installer)")
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
check(agent.install_cmd(win=False)["steps"][1][1][-1] == LINES["claude"][0], "Claude selected -> Claude's installer")

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

# ---------------------------------------------------------------- the page's Install button (node, if installed)
# Label, progress and the command shown come from the doctor row (and, while running, from the run's status), never from
# cached settings: a tab whose settings are stale must not say "Install Claude" while it sends Grok's identity.
_node = shutil.which("node")
if _node:
    import re
    page = (REPO / "openloops" / "index.html").read_text(encoding="utf-8")
    grab = lambda start: next(l for l in page.splitlines() if l.startswith(start))
    fn = page[page.index("function connectBtn("):page.index("async function connectStep(")]
    js = "\n".join([grab("const esc="), grab("const CONNECT_LABEL="), grab("const CONN="), grab("const AI_NAME="), fn, """
let C={agent:'claude'};const agentLabel=()=>'Claude';   // stale settings: this tab still thinks Claude
const row={connect:'install',agent:'grok',command:'grok-cmd'};
const idle=connectBtn(row,true);
CONN.install={busy:true,msg:'Installing Claude.',agent:'claude',command:'claude-cmd'};
const busy=connectBtn(row,true);
console.log(JSON.stringify({idle,busy}));"""])
    out = json.loads(subprocess.run([_node, "-e", js], capture_output=True, text=True, check=True).stdout)
    check("Install Grok" in out["idle"] and "Install Claude" not in out["idle"] and "grok-cmd" in out["idle"],
          "page: the button is labelled from the row's agent, not cached settings")
    check("claude-cmd" in out["busy"] and "grok-cmd" not in out["busy"], "page: while running it shows the run's own command")
else:
    say("skip the page check: node not installed")

# ---------------------------------------------------------------- the runner's Windows branch, in-process
# Windows installs capture output to the log instead of a console window. The branch is taken here with app.WIN forced
# on (CREATE_NO_WINDOW is 0 off Windows), so the log header, the capture and the timeout note are checked on any OS.
from openloops import app  # noqa: E402
_logdir = Path(tempfile.mkdtemp(prefix="openloops-runner-"))
app.WIN, app.connects["t"] = True, {}
lg = _logdir / "t.log"
lg.write_text("", encoding="utf-8")
argv = [sys.executable, "-c", "print('installing'); import sys; sys.exit(3)"]
check(app._install_one("t", argv, lg, time.time() + 20) == (3, False), "Windows branch: exit code comes back")
text = lg.read_text(encoding="utf-8")
check(text.startswith("$ " + subprocess.list2cmdline(argv)) and "installing" in text, "Windows branch: command and output land in the log")
app.INSTALL_TIMEOUT_S = 1
pidf = _logdir / "child.pid"
sleeper = [sys.executable, "-c", f"import os, time; open({str(pidf)!r}, 'w').write(str(os.getpid())); time.sleep(30)"]
check(app._install_one("t", sleeper, lg, time.time() + 1) == (-1, True), "Windows branch: the deadline is reported as a timeout")
check("stopped: the install did not finish within 1 seconds" in lg.read_text(encoding="utf-8"), "Windows branch: the log says why")


def _gone(pid, secs=5):
    """True once no process has this pid (os.kill with signal 0 only asks; it raises when there is none)."""
    for _ in range(secs * 10):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # someone else's process now holds the pid: ours is gone
            return True
        time.sleep(0.1)
    return False


if sys.platform != "win32":  # os.kill(pid, 0) is a liveness probe only off Windows
    check(_gone(int(pidf.read_text())), "Windows branch: the timed-out child is actually gone")
check("t" not in app.connect_procs, "Windows branch: _reap stopped tracking it")
app.WIN = sys.platform == "win32"
shutil.rmtree(_logdir, ignore_errors=True)
_ic = agent.install_cmd()
app.quit_requested = True  # #21's quit ownership covers the install too: once quitting, nothing new starts
check(app.run_install({"agent": _ic["agent"], "command_id": _ic["id"]}) == (False, "Open Loops is closing", 400)
      and "install" not in app.connects, "an Install pressed while the app is quitting starts nothing")
app.quit_requested = False

# ---------------------------------------------------------------- /api/connect/install
if sys.platform == "win32":
    say("skip /api/connect/install: the fake installers are shell scripts")
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
    "stub": PUT_CLAUDE,
    # asks a question: first on the terminal, then on stdin. Neither exists for it, so it must fail at once, not wait
    "prompt": 'echo "Install Claude Code? [y/N]"\nif read -r a < /dev/tty; then exit 0; fi\n'
              'read -r a || { echo "no answer"; exit 1; }\n[ "$a" = y ] || exit 1\n' + PUT_CLAUDE,
    # never finishes: stopped at the deadline (OPENLOOPS_INSTALL_TIMEOUT_S), or by Quit
    "hang": 'echo "Downloading Claude Code..."\nsleep 120\n' + PUT_CLAUDE,  # what arrives before a download is cut off: would install claude if it were ever run
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
TIMEOUT_S = 6  # the app's install deadline for this run; the fake installers other than "hang" take about a second
env = dict(os.environ, OPENLOOPS_PORT=str(PORT), PATH=PATH, HOME=str(tmp / "home"), BROWSER="true",
           OPENLOOPS_INSTALL_TIMEOUT_S=str(TIMEOUT_S))
waiting = lambda: subprocess.run(["pgrep", "-f", f"{tmp}/state/install/"], capture_output=True).returncode == 0
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
    check(not row(doc)["ok"] and row(doc)["connect"] == "install" and row(doc)["fix"].startswith("Open Loops couldn't find Claude"),
          "the row stays red with Install again: a CLI that failed --version was not added to the app's PATH")
    fake.unlink()

    # an installer that asks a question: no terminal, empty stdin, so it fails straight away instead of hanging
    use("prompt")
    t = time.time()
    api("/api/connect/install", shown)
    s = wait_install()
    check(time.time() - t < TIMEOUT_S - 1 and s["rc"] == 1 and s["why"] == "install" and not fake.exists(),
          f"a prompting installer fails at once (rc {s['rc']}, {time.time() - t:.1f}s, last {s['last']!r})")
    check(s["said"].startswith("Claude's installer stopped with an error"), "...and the page says so plainly")

    # an installer that never finishes: stopped at the deadline, the log says why, nothing left running
    use("hang")
    api("/api/connect/install", shown)
    s = wait_install(TIMEOUT_S + 10)
    log = (tmp / "state" / "connect-install.log").read_text(encoding="utf-8")
    check(s["rc"] == -1 and s["why"] == "timeout" and f"stopped: the install did not finish within {TIMEOUT_S} seconds" in log,
          f"the deadline stops it and says so (got {s['rc']}, {s['why']!r})")
    check(s["said"].startswith(f"The install took longer than {TIMEOUT_S} seconds") and not waiting() and not fake.exists(),
          "the page says it took too long; no installer is left running")

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
    check(all("$ " + line.rstrip(" &") in log for line in AT("claude").splitlines()[:-1]) and "Downloading Claude Code" in log
          and f"$ {fake} --version" in log, "state/connect-install.log has every command (the check by the path it found) and what it printed")
    code, doc = api("/api/doctor", {})  # not forced: the finished install must have dropped the cached answer
    check(row(doc)["ok"] and "connect" not in row(doc), "the re-check finds the new claude in ~/.local/bin: green")

    # Grok selected: its own installer, shown the same way (not run: nothing to prove twice)
    api("/api/config", {"agent": "grok"})
    code, s = api("/api/connect/install")
    check(s["command"] == AT("grok"), "with Grok selected the install step runs xAI's script")
    code, out = api("/api/connect/login", {})
    check(code == 400, "the sign-in steps stay Claude-only")

    # the Mac fallback, pasted by hand into a fresh shell: works with no state/install/ yet, and a cut-off download
    # stops it before anything runs, as with the button
    shutil.rmtree(tmp / "state" / "install", ignore_errors=True)
    fake.unlink()  # left by the good install above
    use("partial")
    r = subprocess.run(["bash", "-c", AT("claude")], env=env, capture_output=True, text=True)
    check(r.returncode == 18 and not fake.exists(), f"the pasted fallback stops at a cut-off download (rc {r.returncode})")
    use("ok")
    r = subprocess.run(["bash", "-c", AT("claude")], env=dict(env, PATH=str(fake.parent) + os.pathsep + PATH), capture_output=True, text=True)
    check(r.returncode == 0 and fake.exists() and "fake claude" in r.stdout, "the pasted fallback installs from scratch (mkdir included)")
    fake.unlink()

    # Quit while an install runs: the installer is stopped and the server exits without waiting for it
    api("/api/config", {"agent": "claude"})
    use("hang")
    shown = {k: api("/api/connect/install")[1][k] for k in ("agent", "command_id")}
    code, out = api("/api/connect/install", shown)
    time.sleep(1)
    check(out == {"started": True} and waiting(), "an install is running")
    api("/api/quit", {})
    try:
        srv.wait(10)
    except subprocess.TimeoutExpired:
        raise SystemExit("FAIL: the server kept waiting for the install after Quit")
    time.sleep(0.5)
    check(not waiting() and not fake.exists(), "Quit stopped the install and the server exited")
    say("PASS")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
