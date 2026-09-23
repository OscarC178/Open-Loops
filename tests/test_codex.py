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
check("TOOLS_SEEN:" in pre and "TOOLS_SEEN" not in agent.codex_preamble([]), "a job with tools is asked for a TOOLS_SEEN line")
for said, want_text, want_seen in (
        ("OK\nTOOLS_SEEN: gmail.search_emails, slack.slack_read_channel", "OK", {"gmail.search_emails", "slack.slack_read_channel"}),
        ("OK\n**TOOLS_SEEN:** none\n", "OK", set()),
        ("OK\n`TOOLS_SEEN: search_emails`", "OK", {"search_emails"}),
        ("OK", "OK", None),
        ("Quoted email:\n> TOOLS_SEEN: gmail.search_emails\nThe end.", "Quoted email:\n> TOOLS_SEEN: gmail.search_emails\nThe end.", None),
        ("OK\n> TOOLS_SEEN: gmail.search_emails", "OK\n> TOOLS_SEEN: gmail.search_emails", None),
        ("TOOLS_SEEN: gmail.search_emails\nOK", "TOOLS_SEEN: gmail.search_emails\nOK", None),
        ("OK\nTOOLS_SEEN: none\nTOOLS_SEEN: gmail.search_emails", "OK\nTOOLS_SEEN: none\nTOOLS_SEEN: gmail.search_emails", None),
        ("```\nOK\nTOOLS_SEEN: gmail.search_emails", "```\nOK\nTOOLS_SEEN: gmail.search_emails", None),
        ("```\nx\n```\nOK\nTOOLS_SEEN: gmail.search_emails", "```\nx\n```\nOK", {"gmail.search_emails"})):
    got = agent.codex_tools_seen(said)
    check(got == (want_text, want_seen), f"only one final, unquoted TOOLS_SEEN line is read and removed: {said!r} -> {got!r}")
allowed = {"gmail.search_emails", "slack.slack_read_channel"}
check(agent.codex_seen_services({"mcp__codex_apps__gmail_search_emails"}, allowed) == {"gmail"}
      and agent.codex_seen_services({"slack.slack_read_channel", "gmail.create_draft"}, allowed) == {"slack"}
      and agent.codex_seen_services(set(), allowed) == set(),
      "a seen tool counts for its connector by its exact name or its in-session name, and only if the job allows it")
check(agent.codex_seen_services({"other.search_emails", "mcp__codex_apps__outlook_search_emails", "x_search_emails",
                                 "mcp__codex_apps__gmail__search_emails"}, allowed) == set(),
      "another connector's tool, or a near-miss spelling, is not evidence")
