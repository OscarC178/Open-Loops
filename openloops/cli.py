"""The developer console: this copy's state, settings, jobs, logs and server from a terminal.

    python3 -m openloops <command> [options]      (npm run cli -- <command> runs it for a checkout, on 8766)

Everything the page can do or see, without the page, so a change can be built, run and checked in one terminal.
It works on the copy it is run from (a git checkout, or the installed folder): `python3 -m openloops status` in
~/Library/Application Support/OpenLoops is the live install, in a checkout it is that checkout. When this copy's
server is running the console talks to it over the same /api routes the page uses, so a `done` here is the same
`done` as the button and the page sees it on its next poll; when it is not, loops are read from state.json and
actions are applied under the same file lock the jobs and the page hold (store._locked). It never touches a server
of another copy: a server that answers on the port but reports a different root is named and left alone.

Nothing here keeps a key or a token. Jobs started with `run` use agent.py exactly as the page does, with the AI CLI
the user is signed in to; `agent` warns when a provider key sits in this shell's environment, because a job started
from this terminal would inherit it. OPENLOOPS_AGENT=mock (or `config set agent mock`) runs them against the mock
agent instead, canned answers in seconds and no sign-in (mock_agent.py, #76); `screenshot` captures the page itself.

`--help` imports nothing but this module's stdlib needs and writes nothing (as `python3 -m openloops.app --help`,
#64): the modules that create config.json / state.json are imported only by the commands that need them.
"""
import json, os, re, shutil, socket, subprocess, sys, time, urllib.error, urllib.request, uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from .paths import ROOT
from .store import CONFIG, STATE, TEMPLATE, _locked, isolated, load_cfg, load_state, read_json, update_json, write_json

WIN = sys.platform == "win32"
_PY = "python" if WIN else "python3"
USAGE = f"usage: {_PY} -m openloops <command> [options]"
HELP = """
The developer console: this copy's state, settings, jobs, logs and server,
from a terminal. Run it from the copy you mean (a checkout, or the installed
folder). With the server running, actions go through it, as the page's do.

Commands:
  status                   this copy: root, server, agent, loop counts, jobs
  list [which] [--json]    loops as the page lists them: needs-me (default),
                           waiting, snoozed, done, all
  show <id>                one loop, every field
  act <action> <id> [opt]  done | reopen | snooze --until YYYY-MM-DD |
                           unsnooze | priority --priority high|normal|low |
                           auto_on | auto_off | note --notes T |
                           add_link --url U [--label L] | drop_link --url U
  add <ask> [--owner N] [--notes T]
                           a typed reminder, as + note on the page
  run <job> [job options]  a job in the foreground, output as it comes:
                           refresh [--slack-only], chase <id>,
                           daylog [--digest-only], people, voice,
                           roadmap <mode> [--confirm], standing
                           (OPENLOOPS_AGENT=mock: canned answers, no AI)
  jobs                     what the running server's jobs are doing
  logs [job] [-n N] [--all]
                           the newest run logs, or the tail of a job's newest
  doctor [--detect] [--recheck] [--json]
                           the connection checklist, as the page runs it
  diag [--json]            what the Console's Copy all pastes
  config [key] [--json]    the settings in force (config.json over template)
  config set <key> <value> change one setting (value is JSON, else a string)
  agent [--check]          which AI runs the jobs, its command line, sign-in
  app [start|stop|open|url] [--no-browser] [--now]
                           this copy's server: start it, stop it, open its page
  screenshot [--width N] [--out F]
                           the page, captured headless (Chrome + Node), as a
                           PNG in state/logs/; prints the file's path
  api <METHOD> </api/path> [json body]
                           any route on the running server
  test [name ...]          every test, or the tests whose file name contains
  -h, --help               this list; starts, reads and writes nothing

Options:
  --port N                 which server to look for (default: this copy's own)
  --json                   machine-readable output where offered
  --force                  run: start a job the server is already running
"""
# kept in step with app.JOB_MOD (tests/test_cli.py checks the two agree): the job name the page uses -> its module
JOB_MOD = {"refresh": "refresh", "chase": "chase", "voice": "voice", "people": "people", "standing": "close_standing",
           "daylog": "daylog", "roadmap": "roadmap"}
LOGS = ROOT / "state" / "logs"
SCAN = 20  # the app moves up to SCAN - 1 ports past a taken one (app.pick_port); --stop scans the same range
FLAGS = {"--json", "--detect", "--recheck", "--now", "--no-browser", "--force", "--all", "--check", "-h", "--help"}
VALUED = {"--port", "--until", "--priority", "--notes", "--url", "--label", "--owner", "-n", "--width", "--out"}
PASSTHROUGH = ("run", "api", "test")  # everything after the command's first word belongs to what it runs
KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY")  # a job started here inherits these (agent.run)
MOCK_SAID = "mock (no AI is called: canned answers, for development)"  # how status, agent and doctor name it (#76)


