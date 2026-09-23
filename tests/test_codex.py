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
      and "reply_message_id" in pre and "- slack.slack_read_channel\n" in pre and "do not run shell commands" in pre,
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
# It honours the run's allow-list the way the real CLI was measured to (apps._default off, an app on, a tool off),
# fetches its connector list into CODEX_HOME/cache during a run when none is there (as the real one does), and can be
# told by flag files to misbehave: call a send tool anyway, echo the question, report a 401, run out of allowance.
import json, os, re, sys, time
a = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
flag = lambda n: os.path.exists(os.path.join(here, n))
home = os.environ.get("CODEX_HOME", "")
rec = {"argv": a, "codex_home": home, "cwd": os.getcwd()}
data, cfg = "", ""
TOOLS = {"connector_g": ["gmail.get_profile", "gmail.search_emails", "gmail.read_email_thread", "gmail.create_draft",
                         "gmail.send_email", "gmail.delete_emails"],
         "asdk_app_s": ["slack.slack_read_user_profile", "slack.slack_list_user_channels", "slack.slack_read_channel",
                        "slack.slack_search_users", "slack.slack_send_message", "slack.slack_send_message_draft"],
         "connector_drive": ["google_drive.search"]}
if a[:1] == ["exec"]:
    rec["stdin_is_tty"] = os.isatty(0)
    data = sys.stdin.read()  # returns only at the end of input: a stdin left open hangs here, and the test times out
    try:
        cfg = open(os.path.join(home, "config.toml"), encoding="utf-8").read()
    except OSError:
        cfg = ""
    cache = os.path.join(home, "cache", "codex_apps_tools")
    rec.update(stdin_len=len(data), stdin_head=data[:3000], stdin_tail=data[-100:], config=cfg,
               auth_link=os.path.realpath(os.path.join(home, "auth.json")) if home else "",
               creds=os.path.exists(os.path.join(home, ".credentials.json")),
               cache_had=sorted(os.listdir(cache)) if os.path.isdir(cache) else [],
               account=json.load(open(os.path.join(home, "auth.json")))["tokens"]["account_id"] if os.path.exists(os.path.join(home, "auth.json")) else "")
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
    cache = os.path.join(home, "cache", "codex_apps_tools")
    cold = not (os.path.isdir(cache) and os.listdir(cache))
    if cold and not flag("nofetch"):  # the list arrives during the run; this session never sees it
        os.makedirs(cache, exist_ok=True)
        json.dump({"tools": [{"tool": {"name": n, "_meta": {"connector_id": c}}} for c, ns in TOOLS.items() for n in ns]},
                  open(os.path.join(cache, "abc123.json"), "w"))
    def enabled(tool):  # the real CLI's rules, as measured on 0.156.1
        cid = next((c for c, ns in TOOLS.items() if tool in ns), None)
        if cold or cid is None:
            return False
        default_off = re.search(r"\[apps\._default\]\s*\nenabled = false", cfg) is not None
        app_on = re.search(r"\[apps\.%s\]\s*\nenabled = true" % cid, cfg) is not None
        tool_off = re.search(r"\[apps\.%s\.tools\.%s\]\s*\nenabled = false" % (cid, tool.split(".", 1)[1]), cfg) is not None
        return (app_on or not default_off) and not tool_off
    if flag("slow"):
        time.sleep(30)
    if flag("limit"):
        ev(type="error", message="You've hit your usage limit. Try again in 3 hours.")
        ev(type="turn.failed", error={"message": "You've hit your usage limit."}); sys.exit(1)
    call = lambda t: ev(type="item.completed", item={"type": "mcp_tool_call", "server": "codex_apps", "tool": t, "status": "completed"})
    if flag("e401"):
        ev(type="error", message="unexpected status 401 Unauthorized: token expired")
        text = "GMAIL: CONNECTED\nSLACK: CONNECTED\nSLACK_ID: U0TESTSELF1"
        open(out, "w").write(text); sys.exit(1)
    if "GMAIL: CONNECTED or NOT-CONNECTED" in data:
        if flag("echo"):
            text = "GMAIL: CONNECTED or NOT-CONNECTED\nSLACK: CONNECTED or NOT-CONNECTED\nSLACK_ID: NONE"
        else:
            g = flag("gmail_ok") and enabled("gmail.get_profile")
            s = flag("slack_ok") and enabled("slack.slack_read_user_profile")
            if g and not flag("claim_only"):
                call("gmail.get_profile")
            if s and not flag("claim_only"):
                call("slack.slack_read_user_profile")
            sid = open(os.path.join(here, "slack_id")).read().strip() if flag("slack_id") else "U0TESTSELF1"
            text = ("GMAIL: " + ("CONNECTED" if g else "NOT-CONNECTED") + "\nSLACK: " + ("CONNECTED" if s else "NOT-CONNECTED")
                    + "\nSLACK_ID: " + (sid if s else "NONE"))
            if "MIRO: CONNECTED or NOT-CONNECTED" in data:
                ev(type="item.completed", item={"type": "mcp_tool_call", "server": "miro", "tool": "list_boards", "status": "completed"})
                text += "\nMIRO: CONNECTED"
    else:
        if flag("rogue_send"):  # a Codex that ignores its allow-list: sends anyway, then reports success
            call("gmail.send_email")
            text = "DRAFT_CREATED: thread 123"
        elif flag("rogue"):
            ev(type="item.completed", item={"type": "command_execution", "command": "cat ~/.ssh/id_rsa", "status": "completed"})
            text = "OK"
        else:
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
import io, contextlib, json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, os.getcwd())
from openloops import agent, doctor
from openloops.paths import ROOT
BIN, HOME = Path(sys.argv[1]), Path(os.environ["HOME"])
JOBS = ROOT / "state" / "codex-home"
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