check(agent.codex_seen_services({"search_emails"}, allowed) == set()
      and agent.codex_seen_services({"search_emails"}, {"gmail.search_emails", "gmail.read_email_thread"}) == {"gmail"},
      "a bare name counts only when the job lists one connector's tools")
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
# Four things are set separately. What the session exposes: the listed tools the allow-list enables, less "blind" (none)
# or "half" (no Gmail). What the model calls: every exposed tool, or none with "shy". What its TOOLS_SEEN line claims
# (only when the preamble asks for one): the exposed tools; with "liar" every listed tool, exposed or not; "nofooter"
# leaves it out, "quoted" writes it as "> TOOLS_SEEN: ...", "midfooter" puts it before the last line. What a refresh
# reply says about availability: the file "avail" holds true (default), false or missing.
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
    stale = not cold and time.time() - max(os.path.getmtime(os.path.join(cache, f)) for f in os.listdir(cache)) > 3600
    if (cold or stale) and not flag("nofetch"):  # the list arrives (or is refreshed) during the run; this session never sees it
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
    if flag("e401_rc0"):  # an error event, yet exit 0 with a result left behind
        ev(type="error", message="unexpected status 401 Unauthorized: token expired")
        text = '<<<OPENLOOPS>>>{"new_loops": [], "updates": [], "gmail_available": true, "slack_available": true}<<<END>>>'
        open(out, "w").write(text); sys.exit(0)
    if flag("e401"):
        ev(type="error", message="unexpected status 401 Unauthorized: token expired")
        text = "GMAIL: CONNECTED\nSLACK: CONNECTED\nSLACK_ID: U0TESTSELF1"
        open(out, "w").write(text); sys.exit(1)
    listed = re.findall(r"^- ((?:gmail|slack)\.\w+)", data, re.M)
    exposed = [] if flag("blind") else [t for t in listed if enabled(t) and not (flag("half") and t.startswith("gmail."))]
    claimed = listed if flag("liar") else exposed
    if "GMAIL: CONNECTED or NOT-CONNECTED" in data:
        if flag("echo"):
            text = "GMAIL: CONNECTED or NOT-CONNECTED\nSLACK: CONNECTED or NOT-CONNECTED\nSLACK_ID: NONE"
        else:
            g = flag("gmail_ok") and enabled("gmail.get_profile")
            s = flag("slack_ok") and enabled("slack.slack_read_user_profile")
            bad = lambda t: ev(type="item.completed", item={"type": "mcp_tool_call", "server": "codex_apps", "tool": t,
                                                             "status": "failed", "error": {"message": "not connected"}})
            for t, good in (("gmail.get_profile", g), ("slack.slack_read_user_profile", s)):
                if ("- " + t) in data and enabled(t) and not flag("claim_only"):
                    call(t) if good else bad(t)  # in the list = connected in ChatGPT; it may still fail
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
        elif flag("forbid1") and not flag("forbid1.done"):  # attempt 1: an unlisted read, and Slack never reached
            open(os.path.join(here, "forbid1.done"), "w").close()
            call("gmail.read_email")
            text = "OK"
        else:  # a working run calls the tools its session exposes, unless "shy"
            for t in exposed:
                if not flag("shy"):
                    call(t)
            text = "SENT: thread 1" if flag("marker") else "OK"
            if "<<<OPENLOOPS>>>" in data:  # refresh.py's contract; claims Gmail was searched, whatever it was given
                av = open(os.path.join(here, "avail")).read().strip() if flag("avail") else "true"
                flags_ = "" if av == "missing" else ', "gmail_available": %s, "slack_available": %s' % (av, av)
                text = '<<<OPENLOOPS>>>{"new_loops": [], "updates": []%s}<<<END>>>' % flags_
    if "TOOLS_SEEN" in data and not flag("nofooter"):
        line = ("> " if flag("quoted") else "") + "TOOLS_SEEN: " + (", ".join(claimed) or "none")
        text = (text + "\n" + line) if not flag("midfooter") else (line + "\n" + text)
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
import io, contextlib, json, os, shutil, subprocess, sys, time
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
check("codex: tools used: codex_apps/gmail.search_emails, codex_apps/slack.slack_read_channel" in p.stderr
      and "tokens in 27000 (cached 13000)" in p.stderr, "stderr ends with tools and tokens")
for fl, what in (("blind", "no tool at all"), ("half", "Slack's tools but not Gmail's")):
    flag(fl)
    n = len(execs())
    p = agent.run("Refresh.", ["gmail.search_threads", "slack.read_channel"])
    flag(fl, False)
    check(p.returncode == 3 and p.refused == "notools" and "couldn't reach its Gmail or Slack tools" in p.stdout
          and len(execs()) == n + 2 and "REFUSED: no gmail" in p.stderr,
          f"a run that called {what} is retried once, then fails: a refresh must not move its cursor past unread mail")
flag("half")
p = agent.run("Chase on Slack.", ["slack.read_channel"])
flag("half", False)
check(p.returncode == 0, "...while a Slack-only job that reached Slack is fine")
p = agent.run("hello", [])
check(p.returncode == 0 and p.stdout == "OK", f"a job with no tools: stdout is the final message from -o (got {p.returncode}, {p.stdout!r})")