# ---------------------------------------------------------------- the command line
def bad(line):
    """One line saying what was wrong, the usage line, where to look; exit 1."""
    print(f"  {line}\n  {USAGE}\n  ({_PY} -m openloops --help lists the commands)", file=sys.stderr)
    sys.exit(1)


def parse(argv):
    """argv -> (command, positional words, options). Options are --flag or --name value (see FLAGS / VALUED); for the
    pass-through commands only the words before the first positional are read as options, the rest is handed on."""
    cmd, words, opts, rest = None, [], {}, list(argv)
    while rest:
        a = rest.pop(0)
        if a in ("-h", "--help"):
            opts["help"] = True
        elif a in FLAGS:
            opts[a.lstrip("-")] = True
        elif a in VALUED or (a.startswith("--") and "=" in a and a.split("=", 1)[0] in VALUED):
            k, v = (a.split("=", 1) if "=" in a else (a, rest.pop(0) if rest else None))
            if v is None:
                bad(f"{k} needs a value")
            opts[k.lstrip("-")] = v
        elif a.startswith("-") and cmd not in PASSTHROUGH:
            bad(f"unknown option: {a}")
        elif cmd is None:
            cmd = a
        else:
            words.append(a)
            if cmd in PASSTHROUGH and len(words) == 1 and cmd != "api":
                words += rest  # the job's / the test's own options, untouched
                rest = []
            elif cmd == "api" and len(words) == 2:
                words += rest
                rest = []
    if "port" in opts and not (re.fullmatch(r"[0-9]{1,5}", opts["port"]) and 1024 <= int(opts["port"]) <= 65535):
        bad("--port needs a number from 1024 to 65535, for example: --port 8766")
    return cmd, words, opts


def preferred_port(opts):
    """--port N beats OPENLOOPS_PORT beats config.json "port" beats 8765: the same order as app._port_arg, repeated
    here because importing app.py creates config.json and state.json, which `--help` and `status` must not."""
    if opts.get("port"):
        return int(opts["port"])
    env_port = os.environ.get("OPENLOOPS_PORT", "")
    if re.fullmatch(r"[0-9]{1,5}", env_port) and 1024 <= int(env_port) <= 65535:
        return int(env_port)
    try:
        p = int(load_cfg().get("port") or 8765)
    except (TypeError, ValueError):
        return 8765
    return p if 1024 <= p <= 65535 else 8765


