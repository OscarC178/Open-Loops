"""API test for the colleague-merge additions: snooze normalisation, links, slack-only refresh,
Day log digest + page, Roadmap staging and gating.

    python3 tests/test_api_extras.py    # fast; no Slack/Gmail/Claude. Temp install, spare port.

Nothing here starts the agent: the slack-only refresh is SKIPPED because the temp install has no
Slack id, the day log is written with --digest-only, and every Roadmap mode that would talk to
Miro is refused before it starts (not configured / no confirm).
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORT = 8797
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def api(path, body=None, method=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            b = r.read()
            return r.status, (b.decode("utf-8") if raw else json.loads(b))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b.decode())
        except ValueError:
            return e.code, b.decode("utf-8", "replace")


def wait_job(name, secs=60):
    for _ in range(secs * 5):
        j = api("/api/state")[1]["jobs"][name]
        if not j["running"]:
            return j
        time.sleep(0.2)
    raise SystemExit(f"FAIL: job {name} still running after {secs}s")


tmp = Path(tempfile.mkdtemp(prefix="openloops-extras-"))
say(f"fresh install in {tmp}")
shutil.copytree(REPO / "openloops", tmp / "openloops")
shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
tpl = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
tpl["owner_name"] = "Oscar"
(tmp / "config.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
today = time.strftime("%Y-%m-%d")
(tmp / "state.json").write_text(json.dumps({
    "cursor": "2026-01-01T00:00", "last_refresh": "2026-01-02T00:00",
    "loops": [{"id": "alice-report", "owner": "Alice", "ask": "the report", "channel": "email",
               "asked_at": "2026-01-01T10:00", "status": "waiting", "chases": 0, "snooze_until": None, "notes": ""},
              {"id": "bob-deck", "owner": "Bob", "ask": "the deck", "channel": "slack",
               "asked_at": f"{today}T09:00", "status": "waiting", "chases": 0, "snooze_until": None, "notes": ""},
              {"id": "cat-quote", "owner": "Cat", "ask": "a quote", "channel": "email",
               "asked_at": "2026-02-01T10:00", "status": "done", "closed_at": f"{today}T10:00", "chases": 0, "snooze_until": None}],
}), encoding="utf-8")

env = dict(os.environ, OPENLOOPS_PORT=str(PORT))
srv = subprocess.Popen([sys.executable, "-m", "openloops.app", "--no-browser"], cwd=tmp, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        if socket.socket().connect_ex(("127.0.0.1", PORT)) == 0:
            break
        time.sleep(0.1)
    else:
        raise SystemExit("FAIL: openloops.app did not come up")

    # ---- snooze: zero-padded, bad dates refused
    code, _ = api("/api/action", {"id": "alice-report", "action": "snooze", "until": "2099-1-5"})
    alice = next(l for l in api("/api/state")[1]["state"]["loops"] if l["id"] == "alice-report")
    check(code == 200 and alice["snooze_until"] == "2099-01-05", "snooze zero-pads 2099-1-5")
    code, err = api("/api/action", {"id": "alice-report", "action": "snooze", "until": "next week"})
    check(code == 400 and "YYYY-MM-DD" in err.get("error", ""), "snooze refuses a non-date")
    api("/api/action", {"id": "alice-report", "action": "unsnooze"})

    # ---- links: add, de-dup, refuse non-http, drop
    code, _ = api("/api/action", {"id": "alice-report", "action": "add_link", "url": "https://docs.google.com/d/1", "label": "brief"})
    api("/api/action", {"id": "alice-report", "action": "add_link", "url": "https://docs.google.com/d/1", "label": "again"})
    alice = next(l for l in api("/api/state")[1]["state"]["loops"] if l["id"] == "alice-report")
    check(code == 200 and alice["links"] == [{"url": "https://docs.google.com/d/1", "label": "brief"}], "add_link stores once, first label wins")
    code, err = api("/api/action", {"id": "alice-report", "action": "add_link", "url": "javascript:alert(1)"})
    check(code == 400, "add_link refuses a non-http url")
    api("/api/action", {"id": "alice-report", "action": "drop_link", "url": "https://docs.google.com/d/1"})
    alice = next(l for l in api("/api/state")[1]["state"]["loops"] if l["id"] == "alice-report")
    check(alice["links"] == [], "drop_link removes it")
    code, out = api("/api/action", {"action": "add", "ask": "typed reminder"})
    typed = next(l for l in api("/api/state")[1]["state"]["loops"] if l["id"] == out["id"])
    check(typed["links"] == [], "typed reminders start with an empty links list")

    # ---- slack-only refresh: accepted by the app, skipped by the job (no Slack id), cursor untouched
    code, out = api("/api/refresh", {"slack_only": True})
    check(code == 200 and out["started"] is True, "slack-only refresh starts")
    j = wait_job("refresh")
    check(j.get("rc") == 2 and "SKIPPED" in j["log"], "without a Slack id the slack-only run is SKIPPED (exit 2), not run")
    st = api("/api/state")[1]["state"]
    check(st["cursor"] == "2026-01-01T00:00" and "slack_cursor" not in st, "a skipped slack-only run moves no cursor")

    # ---- day log: digest from state, page written by --digest-only, served through the app
    code, d = api("/api/daylog")
    check(code == 200 and d["date"] == today and d["text"] is None and d["has_page"] is False, "day log digest available before any run")
    g = d["digest"]
    opened = sorted(x["id"] for x in g["opened"])
    check(opened == sorted(["bob-deck", typed["id"]]) and [x["id"] for x in g["closed"]] == ["cat-quote"],
          f"digest lists today's opened (incl. the typed reminder) and closed loops (got {opened})")
    check(g["waiting"] == 2 and g["needs_me"] == 1, f"digest counts waiting=2 needs_me=1 (got {g['waiting']},{g['needs_me']})")
    code, _ = api("/api/daylog/page", raw=True)
    check(code == 404, "no page yet -> 404 with a hint")
    code, out = api("/api/daylog", {"digest_only": True})
    check(code == 200 and out["started"] is True, "day log job starts")
    j = wait_job("daylog")
    check(j.get("rc") == 0, f"digest-only day log exits 0 (log: {j['log'][-200:]!r})")
    code, d = api("/api/daylog")
    check(d["has_page"] is True and d["written_at"], "day log entry saved")
    code, html = api("/api/daylog/page", raw=True)
    check(code == 200 and "<html" in html.lower() and "the deck" in html, "day log page served through the app with today's items")

    # ---- roadmap: staging without Miro; every agent mode gated
    code, r = api("/api/roadmap")
    check(code == 200 and r["configured"]["ok"] is False and r["store"]["rows"] == [], "roadmap store empty and unconfigured on a fresh install")
    rows = [{"title": "Layout tool fixes", "detail": "", "owners": "Rafe", "lane": "Pipeline", "column": "W38", "state": "In Progress"},
            {"title": "", "owners": "nobody"}]
    code, _ = api("/api/roadmap", {"mode": "save", "rows": rows, "pasted": "Rafe still on the layout tool fixes"})
    r = api("/api/roadmap")[1]["store"]
    check(code == 200 and len(r["rows"]) == 2 and r["rows"][0]["state"] == "in_progress" and r["rows"][0]["id"], "save normalises rows and keeps them")
    check(r["pasted"] == "Rafe still on the layout tool fixes" and r["updated_at"], "save keeps the pasted notes")
    check(not (tmp / "state.json").read_text(encoding="utf-8").count("Layout tool fixes"), "staging rows never land in state.json")
    for mode in ("read", "parse", "preview", "build"):
        code, err = api("/api/roadmap", {"mode": mode, "confirm": True})
        check(code == 400 and "Settings" in err.get("error", ""), f"{mode} refused until board + frame are set")
    code, err = api("/api/roadmap", {"mode": "nuke"})
    check(code == 400, "unknown mode is 400")
    api("/api/config", {"roadmap_board": "Planning", "roadmap_frame": "Roadmap Sep 2026"})
    code, err = api("/api/roadmap", {"mode": "build"})
    check(code == 400 and "confirm" in err.get("error", ""), "build without confirm is refused even when configured")
    cfg = json.loads((tmp / "config.json").read_text(encoding="utf-8-sig"))
    check(cfg["roadmap_board"] == "Planning" and cfg["roadmap_frame"] == "Roadmap Sep 2026", "board and frame saved from Settings")
    check(api("/api/roadmap")[1]["embed"] == "", "no live embed while the board is only known by name")
    api("/api/config", {"roadmap_board": "https://miro.com/app/board/uXjVK1abc=/"})
    check(api("/api/roadmap")[1]["embed"].startswith("https://miro.com/app/live-embed/uXjVK1abc=/"), "a board link in Settings gives the page a live embed url")

    # ---- model: saved from Settings, passed to claude as --model, blank = no flag
    def claude_cmd():
        r = subprocess.run([sys.executable, "-c", "from openloops import agent; print(' '.join(agent.claude_args(['slack.read_channel'])))"],
                           cwd=tmp, capture_output=True, text=True)
        return r.stdout.strip()
    check("--model sonnet" in claude_cmd() and "--effort xhigh" in claude_cmd(), "fresh install runs the jobs on sonnet at xhigh (template default)")
    api("/api/config", {"model": "opus", "effort": "medium"})
    check(claude_cmd().endswith("--model opus --effort medium"), "model + effort from Settings reach the claude command line")
    api("/api/config", {"effort": ""})
    check("--effort" not in claude_cmd(), "blank effort -> no --effort flag")
    api("/api/config", {"effort": "xhigh"})
    api("/api/config", {"model": "haiku"})
    check("--model haiku --effort xhigh" in claude_cmd(), "model from Settings reaches the claude command line")
    api("/api/config", {"model": ""})
    check("--model" not in claude_cmd(), "blank model -> no --model flag (Claude Code default)")
    api("/api/config", {"model": "sonnet"})

    # ---- to-do file: status, starter file, path forms, legacy folder setting
    s = api("/api/standing")[1]
    check(s["exists"] is False and s["open"] == 0, "no to-do file on a fresh install")
    check(s["path"] == "", f"blank standing_file points at no file at all, not a default under HOME (got {s['path']!r})")
    todo = tmp / "notes" / "todo.md"
    api("/api/config", {"standing_file": str(todo)})
    code, r = api("/api/standing/create", {})
    check(code == 200 and Path(r["path"]) == todo and todo.exists(), "starter file created at the configured path")
    s = api("/api/standing")[1]
    check(s["exists"] and s["open"] == 1, "starter file has one open example item")
    code, r = api("/api/standing/create", {})
    check(code == 400 and "already" in r["error"], "never overwrites an existing file")
    ids = [l["id"] for l in api("/api/state")[1]["state"]["loops"]]
    check("vault-A1" in ids, "the example item shows as a Needs-me card")
    (tmp / "legacy" / "02-Research").mkdir(parents=True)
    (tmp / "legacy" / "02-Research" / "standing-items.md").write_text("- [ ] A9 | p | legacy item | added 2026-09-01\n", encoding="utf-8")
    api("/api/config", {"standing_file": "", "vault_path": str(tmp / "legacy")})
    check(api("/api/standing")[1]["open"] == 1 and "vault-A9" in [l["id"] for l in api("/api/state")[1]["state"]["loops"]], "legacy vault_path folder still resolves")
    api("/api/config", {"vault_path": ""})

    # ---- pinned links: http only, de-duplicated, label capped, round-trips through config
    api("/api/config", {"pinned_links": [{"url": "https://miro.com/app/board/uXjVK1abc=/", "label": " Planning "},
                                         {"url": "https://miro.com/app/board/uXjVK1abc=/", "label": "dup"},
                                         {"url": "javascript:alert(1)", "label": "bad"}, {"url": "https://docs.google.com/d/9", "label": "x" * 80}]})
    pl = api("/api/config")[1]["config"]["pinned_links"]
    check([p["url"] for p in pl] == ["https://miro.com/app/board/uXjVK1abc=/", "https://docs.google.com/d/9"], "pins keep http links once each, drop the rest")
    check(pl[0]["label"] == "Planning" and len(pl[1]["label"]) == 60, "pin labels are trimmed and capped")
    api("/api/config", {"pinned_links": []})
    check(api("/api/config")[1]["config"]["pinned_links"] == [], "pins can be cleared")

    # ---- priority: the person's call sticks; the AI may only set it while nobody has
    code, err = api("/api/action", {"id": "alice-report", "action": "priority", "priority": "urgent"})
    check(code == 400 and "high, normal or low" in err.get("error", ""), "priority refuses anything but high/normal/low")
    code, _ = api("/api/action", {"id": "alice-report", "action": "priority", "priority": "high"})
    alice = next(l for l in api("/api/state")[1]["state"]["loops"] if l["id"] == "alice-report")
    check(code == 200 and alice["priority"] == "high" and alice["priority_by"] == "you", "priority set by hand is marked as yours")

    # ---- /api/diag: what the Console's "Copy all" pastes
    code, d = api("/api/diag")
    check(code == 200 and d["port"] == PORT and d["build"] == "checkout" and "python" in d and "up_since" in d, "diag names port, build, python, start time")
    check("refresh" in d["jobs"] and "rc" in d["jobs"]["refresh"] and "doctor" in d, "diag carries job results and the last check")

    # ---- page has the new controls
    html = (tmp / "openloops" / "index.html").read_text(encoding="utf-8")
    for needle in ('id="console_wrap"', "function clog(", "'/api/diag'", 'id="st_checkfail"', "return 'checkfail'", "function personRow(", "class=\"blk ", "function priSel(", "setSort("):
        check(needle in html, f"page has {needle}")
    check("Check failed" not in html, "no bare 'Check failed' anywhere on the page")
    check("x.id==='self'&&x.ok" in html, "Update Slack is shown only when Slack is connected and the owner's id is known")
    for needle in ('id="uslack"', 'id="cfg_model"', 'id="cfg_effort"', 'id="cfg_standing"', 'standingCreate(', 'id="pins"', 'pinEmbed(', "linkify(", "addLink(", "noteFor(", 'id="dl_run"', 'id="rm_postbtn"', 'id="cfg_rm_board"', 'id="cs_personal"', 'id="cs_app"', 'cfgRestore(', "ol.settings.open", 'id="hs_needs"', 'id="hs_waiting"', 'id="hs_rest"', 'id="n3"'):
        check(needle in html, f"page has {needle}")
    say("ALL OK")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
