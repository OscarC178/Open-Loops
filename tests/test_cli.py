"""The developer console, `python3 -m openloops <command>` (openloops/cli.py).

    python3 tests/test_cli.py    # fast; no Slack/Gmail/Claude. Throwaway install and $HOME, spare ports.

Checks, in a throwaway install seeded with four loops:
  1. --help: exit 0, every command in cli.COMMANDS has a row, every line under 80 columns, nothing written (run in a
     bare copy holding only openloops/). An unknown command or option: exit 1, one line plus the usage line on stderr.
  2. drift guards, in-process: cli.JOB_MOD is app.JOB_MOD; every --option named in the help text is one the parser
     knows; actions.ACTIONS lists what app.py's /api/action used to switch on.
  3. offline (no server on the port): status, list (each view, the page's order), show, act (done / reopen / snooze
     with a good and a bad date / priority / add_link refused / unknown id), add, config get and set (nested keys,
     JSON values, a note for an unknown key), logs on an empty folder, agent, run (daylog --digest-only writes the
     day's file; an unknown job is refused). Every write lands in state.json / config.json as the page's would.
  4. online: app start --no-browser brings this copy's server up on the spare port; status and list read from it;
     act and api go through it (the server's /api/state shows the change); jobs lists the job table; a console run
     from ANOTHER copy pointed at the same port reports "another copy" and does not use it; app stop stops it.
"""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from _helpers import free_port, fresh_install, isolate_this_process, isolated_env, listening, wait_until  # noqa: E402
isolate_this_process("openloops-cli-parent-")   # part 2 imports app, which writes its files: into a throwaway copy
t0 = time.time()
WIN = sys.platform == "win32"


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if "__pycache__" not in p.parts)


today = time.strftime("%Y-%m-%d")
tmp = fresh_install("openloops-cli-", {"owner_name": "Oscar", "people": {"Alice": {"email": "alice@example.com"}}})
(tmp / "state.json").write_text(json.dumps({
    "cursor": "2026-01-01T00:00", "last_refresh": "2026-01-02T00:00",
    "loops": [{"id": "alice-report", "owner": "Alice", "ask": "the report", "channel": "email", "priority": "normal",
               "asked_at": "2026-01-01T10:00", "status": "waiting", "chases": 0, "snooze_until": None, "notes": ""},
              {"id": "bob-deck", "owner": "Bob", "ask": "the deck", "channel": "slack", "priority": "normal",
               "asked_at": f"{today}T09:00", "status": "waiting", "chases": 0, "snooze_until": None, "notes": ""},
              {"id": "cat-quote", "owner": "Cat", "ask": "a quote", "channel": "email",
               "asked_at": "2026-02-01T10:00", "status": "done", "closed_at": f"{today}T10:00", "chases": 0, "snooze_until": None},
              {"id": "dan-q", "owner": "Dan", "ask": "an answer", "channel": "slack", "priority": "high",
               "asked_at": "2026-03-01T10:00", "status": "needs_me", "chases": 0, "snooze_until": "2099-01-01", "notes": ""}],
}), encoding="utf-8")
env = isolated_env(tmp)
env["PYTHONDONTWRITEBYTECODE"] = "1"
pid = None