# ---------------------------------------------------------------- the server
def api(port, method, path, body=None, timeout=15):
    """One request to the server on `port` -> (status, parsed JSON or text). No X-OL-Page header: the console is not a
    page, so it never keeps the server up or counts as a tab. A connection failure is (0, reason)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code, hdr = r.read(), r.status, r.headers.get("Server", "")
    except urllib.error.HTTPError as e:
        raw, code, hdr = e.read(), e.code, e.headers.get("Server", "")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return 0, str(getattr(e, "reason", e))
    text = raw.decode("utf-8", "replace")
    try:
        return code, json.loads(text) if text.strip() else {}
    except ValueError:
        return code, text if hdr.startswith("OpenLoops") or code >= 400 else {"not_openloops": hdr or "?"}


def find_server(port):
    """The Open Loops server for THIS copy at `port` or up to SCAN - 1 above it -> (port, its /api/diag), else
    (None, others): `others` names servers of other copies seen on the way (root -> port), so `status` can say that
    the thing on 8765 is the installed copy, not this checkout."""
    if port in _found:  # one scan per command: a `done` asks twice (is the server there? which loops are there?)
        return _found[port]
    others = {}
    for p in range(port, min(port + SCAN, 65536)):
        with socket.socket() as sk:  # as app.port_busy: a closed port answers at once, an HTTP try on it may not
            sk.settimeout(0.3)
            if sk.connect_ex(("127.0.0.1", p)) != 0:
                continue
        code, d = api(p, "GET", "/api/diag", timeout=3)
        if code != 200 or not isinstance(d, dict) or d.get("app") != "openloops":
            continue
        try:
            same = Path(d.get("root", "")).resolve() == ROOT.resolve()
        except OSError:
            same = False
        if same:
            _found[port] = (p, d)
            return p, d
        others[d.get("root", "?")] = p
    _found[port] = (None, others)
    return None, others


_found = {}  # preferred port -> find_server's answer, for this process


def need_server(port):
    sp, others = find_server(port)
    if sp is None:
        hint = "".join(f"\n  (port {p} is another copy: {r})" for r, p in others.items())
        print(f"  this copy's server is not running (looked from port {port}); start it: "
              f"{_PY} -m openloops app start{hint}", file=sys.stderr)
        sys.exit(1)
    return sp, others


# ---------------------------------------------------------------- loops, as the page sees them
def workdays(iso):
    """Working days from an ISO date-time to today, as the page counts them (index.html workdays): Monday to Friday,
    today itself not counted. None when the value is not a date."""
    try:
        a = datetime.fromisoformat(str(iso or "")[:19]).date()
    except ValueError:
        return None
    b, n = date.today(), 0
    while a < b:
        if a.weekday() < 5:
            n += 1
        a += timedelta(days=1)
    return n


def age_ref(lp):
    """The date the page measures a loop's age from: the last reply for Needs me, the last chase (else the ask) for
    Waiting on them."""
    if lp.get("status") == "needs_me":
        return lp.get("last_reply_at") or lp.get("asked_at")
    return lp.get("last_chase_at") or lp.get("asked_at")


def band(wd):
    return "" if wd is None else "green" if wd < 2 else "amber" if wd <= 4 else "red"


def loops_now(port):
    """Every loop, the to-do file's cards included -> (loops, source): from the server when this copy's is running
    (exactly what the page gets), else from state.json plus the to-do file read directly, nothing written."""
    sp, _ = find_server(port)
    if sp:
        code, got = api(sp, "GET", "/api/state")
        if code == 200 and isinstance(got, dict):
            return list((got.get("state") or {}).get("loops") or []), f"server on {sp}"
    s = load_state()
    loops = list(s.get("loops") or [])
    try:
        from . import standing
        vault, _dirty = standing.as_loops(None)   # no state passed: first-seen marks are the page's to keep
        loops += vault
    except Exception as e:  # noqa: BLE001 - a to-do file that will not read must not hide the loops
        print(f"  (to-do file not read: {type(e).__name__}: {e})", file=sys.stderr)
    return loops, "state.json"


def pick(loops, which):
    today = date.today().isoformat()
    live = lambda l: l.get("status") != "done" and not ((l.get("snooze_until") or "") > today)
    sets = {"needs-me": lambda l: l.get("status") == "needs_me" and live(l),
            "waiting": lambda l: l.get("status") == "waiting" and live(l),
            "snoozed": lambda l: l.get("status") != "done" and (l.get("snooze_until") or "") > today,
            "done": lambda l: l.get("status") == "done",
            "all": lambda l: True}
    if which not in sets:
        bad(f"list takes one of: {', '.join(sets)} (not {which})")
    out = [l for l in loops if sets[which](l)]
    pri = {"high": 0, "normal": 1, "low": 2}
    return sorted(out, key=lambda l: (pri.get(l.get("priority"), 1), -(workdays(age_ref(l)) or 0)))


def row(lp):
    wd = workdays(age_ref(lp))
    extra = ""
    if lp.get("status") == "done":
        extra = f"closed {str(lp.get('closed_at') or '')[:16]}"
    elif (lp.get("snooze_until") or "") > date.today().isoformat():
        extra = f"snoozed until {lp['snooze_until']}"
    elif lp.get("last_chase_at"):
        extra = f"chased {str(lp['last_chase_at'])[:10]} ({lp.get('last_chase_mode') or 'draft'})"
    return {"id": lp.get("id"), "owner": lp.get("owner") or ("me" if lp.get("channel") == "note" else ""),
            "status": lp.get("status"), "age_wd": wd, "band": band(wd), "priority": lp.get("priority") or "normal",
            "priority_by": lp.get("priority_by") or "", "channel": lp.get("channel"), "ask": lp.get("ask") or "",
            "theme": lp.get("theme") or "", "extra": extra}


def table(rows, cols, widths):
    """Plain aligned columns; the last column is not padded or cut, the others are cut to their width."""
    def cell(v, w):
        v = "" if v is None else str(v)
        return v if w is None else (v[:w - 1] + "…" if len(v) > w else v).ljust(w)
    print("  ".join(cell(c.upper(), w) for c, w in zip(cols, widths)).rstrip())
    for r in rows:
        print("  ".join(cell(r.get(c), w) for c, w in zip(cols, widths)).rstrip())


def out_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- commands
def cmd_status(words, opts):
    port = preferred_port(opts)
    sp, found = find_server(port)   # (port, its diag) when this copy's server answers, else (None, other copies seen)
    d, others = (found, {}) if sp else ({}, found)
    s = load_state()
    loops = list(s.get("loops") or [])
    today = date.today().isoformat()
    live = [l for l in loops if l.get("status") != "done" and not ((l.get("snooze_until") or "") > today)]
    counts = {"needs_me": sum(l.get("status") == "needs_me" for l in live),
              "waiting": sum(l.get("status") == "waiting" for l in live),
              "snoozed": sum(l.get("status") != "done" and (l.get("snooze_until") or "") > today for l in loops),
              "done": sum(l.get("status") == "done" for l in loops)}
    cfg = load_cfg()
    from . import agent   # name() honours OPENLOOPS_AGENT=mock (#76); importing it writes nothing
    stamp = ROOT / "INSTALLED.txt"
    build = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else "checkout"
    if build == "checkout" and (ROOT / ".git").exists() and shutil.which("git"):
        g = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %s"], capture_output=True, text=True)
        branch = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
        if g.returncode == 0:
            build = f"checkout, {branch.stdout.strip()} @ {g.stdout.strip()}"
    info = {"root": str(ROOT), "build": build, "isolated": isolated(cfg), "config": CONFIG.exists(),
            "preferred_port": port, "server": None, "other_servers": others,
            "agent": agent.name(), "mock": agent.name() == "mock",
            "model": cfg.get("codex_model") if (cfg.get("agent") == "codex") else cfg.get("model"),
            "effort": cfg.get("codex_effort") if (cfg.get("agent") == "codex") else cfg.get("effort"),
            "slack_source": cfg.get("slack_source") or "", "miro_source": cfg.get("miro_source") or "",
            "send_internal": bool(cfg.get("send_internal")), "send_external": bool(cfg.get("send_external")),
            "people": len(cfg.get("people") or {}), "loops": counts, "last_refresh": s.get("last_refresh"),
            "cursor": s.get("cursor"), "setup_done": bool(s.get("setup_done")), "jobs": {}}
    if sp:
        code, got = api(sp, "GET", "/api/state")
        jobs = (got.get("jobs") or {}) if code == 200 and isinstance(got, dict) else {}
        info["server"] = {"port": sp, "up_since": d.get("up_since"), "pages": d.get("pages"), "python": d.get("python"),
                          "instance": d.get("instance") or "", "model": d.get("model"), "url": f"http://localhost:{sp}"}
        info["jobs"] = {k: {kk: v.get(kk) for kk in ("running", "rc", "finished_at", "failure", "said") if kk in v}
                        for k, v in jobs.items() if v.get("running") or v.get("rc") is not None}
    if opts.get("json"):
        return out_json(info)
    print(f"copy      {ROOT}")
    print(f"build     {build}{'  (isolated test copy)' if info['isolated'] else ''}{'' if info['config'] else '  (no config.json yet: template defaults)'}")
    if sp:
        sv = info["server"]
        print(f"server    running on {sp}: {sv['url']}  up since {sv['up_since']}, {sv['pages']} page(s) open")
    else:
        print(f"server    not running (looked from port {port}); {_PY} -m openloops app start")
    for root, p in (others if not sp else {}).items():
        print(f"          (port {p} is another copy: {root})")
    send = ", ".join(x for x, on in (("internal", info["send_internal"]), ("external", info["send_external"])) if on) or "drafts only"
    print(f"agent     {MOCK_SAID if info['mock'] else info['agent']} · model {info['model'] or '(CLI default)'} · effort {info['effort'] or '(CLI default)'}"
          f" · slack via {info['slack_source'] or '-'} · miro via {info['miro_source'] or '-'} · send: {send}")
    c = counts
    print(f"loops     needs me {c['needs_me']} · waiting {c['waiting']} · snoozed {c['snoozed']} · done {c['done']}"
          f" · people {info['people']} · last refresh {s.get('last_refresh') or 'never'} · cursor {s.get('cursor') or '-'}")
    if sp:
        running = [k for k, j in info["jobs"].items() if j.get("running")]
        done = [(k, j) for k, j in info["jobs"].items() if not j.get("running")]
        last = max(done, key=lambda kj: kj[1].get("finished_at") or "", default=None)
        print(f"jobs      running: {', '.join(running) or 'none'}" + (
            f" · last ended: {last[0]} rc {last[1].get('rc')} at {last[1].get('finished_at')}"
            + (f" ({last[1].get('said')})" if last[1].get("said") else "") if last else ""))
    for v in KEY_VARS:
        if os.environ.get(v):
            print(f"warning   {v} is set in this shell: a job started from here inherits it, and the CLI may bill it")


def cmd_list(words, opts):
    which = words[0] if words else "needs-me"
    loops, src = loops_now(preferred_port(opts))
    rows = [row(l) for l in pick(loops, which)]
    if opts.get("json"):
        return out_json(rows)
    print(f"{which}: {len(rows)} loop(s), from {src}")
    if rows:
        table(rows, ["id", "owner", "age_wd", "priority", "channel", "ask"], [34, 16, 6, 8, 7, None])
        for r in rows:
            if r["extra"]:
                print(f"    {r['id']}: {r['extra']}")


def cmd_show(words, opts):
    if not words:
        bad("show needs a loop id (see: list)")
    loops, src = loops_now(preferred_port(opts))
    hit = [l for l in loops if l.get("id") == words[0]]
    if not hit:
        bad(f"no loop with id {words[0]} (in {src})")
    out_json(hit[0])


def cmd_act(words, opts):
    from . import actions
    if len(words) < 2:
        bad("act needs an action and a loop id: act done <id>")
    act, lid = words[0], words[1]
    if act not in actions.ACTIONS or act == "add":
        bad(f"act takes one of: {', '.join(a for a in actions.ACTIONS if a != 'add')} (add has its own command)")
    need = {"snooze": "until", "priority": "priority", "note": "notes", "add_link": "url", "drop_link": "url"}
    if act in need and not opts.get(need[act]):
        bad(f"act {act} needs --{need[act]}")
    body = {"action": act, "id": lid}
    for k in ("until", "priority", "notes", "url", "label"):
        if opts.get(k) is not None:
            body[k] = opts[k]
    port = preferred_port(opts)
    sp, _ = find_server(port)
    loops, src = loops_now(port)
    if not any(l.get("id") == lid for l in loops):
        bad(f"no loop with id {lid} (in {src})")
    if lid.startswith("vault-"):
        if not sp:
            bad("a to-do file item is closed through the running server (it starts the write-back job): app start")
        body["closure"] = opts.get("notes") or ""
    if sp:
        code, ans = api(sp, "POST", "/api/action", body)
    else:
        people = load_cfg().get("people") or {}
        with _locked(STATE):
            s = load_state()
            s.setdefault("loops", [])
            ans, code, write = actions.apply(s, body, people)
            if write:
                write_json(STATE, s)
    if code != 200:
        print(f"  refused ({code}): {ans.get('error') if isinstance(ans, dict) else ans}", file=sys.stderr)
        sys.exit(1)
    undo = {"done": "reopen", "snooze": "unsnooze", "auto_off": "auto_on", "auto_on": "auto_off"}.get(act)
    print(f"{act}: {lid}" + (f"  (via server on {sp})" if sp else "  (state.json, server not running)")
          + (f"  undo: act {undo} {lid}" if undo else ""))


def cmd_add(words, opts):
    from . import actions
    if not words:
        bad('add needs the reminder text: add "send the deck" --owner Alice')
    body = {"action": "add", "ask": " ".join(words), "owner": opts.get("owner") or "", "notes": opts.get("notes") or ""}
    port = preferred_port(opts)
    sp, _ = find_server(port)
    if sp:
        code, ans = api(sp, "POST", "/api/action", body)
    else:
        with _locked(STATE):
            s = load_state()
            s.setdefault("loops", [])
            ans, code, write = actions.apply(s, body, load_cfg().get("people") or {})
            if write:
                write_json(STATE, s)
    if code != 200:
        print(f"  refused ({code}): {ans.get('error') if isinstance(ans, dict) else ans}", file=sys.stderr)
        sys.exit(1)
    print(f"added {ans.get('id')}" + (f"  (via server on {sp})" if sp else "  (state.json)"))


def _job_logs(job):
    """This job's log files, newest first (refresh also matches refresh-slack-…)."""
    if not LOGS.exists():
        return []
    return sorted((p for p in LOGS.iterdir() if p.is_file() and p.name.startswith(job + "-")),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def cmd_run(words, opts):
    from . import agent, messages
    if not words:
        bad(f"run needs a job: {', '.join(JOB_MOD)}")
    job, rest = words[0], words[1:]
    if job not in JOB_MOD:
        bad(f"no job called {job}; one of: {', '.join(JOB_MOD)}")
    if job == "chase" and not rest:
        bad("run chase needs a loop id")
    port = preferred_port(opts)
    sp, _ = find_server(port)
    if sp and not opts.get("force"):
        code, got = api(sp, "GET", "/api/state")
        if code == 200 and isinstance(got, dict) and ((got.get("jobs") or {}).get(job) or {}).get("running"):
            bad(f"the server on {sp} is running {job} now; two at once race on state.json (--force runs it anyway)")
    run_id = uuid.uuid4().hex
    env = dict(os.environ, OPENLOOPS_RUN_ID=run_id, OPENLOOPS_AI=agent.name(), PYTHONUNBUFFERED="1")
    for v in KEY_VARS:
        if env.get(v):
            print(f"  warning: {v} is set in this shell and the job inherits it", file=sys.stderr)
    args = [sys.executable, "-m", f"openloops.{JOB_MOD[job]}", *rest]
    print(f"> {' '.join(args[1:])}   (cwd {ROOT}, agent {agent.name()}, run {run_id[:8]})", flush=True)
    t0 = time.time()
    try:
        rc = subprocess.run(args, cwd=ROOT, env=env).returncode
    except KeyboardInterrupt:
        rc = 130
    took = time.time() - t0
    note = {0: "ok", 2: "SKIPPED (the job said why above)"}.get(rc, "failed")
    print(f"< {job}: rc {rc} ({note}) in {took:.0f}s", flush=True)
    ff = messages.failure_file(job, run_id)
    if ff.exists():
        try:
            rec = json.loads(ff.read_text(encoding="utf-8"))
            print(f"  failure record: {rec.get('failure') or rec.get('id') or '?'}: {rec.get('said') or rec.get('what') or json.dumps(rec)[:300]}")
        except ValueError:
            print(f"  failure record {ff} could not be read")
        ff.unlink(missing_ok=True)
    new = [p for p in _job_logs(job) if p.stat().st_mtime >= t0 - 1]
    if new:
        print(f"  log: {new[0]}")
    sys.exit(rc)


def cmd_jobs(words, opts):
    sp, _ = need_server(preferred_port(opts))
    code, got = api(sp, "GET", "/api/state")
    if code != 200 or not isinstance(got, dict):
        bad(f"/api/state on {sp} answered {code}: {str(got)[:200]}")
    jobs = got.get("jobs") or {}
    if opts.get("json"):
        return out_json(jobs)
    for name, j in jobs.items():
        if j.get("running"):
            line = "running"
        elif j.get("rc") is None:
            line = "not run since the server started"
        else:
            line = f"rc {j.get('rc')} at {j.get('finished_at')}" + (f" · {j.get('said')}" if j.get("said") else "")
        print(f"{name:9} {line}")
        tail = [ln for ln in (j.get("log") or "").splitlines() if ln.strip()][-3:]
        for ln in tail:
            print(f"          | {ln[:160]}")


def cmd_logs(words, opts):
    if opts.get("n") is not None and not re.fullmatch(r"[0-9]{1,6}", opts["n"]):
        bad("-n needs a whole number of lines, for example: logs refresh -n 40")
    n = int(opts.get("n") or 0)
    if not words:
        files = sorted((p for p in LOGS.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True) if LOGS.exists() else []
        files = files[: n or 20]
        if opts.get("json"):
            return out_json([{"name": p.name, "bytes": p.stat().st_size, "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")} for p in files])
        print(f"{LOGS} ({'newest ' + str(len(files)) if files else 'empty'})")
        for p in files:
            print(f"  {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}  {p.stat().st_size:>8}  {p.name}")
        return
    want = words[0]
    cand = Path(want)
    if cand.is_file():
        f = cand
    elif (LOGS / want).is_file():
        f = LOGS / want
    else:
        hits = _job_logs(want)
        if not hits:
            bad(f"no log for {want} in {LOGS} (a job name such as refresh, or a file name)")
        f = hits[0]
    text = f.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    keep = lines if opts.get("all") else lines[-(n or 60):]
    print(f"{f}  ({len(lines)} lines{'' if opts.get('all') or len(keep) == len(lines) else f', last {len(keep)}'})")
    print("\n".join(keep))


def cmd_doctor(words, opts):
    args = [sys.executable, "-m", "openloops.doctor"] + [f for f in ("--detect", "--recheck") if opts.get(f.lstrip("-"))]
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = None
    for ln in reversed(p.stdout.splitlines()):
        if ln.startswith("{"):
            try:
                out = json.loads(ln)
                break
            except ValueError:
                pass
    if out is None:
        print(f"  doctor gave no JSON (rc {p.returncode})\n{(p.stdout + p.stderr)[-2000:]}", file=sys.stderr)
        sys.exit(1)
    if opts.get("json"):
        return out_json(out)
    for st in out.get("steps") or []:
        mark = "ok  " if st.get("ok") else ("--  " if st.get("optional") else "FAIL")
        print(f"{mark} {st.get('title')}" + (f"  [{st['detail']}]" if st.get("detail") else ""))
        if not st.get("ok") and st.get("fix"):
            print(f"       {st['fix']}")
    print(f"agent {MOCK_SAID if out.get('mock') else out.get('agent')} · slack via {out.get('slack_source') or '-'}"
          f" · miro via {out.get('miro_source') or '-'} · {'all ok' if out.get('all_ok') else 'not ready'}")
    sys.exit(0 if out.get("all_ok") else 1)


def cmd_diag(words, opts):
    port = preferred_port(opts)
    sp, others = find_server(port)
    if not sp:
        cfg = load_cfg()
        info = {"app": "openloops", "server": "not running", "python": sys.version.split()[0], "platform": sys.platform,
                "root": str(ROOT), "preferred_port": port, "agent": cfg.get("agent") or "claude", "others": others}
        return out_json(info) if opts.get("json") else print("\n".join(f"{k:16} {v}" for k, v in info.items()))
    code, d = api(sp, "GET", "/api/diag")
    if code != 200 or not isinstance(d, dict):
        bad(f"/api/diag on {sp} answered {code}: {str(d)[:200]}")
    if opts.get("json"):
        return out_json(d)
    for k in ("root", "build", "python", "platform", "port", "up_since", "agent", "model", "isolated", "pages"):
        print(f"{k:16} {d.get(k)}")
    for name, j in (d.get("jobs") or {}).items():
        if j.get("running") or j.get("rc") is not None:
            print(f"job {name:12} {'running' if j.get('running') else 'rc ' + str(j.get('rc'))}")
    doc = d.get("doctor") or {}
    if doc:
        bad_steps = [s.get("title") for s in doc.get("steps") or [] if not s.get("ok") and not s.get("optional")]
        print(f"{'doctor':16} {'all ok' if doc.get('all_ok') else 'not ready: ' + '; '.join(bad_steps)}")
    for k in ("doctor_log", "launchd_err_log"):
        if d.get(k):
            print(f"--- {k} (tail)\n{d[k].rstrip()[-800:]}")


def _get_path(obj, key):
    for part in key.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None, False
        obj = obj[part]
    return obj, True


def cmd_config(words, opts):
    if words and words[0] == "set":
        if len(words) < 3:
            bad("config set needs a key and a value: config set effort medium")
        key, raw = words[1], " ".join(words[2:])
        try:
            val = json.loads(raw)
        except ValueError:
            val = raw
        tpl = read_json(TEMPLATE, {}) or {}
        if _get_path(tpl, key)[1] is False and key not in ("port", "first_scan", "setup_done"):
            print(f"  note: {key} is not a setting in config.template.json; written anyway", file=sys.stderr)

        def mutate(c):
            node, parts = c, key.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
                if not isinstance(node, dict):
                    bad(f"{key}: {part} is not an object")
            node[parts[-1]] = val
        if update_json(CONFIG, mutate) is False:
            sys.exit(1)
        print(f"{key} = {json.dumps(val, ensure_ascii=False)}  (config.json; the server reads it fresh on every request)")
        return
    cfg = load_cfg()
    if words:
        val, found = _get_path(cfg, words[0])
        if not found:
            bad(f"no setting called {words[0]}")
        return out_json(val)
    shown = {k: v for k, v in cfg.items() if opts.get("all") or not k.endswith("_note")}
    if opts.get("json"):
        return out_json(shown)
    print(f"{'config.json' if CONFIG.exists() else 'no config.json: config.template.json defaults'} ({ROOT})")
    for k in sorted(shown):
        v = json.dumps(shown[k], ensure_ascii=False)
        print(f"  {k:22} {v if len(v) <= 100 else v[:97] + '...'}")


def cmd_agent(words, opts):
    from . import agent
    name = agent.name()
    exe = agent.cli()
    found = shutil.which(exe) or (exe if Path(exe).is_file() else None)
    info = {"agent": name, "display": agent.display_name(), "command": exe, "found": found, "model": agent.model(),
            "effort": agent.effort(), "slack_source": agent.slack_source(), "miro_source": agent.miro_source(),
            "env_keys_set": [v for v in KEY_VARS if os.environ.get(v)]}
    if name == "mock":   # #76: said in so many words, so it can never be read as a real sign-in
        info.update(mock=True, command="(none)", found=None, note=MOCK_SAID + "; chosen by " + (
            "OPENLOOPS_AGENT=mock in this shell" if os.environ.get("OPENLOOPS_AGENT", "").strip().lower() == "mock" else "config.json agent=mock"))
    if name == "claude":
        info["job_argv"] = agent.claude_args(["slack.search_users", "gmail.search_threads"])
    if opts.get("check"):
        if name == "mock":
            info["signed_in_as"] = "nobody: " + MOCK_SAID
        if found:
            try:
                p = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=30, shell=WIN)
                info["version"] = (p.stdout + p.stderr).strip().splitlines()[0] if (p.stdout + p.stderr).strip() else f"rc {p.returncode}"
            except (OSError, subprocess.TimeoutExpired) as e:
                info["version"] = f"could not run: {e}"
        if name == "claude":
            info["signed_in_as"] = agent.claude_auth() or "unknown (claude auth status gave no authMethod)"
        elif name == "codex":
            info["codex_auth"] = agent.codex_auth()
    if opts.get("json"):
        return out_json(info)
    for k, v in info.items():
        if k == "env_keys_set":
            continue
        print(f"{k:14} {' '.join(v) if isinstance(v, list) else v}")
    for v in info["env_keys_set"]:
        print(f"warning        {v} is set in this shell: a job started from here inherits it, and the CLI may bill it "
              f"instead of using your plan (the page's jobs inherit the app's environment the same way)")