def auth(mode, account="acct-A", email="me@example.com"):
    doc = {"auth_mode": mode, "OPENAI_API_KEY": "sk-test" if mode == "apikey" else None,
           "tokens": {"id_token": sys.argv[2], "access_token": "a", "refresh_token": "r", "account_id": account} if mode == "chatgpt" else None}
    AUTH.write_text(json.dumps(doc))
    (BIN / "mode").write_text(mode)

def rows(recheck=False):
    steps = []
    out = doctor.codex_steps(steps, recheck)
    return {r["id"]: r for r in steps}, out

def age(secs):
    p = json.loads(doctor.CODEX_PROBE.read_text())
    doctor.CODEX_PROBE.write_text(json.dumps(dict(p, at=time.time() - secs)))

def runs_left():
    return [p for p in JOBS.glob("*/run-*")]

def main_doctor():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor.main(detect=True, recheck=True)
    return json.loads(buf.getvalue().strip().splitlines()[-1])

# ---- agent.run() through `codex exec`: first run on a new account warms up, then runs with its allow-list
auth("chatgpt")
check(agent.name() == "codex", "config.json agent codex is what the jobs see")
prompt = "Find my open loops. " + "x" * 300000 + " END-OF-PROMPT"
p = agent.run(prompt, ["gmail.search_threads", "slack.read_channel"])
ex = execs()
check(len(ex) == 2 and "Reply with exactly: OK" in ex[0]["stdin_head"] and "[apps._default]\nenabled = false" in ex[0]["config"]
      and "enabled = true" not in ex[0]["config"], "a new account's first run is a warm-up with every connector off")
