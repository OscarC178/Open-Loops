"""Every fixable failure in plain words (#25): the messages.py table, who uses it, and the page's offline banner.

    python3 tests/test_messages.py    # fast; no Slack/Gmail/Claude. Temp install, spare port; node if installed.

Checks:
  1. every FAILURES entry has a one-sentence "what" and "fix", a known button (or none), and none of the words the
     plain-words rules forbid (exit codes, tracebacks, paths, launchd, TCC, MCP); UK spelling.
  2. every id doctor.py, app.py, standing.py and index.html ask for exists, and every entry is used by one of them.
  3. say() / part() / for_page(): placeholders filled, unknown ones kept, the Windows fix on Windows.
  4. job_failure(): a failed job's log is read for who stopped it (Claude signed out, usage limit, no network).
  5. the served page carries the table for its platform, and before the first connection check shows
     "Checking your connections…" with the lists and Refresh hidden (#34); Console times are local.
  6. with the app gone (a fetch that throws), the page's banner says Open Loops isn't running and how to open it,
     and is actually shown (display:block, not '' which the stylesheet turns into none); an HTTP error says
     something else. Run in node against the functions as served; skipped without node.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from _helpers import fresh_install, isolate_this_process, isolated_env, start_app, stop, wait_until  # noqa: E402
isolate_this_process("openloops-messages-parent-")
from openloops import agent, messages  # noqa: E402
from openloops.messages import FAILURES, say  # noqa: E402

t0 = time.time()


def show(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    show(f"ok   {what}")


# ---------------------------------------------------------------- 1. the table itself
show("1. every entry follows the plain-words rules")
FORBIDDEN = ("rc=", "Traceback", "/Users/", "~/", "launchd", "TCC", "MCP", "exit code", "stderr", "stdout", "plist",
             ".log", "None", "Exception")
US = ("color", "authoriz", "cancele", "recogniz", "organiz", "behavior", "center ")
BUTTONS = set(agent.CONNECT_STEPS) | {agent.INSTALL_STEP}
check(len(FAILURES) >= 40, f"the inventory is there ({len(FAILURES)} entries)")
for fid, m in FAILURES.items():
    check(set(m) <= {"what", "fix", "fix_win", "button"} and m.get("what", "").strip() and m.get("fix", "").strip(),
          f"{fid}: has a non-empty what and fix")
    check(m.get("button") is None or m["button"] in BUTTONS, f"{fid}: button is a real checklist step or none")
    for part in ("what", "fix", "fix_win"):
        text = m.get(part)
        if not text:
            continue
        bad = [w for w in FORBIDDEN if w in text] + [w for w in US if w in text.lower()]
        check(not bad, f"{fid}.{part}: no jargon, paths or US spelling (found {bad})")
        check(text.endswith(".") and ". " not in text.replace("(Windows: PowerShell).", ""), f"{fid}.{part}: one sentence")
        check(text[0].isupper() or text[0] == "{", f"{fid}.{part}: starts like a sentence")
check("privacy settings" in FAILURES["schedule_blocked"]["what"] and "Google" in FAILURES["gmail_signin"]["fix"]
      and "Slack" in FAILURES["slack_signin"]["fix"] and "Claude" in FAILURES["signin_expired"]["what"],
      "failures name who is involved: your Mac's privacy settings, Google, Slack, Claude")
check("internet" not in FAILURES["install_vendor"]["what"] + FAILURES["install_vendor"]["fix"]
      and "internet" in FAILURES["install_network"]["fix"], "a vendor's error is never blamed on the user's internet; no network is")

# ---------------------------------------------------------------- 2. who uses it
show("2. every id used exists, and every entry is used; every placeholder is filled where it is shown")
import ast  # noqa: E402
SOURCES = {p: (REPO / "openloops" / p).read_text(encoding="utf-8")
           for p in ("doctor.py", "app.py", "agent.py", "standing.py", "index.html", "messages.py", "refresh.py", "chase.py")}
asked, calls, dynamic = set(), [], []   # ids the code asks for; every call site: (ids, keywords it passes, where)


def ids_of(node):
    """The message ids an argument can evaluate to: a string, or either branch of a conditional (a if c else b)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return ids_of(node.body) | ids_of(node.orelse)
    return None