def cmd_app(words, opts):
    verb = words[0] if words else "status"
    port = preferred_port(opts)
    if verb == "status":
        sp, others = find_server(port)
        print(f"running on {sp}: http://localhost:{sp}" if sp else f"not running (looked from port {port})")
        for root, p in (others if not sp else {}).items():
            print(f"  (port {p} is another copy: {root})")
        return
    if verb == "url":
        sp, _ = find_server(port)
        return print(f"http://localhost:{sp}" if sp else f"not running; would be http://localhost:{port}")
    if verb == "open":
        import webbrowser
        sp, _ = need_server(port)
        webbrowser.open(f"http://localhost:{sp}")
        return print(f"opened http://localhost:{sp}")
    if verb == "stop":
        args = [sys.executable, "-m", "openloops.app", "--stop", "--port", str(port)] + (["--now"] if opts.get("now") else [])
        sys.exit(subprocess.run(args, cwd=ROOT).returncode)
    if verb == "start":
        sp, _ = find_server(port)
        if sp:
            return print(f"already running on {sp}: http://localhost:{sp}")
        LOGS.mkdir(parents=True, exist_ok=True)
        log = LOGS / "app.log"
        args = [sys.executable, "-m", "openloops.app", "--port", str(port)] + (["--no-browser"] if opts.get("no-browser") else [])
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} {' '.join(args[1:])}\n")
            f.flush()
            kw = {"creationflags": 0x00000008 | 0x00000200} if WIN else {"start_new_session": True}  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            p = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, **kw)
        end = time.time() + 20
        while time.time() < end:
            _found.pop(port, None)  # look again each time: the memo was taken before the start
            sp, _ = find_server(port)
            if sp:
                return print(f"started (pid {p.pid}) on {sp}: http://localhost:{sp}   output: {log}")
            if p.poll() is not None:
                break
            time.sleep(0.25)
        print(f"  did not come up within 20 s (pid {p.pid}, exit {p.poll()}); see {log}", file=sys.stderr)
        sys.exit(1)
    bad(f"app takes start, stop, open, url or nothing (not {verb})")