# #44: the model had the tools and chose not to call them -> a normal result; the session lacked them -> refused
flag("shy")
n = len(execs())
p = agent.run("Refresh.", ["gmail.search_threads", "slack.read_channel"])
check(p.returncode == 0 and not p.refused and p.stdout == "OK" and len(execs()) == n + 1
      and "gmail, slack tools were there but not called" in p.stderr,
      f"tools seen, none called: rc 0, the reply (without TOOLS_SEEN) is the result, no retry (got {p.returncode}, {p.stdout!r})")
n = len(execs())
p = agent.run("Draft a chase.", ["gmail.search_threads", "gmail.create_draft"])
check(p.returncode == 0 and len(execs()) == n + 1, "...a job that can write too")
flag("nofooter")
n = len(execs())
p = agent.run("Refresh.", ["gmail.search_threads", "slack.read_channel"])
flag("nofooter", False); flag("shy", False)
check(p.returncode == 3 and p.refused == "notools" and len(execs()) == n + 2 and "tools seen: not said" in p.stderr,
      "no call and no TOOLS_SEEN line: treated as the start-up race (retried once, then refused)")
flag("nofooter")
p = agent.run("Refresh.", ["gmail.search_threads", "slack.read_channel"])
flag("nofooter", False)
check(p.returncode == 0 and p.stdout == "OK", "no TOOLS_SEEN line but the tools were called: fine")
flag("blind")
n = len(execs())
p = agent.run("Draft a chase.", ["gmail.search_threads", "gmail.create_draft"])
flag("blind", False)
check(p.returncode == 3 and p.refused == "notools" and len(execs()) == n + 1,
      "the session lacked the tools on a job that can write: refused at once, no retry")
for fl, what in (("quoted", "a quoted footer (> TOOLS_SEEN: ...)"), ("midfooter", "a footer that is not the last line")):
    flag("shy"); flag(fl)
    n = len(execs())
    p = agent.run("Refresh.", ["gmail.search_threads", "slack.read_channel"])
    flag("shy", False); flag(fl, False)
    check(p.returncode == 3 and p.refused == "notools" and len(execs()) == n + 2 and "tools seen: not said" in p.stderr,
          f"{what} with no call is no footer: retried, then refused")
# KNOWN RISK, asserted so it stays visible (INSTALL.md says so): the footer is the model's word. A session that lacked
# the tools, a model that called nothing and a footer claiming them all is taken as a normal result.
flag("blind"); flag("liar")
n = len(execs())
p = agent.run("Refresh.", ["gmail.search_threads", "slack.read_channel"])
flag("blind", False); flag("liar", False)
check(p.returncode == 0 and not p.refused and len(execs()) == n + 1 and p.tools_used == [],
      "a dishonest footer (claims tools the session lacked, no calls) passes as a normal result: the documented risk")

# #42: run folders left by killed runs are swept; a fresh one and a live job's are kept
import contextlib, io
dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
def leftover(name, pid=None, hours=0):
    r = acct_home / name
    (r / "work").mkdir(parents=True)
    (r / "auth.json").symlink_to(AUTH)
    if pid is not None:
        (r / "openloops.pid").write_text(str(pid))
    t_ = time.time() - hours * 3600
    os.utime(r, (t_, t_))
    return r
old_ = leftover("run-oldnopid", hours=2)
fresh_ = leftover("run-freshnopid")
dead_ = leftover("run-deadpid", pid=dead.pid)
live_ = leftover("run-liveold", pid=os.getpid(), hours=2)
ancient_ = leftover("run-livepast24h", pid=os.getpid(), hours=25)
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    p = agent.run("hello", [])
check(p.returncode == 0 and not old_.exists() and not dead_.exists() and not ancient_.exists() and AUTH.is_file(),
      "a run first removes leftover run folders: no owner and over an hour old, owner gone, or over a day old (links not followed)")