for name, text in SOURCES.items():
    if not name.endswith(".py"):
        continue
    tree = ast.parse(text)
    # agent.CODEX_REFUSE = {k: say("codex_…")}: templates on purpose, {store} / {limit} filled by .format() where used
    # (TABLES below holds those uses); these builds are not call sites that show a sentence
    templates = [range(n.lineno, n.end_lineno + 1) for n in ast.walk(tree) if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "CODEX_REFUSE" for t in n.targets)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and not any(node.lineno in r for r in templates):
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if fn in ("say", "part"):
                got = ids_of(node.args[0])
                if got is None:
                    if "INSTALL_WHY" not in (ast.get_source_segment(text, node.args[0]) or ""):  # a table: TABLES below
                        dynamic.append(f"{name}:{node.lineno}")
                    continue
                asked |= got
                if any(k.arg is None for k in node.keywords):   # say(id, **fmt): only messages.py's own helpers do this
                    dynamic.append(f"{name}:{node.lineno}")
                calls.append((got, {k.arg for k in node.keywords if k.arg}, f"{name}:{node.lineno}"))
for text in SOURCES.values():   # doctor.SCHEDULE_MSG reads the table directly: _F["schedule_blocked"]["what"]
    asked |= set(re.findall(r"""_F\[["']([a-z_]+)["']\]""", text))
page_src = SOURCES["index.html"]
for m in re.finditer(r"""\bmsg\('([a-z_]+)'(?:,\{([^}]*)\})?\)""", page_src):
    asked.add(m.group(1))
    line_no = page_src.count("\n", 0, m.start()) + 1
    calls.append(({m.group(1)}, set(re.findall(r"(\w+)\s*(?::|,|$)", m.group(2) or "")), f"index.html:{line_no}"))
from openloops import app  # noqa: E402  (importing app writes config/state into the throwaway install only)
# ids reached through tables rather than a literal call, and what their callers fill in
TABLES = {**{i: set() for i in messages.RECHECK_AFTER_JOB},   # first: the entries below say what these are filled with
          **{i: {"ai", "limit", "vendor"} for i in app.INSTALL_WHY.values()},
          **{i: {"job", "ai"} for i, _ in messages.AI_SIGNS}, "job_failed": {"job"}, "job_start_failed": {"job", "job_lower"},
          **{i: {"store", "limit"} for i in messages.CODEX_JOB_IDS}}   # agent.py .format(store=, limit=)
for i, keys in TABLES.items():
    asked.add(i)
    calls.append(({i}, keys, f"table:{i}"))
missing = sorted(i for i in asked if i not in FAILURES)
check(not missing, f"every id the code asks for (conditional ones included) is in FAILURES (missing: {missing})")
check(set(dynamic) <= {f"messages.py:{n}" for n in range(1, 10000)},
      f"ids chosen at run time are only chosen inside messages.py itself (elsewhere: {[d for d in dynamic if not d.startswith('messages.py')]})")
unused = sorted(set(FAILURES) - asked)
check(not unused, f"every FAILURES entry is used somewhere (unused: {unused})")

# Render every entry as its callers do, with realistic values, and scan what a person would actually read
SAMPLE = {"ai": "Claude", "vendor": "Anthropic", "tools": "curl", "email": "sam@example.com", "service": "Slack",
          "party": "Google", "limit": "10 minutes", "job": "The refresh", "job_lower": "the refresh", "port": "8791",
          "store": "the Mac keychain"}
import string as _string  # noqa: E402


def holes_of(fid):
    m = FAILURES[fid]
    return {f for part_ in ("what", "fix", "fix_win") for _, f, _, _ in _string.Formatter().parse(m.get(part_) or "") if f}


def short_calls(cs):
    """Call sites that do not pass every placeholder their entry needs, each judged on its own (not the union)."""
    return [(where, i, sorted(holes_of(i) - keys)) for ids, keys, where in cs for i in ids if i in FAILURES and holes_of(i) - keys]


# the check itself catches one short call beside a complete one (the gap the review reproduced)
check(short_calls([({"install_timeout"}, {"ai", "limit"}, "a:1"), ({"install_timeout"}, {"ai"}, "b:2")]) == [("b:2", "install_timeout", ["limit"])],
      "a call that leaves out {limit} is caught even when another call passes it")