def cmd_screenshot(words, opts):
    """The page as this copy's running server serves it, captured headless -> a PNG, its path printed (#76). Chrome
    and Node are checked before the server, each a one-line refusal; the server missing is need_server's."""
    from . import screenshot
    if words:
        bad("screenshot takes no words, only --width N and --out F")
    if opts.get("width") is not None and not (re.fullmatch(r"[0-9]{3,4}", opts["width"]) and 320 <= int(opts["width"]) <= 4000):
        bad("--width needs a number of pixels from 320 to 4000, for example: --width 800")
    why = screenshot.missing()
    if why:
        bad(f"screenshot: {why}")
    sp, _ = need_server(preferred_port(opts))
    out = Path(opts["out"]).expanduser() if opts.get("out") else screenshot.default_out()
    if out.is_dir():
        out = out / screenshot.default_out().name
    ok, said = screenshot.capture(f"http://localhost:{sp}", out, int(opts.get("width") or 1280))
    if not ok:
        bad(f"screenshot: {said}")
    print(said)


def cmd_api(words, opts):
    if len(words) < 2:
        bad('api needs a method and a path: api GET /api/state, api POST /api/action {"action":"done","id":"x"}')
    method, path, body = words[0].upper(), words[1], " ".join(words[2:])
    if not path.startswith("/"):
        bad(f"the path starts with /: {path}")
    data = None
    if body:
        try:
            data = json.loads(body)
        except ValueError as e:
            bad(f"the body is not JSON ({e}): {body}")
    elif method == "POST":
        data = {}
    sp, _ = need_server(preferred_port(opts))
    code, ans = api(sp, method, path, data, timeout=120)
    if isinstance(ans, (dict, list)):
        out_json(ans)
    else:
        print(ans)
    sys.exit(0 if 200 <= code < 300 else 1)


