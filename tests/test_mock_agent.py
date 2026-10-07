"""The mock agent (openloops/mock_agent.py, #76): every job end to end with no Slack, Gmail or AI sign-in.

    python3 tests/test_mock_agent.py    # fast; throwaway install and $HOME, no network, nothing real is called

Checks:
  1. in-process: OPENLOOPS_AGENT=mock makes agent.name() "mock" and any other value is ignored; the template's
     default is claude and the page's Settings offer no mock; agent.run() answers each job's prompt with the block
     its prompt asks for; MOCK:FAIL / MOCK:FAIL=expired / MOCK:SLEEP in the prompt or OPENLOOPS_MOCK.
  2. through the console, in seconds: run refresh writes two loops to state.json and list shows them (a second
     refresh adds no duplicates); run chase counts a draft; people, daylog, voice and roadmap (read, parse, preview,
     build) write their files; doctor --detect ticks every row, every row says mock, and saves the mock Slack id;
     agent --check, status and doctor say "mock" in so many words; MOCK:FAIL=expired leaves the sign-out failure
     record a page would show; MOCK:SLEEP=1 takes a second.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import fresh_install, isolate_this_process, isolated_env, reserve  # noqa: E402

TMP = isolate_this_process("openloops-mock-parent-")
from openloops import agent, mock_agent  # noqa: E402

t0 = time.time()


def show(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    show(f"ok   {what}")


# ---------------------------------------------------------------- 1. in-process
show("1. choosing the mock, and what it answers")
os.environ.pop("OPENLOOPS_AGENT", None)
os.environ.pop("OPENLOOPS_MOCK", None)
check(agent.name() == "claude", "a fresh install runs Claude (the template default)")
os.environ["OPENLOOPS_AGENT"] = "grok"
check(agent.name() == "claude", "OPENLOOPS_AGENT=grok is ignored: the variable can only choose the mock")
os.environ["OPENLOOPS_AGENT"] = "MOCK"
check(agent.name() == "mock" and agent.display_name() == "Mock (no AI)" and agent.cli() == "mock", "OPENLOOPS_AGENT=mock (any case): the mock, no CLI")
tpl = json.loads((REPO / "config.template.json").read_text(encoding="utf-8-sig"))
html = (REPO / "openloops" / "index.html").read_text(encoding="utf-8")
check(tpl["agent"] == "claude" and 'value="mock"' not in html, "never a default: the template says claude and Settings offer no mock")
check(agent.connect_steps() == () and agent.install_cmd() is None and agent.login_cmd("login") is None,
      "no setup steps, no installer, no sign-in command for the mock")

p = agent.run("Refresh.\n<<<OPENLOOPS>>>\n{...}\n<<<END>>>", ["gmail.search_threads"])
check(p.returncode == 0 and p.agent == "mock" and p.is_error is False and p.refused == "" and "<<<OPENLOOPS>>>" in p.stdout
      and "mock" in p.stderr and "no AI was called" in p.stderr, "run(): a refresh prompt gets an OPENLOOPS block; stderr says no AI was called")
blk = json.loads(re.search(r"<<<OPENLOOPS>>>(.*?)<<<END>>>", p.stdout, re.S).group(1))
check(len(blk["new_loops"]) == 2 and {l["channel"] for l in blk["new_loops"]} == {"email", "slack"} and blk["gmail_available"] is True,
      "...two new loops, one per channel, both sources 'available'")
for marker, key in (("<<<PEOPLE>>>", "people"), ("<<<DAYLOG>>>", "text"), ("<<<VOICE>>>", "general"), ("<<<STANDING>>>", "updates")):
    out = agent.run(f"Do the thing.\n{marker}\n{{}}\n<<<END>>>", []).stdout
    body = json.loads(re.search(re.escape(marker) + r"(.*?)<<<END>>>", out, re.S).group(1))
    check(key in body, f"{marker} prompt -> a block with {key!r}")
for want, key in (('"board_url"', "lanes"), ('"rows": [', "rows"), ('"plan": [', "plan"), ('"created": [', "created")):
    out = agent.run(f"Roadmap.\n- r1 | a | - | ? | ? | not_started\n- r2 | b | - | ? | ? | done\n<<<ROADMAP>>>\n{{{want} ...}}\n<<<END>>>", ["miro.*"]).stdout
    body = json.loads(re.search(r"<<<ROADMAP>>>(.*?)<<<END>>>", out, re.S).group(1))
    check(key in body and (key not in ("plan", "created") or [x["id"] for x in body[key]] == ["r1", "r2"]),
          f"roadmap prompt asking for {want} -> {key} (ids from the prompt's rows)")
check(agent.run("Last line of your reply must be exactly one of:\nDRAFT_CREATED: <where>\nDRAFT_FAILED: <reason>\n", []).stdout.splitlines()[-1].startswith("DRAFT_CREATED:")
      and agent.run("...\nSENT: <where>\nDRAFT_FAILED: <reason>\n", []).stdout.splitlines()[-1].startswith("SENT:"),
      "a chase prompt ends with the marker it asks for (DRAFT_CREATED or SENT)")
check(agent.run("Reply with ONLY these two lines:\nSLACK_ID: <id>\nSLACK_NAME: <name>", ["slack.search_users"]).stdout == f"SLACK_ID: {mock_agent.SLACK_ID}\nSLACK_NAME: Mock User",
      "doctor's Slack lookup gets a plainly fake id")
check(agent.run("Reply with exactly: OK", []).stdout == "OK (mock)", "anything else: OK (mock)")

p = agent.run("Refresh. MOCK:FAIL\n<<<OPENLOOPS>>>", [])
check(p.returncode == 1 and p.stdout == "" and p.is_error is True and p.refused == "" and "FAILED on purpose" in p.stderr,
      "MOCK:FAIL in the prompt: exit 1, no stdout, is_error, no specific reason (the plain 'didn't finish')")
os.environ["OPENLOOPS_MOCK"] = "MOCK:FAIL=expired"
p = agent.run("Refresh.\n<<<OPENLOOPS>>>", [])
check(p.returncode == 1 and p.refused == "job_signed_out", "OPENLOOPS_MOCK=MOCK:FAIL=expired: the sign-out reason, as messages.report files it")
os.environ["OPENLOOPS_MOCK"] = "MOCK:SLEEP=1"
t = time.time()
p = agent.run("Refresh.\n<<<OPENLOOPS>>>", [])
check(p.returncode == 0 and time.time() - t >= 1, "OPENLOOPS_MOCK=MOCK:SLEEP=1: waits a second, then answers")
os.environ.pop("OPENLOOPS_MOCK")
check(mock_agent.directives("a MOCK:SLEEP=4 b MOCK:FAIL=limit c") == (4, "limit") and mock_agent.directives("") == (0, None),
      "directives(): seconds and failure kind from the text")

# ---------------------------------------------------------------- 2. through the console
show("2. the console's run, against the mock")
tmp = fresh_install("openloops-mock-", {"owner_name": "Oscar", "people": {"Sam Mock": {"email": "sam@example.com", "level": "senior"}},
                                        "voice_sample_people": ["Sam Mock"], "roadmap_board": "Planning", "roadmap_frame": "Roadmap"})
(tmp / "state.json").write_text(json.dumps({"cursor": "2026-01-01T00:00", "last_refresh": None, "loops": []}), encoding="utf-8")
# a block of ports with nothing on it, guards let go: the console's server scan answers at once on a closed port,
# but waits its 0.3 s timeout on each held guard (no server is ever started here, so nothing needs guarding)
block = reserve()
block.release()
env = isolated_env(tmp, OPENLOOPS_AGENT="mock", PYTHONDONTWRITEBYTECODE="1", OPENLOOPS_PORT=str(block.port))
env.pop("OPENLOOPS_MOCK", None)


def cli(*args, **extra):
    r = subprocess.run([sys.executable, "-m", "openloops", *args], cwd=tmp, env=dict(env, **extra), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, r.stdout, r.stderr


def state():
    return json.loads((tmp / "state.json").read_text(encoding="utf-8"))


def cfg():
    return json.loads((tmp / "config.json").read_text(encoding="utf-8"))


t = time.time()
rc, out, err = cli("run", "refresh")
check(rc == 0 and "done: 2 new, 0 updated" in out and f"agent mock" in out, f"run refresh: 2 new loops in the foreground ({out.strip()[-200:]} {err.strip()[-200:]})")
check(time.time() - t < 20, f"...in seconds, not minutes ({time.time() - t:.1f}s)")
ids = sorted(l["id"] for l in state()["loops"])
check(ids == ["mock-priya-deck-review", "mock-sam-budget-figures"] and state()["last_refresh"], "state.json has the two loops and a last_refresh")
rc, out, _ = cli("run", "refresh")
check(rc == 0 and "done: 0 new" in out and len(state()["loops"]) == 2, "a second refresh adds no duplicates")
rc, out, _ = cli("list", "--json")
check(rc == 0 and [r["id"] for r in json.loads(out)] == ["mock-priya-deck-review"], "list: the inbound loop is in Needs me")
rc, out, _ = cli("list", "waiting", "--json")
check(rc == 0 and [r["id"] for r in json.loads(out)] == ["mock-sam-budget-figures"], "list waiting: the owner's ask")
rc, out, _ = cli("logs", "refresh")
check(rc == 0 and "no AI was called" in out, "the run log carries the mock's stderr line")

rc, out, _ = cli("run", "chase", "mock-sam-budget-figures")
lp = next(l for l in state()["loops"] if l["id"] == "mock-sam-budget-figures")
check(rc == 0 and lp["chases"] == 1 and lp["last_chase_mode"] == "draft" and "DRAFT_CREATED:" in out, "run chase: a draft is counted on the loop")
rc, out, _ = cli("run", "people")
pf = tmp / "people_suggested.json"
check(rc == 0 and pf.exists() and len(json.loads(pf.read_text(encoding="utf-8"))["people"]) == 3, "run people: people_suggested.json with three people")
rc, out, _ = cli("run", "daylog")
today = time.strftime("%Y-%m-%d")
dl = json.loads((tmp / "state" / "daylog" / f"{today}.json").read_text(encoding="utf-8"))
check(rc == 0 and dl["text"].startswith("Done") and len(dl["highlights"]) == 2, "run daylog: the day's entry has text and highlights")
rc, out, _ = cli("run", "voice")
vf = json.loads((tmp / "voice.json").read_text(encoding="utf-8"))
check(rc == 0 and "Sam Mock" in vf["people"] and set(vf["samples"]) == {"senior", "peer", "junior", "external"}, "run voice: voice.json profiles the sample person")
rc, out, _ = cli("run", "roadmap", "read")
rm = json.loads((tmp / "state" / "roadmap.json").read_text(encoding="utf-8"))
check(rc == 0 and rm["board"]["lanes"] == ["Product", "Platform"] and rm["board"]["existing"], f"run roadmap read: lanes, columns and items saved ({out.strip()[-120:]})")
rm["pasted"] = "deck review with Priya"
(tmp / "state" / "roadmap.json").write_text(json.dumps(rm), encoding="utf-8")
rc1, _, _ = cli("run", "roadmap", "parse")
rc2, _, _ = cli("run", "roadmap", "preview")
rc3, out, _ = cli("run", "roadmap", "build", "--confirm")
rm = json.loads((tmp / "state" / "roadmap.json").read_text(encoding="utf-8"))
check(rc1 == 0 and rc2 == 0 and rc3 == 0 and rm["rows"] and all(r.get("posted_id", "").startswith("mock-item-") for r in rm["rows"]),
      f"run roadmap parse / preview / build --confirm: the staged row is 'posted' with a mock item id ({out.strip()[-120:]})")

rc, out, _ = cli("doctor", "--detect", "--json")
doc = json.loads(out)
rows = {s["id"]: s for s in doc["steps"]}
check(rc == 0 and doc["agent"] == "mock" and doc["mock"] is True and doc["all_ok"] is True, "doctor --detect --json: agent mock, mock true, all ok")
check(all("mock" in rows[k]["title"].lower() for k in ("claude", "login", "slack", "gmail", "miro")) and all(rows[k]["ok"] for k in rows),
      "...every AI and source row is ticked AND says mock in its title")
check(cfg().get("slack_self_id") == mock_agent.SLACK_ID and rows["self"]["ok"] and "Mock User" in rows["self"]["title"],
      "...the Slack lookup saved the plainly fake id and name")
rc, out, _ = cli("doctor")
check(rc == 0 and "agent mock (no AI is called" in out, "doctor (text): the agent line says mock, no AI")
rc, out, _ = cli("agent", "--check", "--json")
ag = json.loads(out)
check(rc == 0 and ag["agent"] == "mock" and ag["mock"] is True and ag["found"] is None and "mock" in ag["signed_in_as"]
      and "OPENLOOPS_AGENT=mock" in ag["note"] and "job_argv" not in ag, "agent --check --json: mock, no command, signed in as nobody, how it was chosen")
rc, out, _ = cli("status")
check(rc == 0 and "agent     mock (no AI is called" in out, "status: the agent line says mock, no AI")
rc, out, _ = cli("status", "--json")
check(json.loads(out)["agent"] == "mock" and json.loads(out)["mock"] is True, "status --json: agent mock, mock true")
rc, out, err = cli("run", "refresh", "--slack-only")
check(rc == 0 and "done: 0 new" in out, f"run refresh --slack-only with the saved Slack id: runs, nothing new ({out.strip()[-120:]})")

rc, out, err = cli("run", "refresh", OPENLOOPS_MOCK="MOCK:FAIL=expired")
check(rc == 1 and "failure record: job_signed_out" in out and "no OPENLOOPS block" in out,
      f"OPENLOOPS_MOCK=MOCK:FAIL=expired: the run fails and leaves the sign-out record the page would show ({out.strip()[-160:]})")
rc, out, err = cli("run", "refresh", OPENLOOPS_MOCK="MOCK:FAIL")
check(rc == 1 and "failure record" not in out, "MOCK:FAIL alone: fails with no specific reason")
t = time.time()
rc, out, _ = cli("run", "daylog", OPENLOOPS_MOCK="MOCK:SLEEP=1")
check(rc == 0 and time.time() - t >= 1, "MOCK:SLEEP=1 through the console: a second longer")

# config.json agent=mock, no environment variable: the same
e2 = dict(env)
e2.pop("OPENLOOPS_AGENT")
r = subprocess.run([sys.executable, "-m", "openloops", "config", "set", "agent", "mock"], cwd=tmp, env=e2, capture_output=True, text=True)
r2 = subprocess.run([sys.executable, "-m", "openloops", "agent", "--json"], cwd=tmp, env=e2, capture_output=True, text=True)
check(r.returncode == 0 and json.loads(r2.stdout)["mock"] is True and "config.json agent=mock" in json.loads(r2.stdout)["note"],
      "config set agent mock: the mock without the variable, and agent says which chose it")
r3 = subprocess.run([sys.executable, "-m", "openloops", "agent", "--json"], cwd=tmp, env=dict(e2, OPENLOOPS_AGENT=" Mock "), capture_output=True, text=True)
check("OPENLOOPS_AGENT=mock in this shell" in json.loads(r3.stdout)["note"],
      "a padded ' Mock ' in the variable is the variable choosing it, as agent.name() reads it (#77 review)")
show("ALL OK")