for name in ("agent.py", "doctor.py"):   # the CODEX_REFUSE templates, where they are shown: each use fills what it needs
    for m in re.finditer(r'CODEX_REFUSE\["(\w+)"\](\.format\(([^)]*(?:\([^)]*\)[^)]*)*)\))?', SOURCES[name]):
        line_no = SOURCES[name].count("\n", 0, m.start()) + 1
        if '"""' in SOURCES[name].splitlines()[line_no - 1]:
            continue   # a docstring naming it, not a use
        calls.append(({"codex_" + m.group(1)}, set(re.findall(r"(\w+)=", m.group(3) or "")), f"{name}:{line_no}"))
short = short_calls(calls)
check(not short, f"every call site passes every placeholder its entry needs (short: {short})")
for fid, m in FAILURES.items():
    holes = holes_of(fid)
    for win in (False, True):
        text = say(fid, win=win, **{k: SAMPLE[k] for k in holes})
        bad = [w for w in FORBIDDEN if w in text]
        check("{" not in text and "}" not in text and not bad, f"{fid} as shown{' on Windows' if win else ''}: no leftover placeholder or forbidden token {bad}")
check("INSTALL_SAID" not in (REPO / "openloops" / "agent.py").read_text(encoding="utf-8"),
      "the install sentences live in messages.py only (was agent.INSTALL_SAID)")
for inline in ("Couldn't ask Claude which connections", "Sign in to Claude first (the row above).\"",
               "This fills in by itself once Slack is connected", "Something went wrong talking to the app"):
    check(not any(inline in t for t in SOURCES.values()), f"no inline copy left of: {inline!r}")

# ---------------------------------------------------------------- 3. say / part / for_page
show("3. say(), part(), for_page()")
check(say("install_timeout", ai="Claude", limit="10 minutes") ==
      "The install took longer than 10 minutes, so Open Loops stopped it. Press Install Claude to try again.", "say() fills placeholders")
check(say("install_timeout", ai="Claude").startswith("The install took longer than {limit}"), "an unfilled placeholder stays as it is")
check(say("server_offline", win=False).endswith("on your Desktop or in Applications.")
      and say("server_offline", win=True).endswith("on your Desktop or in the Start menu."), "server_offline: Mac and Windows wording")
page = messages.for_page(win=True)
check(page["server_offline"]["fix"].endswith("Start menu.") and set(page) == set(FAILURES)
      and all(set(v) == {"what", "fix", "button", "recheck"} for v in page.values()), "for_page(): the whole table, fix chosen for the platform")
needs_list = sorted(k for k, v in FAILURES.items() if (k.startswith("job_") or k in messages.CODEX_JOB_IDS)
                    and ("connection checklist" in v["fix"] or v.get("button") == "login" or k in ("codex_keyring", "codex_link")))
check(needs_list == sorted(messages.RECHECK_AFTER_JOB) and all(page[k]["recheck"] for k in needs_list),
      f"every job failure that points at the checklist re-runs the check, Codex sign-outs included ({needs_list})")

# ---------------------------------------------------------------- 4. job failures
show("4. a failed job's sentence comes from the AI process's own diagnostics, via the job's last line")
from subprocess import CompletedProcess  # noqa: E402
af, jf = messages.ai_failure, messages.job_failure
check(af(1, "Invalid API key · Please run /login") == "job_signed_out", "stderr-only sign-in error -> job_signed_out")
check(af(1, "starting\nloading tools\nretrying once\nInvalid API key") == "job_signed_out",
      "a 4-line stderr diagnostic ending 'Invalid API key' -> job_signed_out")
check(af(1, "Claude AI usage limit reached|1760000000") == "job_usage_limit", "usage limit")
check(af(1, "API Error: Connection error.") == "job_network", "no network")
check(af(0, "Invalid API key") == "", "an AI run that exited 0 is never classified")
EMAIL = "\n".join(['<<<OPENLOOPS', '{"new_loops": [{"owner": "Sam",', '"ask": "reset my expenses login"}]', "Sam wrote:",
                    "Not logged in to the expenses portal, can you help?", "usage limit reached on the analytics plan"])
check(af(3, "", refused="expired") == "codex_expired", "Codex's own structured reason wins")
check(af(1, "warning: something\nthe email said: not logged in") == "", "a stderr line that only contains the words: not classified")
check(messages.ai_failure.__code__.co_varnames[:3] == ("rc", "stderr", "refused"), "ai_failure() takes no stdout at all")


