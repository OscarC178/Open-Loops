"""Claude setup buttons: the `claude mcp list` parser, login_cmd() per step and route, and /api/connect/<step>.

    python3 tests/test_connect.py    # fast; no Slack/Gmail/Claude. Temp install, spare port, fake `claude`.

The API half puts a fake `claude` first on PATH and points BROWSER at a script that only writes down the
link it was given, so nothing signs in to anything and no browser window opens. The fake refuses
`mcp login` when stdin is not a terminal, as the real CLI (2.1.280) does, so a pass also proves the app
runs it on a pseudo-terminal. That half is skipped on Windows, where the app gives the CLI a console window.
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORT = 0  # set by start_app(): the port our own server says it bound, never just one that looked free
t0 = time.time()
from _helpers import isolate_this_process, isolated_env, start_app  # noqa: E402
isolate_this_process("openloops-connect-parent-")  # doctor reads ~/.claude.json in-process: a throwaway one
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
check(doctor.parse_mcp_list("claude.ai Gmail: https://x - \u2714\ufe0f Connected") == {"claude.ai Gmail": "connected"},
      "an emoji-style mark (with U+FE0F) still reads as connected")
check(doctor.parse_mcp_list("") == {} and doctor.parse_mcp_list("No MCP servers configured.") == {}, "nothing listed -> {}")

# ---------------------------------------------------------------- route
servers = doctor.parse_mcp_list(SAMPLE)
check(doctor.route("slack", servers) == ("plugin", "connected", "plugin:slack:slack"), "Slack via the plugin")
check(doctor.route("gmail", servers) == ("connector", "connected", "claude.ai Gmail"), "Gmail via the claude.ai connector")
check(doctor.route("miro", servers) == ("plugin", "auth", "plugin:miro:miro"), "Miro: both need signing in -> the plugin, as the jobs expect")
check(doctor.route("miro", {"plugin:miro:miro": "auth", "claude.ai Miro": "connected"}) == ("connector", "connected", "claude.ai Miro"),
      "a connected route beats one that needs signing in")
check(doctor.route("miro", {"miro": "connected"}) == ("server", "connected", "miro"), "Miro as a user-added server named miro")
check(doctor.route("slack", {"plugin:slack-v2:slack": "auth"}) == ("plugin", "auth", "plugin:slack-v2:slack"), "a renamed plugin still reads as the plugin route, name kept")
check(doctor.route("slack", {"my-slack": "connected"}) == ("", "", ""), "an unknown server named like slack is not guessed at")
check(doctor.route("gmail", {}) == ("", "", ""), "no server -> no route")

# ---------------------------------------------------------------- doctor rows when the listing fails
_real = (doctor.run, doctor.shutil.which)
doctor.shutil.which = lambda _: "/usr/local/bin/claude"
doctor.run = lambda args, timeout=60: ((0, "2.1.0 (Claude Code)") if args[1] == "--version"  # installed and starts (#16)
                                       else (0, '{"loggedIn": true, "email": "me@example.com"}') if args[1] == "auth"
                                       else (1, "Checking MCP server health...\nError: timed out"))
rows = {}
doctor.claude_steps(rows.setdefault("steps", []))
rows = {r["id"]: r for r in rows["steps"]}
check(all("connect" not in rows[k] and "Couldn't ask Claude" in rows[k]["fix"] and "timed out" in rows[k]["fix"]
          for k in ("slack", "gmail", "miro")), "a failed claude mcp list offers no Install/Connect button, says so")
doctor.run = lambda args, timeout=60: ((0, '{"loggedIn": true}') if args[1] == "auth" else (0, "No MCP servers configured."))
rows = {}
doctor.claude_steps(rows.setdefault("steps", []))
rows = {r["id"]: r for r in rows["steps"]}
check(rows["slack"].get("connect") == "slack_install", "an empty listing that ran fine still offers Install Slack plugin")
doctor.run = lambda args, timeout=60: ((0, "2.1.0 (Claude Code)") if args[1] == "--version"  # installed and starts (#16)
                                       else (0, '{"loggedIn": true}') if args[1] == "auth"
                                       else (1, "claude.ai Gmail: https://g - ! Needs authentication\nError: timed out"))
rows = {}
doctor.claude_steps(rows.setdefault("steps", []))
rows = {r["id"]: r for r in rows["steps"]}
check("connect" not in rows["slack"] and "Couldn't ask Claude" in rows["slack"]["fix"],
      "a listing that failed part-way: a service it did not print gets no Install button")
check(rows["gmail"].get("connect") == "gmail", "...while one it did print keeps its Connect button")
doctor.run, doctor.shutil.which = _real

# a check that takes a while must not write back a config.json it read before Settings were saved meanwhile
_cfgdir = Path(tempfile.mkdtemp(prefix="openloops-doctor-cfg-"))
_real_cfg, doctor.CONFIG = doctor.CONFIG, _cfgdir / "config.json"
doctor.CONFIG.write_text(json.dumps({"agent": "claude", "owner_name": "Old"}), encoding="utf-8")
def _slow_steps(steps):
    doctor.CONFIG.write_text(json.dumps({"agent": "claude", "owner_name": "Saved meanwhile"}), encoding="utf-8")
    return "", False, False, "plugin", False, "", {"slack": "plugin:slack:slack"}
_real_steps, doctor.claude_steps = doctor.claude_steps, _slow_steps
_real_name, agent.name = agent.name, lambda: "claude"
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    doctor.main()
saved = json.loads(doctor.CONFIG.read_text(encoding="utf-8"))
check(saved.get("owner_name") == "Saved meanwhile" and saved.get("slack_source") == "plugin"
      and saved.get("claude_servers") == {"slack": "plugin:slack:slack"},
      f"doctor merges its own keys into config.json as it is now, keeping Settings saved meanwhile (got {saved})")
# config.json there but unreadable: doctor must leave it alone, not write its four keys over everything else
for label, prep in (("corrupt", lambda f: f.write_text('{"agent": "claude", "people": {', encoding="utf-8")),
                    ("unreadable", lambda f: (f.write_text('{"agent": "claude", "people": {"A": {}}}', encoding="utf-8"), f.chmod(0)))):
    if label == "unreadable" and (sys.platform == "win32" or os.geteuid() == 0):
        continue  # chmod 0 does not stop Windows or root from reading
    prep(doctor.CONFIG)
    before = (doctor.CONFIG.stat().st_mtime_ns, doctor.CONFIG.stat().st_size)
    with contextlib.redirect_stderr(io.StringIO()) as errbuf:
        doctor._save({"slack_source": "plugin"}, {"slack": "plugin:slack:slack"})
    doctor.CONFIG.chmod(0o600)
    check((doctor.CONFIG.stat().st_mtime_ns, doctor.CONFIG.stat().st_size) == before and "left as it is" in errbuf.getvalue(),
          f"{'an' if label[0] in 'aeiou' else 'a'} {label} config.json is left untouched by doctor's write, and it says why")
doctor.CONFIG, doctor.claude_steps, agent.name = _real_cfg, _real_steps, _real_name
shutil.rmtree(_cfgdir, ignore_errors=True)

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
cfg["slack_source"], cfg["claude_servers"] = "plugin", {"slack": "plugin:slack-v2:slack"}
check(agent.login_cmd("slack") == [["claude", "mcp", "login", "plugin:slack-v2:slack", "--no-browser"]], "a renamed server is signed in to by its listed name")
cfg["slack_source"] = "connector"
check(agent.login_cmd("slack")[0][3] == "claude.ai Slack", "a listed name for another route is ignored")
cfg["slack_source"], cfg["claude_servers"] = "plugin", {"slack": "plugin:slack:x & calc.exe"}
check(agent.login_cmd("slack")[0][3] == "plugin:slack:slack", "a listed name with shell characters is ignored")
# tool ids follow the same server: today's names give today's ids, a renamed server its own
cfg.pop("claude_servers")
for src_s, src_m, want in (("plugin", "plugin", ["mcp__plugin_slack_slack__slack_search_users", "mcp__claude_ai_Gmail__search_threads", "mcp__plugin_miro_miro"]),
                           ("connector", "connector", ["mcp__claude_ai_Slack__slack_search_users", "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Miro"]),
                           ("plugin", "server", ["mcp__plugin_slack_slack__slack_search_users", "mcp__claude_ai_Gmail__search_threads", "mcp__miro"])):
    cfg["slack_source"], cfg["miro_source"] = src_s, src_m
    got = agent._qualify(["slack.search_users", "gmail.search_threads", "miro.*"])
    check(got == want, f"tool ids for slack={src_s} miro={src_m} unchanged from the fixed prefixes (got {got})")
cfg.update(slack_source="plugin", miro_source="plugin", claude_servers={"slack": "plugin:slack-v2:slack", "miro": "plugin:miro-next:miro"})
got = agent._qualify(["slack.search_users", "miro.*"])
check(got == ["mcp__plugin_slack-v2_slack__slack_search_users", "mcp__plugin_miro-next_miro"],
      f"a renamed server's tools are allowed under its own name, as it is signed in to (got {got})")
cfg.pop("claude_servers")
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
if a == ["--version"]:  # doctor.py checks the CLI starts before anything else (#16)
    print("2.1.280 (Claude Code)"); sys.exit(0)
if a[:3] == ["plugin", "marketplace", "list"]:
    print("Configured marketplaces:\n\n  > claude-plugins-official\n"); sys.exit(0)
if a[:2] == ["plugin", "install"]:
    print("Installing plugin " + a[2] + "..."); print("Successfully installed plugin: " + a[2]); sys.exit(0)
here = os.path.dirname(__file__)
flag = lambda n: os.path.exists(os.path.join(here, n))
if a[:2] == ["auth", "status"]:
    print('{"loggedIn": true, "email": "me@example.com"}'); sys.exit(0)
if a[:2] == ["mcp", "list"]:  # Gmail's state is read first, then (if told to) the listing takes 3 s
    line = "claude.ai Gmail: https://g - " + ("\u2714 Connected" if flag("gmail_ok") else "! Needs authentication")
    if flag("slow"):
        time.sleep(3)
    print("Checking MCP server health...\n\n" + line); sys.exit(0)
if a[:2] == ["auth", "login"]:
    time.sleep(0.5); print("Login cancelled"); sys.exit(3)
if a[:2] == ["mcp", "login"]:
    print('Starting authentication for "' + a[2] + '"...')
    if not os.isatty(0):
        print("Couldn't complete authentication: stdin isn't a terminal"); sys.exit(1)
    u = "https://example.invalid/authorize?state=abc&redirect_uri=http%3A%2F%2Flocalhost%3A51580%2Fcallback"
    print("Visit this URL to authorize:\n  \x1b]8;;" + u + "\x1b\\\x1b[94m" + u + "\x1b[39m\x1b]8;;\x1b\\\n", flush=True)
    print("Waiting for authorization... (^C to cancel)", flush=True)
    time.sleep(60 if flag("hang") else 1.5)
    if a[2] == "claude.ai Gmail":
        open(os.path.join(here, "gmail_ok"), "w").close()
    print("Authentication successful. Connected to " + a[2] + "."); sys.exit(0)
print("fake claude: unexpected " + " ".join(a)); sys.exit(9)
'''.replace("PYTHON", sys.executable)


# In-process checks of openloops.app, run in the temp install (importing app writes config/state next to it).
# Each races or breaks something on purpose that is hard to reach over HTTP.
HARNESS = r'''
import os, sys, threading, time
sys.path.insert(0, os.getcwd())
from openloops import agent, app
which = sys.argv[1]

def settle(step, secs=10):
    end = time.time() + secs
    while app.connects[step]["running"] and time.time() < end:
        time.sleep(0.1)
    return not app.connects[step]["running"]

if which == "rollback":  # setup fails after the step is claimed: the claim must be let go
    log = app.connect_log("gmail")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.mkdir()  # a log that cannot be written
    ok, why = app.run_connect("gmail")
    assert not ok and why.startswith("could not start gmail"), (ok, why)
    assert app.connects["gmail"]["running"] is False, app.connects
    log.rmdir()
    agent.login_cmd = lambda step: [["true"]]
    ok, why = app.run_connect("gmail")
    assert ok, "still wedged after a failed start: " + why
    assert settle("gmail")
if which == "quit":
    # Quit lands while a worker is still getting ready: the worker must not start its command afterwards
    ready = threading.Event()
    agent.login_cmd = lambda step: (ready.wait(5), [["sleep", "30"]])[1]
    ok, why = app.run_connect("miro")
    assert ok, why
    app.quit_requested = True
    app.stop_connects()
    ready.set()
    assert settle("miro"), "the worker started its command after Quit"
    assert not app.connect_procs, app.connect_procs
    ok, why = app.run_connect("slack")
    assert not ok and "closing" in why, (ok, why)
    app.quit_requested = False
    # a child that closed its terminal but lives on stays visible to Quit until it has been waited for
    agent.login_cmd = lambda step: [["sh", "-c", "exec >/dev/null 2>&1 </dev/null; sleep 30"]]
    ok, why = app.run_connect("miro")
    assert ok, why
    time.sleep(1.5)
    assert "miro" in app.connect_procs, "a live child dropped out of connect_procs once its terminal closed"
    t = time.time()
    app.stop_connects()
    assert settle("miro", 10) and time.time() - t < 3, "Quit did not stop the child"
print("HARNESS OK " + which)
'''


def harness(which):
    r = subprocess.run([sys.executable, "-c", HARNESS, which], cwd=tmp, capture_output=True, text=True, timeout=60,
                       env=dict(env, OPENLOOPS_PORT="1"))
    return (r.stdout + r.stderr).strip()


def api(path, body=None, origin=None):
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
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
(tmp / "home").mkdir()  # HOME for the app and the harness: nothing is read from the developer's own ~
env = isolated_env(tmp, PATH=str(tmp / "bin") + os.pathsep + os.environ.get("PATH", ""),
                   BROWSER=str(tmp / "bin" / "browser"))
out = harness("rollback")
check(out.endswith("HARNESS OK rollback"), f"a setup step whose start fails is not left 'already running' ({out[-300:]})")
out = harness("quit")
check(out.endswith("HARNESS OK quit"), f"Quit owns every setup-step process, however late it starts or ends ({out[-300:]})")


srv, PORT = start_app(tmp, env)
try:

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
    check("state=abc" not in log and "https://example.invalid/authorize?(rest of the link not saved)" in log,
          "the log keeps the link's address but not its query")

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

    # four clicks at the same moment (two tabs, a double click): exactly one run starts. Four, not more:
    # the server's listen backlog is 5, and a burst past it is reset by the OS, which is not what this tests
    gate, got = threading.Barrier(4), []
    def click():
        gate.wait()
        got.append(api("/api/connect/slack", {})[1].get("started"))
    ts = [threading.Thread(target=click) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check(got.count(True) == 1 and got.count(False) == 3, f"simultaneous clicks start one run (got {got})")
    wait_step("slack")
    check(sum(c.startswith("mcp | login | plugin:slack:slack") for c in calls()) == 1, "and one sign-in command ran")

    # a check that started before a sign-in finished must not be cached over the fresh answer
    (tmp / "bin" / "slow").touch()
    old = []
    slow = threading.Thread(target=lambda: old.append(api("/api/doctor", {"force": True})[1]))
    slow.start()
    time.sleep(1)
    api("/api/connect/gmail", {})
    wait_step("gmail")
    (tmp / "bin" / "slow").unlink()
    slow.join()
    gm = lambda r: next(x for x in r["steps"] if x["id"] == "gmail")
    check(not gm(old[0])["ok"], "the check that started before the sign-in still says Gmail needs signing in")
    fresh = api("/api/doctor", {})[1]  # not forced: would come from the cache if the old answer had been kept
    check(gm(fresh)["ok"], "the next check is fresh: Gmail ticked")

    # cross-site POSTs are refused on every endpoint; this page's own origin and no origin at all are not
    code, out = api("/api/connect/login", {}, origin="http://evil.example")
    check(code == 403, "a POST from another site is refused (403)")
    code, _ = api("/api/refresh", {}, origin="null")
    check(code == 403, "a POST from an opaque origin is refused too")
    code, out = api("/api/bye", {"page": "x"}, origin=f"http://localhost:{PORT}")
    check(code == 200 and out["ok"], "the page's own origin gets through (close-tab beacon)")
    code, out = api("/api/bye", {"page": "x"})
    check(code == 200, "no Origin at all (--stop, scripts) gets through")

    # doctor (its own process) and Settings (the app) rewriting config.json at the same moment: no error, nothing lost
    WRITER = ("import os, sys\nsys.path.insert(0, os.getcwd())\nfrom openloops import doctor\n"
              "for i in range(int(sys.argv[1]), int(sys.argv[2])):\n"
              "    doctor._save({'miro_source': 'plugin'}, {'t%d' % i: 'plugin:slack:slack'})\nprint('WRITER OK')\n")
    codes, rounds, per = [], 3, 60
    for r in range(rounds):
        w = subprocess.Popen([sys.executable, "-c", WRITER, str(r * per), str((r + 1) * per)], cwd=tmp,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for i in range(r * per, (r + 1) * per):
            codes.append(api("/api/config", {"owner_name": f"n{i}"})[0])
        wout = w.communicate(timeout=60)[0]
        check(w.returncode == 0 and "WRITER OK" in wout, f"round {r + 1}: doctor's writer ran without an error ({wout.strip()[-300:]})")
    saved = json.loads((tmp / "config.json").read_text(encoding="utf-8"))
    lost = [f"t{i}" for i in range(rounds * per) if f"t{i}" not in (saved.get("claude_servers") or {})]
    check(set(codes) == {200}, f"every Settings save succeeded (codes {sorted(set(codes))})")
    check(not lost and saved.get("owner_name") == f"n{rounds * per - 1}",
          f"no write lost on either side (lost {len(lost)} of doctor's keys; owner_name={saved.get('owner_name')!r})")
    check(not list(tmp.glob("config.json.*.tmp")), "no temp files left behind")

    # Start over with a config.json that cannot be read: refuse BEFORE deleting anything (Codex review, #21)
    cfg = tmp / "config.json"
    good = cfg.read_text(encoding="utf-8")
    cfg.write_text('{"agent": "claude", "people": {', encoding="utf-8")
    state_before = (tmp / "state.json").read_text(encoding="utf-8") if (tmp / "state.json").exists() else None
    code, out = api("/api/reset", {})
    state_after = (tmp / "state.json").read_text(encoding="utf-8") if (tmp / "state.json").exists() else None
    check(code == 500 and not out.get("ok") and state_after == state_before,
          f"reset with an unreadable config.json refuses and deletes nothing (code {code}, state changed={state_after != state_before})")
    cfg.write_text(good, encoding="utf-8")

    # Grok: no setup buttons
    api("/api/config", {"agent": "grok"})
    code, out = api("/api/connect/login", {})
    check(code == 400 and "Grok" in out.get("error", ""), "with Grok selected the endpoint refuses")

    # quitting stops a sign-in still waiting in the browser, and the server does not wait for it
    api("/api/config", {"agent": "claude"})
    (tmp / "bin" / "hang").touch()
    api("/api/connect/miro", {})
    time.sleep(1)
    waiting = lambda: subprocess.run(["pgrep", "-f", str(tmp / "bin" / "claude")], capture_output=True).returncode == 0
    check(waiting(), "a sign-in is waiting")
    api("/api/quit", {})
    try:
        srv.wait(10)
    except subprocess.TimeoutExpired:
        raise SystemExit("FAIL: the server kept waiting for the sign-in after Quit")
    time.sleep(0.5)
    check(not waiting(), "Quit stopped the waiting sign-in and the server exited")
    say("PASS")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