c = ex[-1]
a = c["argv"]
acct_home = JOBS / agent.codex_auth()["account"]
check(p.returncode == 0 and p.stdout == "OK", f"the run's stdout is the final message from -o (got {p.returncode}, {p.stdout!r})")
check(a[:7] == ["exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "-C"] and a[-1] == "-"
      and "-p" not in a, f"codex exec, read-only sandbox, nothing saved, prompt from stdin (argv {a})")
check(a[a.index("-m") + 1] == "gpt-5.6-sol" and 'model_reasoning_effort="low"' in a and "features.shell_tool=false" in a,
      "the template's model and effort, and the shell tool off, on the command line")
check(Path(c["codex_home"]).parent == acct_home and Path(c["codex_home"]).name.startswith("run-") and c["cwd"] == str(ROOT),
      f"CODEX_HOME is a fresh run folder under the account's job home (got {c['codex_home']})")
check(not runs_left() and not list(acct_home.glob("run-*")), "the run folder, -o file included, is gone afterwards")
check(c["cache_had"] == ["abc123.json"] and (acct_home / "cache" / "codex_apps_tools" / "abc123.json").exists(),
      "the connector list fetched in the warm-up is kept for the account and copied into each run")
check(c["stdin_is_tty"] is False and c["stdin_len"] == len(agent.codex_preamble(["gmail.search_threads", "slack.read_channel"])) + len(prompt)
      and c["stdin_tail"].endswith("END-OF-PROMPT"), "a 300 KB prompt arrives whole on stdin, which is then closed")
check(c["stdin_head"].startswith("[Open Loops: an unattended run.") and "gmail.search_emails" in c["stdin_head"],
      "the preamble with the allowed tools comes first")
cfgt = c["config"]
for line in ('model = "gpt-5.6-sol"', 'model_reasoning_effort = "low"', "project_doc_max_bytes = 0", "memories = false",
             "shell_tool = false", "[apps._default]\nenabled = false", "[apps.connector_g]\nenabled = true",
             "[apps.asdk_app_s]\nenabled = true", "[apps.connector_g.tools.send_email]\nenabled = false",
             "[apps.connector_g.tools.create_draft]\nenabled = false", "[apps.asdk_app_s.tools.slack_send_message]\nenabled = false"):
    check(line in cfgt, "run config.toml has " + line.replace("\n", " "))
for line in ("[apps.connector_g.tools.search_emails]", "[apps.asdk_app_s.tools.slack_read_channel]", "[apps.connector_drive]"):
    check(line not in cfgt, "and not " + line + " (allowed tools stay on; unrelated connectors stay off by _default)")
check(c["auth_link"] == str(AUTH.resolve()), "auth.json in the run folder is a link to the user's own")
check("codex: tools used: none" in p.stderr and "tokens in 27000 (cached 13000)" in p.stderr, "stderr ends with tools and tokens")

# (A) a tool off the job's list fails the job: nothing to apply
flag("rogue_send")
p = agent.run("Draft a chase.", ["gmail.search_threads", "gmail.get_thread", "gmail.create_draft"])
flag("rogue_send", False)
check(p.returncode != 0 and p.stdout.strip() == "Codex used a tool this job did not allow, so nothing was saved."
      and "DRAFT_CREATED" not in p.stdout and "REFUSED: used a tool the job did not list: codex_apps/gmail.send_email" in p.stderr,
      "a send the job did not list fails the run: non-zero, plain sentence, no DRAFT_CREATED for chase.py to trust")
flag("rogue")
p = agent.run("hello", ["gmail.search_threads"])
flag("rogue", False)
check(p.returncode != 0 and "command_execution" in p.stderr, "a shell command fails the run too")
p = agent.run("Send the chase.", ["gmail.search_threads", "gmail.reply"])
check("[apps.connector_g.tools.send_email]" not in execs()[-1]["config"] and "[apps.connector_g.tools.create_draft]\nenabled = false" in execs()[-1]["config"],
      "with Send ticked the job lists gmail.reply, so send_email is on and create_draft off")

# (E) timeouts, launch failures, the app-bundle fallback
cj = json.loads((ROOT / "config.json").read_text())
(ROOT / "config.json").write_text(json.dumps(dict(cj, codex_timeout_s=2)))
flag("slow")
t = time.time()
p = agent.run("hello", ["gmail.search_threads"])
flag("slow", False)
check(p.returncode == 124 and time.time() - t < 20 and "saved nothing" in p.stdout and not runs_left(),
      "an ordinary job stops at codex_timeout_s, says so, and leaves no run folder")
(ROOT / "config.json").write_text(json.dumps(dict(cj, codex_model="", codex_effort="")))
agent.run("hello", [])
a2, c2 = execs()[-1]["argv"], execs()[-1]["config"]
check("-m" not in a2 and not any("model_reasoning_effort" in x for x in a2) and "model = " not in c2,
      "blank model and effort leave Codex's built-in defaults")
(ROOT / "config.json").write_text(json.dumps(cj))
real_cli = agent.cli
agent.cli = lambda: str(BIN / "no-such-codex")
p = agent.run("hello", ["gmail.search_threads"])
agent.cli = real_cli
check(p.returncode != 0 and "couldn't start Codex" in p.stdout and not runs_left(),
      "a CLI that cannot be started (FileNotFoundError): plain sentence, run folder and -o file removed")
real = (agent.shutil.which, agent._exists)
agent.shutil.which, agent._exists = (lambda *_: None), (lambda q: str(q) == "/Applications/Codex.app/Contents/Resources/codex")
got = agent.cli()
agent.shutil.which, agent._exists = real
check(got == "/Applications/Codex.app/Contents/Resources/codex", f"Codex.app alone is found (got {got})")

# (B) keyring sign-in: refuse, never fall back to the user's own home
n = len(execs())
AUTH.unlink()
p = agent.run("hello", ["gmail.search_threads"])
check(p.returncode != 0 and "keychain" in p.stdout and "codex logout" in p.stdout and len(execs()) == n,
      "no auth.json but signed in (keychain): the job refuses and Codex does not run")
r, _ = rows()
check(not r["login"]["ok"] and "keychain" in r["login"]["fix"] and "connect" not in r["login"] and len(execs()) == n,
      "...and the sign-in row says why, with no probe run")
(BIN / "mode").unlink()
p = agent.run("hello", [])
check(p.returncode != 0 and "isn't signed in" in p.stdout, "signed out altogether: refuses with Sign in")
auth("chatgpt")
real_link = agent._link
agent._link = lambda o, d: (_ for _ in ()).throw(OSError("read-only"))
p = agent.run("hello", ["gmail.search_threads"])
agent._link = real_link
check(p.returncode != 0 and "couldn't link" in p.stdout and not runs_left(), "a link that cannot be made: refuses, cleans up")

# (C) account switch: separate homes, no stale MCP credentials
(HOME / ".codex" / ".credentials.json").write_text("{}")
agent.run("hello", [])
check(execs()[-1]["creds"], "the user's MCP sign-ins (.credentials.json) are linked in when present")
(HOME / ".codex" / ".credentials.json").unlink()
agent.run("hello", [])
check(not execs()[-1]["creds"], "...and gone from the next run once the user no longer has them")
a_home = JOBS / agent.codex_auth()["account"]
auth("chatgpt", account="acct-B")
agent.run("hello", ["gmail.search_threads"])
b = execs()[-1]
check(Path(b["codex_home"]).parent != a_home and b["account"] == "acct-B" and "Reply with exactly: OK" in execs()[-2]["stdin_head"],
      "another ChatGPT account gets its own job home (and its own warm-up): nothing of account A is used")
auth("chatgpt")

# (D) the checklist
(BIN / "mode").unlink()
AUTH.unlink()
n = len(execs())
r, out = rows()
check(r["claude"]["ok"] and r["claude"]["title"] == "Codex is installed", "Codex is installed (the fake answers --version)")
check(not r["login"]["ok"] and r["login"].get("connect") == "login" and "codex login --device-auth" in r["login"]["fix"],
      "signed out: Sign in button, device sign-in as the fallback")
check(r["gmail"]["fix"] == "Sign in to ChatGPT first (the row above)." and len(execs()) == n, "sources wait for sign-in, no run")
auth("apikey")
r, _ = rows()
check(not r["login"]["ok"] and r["login"].get("connect") == "login" and r["login"]["fix"].startswith(
      "Codex is signed in with an API key, which can't use Gmail or Slack.") and len(execs()) == n,
      "API-key sign-in: red, says why, offers Sign in, no run")

auth("chatgpt")
flag("gmail_ok")
r, _ = rows(recheck=True)
check(r["login"]["ok"] and r["login"]["title"] == "Signed in to ChatGPT as me@example.com", "signed in with ChatGPT, email shown")
check(r["gmail"]["ok"] and not r["slack"]["ok"] and r["slack"].get("connect") == "slack" and "ChatGPT's apps page" in r["slack"]["fix"],
      "Gmail green (its call succeeded); Slack red with Connect Slack")
probe_cfg = execs()[-1]["config"]
check("[apps.connector_g.tools.send_email]\nenabled = false" in probe_cfg and "[apps.asdk_app_s.tools.slack_send_message]\nenabled = false" in probe_cfg,
      "the probe itself runs with only its read tools on")
m = len(execs())
rows(); rows(recheck=True)
check(len(execs()) == m, "reused: a plain check, and Check again within 30 s, run nothing")
age(60); rows()
check(len(execs()) == m, "a plain check a minute later still reuses a working answer")
rows(recheck=True)
check(len(execs()) == m + 1, "Check again after 30 s asks afresh")

flag("claim_only"); age(60)
r, _ = rows(recheck=True)
flag("claim_only", False)
check(not r["gmail"]["ok"] and r["gmail"].get("connect") == "gmail", "CONNECTED with no successful call behind it is not believed")
flag("echo"); age(60)
r, _ = rows(recheck=True)
flag("echo", False)
check(not r["gmail"]["ok"] and "Couldn't ask Codex" in r["gmail"]["fix"] and "connect" not in r["gmail"],
      "an answer that repeats the question ('GMAIL: CONNECTED or NOT-CONNECTED') is no answer")
flag("e401"); age(60)
r, _ = rows(recheck=True)
flag("e401", False)
check(not r["login"]["ok"] and r["login"].get("connect") == "login" and "run out" in r["login"]["fix"] and not r["gmail"]["ok"],
      "a 401 wins over 'GMAIL: CONNECTED' in the text: sign in again")
flag("limit"); age(60)
r, _ = rows(recheck=True)
flag("limit", False)
check("allowance is used up" in r["slack"]["fix"] and "connect" not in r["slack"], "a spent ChatGPT allowance is named")
m = len(execs())
rows()
check(len(execs()) == m, "a failed attempt is kept too: the next plain check does not ask again at once")
doctor.PROBE_TIMEOUT_S = 2
flag("slow"); age(60)
r, _ = rows(recheck=True)
flag("slow", False)
doctor.PROBE_TIMEOUT_S = 90
check("took too long" in r["gmail"]["fix"] and "connect" not in r["gmail"], "a probe that runs out of time says so")

# warming: bounded to three attempts, then a Connect button
auth("chatgpt", account="acct-C")
flag("nofetch")
seen = []
for i in range(3):
    if i:
        age(60)
    r, _ = rows(recheck=True)
    seen.append((r["gmail"].get("connect"), r["gmail"]["fix"][:30]))
flag("nofetch", False)
check(all(s[0] is None and s[1].startswith("Codex is getting") for s in seen[:2]) and seen[2][0] == "gmail",
      f"no connector list: 'getting ready' twice, then the third attempt offers Connect (got {seen})")
m = len(execs())
rows()
check(len(execs()) == m, "and each of those attempts counted for the cooldown")

# Slack id: detected once, replaced when it changes, cleared on another account without Slack
auth("chatgpt")
flag("slack_ok")
cj = json.loads((ROOT / "config.json").read_text())
(ROOT / "config.json").write_text(json.dumps(dict(cj, slack_self_id="UOLD000001")))
res = main_doctor()
st = {s["id"]: s for s in res["steps"]}
check(res["all_ok"] and st["self"]["ok"] and "U0TESTSELF1" in st["self"]["title"]
      and json.loads((ROOT / "config.json").read_text())["slack_self_id"] == "U0TESTSELF1",
      "the probe's Slack id replaces an older stored one")
(BIN / "slack_id").write_text("UNEW000002"); age(60)
main_doctor()
check(json.loads((ROOT / "config.json").read_text())["slack_self_id"] == "UNEW000002", "...and a changed one again")
(BIN / "slack_id").unlink()
auth("chatgpt", account="acct-D")
flag("slack_ok", False)
main_doctor()
check(json.loads((ROOT / "config.json").read_text())["slack_self_id"] == "", "another account with no Slack clears the stored id")
check(not st["miro"]["ok"] and st["miro"]["fix"].startswith("Miro isn't available with Codex"), "Miro: not available with Codex")

auth("chatgpt")
(HOME / ".codex" / "config.toml").write_text('model = "x"\n[mcp_servers.miro]\nurl = "https://mcp.miro.com/"\n'
                                              '[mcp_servers.miro.env]\nA = "1"\n[mcp_servers.other]\nurl = "https://o"\n')
age(60)
r, out = rows(recheck=True)
check(r["miro"]["ok"] and out[4] is True, "a Miro server the user added to Codex is asked about and used")
cfgt = execs()[-1]["config"]
check("[mcp_servers.miro]" in cfgt and "[mcp_servers.miro.env]" in cfgt and "mcp_servers.other" not in cfgt
      and 'model = "x"' not in cfgt, "only the user's Miro tables are copied into the run config")
(HOME / ".codex" / "config.toml").unlink()
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