import contextlib, io  # noqa: E402,E401


RID = "0123456789abcdef0123456789abcdef"


def reported(p, job="t", run_id=RID, ai="Claude"):
    """What report() leaves behind for a run started with OPENLOOPS_RUN_ID / OPENLOOPS_AI -> (record or None, printed)."""
    env = {k: v for k, v in (("OPENLOOPS_RUN_ID", run_id), ("OPENLOOPS_AI", ai)) if v}
    old = {k: os.environ.pop(k, None) for k in ("OPENLOOPS_RUN_ID", "OPENLOOPS_AI")}
    os.environ.update(env)
    try:
        f = messages.failure_file(job)
        f.unlink(missing_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            messages.report(p, job)
        return (json.loads(f.read_text(encoding="utf-8")) if f.exists() else None), buf.getvalue()
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


check(messages.failure_file("people", RID).name == f"people.{RID}.failure.json"
      and messages.failure_file("people", "").name == "people.scheduled.failure.json"
      and messages.failure_file("people", "../../x").name == "people.scheduled.failure.json",
      "one failure file per run id; no id (the scheduled refresh, a terminal) or a malformed one -> <job>.scheduled")
rec, out = reported(CompletedProcess([], 1, "", "Invalid API key · Please run /login"))
check(rec and rec["failure"] == "job_signed_out" and rec["run_id"] == RID and rec["ai"] == "Claude" and rec["at"] and out == "",
      "report() writes state/jobs/<job>.<run_id>.failure.json {run_id, failure, ai, at} and prints nothing")
rec, _ = reported(CompletedProcess([], 1, "", "Invalid API key"), ai="Grok")
check(rec["ai"] == "Grok", "report() names OPENLOOPS_AI, the AI captured when the run started, not the one chosen now")
rec, _ = reported(CompletedProcess([], 1, "", "Invalid API key"), run_id="")
check(rec and rec["run_id"] == "scheduled", "a run with no id records itself as scheduled (in <job>.scheduled.failure.json)")
rec, _ = reported(CompletedProcess([], 1, "Not logged in to the expenses portal, can you help?", ""))
check(rec is None, "stdout 'Not logged in to the expenses portal, can you help?': NOT classified, no file")
rec, _ = reported(CompletedProcess([], 1, EMAIL, ""))
check(rec is None, "...nor a longer model answer quoting the email")
line = agent.CODEX_REFUSE["timeout"].format(limit="15 minutes")
cp = CompletedProcess([], 3, line + "\n", "")
cp.refused = "timeout"
rec, _ = reported(cp)
check(rec and rec["failure"] == "codex_timeout" and rec["said"] == line, "a refused Codex run: its id and its own filled-in sentence")
check(jf("refresh", 3, "whatever", failure=rec) == ("codex_timeout", line), "...which is what the page shows")
check(jf("refresh", 1, "!! no OPENLOOPS block\nInvalid API key\nOPENLOOPS_FAILURE: job_signed_out")[0] == "job_failed",
      "job_failure: nothing in the job's output decides, only the failure file")
check(jf("refresh", 1, "x", ai="Claude", failure={"failure": "job_signed_out", "ai": "Grok"})[1].startswith("The refresh stopped because Claude"),
      "the record picks the sentence; the AI named is the one the app captured at start, never the record's")
check(jf("refresh", 1, "x", failure={"failure": "server_offline"})[0] == "job_failed", "an id that is not a job failure is ignored")
i, said = jf("voice", 1, "!! no VOICE block. See log.")
check(i == "job_failed" and said.startswith("Learning your tone didn't finish."), f"anything else: the job, named, didn't finish ({said!r})")
i, said = jf("refresh", -1, "could not start refresh: FileNotFoundError: python")
check(i == "job_start_failed" and "couldn't start the refresh" in said, "a job that could not start")
check(agent.CODEX_REFUSE["unlisted"] == say("codex_unlisted") and "{store}" in agent.CODEX_REFUSE["keyring"],
      "agent.CODEX_REFUSE is built from the table, placeholders left for the caller")
check("OPENLOOPS_FAILURE" not in "".join(SOURCES[f] for f in SOURCES if f.endswith(".py")) + "".join(
      (REPO / "openloops" / f).read_text(encoding="utf-8") for f in ("people.py", "voice.py")), "no stdout failure protocol is left")

e = app._ended("refresh", 1, "x", ai="Claude", failure={"failure": "job_signed_out", "ai": "Claude"})
check(e["failure"] == "job_signed_out" and e["said"].startswith("The refresh stopped because Claude") and e["rc"] == 1,
      "app: a failed job carries its failure id and sentence for the page")
check("said" not in app._ended("refresh", 0, "done") and "said" not in app._ended("chase", 2, "SKIPPED: x"),
      "app: a job that worked, or was SKIPPED (exit 2, it says why itself), carries no failure")
src = (REPO / "openloops" / "app.py").read_text(encoding="utf-8")
run_src = src[src.index("def run_job("):]
check(run_src.index('jobs[name] = {"running": True') < run_src.index("ai = _ai_now()") < run_src.index("OPENLOOPS_RUN_ID=run_id, OPENLOOPS_AI=ai")
      < run_src.index("p = subprocess.Popen(args"), "app: the job is claimed, and its AI and run id taken, before the process starts")

# a record that cannot be deleted is not swallowed: the job's log says so (and it is still this run's own file only)
stuck = messages.failure_file("voice", RID)
stuck.mkdir(parents=True, exist_ok=True)   # a folder where the file should be: unlink fails
rec_, note_ = app._run_failure("voice", RID, 1)
check(rec_ is None and "could not remove" in note_ and stuck.name in note_, f"a failure file that will not go is noted in the job's log ({note_.strip()!r})")
stuck.rmdir()

# two starts at the same moment: one run (the claim is taken under a lock before the process starts)
import threading  # noqa: E402
started, real_popen = [], app.subprocess.Popen


class SlowProc:
    pid, returncode = 999999, 0

    def __init__(self, *a, **kw):
        started.append(kw.get("env", {}).get("OPENLOOPS_RUN_ID"))
        time.sleep(0.3)

    def communicate(self):
        return "", ""


app.subprocess.Popen = SlowProc
try:
    gate, res = threading.Barrier(8), []
    ts = [threading.Thread(target=lambda: (gate.wait(), res.append(app.run_job("daylog")))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    wait_until(lambda: not app.jobs["daylog"]["running"], 5)
finally:
    app.subprocess.Popen = real_popen
check(res.count(True) == 1 and len(started) == 1 and messages.RUN_ID_RE.fullmatch(started[0] or ""),
      f"eight simultaneous starts: one run, with a run id ({res.count(True)} started, {len(started)} processes)")

# end to end through the app: a real people.py job against a fake claude
if sys.platform != "win32":
    e2e = fresh_install("openloops-jobfail-")
    (e2e / "bin").mkdir()
    fake = e2e / "bin" / "claude"
    srv2 = None

    def run_people(script):
        fake.write_text(f"#!{sys.executable}\n" + script, encoding="utf-8")
        fake.chmod(0o755)
        req = urllib.request.Request(f"http://127.0.0.1:{port2}/api/people", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        for _ in range(300):
            with urllib.request.urlopen(f"http://127.0.0.1:{port2}/api/state", timeout=10) as r:
                j = json.loads(r.read())["jobs"]["people"]
            if not j["running"] and "rc" in j:
                return j
            time.sleep(0.1)
        raise SystemExit("FAIL: the people job did not finish")

    try:
        srv2, port2 = start_app(e2e, isolated_env(e2e, BROWSER="true", PATH=f"{e2e / 'bin'}:/usr/bin:/bin"))
        j = run_people("import sys\nsys.stderr.write('Invalid API key · Please run /login\\n'); sys.exit(1)\n")
        check(j["rc"] == 1 and j.get("failure") == "job_signed_out", f"people.py, claude signed out (stderr only): signed out ({j.get('failure')})")
        j = run_people("print('OPENLOOPS_FAILURE: job_signed_out')\nprint('Not logged in to the expenses portal, can you help?')\n")
        check(j["rc"] == 1 and j.get("failure") == "job_failed",
              "the AI's answer printing 'OPENLOOPS_FAILURE: job_signed_out' / 'Not logged in…' cannot forge a sign-out")
        jd = e2e / "state" / "jobs"
        jd.mkdir(parents=True, exist_ok=True)
        other = jd / "people.ffffffffffffffffffffffffffffffff.failure.json"
        sched = jd / "people.scheduled.failure.json"
        other.write_text(json.dumps({"run_id": "f" * 32, "failure": "job_signed_out", "ai": "Claude"}), encoding="utf-8")
        sched.write_text(json.dumps({"run_id": "scheduled", "failure": "job_signed_out", "ai": "Claude"}), encoding="utf-8")
        j = run_people("raise SystemExit(1)\n")
        check(j.get("failure") == "job_failed" and other.exists() and sched.exists(),
              "another run's failure file and the scheduled run's are ignored (and left alone)")
        cfgf = e2e / "config.json"
        j = run_people("import json, sys\n"
                       f"p = {str(cfgf)!r}\nc = json.load(open(p)); c['agent'] = 'grok'; json.dump(c, open(p, 'w'))\n"
                       "sys.stderr.write('Invalid API key\\n'); sys.exit(1)\n")
        check(j.get("failure") == "job_signed_out" and "because Claude has signed you out" in j.get("said", ""),
              f"the AI switched to Grok while the run was going: the sentence names Claude, which ran ({j.get('said')!r})")
        check(not [f for f in jd.glob("people.*.failure.json") if f not in (other, sched)],
              "the app deletes its own run's failure file once read")
    finally:
        stop(srv2)
        shutil.rmtree(e2e, ignore_errors=True)

show("4b. download_why(): a vendor's error is not the user's internet")
lg = Path(tempfile.mkdtemp(prefix="openloops-dlwhy-")) / "install.log"
lg.write_text("", encoding="utf-8")
dw = app.download_why
check(dw(["curl", "-fsSL"], 22, lg) == "vendor" and dw(["curl"], 6, lg) == "network" and dw(["curl"], 28, lg) == "network"
      and dw(["curl"], 18, lg) == "download", "curl: 22 (HTTP error) = vendor, 6/28 = network, anything else = can't tell")
check(dw(["test", "-s", "x"], 1, lg) == "vendor" and dw(["mkdir", "-p", "x"], 1, lg) == "download", "an empty download = vendor")
lg.write_text("Invoke-WebRequest : The remote server returned an error: (404) Not Found.", encoding="utf-8")
check(dw(["powershell", "-Command", "x"], 1, lg) == "vendor", "Windows: a 404 from the site = vendor")
lg.write_text("Invoke-WebRequest : The remote name could not be resolved: 'claude.ai'", encoding="utf-8")
check(dw(["powershell", "-Command", "x"], 1, lg) == "network", "Windows: no DNS = network")
shutil.rmtree(lg.parent, ignore_errors=True)

# ---------------------------------------------------------------- 5. the served page
show("5. the page as the app serves it")
tmp = fresh_install("openloops-messages-")
srv = None
try:
    srv, port = start_app(tmp, isolated_env(tmp, BROWSER="true"))
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
        html = r.read().decode("utf-8")
        cache = r.headers.get("Cache-Control")
finally:
    stop(srv)
    shutil.rmtree(tmp, ignore_errors=True)
m = re.search(r"^const MSG=(.*);$", html, re.M)
check(m and "/*OL_MESSAGES*/" not in html, "the app fills in the page's failure table as it serves it")
served = json.loads(m.group(1))
check(cache == "no-store", "the page is served with Cache-Control: no-store, so a cached copy never keeps old wording")
check(served == messages.for_page(sys.platform == "win32"), "...with exactly messages.for_page() for this platform")
check('<div id="lists" style="display:none">' in html, "before the first check, the lists are hidden (#34)")
check(re.search(r'<button class="primary" id="refresh"[^>]*style="display:none"', html), "...and so is Refresh")
check(re.search(r'<div id="steps" class="steps"><span class="sub"><span class="spin"></span>Checking your connections…</span></div>', html),
      "...and the page says it is checking your connections")
check("$('#lists').style.display=st==='ready'?'':'none'" in html, "the lists come back only once setup is done")
con = [l for l in html.splitlines() if l.startswith("function clog(") or "Open Loops diagnostics" in l]
check(len(con) == 2 and not any("toISOString" in l for l in con) and all("stamp()" in l for l in con)
      and "d.getHours()" in html, "Console times (and Copy all's header) are local, not UTC (#34)")
check(re.search(r'href="\$\{esc\(l\.link\)\}" target="_blank" rel="noopener">open</a>', html), "the loop row's open link has rel=noopener")
check("they replied or you wrote a note, you owe a reply" not in html and "someone asked you something" in html,
      "Needs me says inbound asks are in it")
check("you haven't replied yet" in html and "no reply yet`" not in html, "inbound cards say you haven't replied, not 'no reply yet'")
check("Two or three minutes" not in html and "C.history_days" in html and "ten minutes" in html,
      "first scan copy uses the History setting and an honest time")
check("gmail ok" not in html and "not connected (optional)" not in html, "no lower-case 'gmail ok' pill")
check("<summary>Show the exact command" in html and "This runs" not in html, "the install command is folded behind 'Show the exact command'")
check("'▫️'" not in html and "'⬜'" not in html and "!s.optional||s.connect||s.alert?'<b style=\"color:var(--r)\" title=\"needs attention\"" in html,
      "an unticked row that needs something done gets a red mark, not a white box")
check("02-Research" not in html and "C:\\\\Users\\\\you\\\\Documents\\\\to-do.md" in html and "/Users/you/Documents/to-do.md" in html,
      "the to-do file example is per platform and names no developer folder")
check("black window" not in html.split("id=\"agent_help\"")[1].split("</div>")[0], "Home copy no longer promises a black window with /mcp")

# the table goes into an inline <script>: a sentence holding </script>, quotes, backslashes or U+2028 must survive
from html.parser import HTMLParser  # noqa: E402
NASTY = 'He said "</script><script>alert(1)</script>" & \\ it\'s <!-- odd --> \u2028 fine.'
evil = dict(served, server_offline=dict(served["server_offline"], what=NASTY))
evil_html = app.index_bytes(evil).decode("utf-8")


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside, self.found = False, []

    def handle_starttag(self, tag, attrs):
        self.inside = tag == "script"
        if self.inside:
            self.found.append("")

    def handle_endtag(self, tag):
        self.inside = False if tag == "script" else self.inside

    def handle_data(self, data):
        if self.inside:
            self.found[-1] += data


sp = Scripts()
sp.feed(evil_html)
check(len(sp.found) == 1 and "const MSG=" in sp.found[0] and "</script>" not in sp.found[0].split("const MSG=")[1].split("\n")[0],
      "a sentence holding </script> does not end the page's script element (HTML parsing sees one script)")
msg_line = next(l for l in sp.found[0].splitlines() if l.startswith("const MSG="))

# ---------------------------------------------------------------- 6. offline banner, run in node
show("6. the offline banner, in node")
node = shutil.which("node")
if not node:
    if os.environ.get("GITHUB_ACTIONS"):   # CI installs node (tests.yml): a missing one there is a broken setup, not a skip
        raise SystemExit("FAIL: node is not on PATH in CI, so the page's JavaScript was not tested")
    show("SKIP the node run: node not installed (the static checks above still ran)")
    raise SystemExit(0)
lines = html.splitlines()
grab = lambda start: next(l for l in lines if l.startswith(start))
api_src = html[html.index("const api=async"):html.index("return r.json()};") + len("return r.json()};")]
banner_css = re.search(r"#banner\{display:none", html)
check(banner_css, "the stylesheet hides #banner by default (so '' would hide it)")
JS = "\n".join([
    "const els={};const $=s=>els[s]||(els[s]={style:{},textContent:''});const CON=[];function clog(m){CON.push(String(m))}",
    "const PAGE='t';", grab("const MSG="), grab("const fill="), grab("function msg("), api_src,
    "let lastBanner='';", grab("function banner("), grab("const appDown="), grab("const localErr="), grab("const errSaid="),
    """(async()=>{const out={};
 global.fetch=async()=>{throw new TypeError('Failed to fetch')};
 try{await api('/api/state')}catch(e){appDown(e)}out.offline={display:$('#banner').style.display,text:$('#banner').textContent};
 global.fetch=async()=>({ok:false,status:500,text:async()=>'{"error":"boom"}'});
 try{await api('/api/state')}catch(e){out.status=e.status;out.body=e.body;appDown(e)}out.error={display:$('#banner').style.display,text:$('#banner').textContent};
 banner('');out.cleared=$('#banner').style.display;
 global.fetch=async()=>({ok:false,status:400,text:async()=>'{"ok":false,"error":"Open Loops cannot create a file in that folder. Check the folder exists.","detail":"[Errno 13] Permission denied: /Users/x/notes"}'});
 try{await api('/api/standing/create',{})}catch(e){out.said400=errSaid(e)}
 global.fetch=async()=>({ok:false,status:409,text:async()=>'{"started":false,"error":"updated","said":"Open Loops was updated."}'});
 try{await api('/api/connect/install',{})}catch(e){out.said409=errSaid(e)}
 global.fetch=async()=>{throw new TypeError('Failed to fetch')};
 try{await api('/api/config',{})}catch(e){out.saidOff=errSaid(e)}
 out.console=CON.join(' | ');
 out.local=errSaid(localErr('pick a date'));out.bare=errSaid(new Error('boom -> 500 {"x":1}'));
 try{await (async()=>{throw localErr('already pinned')})()}catch(e){out.pinned=errSaid(e)}
 console.log(JSON.stringify(out))})();"""])
r2 = subprocess.run([node, "-e", msg_line + "\nconsole.log(JSON.stringify(MSG.server_offline.what))"], capture_output=True, text=True, timeout=30)
check(r2.returncode == 0 and json.loads(r2.stdout) == NASTY,
      f"...and JavaScript reads it back exactly: quotes, backslashes, <!--, & and U+2028 intact ({r2.stderr.strip()[-200:]})")
r = subprocess.run([node, "-e", JS], capture_output=True, text=True, timeout=30)
check(r.returncode == 0, f"node ran the page's own functions ({r.stderr.strip()[-300:]})")
out = json.loads(r.stdout.strip().splitlines()[-1])
want = say("server_offline")
check(out["offline"] == {"display": "block", "text": want},
      f"app not running: banner shown (display:block) saying {want!r} (got {out['offline']})")
check(out["error"]["display"] == "block" and out["error"]["text"] == say("server_error") and out["status"] == 500
      and out["body"] == {"error": "boom"}, "app answered with an error: a different sentence, and the error carries status and body")
check(out["cleared"] == "none", "banner('') hides it again")
PS = grab("window.addEventListener('pageshow'")
for ok_, want_ in ((True, "reload"), (False, "loop")):
    js = ("let did=[];const location={reload:()=>did.push('reload')};const loop=()=>did.push('loop');let stopped=false;const H={};"
          "const window={addEventListener:(n,f)=>H[n]=f};global.fetch=async()=>" + ("({ok:true})" if ok_ else "{throw new TypeError('x')}") + ";\n"
          + PS + "\nH.pageshow({persisted:true});H.pageshow({persisted:false});setTimeout(()=>console.log(JSON.stringify(did)),50);")
    r3 = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    check(r3.returncode == 0 and json.loads(r3.stdout) == [want_], f"restored page, app {'up' if ok_ else 'gone'}: {want_} ({r3.stdout.strip()} {r3.stderr.strip()[-150:]})")
check(out["said400"] == "Open Loops cannot create a file in that folder. Check the folder exists." and out["said409"] == "Open Loops was updated."
      and out["saidOff"] == want, "a failed request shows the app's own sentence (said, else error), or 'not running' when offline")
check(out["local"] == "pick a date" and out["pinned"] == "already pinned" and out["bare"] == say("server_error"),
      "a check made on the page keeps its own words ('pick a date', 'already pinned'); an unmarked error never shows its raw text")
check("if(e.persisted&&!stopped)fetch('/',{cache:'no-store'}).then(r=>{if(r.ok)location.reload();else loop()},()=>loop())" in html,
      "a page restored from the back/forward cache reloads (fresh wording) when the app answers, else shows 'not running'")
check("throw new Error(" not in html and html.count("throw localErr(") >= 5, "every validation the page does itself is thrown as a local error")
check("Permission denied" in out["console"] and "-> 400" in out["console"], "...while the status, raw body and detail go to the Console only")
check("if(MSG[j.failure]&&MSG[j.failure].recheck)doctor(true)" in html and "j.failure==='job_signed_out'" not in html,
      "the page re-checks connections after any such failure, not only job_signed_out")
check("e.message.replace(/^.*-> " not in html and html.count("errSaid(e)") >= 11,
      "no toast or message shows a raw response body any more (Settings save, to-do file, dialogs, card actions)")
show("PASS")