def cli(*args, port=None, cwd=None, timeout=90):
    """The console in the throwaway install -> (exit code, stdout, stderr). port -> OPENLOOPS_PORT for that run."""
    e = dict(env)
    if port:
        e["OPENLOOPS_PORT"] = str(port)
    r = subprocess.run([sys.executable, "-m", "openloops", *args], cwd=cwd or tmp, env=e, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def cli_json(*args, **kw):
    rc, out, err = cli(*args, "--json", **kw)
    check(rc == 0, f"{' '.join(args)} --json exits 0 ({rc}: {err.strip()[-300:]})")
    return json.loads(out)


def state():
    return json.loads((tmp / "state.json").read_text(encoding="utf-8"))


def loop(lid):
    return next(l for l in state()["loops"] if l["id"] == lid)


try:
    # ------------------------------------------------------------ 1. --help writes nothing; errors are one line + usage
    bare = tmp / "bare"
    shutil.copytree(REPO / "openloops", bare / "openloops", ignore=shutil.ignore_patterns("__pycache__"))
    before = tree(bare)
    rc, out, err = cli("--help", cwd=bare)
    lines = out.splitlines()
    check(rc == 0 and not err and lines[0].startswith("usage: ") and "-m openloops " in lines[0], "--help: exit 0, usage line first")
    check(all(len(ln) < 80 for ln in lines), "--help: every line under 80 columns")
    check(tree(bare) == before and not (bare / "config.json").exists(), "--help: nothing written (no config.json, state.json or state/)")
    rc2, out2, _ = cli(cwd=bare)
    check(rc2 == 0 and out2 == out, "no command at all prints the same help")
    for args, said in ((["bogus"], "unknown command: bogus"), (["list", "--nope"], "unknown option: --nope"),
                       (["status", "--port", "80"], "--port needs a number"), (["act", "done"], "act needs an action and a loop id"),
                       (["show"], "show needs a loop id"), (["list", "later"], "list takes one of")):
        rc, out, err = cli(*args, cwd=bare)
        check(rc == 1 and not out and said in err and "usage: " in err, f"{' '.join(args)}: exit 1, '{said}' + usage on stderr")
    check(tree(bare) == before, "the refused commands wrote nothing either")

    # ------------------------------------------------------------ 2. drift guards
    from openloops import actions, app, cli as console   # noqa: E402  (the throwaway copy isolate_this_process made)
    check(console.JOB_MOD == app.JOB_MOD, "cli.JOB_MOD is app.JOB_MOD")
    named = set(re.findall(r"(?<![\w-])(--[a-z-]+|-n)(?=[ |\]\n])", console.HELP))
    known = console.FLAGS | console.VALUED
    # the job options in the `run` rows belong to the jobs, not the console
    job_opts = {"--slack-only", "--digest-only", "--confirm"}
    check(named - job_opts <= known, f"every option the help names is one the parser knows ({sorted(named - job_opts - known)})")
    for cmd in console.COMMANDS:
        check(any(re.match(rf"  {re.escape(cmd)}( |$)", ln) for ln in console.HELP.splitlines()), f"help has a row for {cmd}")
    src = (REPO / "openloops" / "app.py").read_text(encoding="utf-8")
    check("actions.apply(" in src and "def act_on" not in src, "app.py's /api/action goes through actions.apply")
    check(set(actions.ACTIONS) == {"add", "done", "reopen", "snooze", "unsnooze", "priority", "auto_on", "auto_off",
                                   "note", "add_link", "drop_link"}, "actions.ACTIONS lists every click")
    # two alike adds within a second get distinct ids, so a later done hits one loop, not both (#75 review)
    s = {"loops": []}
    a = actions.apply(s, {"action": "add", "ask": "send the deck", "owner": "Alice"})[0]["id"]
    b = actions.apply(s, {"action": "add", "ask": "send the deck", "owner": "Alice"})[0]["id"]
    check(a != b and a.startswith("note-alice-send-the-deck-") and b.startswith("note-alice-send-the-deck-"),
          "add: two alike adds within a second get distinct ids")
    actions.apply(s, {"action": "done", "id": a})
    check([l["status"] for l in s["loops"]] == ["needs_me", "done"], "done on one of them closes only that one")
    # a click on nothing is refused and writes nothing: an unknown id, an unknown action, no id
    ans, code, write = actions.apply(s, {"action": "done", "id": "no-such-loop"})
    check(code == 404 and not write and "no-such-loop" in ans["error"], "apply: an unknown loop id is 404, write=False")
    ans, code, write = actions.apply(s, {"action": "explode", "id": a})
    check(code == 400 and not write and "explode" in ans["error"], "apply: an unknown action is 400, write=False")
    check(actions.apply(s, {"action": "done"})[1:] == (404, False), "apply: a click with no id is 404, write=False")

    # ------------------------------------------------------------ 3. offline: no server on this port
    port = free_port()
    st = cli_json("status", port=port)
    check(st["server"] is None and st["preferred_port"] == port and Path(st["root"]).resolve() == tmp.resolve(), "status: no server, this copy's root and port")
    check(st["loops"] == {"needs_me": 0, "waiting": 2, "snoozed": 1, "done": 1} and st["agent"] == "claude" and st["people"] == 1,
          f"status: the page's counts (snoozed Dan is not in Needs me) ({st['loops']})")
    rc, out, err = cli("status", port=port)
    check(rc == 0 and "not running" in out and "needs me 0 · waiting 2 · snoozed 1 · done 1" in out, "status (text): server not running, counts")
    check([r["id"] for r in cli_json("list", port=port)] == [], "list (needs-me by default): nothing live needs me")
    rows = cli_json("list", "waiting", port=port)
    check([r["id"] for r in rows] == ["alice-report", "bob-deck"] and rows[0]["age_wd"] > rows[1]["age_wd"] == 0,
          "list waiting: oldest first within a priority, age in workdays as the page counts")
    check(rows[0]["band"] == "red" and rows[1]["band"] == "green", "list: the page's colour band per row")
    check([r["id"] for r in cli_json("list", "snoozed", port=port)] == ["dan-q"], "list snoozed: Dan")
    check([r["id"] for r in cli_json("list", "done", port=port)] == ["cat-quote"], "list done: Cat")
    check(len(cli_json("list", "all", port=port)) == 4, "list all: every loop")
    rc, out, _ = cli("list", "waiting", port=port)
    check(rc == 0 and out.startswith("waiting: 2 loop(s), from state.json") and "alice-report" in out, "list (text): source and rows")
    rc, out, _ = cli("show", "alice-report", port=port)
    check(rc == 0 and json.loads(out)["ask"] == "the report", "show: the loop as JSON")
    rc, _, err = cli("show", "nobody", port=port)
    check(rc == 1 and "no loop with id nobody" in err, "show: unknown id refused")

    rc, out, _ = cli("act", "done", "bob-deck", port=port)
    check(rc == 0 and loop("bob-deck")["status"] == "done" and loop("bob-deck")["closed_at"][:10] == today and "undo: act reopen bob-deck" in out,
          "act done: status done with closed_at, undo named")
    rc, _, _ = cli("act", "reopen", "bob-deck", port=port)
    check(rc == 0 and loop("bob-deck")["status"] == "waiting", "act reopen: back to waiting")
    rc, _, _ = cli("act", "snooze", "alice-report", "--until", "2099-1-5", port=port)
    check(rc == 0 and loop("alice-report")["snooze_until"] == "2099-01-05", "act snooze: date zero-padded, as the page's")
    rc, _, err = cli("act", "snooze", "alice-report", "--until", "next week", port=port)
    check(rc == 1 and "date must be YYYY-MM-DD" in err and loop("alice-report")["snooze_until"] == "2099-01-05", "act snooze: a bad date is refused, nothing changes")
    rc, _, err = cli("act", "snooze", "alice-report", port=port)
    check(rc == 1 and "needs --until" in err, "act snooze without --until: told what is missing")
    rc, _, _ = cli("act", "unsnooze", "alice-report", port=port)
    check(rc == 0 and loop("alice-report")["snooze_until"] is None, "act unsnooze")
    rc, _, err = cli("act", "priority", "alice-report", "--priority", "urgent", port=port)
    check(rc == 1 and "high, normal or low" in err, "act priority: only the three values")
    rc, _, _ = cli("act", "priority", "alice-report", "--priority", "high", port=port)
    check(rc == 0 and loop("alice-report")["priority"] == "high" and loop("alice-report")["priority_by"] == "you", "act priority high: marked as yours")
    rc, _, err = cli("act", "add_link", "alice-report", "--url", "javascript:alert(1)", port=port)
    check(rc == 1 and "must start with http" in err, "act add_link: a non-http link is refused")
    rc, _, _ = cli("act", "add_link", "alice-report", "--url", "https://docs.google.com/d/1", "--label", "brief", port=port)
    check(rc == 0 and loop("alice-report")["links"] == [{"url": "https://docs.google.com/d/1", "label": "brief"}], "act add_link")
    rc, _, _ = cli("act", "note", "alice-report", "--notes", "ask again Friday", port=port)
    check(rc == 0 and loop("alice-report")["notes"] == "ask again Friday", "act note")
    rc, _, err = cli("act", "done", "nobody", port=port)
    check(rc == 1 and "no loop with id nobody" in err, "act on an unknown id: refused before any write")
    rc, _, err = cli("act", "shout", "alice-report", port=port)
    check(rc == 1 and "act takes one of" in err, "act with an unknown action: refused")
    rc, out, _ = cli("add", "typed", "reminder", "--owner", "alice", port=port)
    new = [l for l in state()["loops"] if l["id"].startswith("note-alice-typed-reminder-")]
    check(rc == 0 and new and new[0]["status"] == "needs_me" and new[0]["owner"] == "Alice" and new[0]["owner_email"] == "alice@example.com",
          "add: a typed reminder, owner matched to the people list, in Needs me")
    check([r["id"] for r in cli_json("list", port=port)] == [new[0]["id"]], "list needs-me now shows it")

    check(cli_json("config", "effort", port=port) == "high", "config <key>: the value in force")
    rc, out, _ = cli("config", "set", "effort", "medium", port=port)
    cfgf = json.loads((tmp / "config.json").read_text(encoding="utf-8"))
    check(rc == 0 and cfgf["effort"] == "medium" and 'effort = "medium"' in out, "config set: a plain string")
    rc, _, _ = cli("config", "set", "auto_chase.enabled", "true", port=port)
    rc2, _, _ = cli("config", "set", "history_days", "14", port=port)
    cfgf = json.loads((tmp / "config.json").read_text(encoding="utf-8"))
    check(rc == 0 and rc2 == 0 and cfgf["auto_chase"]["enabled"] is True and cfgf["auto_chase"]["after_workdays"] == 3 and cfgf["history_days"] == 14,
          "config set: nested key and JSON values, the rest of the object kept")
    rc, _, err = cli("config", "set", "made_up", "1", port=port)
    check(rc == 0 and "not a setting in config.template.json" in err, "config set: an unknown key is written with a note")
    cfg_all = cli_json("config", port=port)
    check(cfg_all["effort"] == "medium" and "effort_note" not in cfg_all and "made_up" in cfg_all, "config: every setting, the _note keys left out")
    rc, _, err = cli("config", "nothing.here", port=port)
    check(rc == 1 and "no setting called" in err, "config <unknown key>: refused")

    rc, out, _ = cli("logs", port=port)
    check(rc == 0 and "empty" in out, "logs: an empty logs folder says so")
    rc, _, err = cli("logs", "refresh", port=port)
    check(rc == 1 and "no log for refresh" in err, "logs refresh: none yet")
    ag = cli_json("agent", port=port)
    check(ag["agent"] == "claude" and ag["job_argv"][:4] == ["claude", "-p", "--output-format", "json"] and "--allowedTools" in ag["job_argv"],
          "agent: the job's command line, as agent.claude_args builds it")
    rc, out, _ = cli("agent", port=port)
    check(rc == 0 and "agent          claude" in out, "agent (text)")

    rc, out, err = cli("run", "daylog", "--digest-only", port=port)
    check(rc == 0 and "< daylog: rc 0 (ok)" in out and (tmp / "state" / "daylog" / f"{today}.json").exists(),
          f"run daylog --digest-only: runs in the foreground, writes the day's file ({err.strip()[-200:]})")
    rc, _, err = cli("run", "mop", port=port)
    check(rc == 1 and "no job called mop" in err, "run: an unknown job is refused")
    rc, _, err = cli("run", "chase", port=port)
    check(rc == 1 and "needs a loop id" in err, "run chase without an id is refused")
    rc, _, err = cli("jobs", port=port)
    check(rc == 1 and "not running" in err and "app start" in err, "jobs without a server: says how to start one")
    rc, out, _ = cli("app", port=port)
    check(rc == 0 and "not running" in out, "app: not running")
    rc, out, _ = cli("app", "url", port=port)
    check(rc == 0 and f"would be http://localhost:{port}" in out, "app url: what it would be")

    # ------------------------------------------------------------ 4. online: this copy's server, started by the console
    rc, out, err = cli("app", "start", "--no-browser", port=port)
    m = re.search(r"started \(pid (\d+)\) on (\d+)", out)
    check(rc == 0 and m and int(m.group(2)) == port, f"app start: comes up on the spare port ({out.strip()} {err.strip()[-200:]})")
    pid = int(m.group(1))
    check((tmp / "state" / "logs" / "app.log").exists(), "app start: the server's output goes to state/logs/app.log")
    rc, out, _ = cli("app", "start", "--no-browser", port=port)
    check(rc == 0 and "already running" in out, "app start again: already running, nothing started")
    st = cli_json("status", port=port)
    check(st["server"] and st["server"]["port"] == port and st["server"]["url"] == f"http://localhost:{port}", "status: sees this copy's server")
    rc, out, _ = cli("list", "waiting", port=port)
    check(rc == 0 and f"from server on {port}" in out and "alice-report" in out, "list: read from the server now")
    rc, out, _ = cli("act", "done", "alice-report", port=port)
    check(rc == 0 and f"via server on {port}" in out, "act done: through the server")
    rc, out, _ = cli("api", "GET", "/api/state", port=port)
    got = json.loads(out)
    check(rc == 0 and next(l for l in got["state"]["loops"] if l["id"] == "alice-report")["status"] == "done", "api GET /api/state: the server has the change")
    rc, out, _ = cli("api", "POST", "/api/action", '{"action": "reopen", "id": "alice-report"}', port=port)
    check(rc == 0 and json.loads(out) == {"ok": True} and loop("alice-report")["status"] == "waiting", "api POST /api/action: a body, applied")
    rc, out, _ = cli("api", "POST", "/api/action", '{"action": "done", "id": "typo"}', port=port)
    check(rc == 1 and "typo" in out, "api POST /api/action: a click on an unknown id is refused by the server, exit 1")
    rc, out, err = cli("api", "POST", "/api/action", "{not json", port=port)
    check(rc == 1 and "not JSON" in err, "api: a bad body is refused before any request")
    rc, out, _ = cli("api", "GET", "/api/nothing", port=port)
    check(rc == 1 and "not found" in out, "api: the server's error and exit 1")
    rc, out, _ = cli("jobs", port=port)
    check(rc == 0 and all(j in out for j in app.JOB_MOD), "jobs: every job named")
    rc, out, _ = cli("diag", port=port)
    check(rc == 0 and f"port             {port}" in out and str(tmp.resolve()) in out, "diag: the server's own report")
    rc, out, _ = cli("run", "daylog", "--digest-only", port=port)
    check(rc == 0, "run with the server up: the job is not running there, so it runs")
    # another copy pointed at this port: named, never used
    other = fresh_install("openloops-cli-other-")
    rc, out, _ = cli("app", port=port, cwd=other)
    check(rc == 0 and "not running" in out and f"port {port} is another copy: {tmp.resolve()}" in out, "a console run from another copy names this server as another copy")
    rc, _, err = cli("act", "done", "alice-report", port=port, cwd=other)
    check(rc == 1 and "no loop with id" in err and loop("alice-report")["status"] == "waiting", "...and its act never reaches this server")
    shutil.rmtree(other, ignore_errors=True)
    rc, out, _ = cli("app", "stop", port=port)
    check(rc == 0 and wait_until(lambda: not listening(port), 20), "app stop: the server is gone")
    pid = None
    say("all passed")
finally:
    if pid:
        try:
            if WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                os.kill(pid, 9)
        except OSError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)
