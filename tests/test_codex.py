"""Codex as the third agent (#12): tool names, the job home, the headless run, the checklist rows, the setup buttons.

    python3 tests/test_codex.py    # fast; no ChatGPT, Gmail or Slack. Temp install, spare port, fake `codex`.

The first half checks agent.py's mapping in this process. The rest copies the app to a temp folder (so state/ and
config.json are its own), puts a fake `codex` first on PATH and a fake home with a fake ~/.codex/auth.json, and runs
the real code against them: agent.run() through `codex exec`, doctor.codex_steps() for every row, and the app's
/api/connect/<step> buttons. The fake writes down every call (argv, CODEX_HOME, what came in on stdin) and reads its
whole stdin before answering, so a run that left stdin open would hang instead of passing. Skipped on Windows past
the first half: the fake is a script with a #! line, and the sign-in runs in a console window there.
"""
import base64, json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
t0 = time.time()
sys.path.insert(0, str(REPO))
from openloops import agent, doctor  # noqa: E402


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


# ---------------------------------------------------------------- mapping (this process)
cfg = {"agent": "codex", "codex_model": "gpt-5.6-sol", "codex_effort": "low", "model": "sonnet", "effort": "xhigh"}
agent._cfg = lambda: cfg
check(agent.name() == "codex" and agent.display_name() == "Codex", "agent codex is accepted and called Codex")
got = agent._qualify(["gmail.search_threads", "gmail.get_thread", "gmail.create_draft", "gmail.reply", "gmail.get_profile",
                      "slack.read_channel", "slack.read_thread", "slack.search_public_and_private", "slack.search_users",
                      "slack.send_message", "slack.send_message_draft", "miro.*"])
check(got == ["gmail.search_emails", "gmail.read_email_thread", "gmail.create_draft", "gmail.send_email", "gmail.get_profile",
              "slack.slack_read_channel", "slack.slack_read_thread", "slack.slack_search_public_and_private",
              "slack.slack_search_users", "slack.slack_send_message", "slack.slack_send_message_draft", "miro"],
      f"logical tools map to the ChatGPT connector names from #18 (got {got})")
check(agent.model() == "gpt-5.6-sol" and agent.effort() == "low", "Codex reads codex_model / codex_effort, not Claude's keys")
cfg["agent"] = "claude"
check(agent.model() == "sonnet" and agent.effort() == "xhigh", "...and Claude still reads model / effort")
check(agent._qualify(["gmail.search_threads"]) == ["mcp__claude_ai_Gmail__search_threads"], "Claude's tool ids are unchanged")
cfg["agent"] = "codex"
pre = agent.codex_preamble(["gmail.search_threads", "gmail.reply", "slack.read_channel"])
check("- gmail.search_emails (the instructions below may call it search_threads)" in pre
      and "reply_message_id" in pre and "- slack.slack_read_channel\n" in pre and "Do not run shell commands" in pre,
      "the preamble lists only the job's tools, by connector name, with the short name the prompts use")
check("Use no tools at all" in agent.codex_preamble([]), "a job with no tools is told to use none")
check(agent.login_cmd("login") == [[agent.cli(), "login"]] and agent.login_cmd("gmail") is None, "Sign in runs codex login")
check(agent.connect_steps() == ("login", "gmail", "slack") and agent.connect_url("gmail") == "https://chatgpt.com/apps"
      and agent.connect_url("login") is None, "Gmail / Slack buttons open ChatGPT's apps page; no Miro or plugin step")
check(agent.install_cmd("codex")["cli"] == "codex", "the Install button knows Codex (from #32)")
check(doctor.parse_probe("GMAIL: CONNECTED\nSLACK: NOT-CONNECTED\nSLACK_ID: NONE") ==
      {"gmail": True, "slack": False, "miro": None, "slack_id": ""}, "the probe's answer is read line by line")
check(doctor.parse_probe("**GMAIL:** CONNECTED\n- SLACK: CONNECTED\nSLACK_ID: U01ABCDEF9")["slack_id"] == "U01ABCDEF9",
      "...with markdown around it, and the Slack id")