check(fresh_.exists() and live_.exists(), "...and keeps one being set up (under an hour, no owner yet) and a live job's")
check("removed 3 leftover Codex run folders" in buf.getvalue(), f"...and says so in one line (got {buf.getvalue()!r})")
check(agent.codex_sweep() == 0, "a second sweep finds nothing more")
shutil.rmtree(fresh_); shutil.rmtree(live_)


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
store = agent.codex_keyring_store()  # "the Mac keychain" / "Windows Credential Manager" / "the system keyring"
check(p.returncode != 0 and p.refused == "keyring" and store in p.stdout and "codex logout" in p.stdout and len(execs()) == n,
      "no auth.json but signed in (keychain): the job refuses and Codex does not run")
real_platform = sys.platform
for plat, name in (("linux", "the system keyring"), ("darwin", "the Mac keychain")):  # the refusal is the same everywhere
    sys.platform = plat
    try:
        p = agent.run("hello", ["gmail.search_threads"])
    finally:
        sys.platform = real_platform
    check(p.returncode != 0 and p.refused == "keyring" and name in p.stdout and len(execs()) == n,
          f"...on {plat} too, naming {name}")
r, _ = rows()
check(not r["login"]["ok"] and store in r["login"]["fix"] and "connect" not in r["login"] and len(execs()) == n,
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

# review 2: one snapshot, fail closed, warm-up failures surfaced
def write_cache(folder, names, mtime=None):
    d = folder / "cache" / "codex_apps_tools"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    f = d / "abc123.json"
    f.write_text(json.dumps({"tools": [{"tool": {"name": n, "_meta": {"connector_id": "connector_g" if n.startswith("gmail.") else "asdk_app_s"}}}
                                       for n in names]}))
    if mtime:
        os.utime(f, (mtime, mtime))

auth("chatgpt", account="acct-S")
s_home = JOBS / agent.codex_auth()["account"]
write_cache(s_home, ["gmail.get_profile", "gmail.search_emails", "gmail.read_email_thread"])  # no create_draft
flag("nofetch")
n = len(execs())
p = agent.run("Draft a chase.", ["gmail.search_threads", "gmail.get_thread", "gmail.create_draft"])
flag("nofetch", False)
new = execs()[n:]
check(p.returncode != 0 and p.refused == "cold" and "still getting ready" in p.stdout
      and len(new) == 1 and "Reply with exactly: OK" in new[0]["stdin_head"],
      "a snapshot without a tool the job lists: one warm-up, then not run at all (fail closed)")
write_cache(s_home, ["gmail.get_profile", "gmail.create_draft", "gmail.send_email"])
real_env = agent.codex_job_env
def mutating_env():
    home = real_env()
    write_cache(s_home, ["gmail.get_profile", "gmail.create_draft", "gmail.brand_new_send"])  # after the run's copy
    return home
agent.codex_job_env = mutating_env
agent.run("Draft a chase.", ["gmail.create_draft"])
agent.codex_job_env = real_env
cfgt = execs()[-1]["config"]
check("[apps.connector_g.tools.send_email]\nenabled = false" in cfgt and "brand_new_send" not in cfgt,
      "the deny-list comes from the run's own copy, not the account's list changed meanwhile")
write_cache(s_home, ["gmail.get_profile", "gmail.search_emails", "gmail.read_email_thread", "gmail.create_draft"],
            mtime=time.time() - 25 * 3600)
n = len(execs())
agent.run("Draft a chase.", ["gmail.create_draft"])
new = execs()[n:]
check(len(new) == 2 and "Reply with exactly: OK" in new[0]["stdin_head"] and agent.codex_snapshot_age(s_home) < 60,
      "a list older than 24 h is refreshed by a warm-up before the job")
for acct, flags, want, words in (("acct-W1", ("nofetch", "e401"), "expired", "sign-in has run out"),
                                 ("acct-W2", ("nofetch", "limit"), "limit", "allowance is used up")):
    auth("chatgpt", account=acct)
    for f_ in flags:
        flag(f_)
    p = agent.run("hello", ["gmail.search_threads"])
    for f_ in flags:
        flag(f_, False)
    check(p.returncode != 0 and p.refused == want and words in p.stdout, f"a warm-up that fails ({want}) says so, not 'getting ready'")
auth("chatgpt", account="acct-W3")
flag("nofetch"); flag("slow")
t = time.time()
p = agent.run("hello", ["gmail.search_threads"], timeout=3)
flag("nofetch", False); flag("slow", False)
check(p.refused == "timeout" and time.time() - t < 20 and not runs_left(),
      "a slow warm-up stops within the caller's time budget and says it timed out")
auth("chatgpt")

# review 3: retry safety
flag("forbid1"); flag("nofooter")  # no footer, so Slack counts as missed and the retry-safety guard is what stops it
n = len(execs())
p = agent.run("Refresh.", ["gmail.search_threads", "slack.read_channel"])
flag("forbid1", False); flag("forbid1.done", False); flag("nofooter", False)
check(p.returncode == 3 and p.refused == "unlisted" and "codex_apps/gmail.read_email" in p.tools_used and len(execs()) == n + 1
      and "no slack tool was called in attempt 1" in p.stderr,
      "a forbidden call in attempt 1 fails the run (rc 3), stays in tools_used, and is not retried away")
flag("blind"); flag("marker")
n = len(execs())
p = agent.run("Chase.", ["gmail.search_threads", "gmail.get_thread", "gmail.create_draft"])
check(p.returncode == 3 and len(execs()) == n + 1, "a job that can write is never retried")
n = len(execs())
p = agent.run("Read.", ["gmail.search_threads", "slack.read_channel"])
flag("blind", False); flag("marker", False)
check(p.returncode == 3 and len(execs()) == n + 1, "no retry after a SENT/DRAFT_CREATED marker, even for a read-only job")
flag("e401")
n = len(execs())
p = agent.run("Read.", ["gmail.search_threads"])
flag("e401", False)
check(p.returncode != 0 and len(execs()) == n + 1 and "401" in p.stderr, "a 401 in attempt 1 stops: no retry")
flag("e401_rc0")
p = agent.run("Refresh.", ["gmail.search_threads"])
flag("e401_rc0", False)
check(p.returncode != 0 and p.refused == "expired" and "<<<OPENLOOPS>>>" not in p.stdout and "{" not in p.stdout
      and "sign-in has run out" in p.stdout, "a 401 event with exit 0: non-zero, plain sentence, no OPENLOOPS JSON for refresh to apply")

# review 3: stale snapshots refuse
for label, when in (("more than a day old", time.time() - 25 * 3600), ("dated in the future", time.time() + 3600)):
    auth("chatgpt", account="acct-T" + label[:4])
    write_cache(JOBS / agent.codex_auth()["account"], ["gmail.get_profile", "gmail.search_emails"], mtime=when)
    flag("nofetch")
    n = len(execs())
    p = agent.run("Read.", ["gmail.search_threads"])
    flag("nofetch", False)
    new = execs()[n:]
    check(p.refused == "stale" and "more than a day old" in p.stdout and len(new) == 1 and "Reply with exactly: OK" in new[0]["stdin_head"],
          f"a list {label} that a warm-up did not refresh: not run ('stale')")

# review 3: a source not connected in ChatGPT is dropped, and its cursor holds
auth("chatgpt", account="acct-R")
write_cache(JOBS / agent.codex_auth()["account"], ["slack.slack_read_channel", "slack.slack_read_thread",
                                                    "slack.slack_search_public_and_private", "slack.slack_search_users"])
cj = json.loads((ROOT / "config.json").read_text())
(ROOT / "config.json").write_text(json.dumps(dict(cj, slack_self_id="U0TESTSELF1")))
old = "2026-09-01T09:00+01:00"
(ROOT / "state.json").write_text(json.dumps({"cursor": old, "slack_cursor": old, "loops": []}))
n = len(execs())
r = subprocess.run([sys.executable, "-m", "openloops.refresh"], cwd=ROOT, capture_output=True, text=True, timeout=60)
st = json.loads((ROOT / "state.json").read_text())
last = execs()[-1]
check(r.returncode == 0 and len(execs()) == n + 1, f"a refresh on an account with Slack but no Gmail runs ({r.stdout[-300:]}{r.stderr[-300:]})")
check(last["stdin_head"].count("Gmail is not connected in this ChatGPT account; skip email.") == 1
      and "gmail." not in last["stdin_head"].split("[End of the Open Loops note")[0] and "[apps.connector_g]" not in last["config"],
      "...with Gmail's tools dropped and the job told to skip email")
check(st["gmail_cursor"] == old and st["slack_cursor"] != old and st["gmail_available"] is False,
      "...and the Gmail cursor does not move, though the model claimed gmail_available true; Slack's does")
write_cache(JOBS / agent.codex_auth()["account"], ["google_drive.search"])
p = agent.run("Read.", ["gmail.search_threads", "slack.read_channel"])
check(p.refused == "nosources" and "Neither Gmail nor Slack" in p.stdout, "neither source connected: not run, says so")
(ROOT / "config.json").write_text(json.dumps(cj))
auth("chatgpt")

# #44 end to end: a refresh whose model had both tools and called neither is applied as is; its cursors follow the
# availability flags in its reply (true or missing: moved; false: held)
auth("chatgpt", account="acct-Q")
write_cache(JOBS / agent.codex_auth()["account"], ["gmail.search_emails", "gmail.read_email_thread", "slack.slack_read_channel",
                                                    "slack.slack_read_thread", "slack.slack_search_public_and_private", "slack.slack_search_users"])
cj = json.loads((ROOT / "config.json").read_text())
(ROOT / "config.json").write_text(json.dumps(dict(cj, slack_self_id="U0TESTSELF1")))
old = "2026-09-01T09:00+01:00"
for av, moves in (("true", True), ("false", False), ("missing", True)):
    (ROOT / "state.json").write_text(json.dumps({"cursor": old, "slack_cursor": old, "gmail_cursor": old, "loops": []}))
    (BIN / "avail").write_text(av)
    flag("shy")
    n = len(execs())
    r = subprocess.run([sys.executable, "-m", "openloops.refresh"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    flag("shy", False); flag("avail", False)
    st = json.loads((ROOT / "state.json").read_text())
    check(r.returncode == 0 and len(execs()) == n + 1 and "gmail.search_emails" in execs()[-1]["stdin_head"]
          and "slack.slack_search_public_and_private" in execs()[-1]["stdin_head"],
          f"tools seen, none called, availability {av}: the refresh runs once and is applied ({r.stderr[-200:]})")
    check(st["cursor"] != old and (st["gmail_cursor"] != old) == moves and (st["slack_cursor"] != old) == moves,
          f"...and the Gmail and Slack cursors {'move' if moves else 'hold'} (availability {av})")
(ROOT / "config.json").write_text(json.dumps(cj))
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
auth("chatgpt", account="acct-G")
write_cache(JOBS / agent.codex_auth()["account"], ["gmail.get_profile", "gmail.search_emails"])  # this account has no Slack
r, _ = rows(recheck=True)
check(r["gmail"]["ok"] and r["slack"].get("connect") == "slack" and "SLACK:" not in execs()[-1]["stdin_head"],
      "a source missing from Codex's list is 'not connected' (Connect) without asking; only Gmail is probed")
auth("chatgpt")
rows(recheck=True)  # back to account A's answer for the cooldown checks below
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
check(not r["gmail"]["ok"] and "Couldn't ask Codex" in r["gmail"]["fix"],
      "CONNECTED with no call behind it is not believed (a run that called no tool is a failed run)")
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
for i in range(6):
    if i:
        age(60)
    r, _ = rows(recheck=True)
    seen.append("Connect" if r["gmail"].get("connect") == "gmail" else "ready" if "still getting ready" in r["gmail"]["fix"] else r["gmail"]["fix"][:40])
flag("nofetch", False)
check(seen == ["ready", "ready", "Connect", "Connect", "Connect", "Connect"],
      f"no connector list: 'still getting ready' twice, then Connect on every later check (got {seen})")
check(json.loads(doctor.CODEX_PROBE.read_text())["tries"] == 6, "the warming count is kept, not reset, after the third")
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
