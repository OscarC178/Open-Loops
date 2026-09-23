"""Claude jobs run `claude -p --output-format json` (#46): agent.run() unwraps the answer and reads failures from the flag.

    python3 tests/test_claude_json.py    # fast; no Slack/Gmail/Claude. Temp install, a fake `claude` first on PATH.

Checks:
  1. claude_args() asks for --output-format json.
  2. a normal answer: .stdout is the "result" text (OPENLOOPS block intact, what the jobs parse), is_error False,
     refused "", usage and cost attached, and a one-line summary on stderr.
  3. is_error results: "Not logged in" -> refused "expired" -> report() writes job_signed_out; usage limit wording or
     API status 429 -> job_usage_limit; 401 -> job_signed_out; no network -> job_network; anything else -> "failed",
     which has no sentence of its own (the plain "didn't finish"). An is_error run that exited 0 is made non-zero.
  4. a normal answer that SAYS "not logged in" (an email quoted by the model) is never classified.
  5. output that is not JSON (an older CLI): passed on as it came, noted on the run's stderr, warned once per process.
"""
import contextlib, io, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import isolate_this_process  # noqa: E402

TMP = isolate_this_process("openloops-claude-json-")
from openloops import agent, messages  # noqa: E402

t0 = time.time()


def show(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    show(f"ok   {what}")


if sys.platform == "win32":  # the fake CLI is a POSIX script; the parsing is covered on Mac/Linux
    show("SKIP: the fake claude is a POSIX script")
    raise SystemExit(0)

# The fake claude prints bin/out.txt and exits with bin/rc.txt, recording its arguments in bin/argv.json.
BIN = TMP / "bin"
BIN.mkdir()
(BIN / "claude").write_text(f"""#!{sys.executable}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
json.dump(sys.argv[1:], open(os.path.join(here, "argv.json"), "w"))
sys.stdin.read()
sys.stdout.write(open(os.path.join(here, "out.txt"), encoding="utf-8").read())
sys.exit(int(open(os.path.join(here, "rc.txt")).read()))
""", encoding="utf-8")
(BIN / "claude").chmod(0o755)
os.environ["PATH"] = f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}"
check(agent.name() == "claude", "the throwaway install runs Claude (template default)")

# The shape `claude -p --output-format json "Reply with exactly: OK"` printed on Claude Code 2.1.280 (trimmed of
# timing and per-model fields that the parser ignores).
REAL = {"type": "result", "subtype": "success", "is_error": False, "api_error_status": None, "result": "OK",
        "stop_reason": "end_turn", "session_id": "d14616e5-0000-0000-0000-000000000000", "num_turns": 1,
        "total_cost_usd": 0.4398785, "permission_denials": [], "terminal_reason": "completed",
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 21855, "cache_read_input_tokens": 10234,
                  "output_tokens": 4}}


def fake(out, rc=0):
    (BIN / "out.txt").write_text(out if isinstance(out, str) else json.dumps(out), encoding="utf-8")
    (BIN / "rc.txt").write_text(str(rc), encoding="utf-8")


def result(text, is_error=False, status=None, subtype="success", **extra):
    return dict(REAL, result=text, is_error=is_error, api_error_status=status, subtype=subtype, **extra)


RID = "0123456789abcdef0123456789abcdef"