check(doctor.parse_probe("I could not tell.") == {"gmail": None, "slack": None, "miro": None, "slack_id": ""},
      "an answer without the lines is unknown, not 'not connected'")
cfg["agent"] = "grok"
check(agent.connect_steps() == () and agent.login_cmd("login") is None, "Grok still has no setup buttons")

if sys.platform == "win32":
    say("skip the fake-CLI half: Windows cannot run the #! fake, and signs in in a console window")
    raise SystemExit(0)

# ---------------------------------------------------------------- the fake CLI and a temp install
FAKE = r'''#!PYTHON
# Fake Codex CLI for test_codex.py: records each call; answers like codex-cli 0.156.1 where it matters.
import json, os, sys, time
a = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
flag = lambda n: os.path.exists(os.path.join(here, n))
rec = {"argv": a, "codex_home": os.environ.get("CODEX_HOME", ""), "cwd": os.getcwd()}
data = ""
if a[:1] == ["exec"]:
    rec["stdin_is_tty"] = os.isatty(0)
    data = sys.stdin.read()  # returns only at the end of input: a stdin left open hangs here, and the test times out
    home = os.environ.get("CODEX_HOME", "")
    rec.update(stdin_len=len(data), stdin_head=data[:3000], stdin_tail=data[-100:],
               auth_link=os.path.realpath(os.path.join(home, "auth.json")) if home else "",
               cache_seeded=os.path.isdir(os.path.join(home, "cache", "codex_apps_tools")))
    try:
        rec["config"] = open(os.path.join(home, "config.toml"), encoding="utf-8").read()
    except OSError:
        rec["config"] = None
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps(rec) + "\n")
if a == ["--version"]:
    print("codex-cli 0.156.1"); sys.exit(0)
if a[:2] == ["login", "status"]:
    mode = open(os.path.join(here, "mode")).read().strip() if flag("mode") else ""
    if mode == "chatgpt":
        print("Logged in using ChatGPT"); sys.exit(0)
    if mode == "apikey":
        print("Logged in using an API key - sk-proj-***ABCD"); sys.exit(0)
    print("Not logged in"); sys.exit(1)
if a == ["login"]:
    print("Starting local login server on http://localhost:1455.\nIf your browser did not open, navigate to this URL to "
          "authenticate:\n\nhttps://auth.openai.example/oauth/authorize?state=xyz&code_challenge=abc\n", flush=True)
    time.sleep(1); print("Successfully logged in"); sys.exit(0)
if a[:1] == ["exec"]:
    out = a[a.index("-o") + 1]
    ev = lambda **e: print(json.dumps(e), flush=True)
    ev(type="thread.started", thread_id="t1"); ev(type="turn.started")
    if flag("slow"):
        time.sleep(30)
    if flag("limit"):
        ev(type="error", message="You've hit your usage limit. Try again in 3 hours.")
        ev(type="turn.failed", error={"message": "You've hit your usage limit."}); sys.exit(1)
    call = lambda t: ev(type="item.completed", item={"type": "mcp_tool_call", "server": "codex_apps", "tool": t, "status": "completed"})
    if "GMAIL: CONNECTED or NOT-CONNECTED" in data:
        g, s = flag("gmail_ok"), flag("slack_ok")
        if g:
            call("gmail.get_profile")
        if s:
            call("slack.slack_read_user_profile")
        text = ("GMAIL: " + ("CONNECTED" if g else "NOT-CONNECTED") + "\nSLACK: " + ("CONNECTED" if s else "NOT-CONNECTED")
                + "\nSLACK_ID: " + ("U0TESTSELF1" if s else "NONE"))
        if "MIRO: CONNECTED or NOT-CONNECTED" in data:
            text += "\nMIRO: CONNECTED"
    else:
        if flag("rogue"):
            ev(type="item.completed", item={"type": "command_execution", "command": "cat ~/.ssh/id_rsa", "status": "completed"})
        text = "OK"
    ev(type="item.completed", item={"type": "agent_message", "text": text})
    ev(type="turn.completed", usage={"input_tokens": 27000, "cached_input_tokens": 13000, "output_tokens": 50})
    open(out, "w", encoding="utf-8").write(text)
    sys.exit(0)
print("fake codex: unexpected " + " ".join(a)); sys.exit(9)
'''.replace("PYTHON", sys.executable)