def cmd_test(words, opts):
    tests = ROOT / "tests"
    if not tests.is_dir():
        bad(f"no tests/ in this copy ({ROOT}); run from a git checkout")
    if not words:
        sys.exit(subprocess.run([sys.executable, str(tests / "run_all.py")], cwd=ROOT).returncode)
    files = sorted({p for w in words for p in tests.glob(f"test_*{w}*.py")} | {tests / w for w in words if (tests / w).is_file()})
    if not files:
        bad(f"no tests/test_*.py matching {', '.join(words)}")
    failed = []
    for f in files:
        t = time.time()
        r = subprocess.run([sys.executable, str(f)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        ok = r.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {f.name}  ({time.time() - t:.0f}s)", flush=True)
        if not ok:
            failed.append(f.name)
            print((r.stdout + r.stderr)[-2500:], flush=True)
    print(f"{len(files) - len(failed)}/{len(files)} passed" + (f" - FAILED: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


COMMANDS = {"status": cmd_status, "list": cmd_list, "show": cmd_show, "act": cmd_act, "add": cmd_add, "run": cmd_run,
            "jobs": cmd_jobs, "logs": cmd_logs, "doctor": cmd_doctor, "diag": cmd_diag, "config": cmd_config,
            "agent": cmd_agent, "app": cmd_app, "screenshot": cmd_screenshot, "api": cmd_api, "test": cmd_test}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):  # Windows: a piped console is cp1252, and names and logs are not
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd, words, opts = parse(sys.argv[1:] if argv is None else list(argv))
    if opts.get("help") or cmd is None:
        print(USAGE + "\n" + HELP.rstrip("\n"))
        return 0 if opts.get("help") or cmd is None else 1
    if cmd not in COMMANDS:
        bad(f"unknown command: {cmd}")
    try:
        COMMANDS[cmd](words, opts)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # `| head`
        return 0
    return 0
