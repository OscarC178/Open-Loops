"""Claude setup buttons: the `claude mcp list` parser, login_cmd() per step and route, and /api/connect/<step>.

    python3 tests/test_connect.py    # fast; no Slack/Gmail/Claude. Temp install, spare port, fake `claude`.

The API half puts a fake `claude` first on PATH and points BROWSER at a script that only writes down the
link it was given, so nothing signs in to anything and no browser window opens. The fake refuses
`mcp login` when stdin is not a terminal, as the real CLI (2.1.280) does, so a pass also proves the app
runs it on a pseudo-terminal. That half is skipped on Windows, where the app gives the CLI a console window.
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORT = 8805
t0 = time.time()
sys.path.insert(0, str(REPO))
from openloops import agent, doctor  # noqa: E402


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


# ---------------------------------------------------------------- parser
SAMPLE = """Checking MCP server health…

claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected
plugin:slack:slack: https://mcp.slack.com/mcp (HTTP) - ✔ Connected
plugin:miro:miro: https://mcp.miro.com/ (HTTP) - ! Needs authentication
claude.ai Miro: https://mcp.miro.com - ! Needs authentication
local-thing: npx -y some-server --flag - ✗ Failed to connect
"""
got = doctor.parse_mcp_list(SAMPLE)
check(got == {"claude.ai Gmail": "connected", "plugin:slack:slack": "connected", "plugin:miro:miro": "auth",
              "claude.ai Miro": "auth", "local-thing": "failed"}, f"parses the plain list, header skipped (got {got})")
coloured = ("\x1b[2mChecking MCP server health…\x1b[22m\r\n"
            "\x1b[1mclaude.ai Slack\x1b[22m: https://mcp.slack.com - \x1b[32m✔\x1b[39m Connected\r\n"
            "\x1b]8;;https://mcp.miro.com\x07plugin:miro:miro\x1b]8;;\x07: https://mcp.miro.com/ (HTTP) - \x1b[33m!\x1b[39m Needs authentication\r\n")
got = doctor.parse_mcp_list(coloured)
check(got == {"claude.ai Slack": "connected", "plugin:miro:miro": "auth"}, f"ANSI colours and links stripped (got {got})")
check(doctor.parse_mcp_list("claude.ai Gmail: https://x - √ Connected") == {"claude.ai Gmail": "connected"},
      "another glyph before the state still reads as connected (Windows consoles)")
check(doctor.parse_mcp_list("") == {} and doctor.parse_mcp_list("No MCP servers configured.") == {}, "nothing listed -> {}")

# ---------------------------------------------------------------- route
servers = doctor.parse_mcp_list(SAMPLE)
check(doctor.route("slack", servers) == ("plugin", "connected"), "Slack via the plugin")
check(doctor.route("gmail", servers) == ("connector", "connected"), "Gmail via the claude.ai connector")
check(doctor.route("miro", servers) == ("plugin", "auth"), "Miro: both need signing in -> the plugin, as the jobs expect")
check(doctor.route("miro", {"plugin:miro:miro": "auth", "claude.ai Miro": "connected"}) == ("connector", "connected"),
      "a connected route beats one that needs signing in")
check(doctor.route("miro", {"miro": "connected"}) == ("server", "connected"), "Miro as a user-added server named miro")
check(doctor.route("slack", {"plugin:slack-v2:slack": "auth"}) == ("plugin", "auth"), "a renamed plugin still reads as the plugin route")
check(doctor.route("slack", {"my-slack": "connected"}) == ("", ""), "an unknown server named like slack is not guessed at")
check(doctor.route("gmail", {}) == ("", ""), "no server -> no route")

# ---------------------------------------------------------------- login_cmd
cfg = {"agent": "claude"}
agent._cfg = lambda: cfg
agent._has_marketplace = lambda: True
agent.WIN = False
check(agent.login_cmd("login") == [["claude", "auth", "login"]], "login -> claude auth login")
check(agent.login_cmd("slack_install") == [["claude", "plugin", "install", "slack@claude-plugins-official"]],
      "slack_install with the marketplace known -> just the install")
agent._has_marketplace = lambda: False
check(agent.login_cmd("slack_install") == [["claude", "plugin", "marketplace", "add", "anthropics/claude-plugins-official"],
                                           ["claude", "plugin", "install", "slack@claude-plugins-official"]],
      "slack_install without it -> marketplace add first")
check(agent.login_cmd("slack") == [["claude", "mcp", "login", "plugin:slack:slack", "--no-browser"]], "Slack, plugin route (default)")
cfg["slack_source"] = "connector"
check(agent.login_cmd("slack") == [["claude", "mcp", "login", "claude.ai Slack", "--no-browser"]], "Slack, connector route")
check(agent.login_cmd("gmail") == [["claude", "mcp", "login", "claude.ai Gmail", "--no-browser"]], "Gmail -> claude.ai Gmail")
check(agent.login_cmd("miro") == [["claude", "mcp", "login", "plugin:miro:miro", "--no-browser"]], "Miro, plugin route (default)")
for src, name in (("connector", "claude.ai Miro"), ("server", "miro")):
    cfg["miro_source"] = src
    check(agent.login_cmd("miro") == [["claude", "mcp", "login", name, "--no-browser"]], f"Miro, {src} route")
agent.WIN = True
check(agent.login_cmd("gmail") == [["claude", "mcp", "login", "claude.ai Gmail"]], "Windows: no --no-browser (the CLI opens the browser)")
agent.WIN = sys.platform == "win32"
check(agent.login_cmd("nope") is None, "unknown step -> None")
cfg["agent"] = "grok"
check(all(agent.login_cmd(s) is None for s in agent.CONNECT_STEPS), "Grok -> None for every step")

# ---------------------------------------------------------------- /api/connect
if sys.platform == "win32":
    say("skip /api/connect: Windows runs the CLI in its own console window")
    raise SystemExit(0)

FAKE = r'''#!PYTHON
# Fake Claude CLI for test_connect.py: records each call, behaves like the real one where it matters.
import os, sys, time
a = sys.argv[1:]
with open(os.path.join(os.path.dirname(__file__), "calls.txt"), "a") as f:
    f.write(" | ".join(a) + "\n")
if a[:3] == ["plugin", "marketplace", "list"]:
    print("Configured marketplaces:\n\n  > claude-plugins-official\n"); sys.exit(0)
if a[:2] == ["plugin", "install"]:
    print("Installing plugin " + a[2] + "..."); print("Successfully installed plugin: " + a[2]); sys.exit(0)
if a[:2] == ["auth", "login"]:
    time.sleep(0.5); print("Login cancelled"); sys.exit(3)
if a[:2] == ["mcp", "login"]:
    print('Starting authentication for "' + a[2] + '"...')
    if not os.isatty(0):
        print("Couldn't complete authentication: stdin isn't a terminal"); sys.exit(1)
    u = "https://example.invalid/authorize?state=abc&redirect_uri=http%3A%2F%2Flocalhost%3A51580%2Fcallback"
    print("Visit this URL to authorize:\n  \x1b]8;;" + u + "\x1b\\\x1b[94m" + u + "\x1b[39m\x1b]8;;\x1b\\\n", flush=True)
    print("Waiting for authorization... (^C to cancel)", flush=True)
    time.sleep(1.5)
    print("Authentication successful. Connected to " + a[2] + "."); sys.exit(0)
print("fake claude: unexpected " + " ".join(a)); sys.exit(9)
'''.replace("PYTHON", sys.executable)


def api(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_step(step, secs=20):
    for _ in range(secs * 5):
        s = api(f"/api/connect/{step}")[1]
        if not s["running"]:
            return s
        time.sleep(0.2)
    raise SystemExit(f"FAIL: {step} still running after {secs}s")


tmp = Path(tempfile.mkdtemp(prefix="openloops-connect-"))
say(f"fresh install in {tmp}")
shutil.copytree(REPO / "openloops", tmp / "openloops")
shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
tpl = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
tpl.update(agent="claude", miro_source="server", slack_source="plugin")
(tmp / "config.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
(tmp / "bin").mkdir()
(tmp / "bin" / "claude").write_text(FAKE, encoding="utf-8")
(tmp / "bin" / "browser").write_text(f"#!/bin/sh\necho \"$1\" >> '{tmp / 'opened.txt'}'\n", encoding="utf-8")
for f in ("claude", "browser"):
    os.chmod(tmp / "bin" / f, 0o755)
calls = lambda: (tmp / "bin" / "calls.txt").read_text().splitlines() if (tmp / "bin" / "calls.txt").exists() else []
env = dict(os.environ, OPENLOOPS_PORT=str(PORT), PATH=str(tmp / "bin") + os.pathsep + os.environ.get("PATH", ""),
           BROWSER=str(tmp / "bin" / "browser"))
srv = subprocess.Popen([sys.executable, "-m", "openloops.app", "--no-browser"], cwd=tmp, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        if socket.socket().connect_ex(("127.0.0.1", PORT)) == 0:
            break
        time.sleep(0.1)
    else:
        raise SystemExit("FAIL: openloops.app did not come up")

    code, _ = api("/api/connect/bogus")
    check(code == 404, "GET an unknown step -> 404")
    code, out = api("/api/connect/bogus", {})
    check(code == 400 and not out["started"], "POST an unknown step -> 400, nothing started")
    code, out = api("/api/connect/miro")
    check(code == 200 and out["running"] is False and out["rc"] is None, "status before any run: idle")

    # Miro on the "server" route: mcp login on a pseudo-terminal, link read from the output and opened once
    code, out = api("/api/connect/miro", {})
    check(code == 200 and out == {"started": True}, "POST /api/connect/miro answers at once with started")
    code, out = api("/api/connect/miro", {})
    check(out.get("started") is False and out.get("error") == "already running", "a second click while it runs starts nothing")
    s = wait_step("miro")
    link = "https://example.invalid/authorize?state=abc&redirect_uri=http%3A%2F%2Flocalhost%3A51580%2Fcallback"
    check(s["rc"] == 0, f"mcp login exited 0 on a terminal (got rc={s['rc']}, last={s['last']!r})")
    check(s["url"] == link, f"the sign-in link was read out of the colour codes (got {s['url']!r})")
    check((tmp / "opened.txt").read_text().split() == [link], "the app opened the link in the browser, once")
    check(s["last"].startswith("Authentication successful"), f"last log line reported (got {s['last']!r})")
    check("mcp | login | miro | --no-browser" in calls(), f"the configured Miro route was used (calls: {calls()})")
    log = (tmp / "state" / "connect-miro.log").read_text(encoding="utf-8")
    check("\x1b" not in log and "Waiting for authorization" in log, "state/connect-miro.log kept, without escape codes")

    # install: marketplace already known -> install only, no browser
    api("/api/connect/slack_install", {})
    s = wait_step("slack_install")
    check(s["rc"] == 0 and "plugin | install | slack@claude-plugins-official" in calls()
          and not any(c.startswith("plugin | marketplace | add") for c in calls()), "Slack plugin installed without re-adding the marketplace")
    check(s["url"] == "" and len((tmp / "opened.txt").read_text().split()) == 1, "an install opens no browser")

    # a sign-in that is cancelled: exit code and last line come back for the page to show
    api("/api/connect/login", {})
    s = wait_step("login")
    check(s["rc"] == 3 and s["last"] == "Login cancelled", f"a cancelled sign-in reports its exit code (got {s['rc']}, {s['last']!r})")

    # Grok: no setup buttons
    api("/api/config", {"agent": "grok"})
    code, out = api("/api/connect/login", {})
    check(code == 400 and "Grok" in out.get("error", ""), "with Grok selected the endpoint refuses")
    say("PASS")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