def id_token(email):
    """An unsigned JWT with an email claim, shaped like the one in ~/.codex/auth.json."""
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return b({"alg": "none"}) + "." + b({"email": email}) + ".sig"


tmp = Path(tempfile.mkdtemp(prefix="openloops-codex-"))
say(f"fresh install in {tmp}")
shutil.copytree(REPO / "openloops", tmp / "openloops")
shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
tpl = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
tpl.update(agent="codex", owner_name="Tester")
(tmp / "config.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
home, fbin = tmp / "home", tmp / "bin"
(home / ".codex").mkdir(parents=True)
fbin.mkdir()
(fbin / "codex").write_text(FAKE, encoding="utf-8")
(fbin / "browser").write_text(f"#!/bin/sh\necho \"$1\" >> '{tmp / 'opened.txt'}'\n", encoding="utf-8")
for f in ("codex", "browser"):
    os.chmod(fbin / f, 0o755)
env = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
env.update(PATH=str(fbin) + os.pathsep + os.environ.get("PATH", ""), HOME=str(home), BROWSER=str(fbin / "browser"))

# The in-install checks: one script, run with the temp install as its root and the fake home as HOME.
HARNESS = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, os.getcwd())
from openloops import agent, doctor
from openloops.paths import ROOT
BIN, HOME = Path(sys.argv[1]), Path(os.environ["HOME"])
JOB = ROOT / "state" / "codex-home"
AUTH = HOME / ".codex" / "auth.json"

def check(cond, what):
    if not cond:
        print("FAIL: " + what, flush=True); sys.exit(1)
    print("ok   " + what, flush=True)

def calls():
    f = BIN / "calls.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
execs = lambda: [c for c in calls() if c["argv"][:1] == ["exec"]]
flag = lambda n, on=True: (BIN / n).touch() if on else (BIN / n).unlink(missing_ok=True)

def auth(mode, email="me@example.com"):
    doc = {"auth_mode": mode, "OPENAI_API_KEY": "sk-test" if mode == "apikey" else None,
           "tokens": {"id_token": sys.argv[2], "access_token": "a", "refresh_token": "r"} if mode == "chatgpt" else None}
    AUTH.write_text(json.dumps(doc))
    (BIN / "mode").write_text(mode)

def rows(recheck=False):
    steps = []
    out = doctor.codex_steps(steps, recheck)
    return {r["id"]: r for r in steps}, out