def reported(p, job="refresh"):
    """The failure file report() leaves for run p (as a job started by the app would) -> dict or None."""
    os.environ["OPENLOOPS_RUN_ID"], os.environ["OPENLOOPS_AI"] = RID, "Claude"
    f = messages.failure_file(job)
    f.unlink(missing_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        messages.report(p, job)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def sentence(rec):
    return messages.job_failure("refresh", 1, "log", ai="Claude", failure=rec)


# ---------------------------------------------------------------- 1. the command line
show("1. the command line")
check(agent.claude_args([])[:4] == ["claude", "-p", "--output-format", "json"], "claude_args(): -p --output-format json")

# ---------------------------------------------------------------- 2. a normal answer
show("2. a normal answer is unwrapped")
BLOCK = '<<<OPENLOOPS>>>{"new_loops": [], "updates": [], "closed": []}<<<END>>>'
fake(result("Read 12 threads.\n" + BLOCK))
p = agent.run("Refresh.", ["gmail.search_threads"])
argv = json.loads((BIN / "argv.json").read_text())
check("--output-format" in argv and argv[argv.index("--output-format") + 1] == "json", "the real run asked for json")
check(p.returncode == 0 and p.stdout == "Read 12 threads.\n" + BLOCK, "stdout is the result text, OPENLOOPS block intact")
check(p.is_error is False and p.refused == "" and p.error_text == "" and p.agent == "claude", "not an error, nothing refused")
check(p.usage and p.usage["output_tokens"] == 4 and abs(p.cost_usd - 0.4398785) < 1e-9 and p.session_id.startswith("d14616e5"),
      "usage, cost and session id attached")
check("claude: success; tokens in 2, out 4; cost $0.4399" in p.stderr, f"one summary line on stderr ({p.stderr.strip()!r})")
check(reported(p) is None, "report() after a good run writes nothing")

# ---------------------------------------------------------------- 3. is_error results
show("3. failures come from the is_error flag")
cases = [
    ("Not logged in · Please run /login", None, 1, "expired", "job_signed_out"),
    ("Invalid API key · Please run /login", None, 1, "expired", "job_signed_out"),
    ("Login expired · Please run /login", None, 1, "expired", "job_signed_out"),
    ("API Error: 401 Invalid authentication credentials", 401, 1, "expired", "job_signed_out"),
    ("You've hit your session limit · resets 3pm (Europe/London)", None, 1, "limit", "job_usage_limit"),
    ("Claude AI usage limit reached|1760000000", None, 1, "limit", "job_usage_limit"),
    ("API Error: Request rejected", 429, 1, "limit", "job_usage_limit"),
    ("API Error: Unable to connect to API (ECONNREFUSED)", None, 1, "network", "job_network"),
    ("Not logged in · Please run /login", None, 0, "expired", "job_signed_out"),  # is_error but exit 0
]
for text, status, rc, want, fid in cases:
    fake(result(text, is_error=True, status=status), rc=rc)
    p = agent.run("Refresh.", ["gmail.search_threads"])
    check(p.is_error is True and p.refused == want and p.error_text == text and p.returncode != 0,
          f"{text[:45]!r} (status {status}, exit {rc}) -> refused {want!r}, non-zero exit")
    rec = reported(p)
    check(rec and rec["failure"] == fid and sentence(rec)[0] == fid, f"...report() -> {fid}")
check("FAILED: expired" in p.stderr and "claude error: Not logged in" in p.stderr and p.stdout == "",
      "the run's stderr says why, for the log; the job gets no stdout from a failed run")
check(sentence(reported(p))[1].startswith("The refresh stopped because Claude has signed you out."),
      "the page's sentence names Claude, not ChatGPT (Codex's 'expired' sentence is not used)")
# The API status decides before any text is read, and a number merely quoted in the text decides nothing (#47 review).
fake(result("Request rejected: invalid authentication credentials", is_error=True, status=401), rc=1)
p = agent.run("Refresh.", ["gmail.search_threads"])
check(p.refused == "expired" and reported(p)["failure"] == "job_signed_out",
      "status 401 with text that says 'Request rejected' -> expired (signed out), not a usage limit")
cf = agent.claude_failure
check(cf(429, "Not logged in") == "limit" and cf(401, "You've hit your session limit") == "expired",
      "a status beats conflicting text either way")
check(cf(None, "Unable to connect while processing reference 429") == "network"
      and cf(None, "Request rejected") == "failed" and cf(None, "error 401 in module") == "failed",
      "no status: a bare 429/401 or 'Request rejected' in the text is not a limit or a sign-out")

fake(result("Something went wrong on our side.", is_error=True), rc=1)
p = agent.run("Refresh.", ["gmail.search_threads"])
check(p.refused == "failed" and reported(p) is None and sentence(None)[0] == "job_failed",
      "an is_error run that says nothing known -> 'failed', no specific sentence (the plain 'didn't finish')")
fake(dict(REAL, subtype="error_max_turns", is_error=True, result=None, errors=["Reached maximum number of turns"]), rc=1)
p = agent.run("Refresh.", ["gmail.search_threads"])
check(p.is_error and p.stdout == "" and p.refused == "failed" and "maximum number of turns" in p.error_text,
      "an error subtype with no result: empty stdout, the errors list is the error text")

# ---------------------------------------------------------------- 3b. a failed run's text is never applied
show("3b. an error result's text never reaches a job (#47 review)")
import re, subprocess  # noqa: E402,E401


def job(*args):
    """Run one job module in the throwaway install, against the fake claude -> CompletedProcess."""
    return subprocess.run([sys.executable, "-m", *args], cwd=TMP, env=dict(os.environ), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def chases():
    return json.loads((TMP / "state.json").read_text(encoding="utf-8"))["loops"][0].get("chases", 0)


(TMP / "state.json").write_text(json.dumps({"cursor": None, "last_refresh": None, "loops": [
    {"id": "L1", "channel": "email", "owner": "Sam", "ask": "the budget", "thread": "Budget", "chases": 0}]}), encoding="utf-8")
fake(result("Drafted the reply.\nDRAFT_CREATED: Gmail draft on 'Budget'", is_error=True), rc=1)
r = job("openloops.chase", "L1")
check(r.returncode == 1 and chases() == 0, f"chase: DRAFT_CREATED inside an error result does not count a chase (rc {r.returncode})")
fake(result("Drafted the reply.\nDRAFT_CREATED: Gmail draft on 'Budget'"))
r = job("openloops.chase", "L1")
check(r.returncode == 0 and chases() == 1, "...the same line in a normal answer does (so the check above can fail)")

people = TMP / "people_suggested.json"
people.unlink(missing_ok=True)
fake(result('<<<PEOPLE>>>{"people": [{"name": "Sam"}]}<<<END>>>', is_error=True), rc=1)
r = job("openloops.people")
check(r.returncode == 1 and not people.exists(), f"people: a PEOPLE block inside an error result is not saved (rc {r.returncode})")
fake(result('<<<PEOPLE>>>{"people": [{"name": "Sam"}]}<<<END>>>'))
r = job("openloops.people")
check(r.returncode == 0 and people.exists(), "...the same block in a normal answer is (so the check above can fail)")

src = (TMP / "openloops" / "doctor.py").read_text(encoding="utf-8")
check('re.search(r"\\bU[0-9A-Z]{8,}\\b", p.stdout or "")' in src, "doctor's Slack-id lookup reads p.stdout with this pattern")
fake(result("Slack said: unauthorised for U12345678", is_error=True), rc=1)
p = agent.run("Reply with ONLY the current logged-in user's Slack user id", ["slack.search_users"])
check(not re.search(r"\bU[0-9A-Z]{8,}\b", p.stdout or "") and "U12345678" in p.error_text,
      "doctor: a Slack-id-looking string in an error result is not in stdout, so it is never stored")

# ---------------------------------------------------------------- 4. the model's own words never classify
show("4. a normal answer that mentions a sign-out is not one")
fake(result("Sam wrote: Not logged in to the expenses portal, can you help?\nAlso: usage limit reached on analytics."))
p = agent.run("Refresh.", ["gmail.search_threads"])
check(p.returncode == 0 and p.is_error is False and p.refused == "", "is_error false: nothing refused")
p.returncode = 1   # the job would still fail (no OPENLOOPS block) and call report()
check(reported(p) is None, "...and report() finds no reason: NOT classified, no failure file")

# ---------------------------------------------------------------- 5. not JSON
show("5. output that is not JSON")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    fake("Here you go.\n" + BLOCK + "\n")
    p = agent.run("Refresh.", ["gmail.search_threads"])
    fake("{not json\n")
    q = agent.run("Refresh.", ["gmail.search_threads"])
check(p.stdout == "Here you go.\n" + BLOCK + "\n" and p.is_error is None and p.refused == "",
      "plain text is passed on as it came (an older CLI still works)")
check("claude: output was not JSON" in p.stderr and "claude: output was not JSON" in q.stderr and q.stdout == "{not json\n",
      "each such run notes it on its own stderr (the job's log)")
check(err.getvalue().count("did not answer in JSON") == 1, "the process warns once, not once per run")
fake("", rc=1)
p = agent.run("Refresh.", ["gmail.search_threads"])
check(p.stdout == "" and p.is_error is None and "not JSON" not in p.stderr, "no output at all: nothing to parse, no warning")

show("ALL OK")