# ---- agent.run() through `codex exec`
auth("chatgpt")
check(agent.name() == "codex", "config.json agent codex is what the jobs see")
prompt = "Find my open loops. " + "x" * 300000 + " END-OF-PROMPT"
p = agent.run(prompt, ["gmail.search_threads", "slack.read_channel"])
c = execs()[-1]
a = c["argv"]
check(p.returncode == 0 and p.stdout == "OK", f"the run's stdout is the final message from -o (got {p.returncode}, {p.stdout!r})")
check(a[:7] == ["exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "-C"] and a[-1] == "-",
      f"codex exec, read-only sandbox, nothing saved, prompt from stdin (argv {a})")
check(a[a.index("-m") + 1] == "gpt-5.6-sol" and 'model_reasoning_effort="low"' in a and "features.shell_tool=false" in a,
      "the template's model and effort, and the shell tool off, on the command line")
check(c["codex_home"] == str(JOB) and c["cwd"] == str(ROOT), f"CODEX_HOME is the job home state/codex-home (got {c['codex_home']})")
check(Path(a[a.index("-C") + 1]) == JOB / "work" and (JOB / "work").is_dir(), "the run works in an empty folder of its own")
check(c["stdin_is_tty"] is False and c["stdin_len"] == len(agent.codex_preamble(["gmail.search_threads", "slack.read_channel"])) + len(prompt)
      and c["stdin_tail"].endswith("END-OF-PROMPT"), "a 300 KB prompt arrives whole on stdin, which is then closed")
check(c["stdin_head"].startswith("[Open Loops: an unattended run.") and "gmail.search_emails" in c["stdin_head"]
      and "slack.slack_read_channel" in c["stdin_head"], "the preamble with the allowed tools comes first")
cfgt = c["config"] or ""
for line in ('model = "gpt-5.6-sol"', 'model_reasoning_effort = "low"', "project_doc_max_bytes = 0", "[features]",
             "memories = false", "shell_tool = false", "[apps.gmail]", "[apps.slack]", 'default_tools_approval_mode = "auto"'):
    check(line in cfgt, f"job config.toml has {line}")
check(c["auth_link"] == str(AUTH.resolve()) and (JOB / "auth.json").is_symlink(), "auth.json is a link to the user's own")
check(not list(JOB.glob("codex-last-*.txt")), "the -o file is removed after the run")
check("codex: tools used: none" in p.stderr and "tokens in 27000 (cached 13000)" in p.stderr, "stderr ends with tools and tokens")
flag("rogue")
p = agent.run("hello", ["gmail.search_threads"])
flag("rogue", False)
check("WARNING: used a tool the job did not list: command_execution" in p.stderr, "a tool off the list is flagged in the log")
cj = json.loads((ROOT / "config.json").read_text())
(ROOT / "config.json").write_text(json.dumps(dict(cj, codex_model="", codex_effort="")))
agent.run("hello", [])
a2, c2 = execs()[-1]["argv"], execs()[-1]["config"]
check("-m" not in a2 and not any("model_reasoning_effort" in x for x in a2) and "model = " not in c2,
      "blank model and effort leave Codex's own defaults")
(ROOT / "config.json").write_text(json.dumps(cj))
alt = HOME / "alt-codex"
alt.mkdir()
(alt / "auth.json").write_text(AUTH.read_text())
os.environ["CODEX_HOME"] = str(alt)
agent.run("hello", [])
check(execs()[-1]["auth_link"] == str((alt / "auth.json").resolve()), "a user CODEX_HOME is where auth.json comes from")
del os.environ["CODEX_HOME"]
agent.run("hello", [])
check(execs()[-1]["auth_link"] == str(AUTH.resolve()), "...and back to ~/.codex without it")

# ---- the checklist
(BIN / "mode").unlink()
AUTH.unlink()
n = len(execs())
r, out = rows()
check(r["claude"]["ok"] and r["claude"]["title"] == "Codex is installed", "Codex is installed (the fake answers --version)")
check(not r["login"]["ok"] and r["login"].get("connect") == "login" and "ChatGPT sign-in page" in r["login"]["fix"]
      and "codex login --device-auth" in r["login"]["fix"], "signed out: Sign in button, device sign-in as the fallback")
check(r["gmail"]["fix"] == "Sign in to ChatGPT first (the row above)." and "connect" not in r["gmail"], "sources wait for sign-in")
check(len(execs()) == n, "no Codex run while signed out")
auth("apikey")
r, _ = rows()
check(not r["login"]["ok"] and r["login"].get("connect") == "login" and r["login"]["fix"].startswith(
      "Codex is signed in with an API key, which can't use Gmail or Slack. Sign in with your ChatGPT account instead"),
      "API-key sign-in: red, says why, offers Sign in")
check(len(execs()) == n, "no Codex run with an API key (connectors can't work)")

auth("chatgpt")
r, _ = rows()
check(r["login"]["ok"] and r["login"]["title"] == "Signed in to ChatGPT as me@example.com", "signed in with ChatGPT, email shown")
check(len(execs()) == n + 1 and not execs()[-1]["cache_seeded"], "one probe run, on a job home with no connector list yet")
check(not r["gmail"]["ok"] and "getting your ChatGPT connections ready" in r["gmail"]["fix"] and "connect" not in r["gmail"],
      "a cold first run that tried no tool is 'not ready yet', not 'not connected'")
rows()
check(len(execs()) == n + 2, "...and is not kept: the next check asks again")

(HOME / ".codex" / "cache" / "codex_apps_tools").mkdir(parents=True)
(HOME / ".codex" / "cache" / "codex_apps_tools" / "abc.json").write_text('{"tools": []}')
flag("gmail_ok")
r, _ = rows(recheck=True)
check(execs()[-1]["cache_seeded"] and (JOB / "cache" / "codex_apps_tools" / "abc.json").exists(),
      "the job home is seeded with the user's connector list")
check(r["gmail"]["ok"] and not r["slack"]["ok"] and r["slack"].get("connect") == "slack"
      and "ChatGPT's apps page" in r["slack"]["fix"], "Gmail green; Slack red with Connect Slack (ChatGPT's apps page)")
m = len(execs())
rows()
rows(recheck=True)
check(len(execs()) == m, "the answer is reused: a plain check, and a forced one within 30 s, run nothing")
probe = json.loads(doctor.CODEX_PROBE.read_text())
doctor.CODEX_PROBE.write_text(json.dumps(dict(probe, at=probe["at"] - 60)))
rows()
check(len(execs()) == m, "a plain check a minute later still reuses a working answer")
rows(recheck=True)
check(len(execs()) == m + 1, "Check again after 30 s asks Codex afresh")

flag("slack_ok")
age = lambda secs: doctor.CODEX_PROBE.write_text(json.dumps(dict(json.loads(doctor.CODEX_PROBE.read_text()),
                                                                 at=time.time() - secs)))
age(60)  # past the 30 s floor, so the forced check below asks again
cj = json.loads((ROOT / "config.json").read_text())
cj["slack_self_id"] = ""
(ROOT / "config.json").write_text(json.dumps(cj))
import io, contextlib
m = len(execs())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    doctor.main(detect=True, recheck=True)
res = json.loads(buf.getvalue().strip().splitlines()[-1])
st = {s["id"]: s for s in res["steps"]}
check(res["agent"] == "codex" and res["all_ok"] and st["slack"]["ok"] and st["gmail"]["ok"], f"all green with both connected ({res})")
check(st["self"]["ok"] and "U0TESTSELF1" in st["self"]["title"] and json.loads((ROOT / "config.json").read_text())["slack_self_id"] == "U0TESTSELF1",
      "the Slack id comes from the same probe and is saved")
check(len(execs()) == m + 1, "one Codex run for the whole checklist, Slack id included")
check(not st["miro"]["ok"] and st["miro"]["fix"].startswith("Miro isn't available with Codex"), "Miro: not available with Codex")

(HOME / ".codex" / "config.toml").write_text('model = "x"\n[mcp_servers.miro]\nurl = "https://mcp.miro.com/"\n'
                                              '[mcp_servers.miro.env]\nA = "1"\n[mcp_servers.other]\nurl = "https://o"\n')
r, out = rows()
check(r["miro"]["ok"] and out[4] is True, "a Miro server the user added to Codex is asked about and used")
cfgt = execs()[-1]["config"]
check("[mcp_servers.miro]" in cfgt and '[mcp_servers.miro.env]' in cfgt and "mcp_servers.other" not in cfgt
      and 'model = "x"' not in cfgt, "only the user's Miro tables are copied into the job config")
(HOME / ".codex" / "config.toml").unlink()

doctor.PROBE_TIMEOUT_S = 2
flag("slow")
r, _ = rows(recheck=True)
flag("slow", False)
check("took too long" in r["gmail"]["fix"] and "connect" not in r["gmail"] and not r["gmail"]["ok"],
      "a probe that runs out of time says so, with no Connect button")
m = len(execs())
rows()
check(len(execs()) == m + 1, "...and is not kept")
flag("limit")
age(60)  # the last kept answer (the Miro one) is seconds old: past the 30 s floor, so this asks again
r, _ = rows(recheck=True)
flag("limit", False)
check("Codex allowance is used up" in r["slack"]["fix"] and "connect" not in r["slack"], "a spent ChatGPT allowance is named")

print("HARNESS OK", flush=True)
'''

r = subprocess.run([sys.executable, "-c", HARNESS, str(fbin), id_token("me@example.com")], cwd=tmp, env=env,
                   capture_output=True, text=True, timeout=240)
for ln in (r.stdout + r.stderr).strip().splitlines():
    say("  " + ln)
check(r.returncode == 0 and r.stdout.strip().endswith("HARNESS OK"), "agent.run() and the checklist against the fake codex")


# ---------------------------------------------------------------- the app's buttons
def free_port():
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def start_server():
    """As test_connect.py: start on a port that was free a moment ago and read which one it says it bound."""
    import select
    for _ in range(5):
        p = subprocess.Popen([sys.executable, "-m", "openloops.app", "--no-browser"], cwd=tmp,
                             env=dict(env, OPENLOOPS_PORT=str(free_port()), PYTHONUNBUFFERED="1"),
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        line = p.stdout.readline() if select.select([p.stdout], [], [], 15)[0] else ""
        m = re.match(r"Open Loops -> http://localhost:(\d+)", line)
        if m:
            return p, int(m.group(1))
        p.kill()
        p.wait()
    raise SystemExit("FAIL: openloops.app did not come up on a port of its own")


def api(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_step(step, secs=20):
    for _ in range(secs * 5):
        s = api(f"/api/connect/{step}")[1]
        if not s["running"]:
            return s
        time.sleep(0.2)
    raise SystemExit(f"FAIL: {step} still running after {secs}s")


srv, PORT = start_server()
try:
    code, out = api("/api/connect/login", {})
    check(code == 200 and out == {"started": True}, "Sign in starts with Codex selected")
    s = wait_step("login")
    fcalls = [json.loads(x)["argv"] for x in (fbin / "calls.jsonl").read_text().splitlines()]
    check(s["rc"] == 0 and ["login"] in fcalls, f"it ran codex login (rc {s['rc']}, last {s['last']!r})")
    check(s["url"].startswith("https://auth.openai.example/oauth/authorize?state=xyz"),
          "the sign-in link it printed is kept for the page's fallback link")
    log = (tmp / "state" / "connect-login.log").read_text(encoding="utf-8")
    check("state=xyz" not in log, "the log keeps the link's address but not its query")
    before = (tmp / "opened.txt").read_text().split() if (tmp / "opened.txt").exists() else []
    check(before == [], "codex login opens the browser itself: the app opens nothing")
    code, out = api("/api/connect/gmail", {})
    s = wait_step("gmail")
    check(code == 200 and s["rc"] == 0 and s.get("opened") and s["url"] == "https://chatgpt.com/apps",
          "Connect Gmail opens ChatGPT's apps page and says so")
    check((tmp / "opened.txt").read_text().split() == ["https://chatgpt.com/apps"], "the browser got chatgpt.com/apps")
    for step in ("slack_install", "miro"):
        code, out = api(f"/api/connect/{step}", {})
        check(code == 400 and "Codex" in out.get("error", ""), f"{step} is not a Codex step: refused")
    code, out = api("/api/config", {"codex_model": "gpt-6-astra", "codex_effort": "medium"})
    saved = json.loads((tmp / "config.json").read_text(encoding="utf-8"))
    check(code == 200 and saved["codex_model"] == "gpt-6-astra" and saved["codex_effort"] == "medium",
          "Settings can save Codex's model and effort")
    code, out = api("/api/doctor", {"force": True})
    check(code == 200 and out.get("agent") == "codex" and [s["id"] for s in out["steps"]][:2] == ["claude", "login"],
          "the app's connection check runs the Codex checklist")
    dl = (tmp / "state" / "logs" / "doctor-last.log").read_text(encoding="utf-8")
    check("--recheck" in dl.splitlines()[0], "a forced check asks Codex afresh (--recheck)")
    say("PASS")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
