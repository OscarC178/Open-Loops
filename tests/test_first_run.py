"""First run (#38) and isolated test copies (#36): setup finishes with Slack only, nothing scans before a press, and
an isolated copy reads no to-do file and starts nothing by itself.

    python3 tests/test_first_run.py    # no Slack/Gmail/Claude: a fake `claude` on PATH, temp installs, spare ports.

The page's own setup code (stage machine, tick, stagePeople, stageAuto, paintButtons, the Start the first scan gate)
is cut out of the page as the app serves it and run in node against the real app, whose jobs run the fake `claude`.
So a pass means the served page, the app and the jobs agree, not that a copy of the logic does. Checks:
  1. Slack only (Gmail not connected): the checklist is green; before the press nothing runs and the page says what
     the first scan reads and how long ("Looking back 30 days across Slack. ..."), with Update Slack on show; after
     Start the first scan: who's who, tone, then the first scan, which is the Slack-only pass; then the lists appear.
     With Gmail connected the first scan would be the full refresh, and a Slack pass alone would not finish setup.
  2. The gate: several ticks start no job (server side too: the fake saw no scan prompt); Not now is remembered for
     the session (a reload still waits and says so); a session that pressed Start carries on after a reload.
  3. Isolated (config.json "isolated": true, and OPENLOOPS_ISOLATED=1): no to-do file is read, /api/state says
     isolated, the grey pill shows, a session that pressed Start before still waits after a reload, and the button
     still starts it. The same install without the mark does read the to-do file (so the check means something).
  4. install.sh --isolated: writes "isolated" and "test_copy", no app and no launchd job, the first-scan cursor is
     history_days back (not a week); a re-run without it takes the mark off. Over an old ~/Documents install it
     copies without sending anything to a port (the stub curl logs nothing). setup.ps1 -Isolated: static check.
  5. #49: a job that fails within a second (signed out) is in /api/state with its sentence and a new seq within
     3 s; the page, set up and idle on its 60 s poll, shows that sentence, logs it in the Console and re-checks,
     which brings back Sign in, with "Knows who you are on Slack" unticked and "At least one source" saying sign in
     first. A job that starts and ends between two polls (started elsewhere) is still announced. With Claude
     missing, "At least one source" says install Claude first.
  7. Set-up (#28): with no AI CLI at all, the three cards (your AI, your sources, your schedule) are the page, with
     Install Claude as the one button; from a doctor payload with mixed states each card shows Not started / In
     progress / Done / Needs you; choosing an AI POSTs it to /api/config and re-checks; Connect opens the pop-up, which
     shows the sign-in link and closes itself on the poll after the row turns green; the first-scan box is the last
     step and nothing starts by itself; once set up, ⚙ Settings → Set-up brings the view back.
Node is required in CI (as test_messages.py); locally without node parts 1-3, 7 and the page half of 5 are skipped.
"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from openloops import messages  # noqa: E402  (the wording table only; importing it writes nothing)
from _helpers import fresh_install, isolated_env, start_app, stop  # noqa: E402

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def api(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


# A fake Claude CLI: signed in, Slack connected through the plugin, no Gmail, and headless runs (-p) that answer each
# job's prompt with a block of the shape the job expects. Every -p run's kind is written to prompts.txt.
FAKE = r'''#!PYTHON
import os, sys
a = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
if a == ["--version"]:
    print("2.1.280 (Claude Code)"); sys.exit(0)
out_ = os.path.exists(os.path.join(here, "signed_out"))   # #49: signed out, as a real expired sign-in looks
if a[:2] == ["auth", "status"]:
    print('{"loggedIn": %s, "email": "me@example.com"}' % ("false" if out_ else "true")); sys.exit(0)
if a[:2] == ["mcp", "list"]:
    print("Checking MCP server health...\n\nplugin:slack:slack: https://mcp.slack.com/mcp (HTTP) - ✔ Connected"); sys.exit(0)
if a[:1] == ["-p"]:
    prompt = sys.stdin.read()
    if out_:   # fails within a second, the way Claude Code does when the sign-in has gone
        sys.stderr.write("Not logged in \u00b7 Please run /login\n"); sys.exit(1)
    kind = ("people" if "<<<PEOPLE>>>" in prompt else "voice" if "<<<VOICE>>>" in prompt else
            ("refresh-slack-only" if "SLACK-ONLY RUN" in prompt else "refresh-full") if "<<<OPENLOOPS>>>" in prompt else
            "slack-id" if "Slack user id" in prompt else "other")
    with open(os.path.join(here, "prompts.txt"), "a") as f:
        f.write(kind + "\n")
    if kind == "slack-id":
        print("U0TEST12345")
    elif kind == "people":
        print('<<<PEOPLE>>>{"people": [{"name": "Sam Lee", "email": null, "channel": "slack", "count": 9, "guess": "peer", "example": "can you send the deck?"}]}<<<END>>>')
    elif kind == "voice":
        print('<<<VOICE>>>{"general": "Short and warm.", "people": {"Sam Lee": {"level": "peer", "style": "Brief.", "examples": ["ta"]}}, "samples": {"peer": "Any news?"}}<<<END>>>')
    elif kind.startswith("refresh"):
        print('<<<OPENLOOPS>>>{"new_loops": [{"id": "sam-lee-deck", "owner": "Sam Lee", "owner_email": null, "ask": "send the deck", '
              '"channel": "slack", "thread": "DM Sam D0TEST12345 1758600000.000100", '
              '"link": "https://example.slack.com/archives/D0TEST12345/p1758600000000100", "asked_at": "2026-09-22T10:00", '
              '"status": "waiting", "inbound": false, "priority": "normal", "theme": "deck"}], "updates": [], '
              '"gmail_available": false, "slack_available": true}<<<END>>>')
    sys.exit(0)
print("fake claude: unexpected " + " ".join(a)); sys.exit(9)
'''.replace("PYTHON", sys.executable)


def install_with_fake(prefix, config=None):
    tmp = fresh_install(prefix, dict({"agent": "claude", "owner_name": "Test", "slack_source": "plugin"}, **(config or {})))
    (tmp / "bin").mkdir()
    (tmp / "bin" / "claude").write_text(FAKE, encoding="utf-8")
    os.chmod(tmp / "bin" / "claude", 0o755)
    return tmp


def prompts(tmp):
    f = tmp / "bin" / "prompts.txt"
    return f.read_text().split() if f.exists() else []


def env_for(tmp, **extra):
    return isolated_env(tmp, PATH=str(tmp / "bin") + os.pathsep + os.environ.get("PATH", ""), **extra)


# ---------------------------------------------------------------- the page's own code, in node
def page_js(port, session, scenario, tmp):
    """The served page's setup code with a stub DOM, talking to the real app on `port`; `session` is the browser
    session's sessionStorage as the page load finds it; `scenario` is JS run once everything is defined. seen() in
    the scenario is what the fake claude in `tmp` has been asked so far."""
    html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode("utf-8")
    lines = html.splitlines()
    grab = lambda start: next(l for l in lines if l.startswith(start))
    cut = lambda a, b: html[html.index(a):html.index(b, html.index(a))]
    parts = [
        f"const BASE='http://127.0.0.1:{port}';const SS={json.dumps(session)};const PF={json.dumps(str(tmp / 'bin' / 'prompts.txt'))};",
        "const fs=require('fs');const seen=()=>fs.existsSync(PF)?fs.readFileSync(PF,'utf8').split(/\\s+/).filter(Boolean):[];",
        "const sessionStorage={getItem:k=>k in SS?SS[k]:null,setItem:(k,v)=>{SS[k]=String(v)},removeItem:k=>{delete SS[k]}};",
        "const els={};const $=s=>els[s]||(els[s]={style:{},textContent:'',innerHTML:'',disabled:false,title:'',classList:{toggle(){}}});",
        "const CON=[];function clog(m){CON.push(String(m))}const TOASTS=[];function toast(m){TOASTS.push(String(m))}",
        "function renderLists(){}function paintVoice(){}function banner(){}function appDown(e){CON.push('appDown '+e)}",
        "function paintSchedule(){}function schedBad(){return false}function paintConnect(){}function paintDaylog(){}function paintRm(){}function paintSetup(){}",
        "async function loadDaylog(){}async function loadRm(){}function agentUI(){}const PAGE='t';let stopped=false;",
        "let S=null,J=null,TODAY=null,C=null,V=null,P=null,DOC=null;",
        grab("const esc="), grab("const fmt="), grab("const MSG="), grab("const fill="), grab("function msg("), grab("const errSaid="),
        cut("const api=async", "let lastBanner="),
        grab("async function loadState("), grab("async function loadCfg("),
        grab("const running="), grab("const havePeople="), grab("const haveVoice="),
        cut("function stage(){", "\nasync function tick(){"), cut("async function tick(){", "\nfunction paintConnect("),
        cut("let peopleRendered=''", "async function findPeople("),
        cut("async function stageAuto(", "// Update Slack needs Slack"),
        cut("// Update Slack needs Slack", "\n// ---------- lists"),
        cut("const counts=()=>", "\n// ---------- day log"),
        cut("let docAt=0", "document.addEventListener('visibilitychange'"),   # doctor(), the poll loop, finished() (#49)
        """const CALLS=[];const realFetch=global.fetch;
let OFF=false;const FAIL_ONCE=new Set();global.fetch=(u,o)=>{if(OFF)return Promise.reject(new TypeError('Failed to fetch'));
 if(FAIL_ONCE.has(u)){FAIL_ONCE.delete(u);return Promise.resolve({ok:false,status:500,text:async()=>'{"error":"boom"}'})}if(o&&o.method==='POST')CALLS.push(u+' '+(o.body||''));return realFetch(BASE+u,o)};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(f,ms=60000){const end=Date.now()+ms;while(!f()){if(Date.now()>end)throw new Error('timed out');await sleep(100)}}
async function waitJob(n){await sleep(300);const end=Date.now()+90000;while(true){await loadState();if(!running(n))return J[n];if(Date.now()>end)throw new Error(n+' still running');await sleep(200)}}
const shown=()=>['connect','checkfail','people','auto','start'].filter(k=>$('#st_'+k).style.display==='');
const jobCalls=()=>CALLS.filter(c=>/^\\/api\\/(people|voice|refresh) /.test(c));
async function endElsewhere(){const before=(J.refresh&&J.refresh.seq)||0;   // a refresh this page did not start, run to its end
 await realFetch(BASE+'/api/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
 const end=Date.now()+10000;while(Date.now()<end){const s=await (await realFetch(BASE+'/api/state')).json();if(!s.jobs.refresh.running&&(s.jobs.refresh.seq||0)>before)return;await sleep(100)}throw new Error('refresh did not end')}
async function boot(){await loadCfg();await loadState();DOC=await api('/api/doctor',{force:true,detect:true})}
const snap=()=>({stage:stage(),shown:shown(),jobs:jobCalls(),said:$('#start_said').textContent,ask:$('#start_ask').textContent,
  later:$('#start_msg').textContent,uslack:$('#uslack').style.display,uslack_label:$('#uslack').textContent,uslack_disabled:$('#uslack').disabled,pill:$('#isolated_pill').style.display,lists:$('#lists').style.display});
(async()=>{const out={};try{""" + scenario + """}catch(e){out.error=String(e&&e.stack||e)}
 stopped=true;clearTimeout(loopT);out.SS=SS;console.log(JSON.stringify(out));process.exit(0)})();""",
    ]
    r = subprocess.run([NODE, "-e", "\n".join(parts)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"FAIL: node could not run the page's setup code: {r.stderr.strip()[-800:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    if out.get("error"):
        raise SystemExit(f"FAIL: the page's setup code threw: {out['error'][-800:]}")
    return out


NODE = shutil.which("node")
if not NODE and os.environ.get("GITHUB_ACTIONS"):
    raise SystemExit("FAIL: node is not on PATH in CI, so the page's setup code was not tested")
if sys.platform == "win32":
    NODE = None   # the fake claude here is a #! script, which Windows cannot run
    say("SKIP parts 1-3 on Windows: the fake claude here is a POSIX script")
elif not NODE:
    say("SKIP parts 1-3: node not installed (part 4 still runs)")

if NODE:
    # ------------------------------------------------------------ 1. Slack only, end to end
    say("1. Slack only: setup finishes, and the first scan is the Slack-only pass")
    tmp = install_with_fake("openloops-firstrun-slack-")
    srv, port = start_app(tmp, env_for(tmp))
    try:
        out = page_js(port, {}, """
 await boot();await tick();await tick();await tick();out.before=snap();out.seen=seen();out.doc=DOC.steps.map(x=>x.id+':'+x.ok);out.all_ok=DOC.all_ok;
 startScan();await until(()=>jobCalls().length>0);await waitJob('people');await loadCfg();await tick();out.people=snap();out.P=P&&P.people.length;
 await api('/api/config',{people:{'Sam Lee':{level:'peer',aliases:['Sam'],email:null}},voice_sample_people:['Sam Lee']});
 await loadCfg();await tick();out.voice=snap();await waitJob('voice');await loadCfg();
 await tick();out.scan=snap();out.scanTitle=$('#auto_title').innerHTML;out.scanSub=$('#auto_sub').textContent;
 out.refreshJob=await waitJob('refresh');await loadState();await tick();out.end=snap();out.meta=$('#meta').textContent;
 out.state={loops:S.loops.map(l=>l.id),last_refresh:S.last_refresh,last_slack_refresh:S.last_slack_refresh};
 await until(()=>S.setup_done,5000);await loadState();out.persisted=S.setup_done;
 const d=DOC;DOC=JSON.parse(JSON.stringify(d));DOC.steps.find(x=>x.id==='gmail').ok=true;await tick();
 out.withGmail={stage:stage(),lists:$('#lists').style.display,later:$('#gmail_later').style.display,sources:scanSources()};
 S.setup_done=false;out.fresh={stage:stage(),slackOnly:slackOnlyScan()};S.setup_done=true;DOC=d;""", tmp)
        check(out["all_ok"] and "slack:true" in out["doc"] and "gmail:false" in out["doc"] and "self:true" in out["doc"],
              f"checklist green with Slack only, Gmail not connected, Slack id found ({out['doc']})")
        b = out["before"]
        check(b["stage"] == "people" and b["shown"] == ["start"] and b["jobs"] == [],
              f"before the press: the Start the first scan box and no job started ({b})")
        check(b["said"] == "Looking back 30 days across Slack. The first pass can take ten minutes.",
              f"...saying what the first scan reads and how long, from history_days and what is connected ({b['said']!r})")
        check(b["uslack"] == "" and b["uslack_label"] == "Update Slack" and b["uslack_disabled"] is False,
              "Update Slack is on show during setup, Slack being connected, and pressable")
        check(out["scan"]["uslack_label"] == "Updating…" and out["scan"]["uslack_disabled"] is True,
              "...and while the first scan (a refresh) runs it is disabled and says Updating…")
        check(out["seen"] == ["slack-id"], f"...and the fake claude was asked nothing but the Slack id before the press ({out['seen']})")
        check(out["people"]["stage"] == "people" and out["people"]["shown"] == ["people"] and out["P"] == 1,
              "after Start the first scan: who's who ran and its picks are on show")
        check(out["voice"]["jobs"][-1].startswith("/api/voice "), "saving who's who starts Learn my tone by itself (the press covers the chain)")
        check(out["scan"]["stage"] == "scan" and out["scan"]["jobs"][-1] == '/api/refresh {"slack_only":true}',
              f"the first scan, Gmail not connected, is the Slack-only pass ({out['scan']['jobs'][-1:]})")
        check("First scan of your Slack…" in out["scanTitle"] and out["scanSub"] == b["said"],
              f"...and while it runs the page says the same sentence ({out['scanTitle']!r})")
        check(out["refreshJob"].get("rc") == 0 and prompts(tmp)[-1] == "refresh-slack-only",
              f"the job was a Slack-only run and it finished ({prompts(tmp)})")
        e = out["end"]
        check(e["stage"] == "ready" and e["lists"] == "" and e["shown"] == [] and out["state"]["loops"] == ["sam-lee-deck"]
              and out["state"]["last_slack_refresh"] and not out["state"]["last_refresh"],
              f"setup finished: the lists appear with the Slack loop, no full refresh needed ({e}, {out['state']})")
        check(out["meta"].startswith("slack ") and "never" not in out["meta"], f"the header says when Slack was read, not 'never' ({out['meta']!r})")
        check(out["persisted"] is True, "reaching ready saves setup_done in state.json")
        check(json.loads((tmp / "config.json").read_text(encoding="utf-8")).get("first_scan") == "go",
              "Start the first scan saved first_scan \"go\" in config.json")
        check(out["withGmail"] == {"stage": "ready", "lists": "", "later": "", "sources": "Slack and Gmail"},
              f"Gmail connected later: setup stays done, the lists stay, and a full scan is offered ({out['withGmail']})")
        check(out["fresh"] == {"stage": "scan", "slackOnly": False},
              "before setup is done, with both connected, the first scan is the full refresh and a Slack pass alone is not enough")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------ 2. the gate
    say("2. nothing runs before Start the first scan; Not now and Start are remembered for the tab")
    tmp = install_with_fake("openloops-firstrun-gate-")
    srv, port = start_app(tmp, env_for(tmp))
    try:
        out = page_js(port, {}, """
 await boot();for(let i=0;i<5;i++){await tick();await sleep(50)}out.before=snap();notNow();await sleep(100);await tick();out.after=snap();""", tmp)
        st = api(port, "/api/state")
        check(out["before"]["shown"] == ["start"] and out["before"]["jobs"] == [] and out["after"]["jobs"] == []
              and not any(j["running"] or "rc" in j for j in st["jobs"].values()) and "people" not in prompts(tmp),
              "five ticks and Not now: no job started, by the page or on the server")
        check(out["after"]["later"] == "Not started. Press Start the first scan when you're ready." and out["SS"] == {"ol.firstscan": "later"},
              f"Not now says so and is remembered for the tab ({out['after']['later']!r}, {out['SS']})")
        check(json.loads((tmp / "config.json").read_text(encoding="utf-8")).get("first_scan") == "later",
              "...and saved as first_scan \"later\" in config.json, for the weekday task")
        out = page_js(port, {"ol.firstscan": "later"}, "await boot();await tick();await tick();out.reload=snap();", tmp)
        check(out["reload"]["shown"] == ["start"] and out["reload"]["jobs"] == [] and out["reload"]["later"].startswith("Not started."),
              "a reload after Not now still waits, and still says so")
        out = page_js(port, {"ol.firstscan": "go"}, "await boot();await tick();out.reload=snap();await waitJob('people');", tmp)
        check(json.loads((tmp / "config.json").read_text(encoding="utf-8")).get("first_scan") == "later",
              "a reload changes nothing in config.json")
        check(out["reload"]["jobs"] == ["/api/people {}"] and out["reload"]["shown"] == ["people"],
              "a session that pressed Start the first scan carries on after a reload (not an isolated copy)")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------ 3. isolated
    say("2b. a choice that cannot be saved changes nothing: the box stays, says so, and offers Retry")
    tmp = install_with_fake("openloops-firstrun-choice-")
    srv, port = start_app(tmp, env_for(tmp))
    try:
        cfg0 = (tmp / "config.json").read_bytes()
        out = page_js(port, {}, """
 await boot();await tick();FAIL_ONCE.add('/api/config');await startScan();await tick();
 out.failed=snap();out.retry=$('#start_retry').style.display;out.pressed=pressed;
 FAIL_ONCE.add('/api/config');await notNow();await tick();out.failedLater=snap();
 await retryChoice();await tick();out.later=snap();out.retryAfter=$('#start_retry').style.display;""", tmp)
        f = out["failed"]
        check(f["shown"] == ["start"] and f["jobs"] == [] and f["later"] == messages.say("first_scan_not_saved")
              and out["retry"] == "" and out["pressed"] is False and out["SS"].get("ol.firstscan") == "later",
              f"a failed save of Start: the box stays with the sentence and Retry, nothing starts ({f['later']!r})")
        check(out["failedLater"]["jobs"] == [] and out["later"]["later"] == messages.say("first_scan_later") and out["retryAfter"] == "none",
              "a failed Not now likewise; Retry saves it and the box says Not started")
        check(json.loads((tmp / "config.json").read_text(encoding="utf-8")).get("first_scan") == "later",
              "config.json holds only the choice that was saved")
        (tmp / "config.json").write_bytes(cfg0)
        out = page_js(port, {}, """
 await boot();await tick();FAIL_ONCE.add('/api/config');await startScan();""", tmp)
        check("first_scan" not in json.loads((tmp / "config.json").read_text(encoding="utf-8")) and out["SS"] == {},
              "a failed save leaves config.json's first_scan and the tab's memory as they were")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    say("3. isolated: no to-do file, no scan by itself, the pill; by config.json and by OPENLOOPS_ISOLATED=1")
    tmp = install_with_fake("openloops-firstrun-iso-")
    todo = tmp / "home" / "Notes" / "to-do.md"
    todo.parent.mkdir(parents=True)
    todo.write_text("- [ ] A1 | Dev | a developer's own item | added 2026-09-01\n", encoding="utf-8")
    cfgf = tmp / "config.json"
    base_cfg = json.loads(cfgf.read_text(encoding="utf-8"))
    for how in ("config", "env", "neither"):
        cfg = dict(base_cfg, standing_file=str(todo), **({"isolated": True} if how == "config" else {}))
        cfgf.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        srv, port = start_app(tmp, env_for(tmp, **({"OPENLOOPS_ISOLATED": "1"} if how == "env" else {})))
        try:
            st = api(port, "/api/state")
            vault = [l["id"] for l in st["state"]["loops"] if l.get("channel") == "vault"]
            if how == "neither":
                check(not st["isolated"] and vault == ["vault-A1"], "without the mark the same install reads the to-do file (the check below means something)")
                continue
            check(st["isolated"] is True and vault == [] and api(port, "/api/standing")["path"] == "",
                  f"{how}: /api/state says isolated, and no to-do file is read (not even one named in Settings)")
            out = page_js(port, {"ol.firstscan": "go"}, """
 await boot();for(let i=0;i<3;i++){await tick();await sleep(50)}out.reload=snap();out.seen=seen();startScan();await until(()=>jobCalls().length>0);out.pressed=snap();await waitJob('people');""", tmp)
            check(out["reload"]["shown"] == ["start"] and out["reload"]["jobs"] == [] and out["reload"]["pill"] == ""
                  and "people" not in out["seen"],
                  f"{how}: a session that pressed Start before still waits after a reload, and the grey pill shows")
            check(out["pressed"]["jobs"] == ["/api/people {}"], f"{how}: the button still starts it")
        finally:
            stop(srv)
        (tmp / "people_suggested.json").unlink(missing_ok=True)   # each round starts as a fresh setup
        (tmp / "bin" / "prompts.txt").unlink(missing_ok=True)
    check(todo.read_text(encoding="utf-8").count("\n") == 1, "the to-do file was left as it was")
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 5. a job that fails fast (#49)
if sys.platform == "win32":
    say("SKIP part 5 on Windows: the fake claude here is a POSIX script")
else:
    say("5. a job that fails within a second is noticed, and the checklist says what to do first")
    tmp = install_with_fake("openloops-firstrun-fast-", {"slack_self_id": "U0TEST12345",
                                                         "people": {"Sam Lee": {"level": "peer", "aliases": ["Sam"], "email": None}},
                                                         "voice_sample_people": ["Sam Lee"]})
    (tmp / "voice.json").write_text(json.dumps({"general": "x", "people": {"Sam Lee": {"level": "peer"}}}), encoding="utf-8")
    (tmp / "state.json").write_text(json.dumps({"cursor": "2026-09-01T00:00+00:00", "last_refresh": "2026-09-22T09:00+00:00", "loops": []}),
                                    encoding="utf-8")
    srv, port = start_app(tmp, env_for(tmp))
    try:
        # the app on its own: the failure, its sentence and a new seq are in /api/state within 3 s
        (tmp / "bin" / "signed_out").write_text("")
        api(port, "/api/refresh", {})
        t1 = time.time()
        while time.time() - t1 < 3 and api(port, "/api/state")["jobs"]["refresh"]["running"]:
            time.sleep(0.1)
        j = api(port, "/api/state")["jobs"]["refresh"]
        check(not j["running"] and j.get("failure") == "job_signed_out" and j.get("said") == messages.say("job_signed_out", job="The refresh", ai="Claude")
              and j.get("seq") == 1 and j.get("finished_at"), f"app: within 3 s the job is over with job_signed_out, its sentence and seq 1 ({time.time() - t1:.1f}s)")
        api(port, "/api/refresh", {})
        t1 = time.time()
        while time.time() - t1 < 5 and api(port, "/api/state")["jobs"]["refresh"].get("seq") != 2:
            time.sleep(0.1)
        check(api(port, "/api/state")["jobs"]["refresh"].get("seq") == 2, "app: the next end of any job takes the next seq")
        (tmp / "bin" / "signed_out").unlink()
        if NODE:
            out = page_js(port, {}, """
     await boot();await loop();out.idle={stage:stage(),seen:SEEN.refresh};
     fs.writeFileSync(PF.replace('prompts.txt','signed_out'),'');
     const t0=Date.now();await refresh();await until(()=>TOASTS.length>0,3000);out.ms=Date.now()-t0;
     out.toasts=TOASTS.slice();out.said=J.refresh.said;out.con=CON.filter(l=>/refresh/.test(l));
     await until(()=>DOC&&DOC.steps.some(x=>x.id==='login'&&!x.ok),10000);await tick();
     out.after={stage:stage(),rows:Object.fromEntries(DOC.steps.map(x=>[x.id,{ok:x.ok,fix:x.fix,connect:x.connect||''}]))};
     out.watch=Object.keys(WATCH);""", tmp)
            check(out["idle"]["stage"] == "ready" and out["idle"]["seen"] == 2, "page: set up and idle, earlier job ends noted but not announced")
            check(out["ms"] < 3000 and out["toasts"] and out["toasts"][0] == out["said"] and out["said"],
                  f"page: the sentence is shown within 3 s of pressing Refresh ({out['ms']} ms: {out['toasts'][:1]})")
            check(any(l.startswith("refresh finished rc=1") for l in out["con"]), f"page: a Console line says the refresh ended ({out['con']})")
            rows = out["after"]["rows"]
            signin = messages.say("needs_signin", ai="Claude")
            check(out["after"]["stage"] == "connect" and rows["login"]["connect"] == "login" and not rows["login"]["ok"],
                  "page: the re-check brings the checklist back with its Sign in button")
            check(not rows["self"]["ok"] and rows["self"]["fix"] == signin, f"'Knows who you are on Slack' is not ticked while signed out ({rows['self']})")
            check(not rows["channel"]["ok"] and rows["channel"]["fix"] == signin, f"'At least one source' says sign in first ({rows['channel']['fix']!r})")
            check(out["watch"] == [], "page: back to the slow poll once the job's end was seen")
            out = page_js(port, {}, """
 const realST=global.setTimeout;const delays=[];global.setTimeout=(f,ms)=>{if(f===loop)delays.push(ms);return realST(f,ms)};
 await boot();await loop();jobStarted('refresh');await loop();out.watching=delays.slice(-1)[0];
 OFF=true;await loop();out.offline=delays.slice(-1)[0];OFF=false;await loop();out.back=delays.slice(-1)[0];
 stopped=true;const n=delays.length;pollSoon();out.afterQuit=delays.length-n;""", tmp)
            check(out["watching"] == 1500 and out["offline"] == 60000 and out["back"] == 1500 and out["afterQuit"] == 0,
                  f"page: fast polls while watching a job, the slow interval while the app does not answer, none after quit ({out})")
            (tmp / "bin" / "signed_out").unlink()
            out = page_js(port, {}, """
     await boot();await loop();
     await realFetch(BASE+'/api/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});   // not by this page
     const end=Date.now()+10000;while(Date.now()<end){const s=await (await realFetch(BASE+'/api/state')).json();if(!s.jobs.refresh.running&&s.jobs.refresh.seq>SEEN.refresh)break;await sleep(100)}
     out.between=CON.filter(l=>/refresh (started|finished)/.test(l));await loop();
     out.after=CON.filter(l=>/refresh finished/.test(l));out.toasts=TOASTS.slice();""", tmp)
            check(out["between"] == [] and any(l.startswith("refresh finished rc=0") for l in out["after"])
                  and any(x.startswith("Refresh done") for x in out["toasts"]),
                  f"page: a job that started and ended between two polls is still announced ({out['after']}, {out['toasts']})")
            out = page_js(port, {}, """
     await boot();await loop();await endElsewhere();FAIL_ONCE.add('/api/config');const seen0=SEEN.refresh;
     await loop();out.failed={toasts:TOASTS.length,seen:SEEN.refresh===seen0};await loop();out.retried={toasts:TOASTS.length,seen:SEEN.refresh===J.refresh.seq};
     await endElsewhere();await loadState();SEEN_INST='a server that has since restarted';SEEN.refresh=J.refresh.seq;   // as if the new server's first end had this seq
     await loop();out.restart={toasts:TOASTS.length,inst:SEEN_INST===INST};""", tmp)
            check(out["failed"] == {"toasts": 0, "seen": True} and out["retried"] == {"toasts": 1, "seen": True},
                  f"page: a follow-up load that fails does not use up the job's end; the next poll announces it ({out})")
            out2 = page_js(port, {}, """
     await boot();await loop();await endElsewhere();await Promise.all([loop(),loop(),loop()]);
     out.toasts=TOASTS.filter(x=>x.startsWith('Refresh done')).length;out.pending=pendingLoop;out.inLoop=inLoop;
     await endElsewhere();CLAIMED.add(INST+':refresh:'+(J.refresh.seq+1));await loop();out.claimedSkipped=TOASTS.filter(x=>x.startsWith('Refresh done')).length;""", tmp)
            check(out2["toasts"] == 1 and out2["pending"] is False and out2["inLoop"] is False,
                  f"page: three overlapping polls announce one job end exactly once ({out2})")
            check(out2["claimedSkipped"] == 1, "page: a job end claimed by a pass in progress is not handled by another")
            check(out["restart"] == {"toasts": 2, "inst": True},
                  f"page: a new server instance starts SEEN afresh, so a seq it had already seen is still announced ({out['restart']})")
        inst1 = api(port, "/api/state")["instance"]
    finally:
        stop(srv)
    srv, port = start_app(tmp, env_for(tmp))
    try:
        inst2 = api(port, "/api/state")["instance"]
        check(len(inst1) == 32 and len(inst2) == 32 and inst1 != inst2, "app: /api/state names its instance, new at every start")
    finally:
        stop(srv)
    # Claude missing: "At least one source" says install it first (the checker on its own, no claude anywhere on PATH)
    bare = os.pathsep.join(d for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin") if not shutil.which("claude", path=d))
    r = subprocess.run([sys.executable, "-m", "openloops.doctor"], cwd=tmp, env=isolated_env(tmp, PATH=bare),
                       capture_output=True, text=True, timeout=120)
    rows = {s["id"]: s for s in json.loads(r.stdout.strip().splitlines()[-1])["steps"]}
    check(not rows["channel"]["ok"] and rows["channel"]["fix"] == messages.say("needs_install", ai="Claude")
          and not rows["self"]["ok"], f"Claude missing: 'At least one source' says install Claude first ({rows['channel']['fix']!r})")
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 6. the scheduled runner obeys the choice and isolation
say("6. a refresh not started by the app (the weekday task) skips before the first scan and on an isolated copy")
if sys.platform == "win32":
    say("SKIP part 6 on Windows: the fake claude here is a POSIX script")
else:
    tmp = install_with_fake("openloops-firstrun-sched-", {"slack_self_id": "U0TEST12345"})
    (tmp / "state.json").write_text(json.dumps({"cursor": "2026-09-01T00:00+00:00", "last_refresh": None, "loops": []}), encoding="utf-8")
    cfgf = tmp / "config.json"
    base_cfg = json.loads(cfgf.read_text(encoding="utf-8"))
    env = {k: v for k, v in env_for(tmp).items() if k != "OPENLOOPS_RUN_ID"}   # as run-refresh.sh starts it
    for label, extra, want in (("first_scan later", {"first_scan": "later"}, messages.say("scheduled_later")),
                               ("isolated", {"isolated": True, "first_scan": "go"}, messages.say("scheduled_isolated")),
                               ("no first_scan key (an install from before)", {}, None)):
        cfgf.write_text(json.dumps(dict(base_cfg, **extra), indent=2), encoding="utf-8")
        (tmp / "bin" / "prompts.txt").unlink(missing_ok=True)
        r = subprocess.run([sys.executable, "-m", "openloops.refresh"], cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
        if want:
            check(r.returncode == 2 and r.stdout.strip().endswith("SKIPPED: " + want) and prompts(tmp) == [],
                  f"scheduled, {label}: SKIPPED in one plain line, the AI never asked ({r.stdout.strip()[-160:]!r})")
        else:
            check(r.returncode == 0 and prompts(tmp) == ["refresh-full"], f"scheduled, {label}: runs as before (rc {r.returncode})")
    r = subprocess.run([sys.executable, "-m", "openloops.autochase"], cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
    cfgf.write_text(json.dumps(dict(base_cfg, isolated=True, auto_chase={"enabled": True}), indent=2), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "openloops.autochase"], cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
    check(r.returncode == 0 and "SKIPPED: auto-chase (isolated)" in r.stdout, f"scheduled auto-chase skips on an isolated copy ({r.stdout.strip()!r})")
    for label, extra in (("first_scan later", {"first_scan": "later"}), ("isolated", {"isolated": True})):
        cfgf.write_text(json.dumps(dict(base_cfg, **extra), indent=2), encoding="utf-8")
        (tmp / "bin" / "prompts.txt").unlink(missing_ok=True)
        srv, port = start_app(tmp, env_for(tmp))
        try:
            api(port, "/api/refresh", {})
            t1 = time.time()
            while time.time() - t1 < 20 and api(port, "/api/state")["jobs"]["refresh"]["running"]:
                time.sleep(0.1)
            j = api(port, "/api/state")["jobs"]["refresh"]
            check(j.get("rc") == 0 and prompts(tmp) == ["refresh-full"], f"started by the app, {label}: the refresh runs (a press is consent) ({j.get('rc')}, {prompts(tmp)}, {j.get('log', '')[-200:]!r})")
        finally:
            stop(srv)
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 7. the Set-up view (#28)
# A fake Claude for the Set-up view: signed in, the Slack plugin there but needing its sign-in until `mcp login`
# finishes, and that sign-in waits (on a terminal, as the real one) until the test drops an "allow" file, as a person
# clicking Allow would. No Gmail. Headless runs only answer the Slack id lookup.
FAKE7 = r'''#!PYTHON
import os, sys, time
a = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
flag = lambda n: os.path.exists(os.path.join(here, n))
if a == ["--version"]:
    print("2.1.280 (Claude Code)"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    print('{"loggedIn": true, "email": "me@example.com"}'); sys.exit(0)
if a[:2] == ["mcp", "list"]:
    print("Checking MCP server health...\n\nplugin:slack:slack: https://mcp.slack.com/mcp (HTTP) - "
          + ("✔ Connected" if flag("slack_ok") else "! Needs authentication")); sys.exit(0)
if a[:2] == ["mcp", "login"]:
    if not os.isatty(0):
        print("Couldn't complete authentication: stdin isn't a terminal"); sys.exit(1)
    print("Visit this URL to authorize:\n  https://example.invalid/authorize?state=abc\n", flush=True)
    end = time.time() + 60
    while not flag("allow") and time.time() < end:
        time.sleep(0.2)
    if not flag("allow"):
        sys.exit(3)
    open(os.path.join(here, "slack_ok"), "w").close()
    print("Authentication successful."); sys.exit(0)
if a[:1] == ["-p"]:
    prompt = sys.stdin.read()
    kind = "slack-id" if "Slack user id" in prompt else "other"
    with open(os.path.join(here, "prompts.txt"), "a") as f:
        f.write(kind + "\n")
    print("U0TEST12345" if kind == "slack-id" else ""); sys.exit(0)
print("fake claude: unexpected " + " ".join(a)); sys.exit(9)
'''.replace("PYTHON", sys.executable)
# A fake Codex: installed, not signed in to ChatGPT. Every call is written down (calls-codex.txt).
FAKE7_CODEX = r'''#!PYTHON
import os, sys
a = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls-codex.txt"), "a") as f:
    f.write(" ".join(a) + "\n")
if a == ["--version"]:
    print("codex-cli 0.156.1"); sys.exit(0)
if a[:2] == ["login", "status"]:
    print("Not logged in"); sys.exit(1)
print("fake codex: unexpected " + " ".join(a)); sys.exit(9)
'''.replace("PYTHON", sys.executable)
# Only the system folders on PATH: no real claude, codex or grok of the developer's is ever run (the fake is added in front)
BARE = os.pathsep.join(d for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin") if os.path.isdir(d))


def setup_install(prefix, fake=True, config=None):
    tmp = fresh_install(prefix, dict({"agent": "claude", "owner_name": "Test", "slack_source": "plugin"}, **(config or {})))
    (tmp / "bin").mkdir()
    if fake:
        (tmp / "bin" / "claude").write_text(FAKE7, encoding="utf-8")
        (tmp / "bin" / "codex").write_text(FAKE7_CODEX, encoding="utf-8")
    # the browser: only writes down what it was asked to open. It never fails: Python's webbrowser would then go on to
    # the real default browser, so a failed browser step is faked on the page side instead (FAKE in setup_js).
    (tmp / "bin" / "browser").write_text(f"#!/bin/sh\necho \"$1\" >> '{tmp / 'opened.txt'}'\n", encoding="utf-8")
    for f in (tmp / "bin").iterdir():
        os.chmod(f, 0o755)
    return tmp


def setup_env(tmp):
    return isolated_env(tmp, PATH=str(tmp / "bin") + os.pathsep + BARE, BROWSER=str(tmp / "bin" / "browser"))


def setup_js(port, scenario, tmp):
    """The served page's Set-up view with everything it calls (stage machine, tick, checklist rows and buttons,
    connectStep, doctor), a stub DOM whose elements keep what they are given, talking to the real app on `port`."""
    html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode("utf-8")
    lines = html.splitlines()
    grab = lambda start: next(l for l in lines if l.startswith(start))
    cut = lambda a, b: html[html.index(a):html.index(b, html.index(a))]
    parts = [
        f"const BASE='http://127.0.0.1:{port}';const SS={{}};const BIN={json.dumps(str(tmp / 'bin'))};",
        "const fs=require('fs');const seen=()=>{const f=BIN+'/prompts.txt';return fs.existsSync(f)?fs.readFileSync(f,'utf8').split(/\\s+/).filter(Boolean):[]};",
        "const sessionStorage={getItem:k=>k in SS?SS[k]:null,setItem:(k,v)=>{SS[k]=String(v)},removeItem:k=>{delete SS[k]}};",
        "const els={};const mk=id=>({id,style:{},textContent:'',innerHTML:'',disabled:false,title:'',open:false,className:'',kids:[],"
        "classList:{toggle(){}},appendChild(c){this.kids.push(c);c.parent=this;return c},showModal(){this.open=true},close(){this.open=false},addEventListener(){}});",
        "const $=s=>els[s]||(els[s]=mk(s));const document={getElementById:id=>$('#'+id),createElement:()=>mk('new')};",
        "const CON=[];function clog(m){CON.push(String(m))}const TOASTS=[];function toast(m){TOASTS.push(String(m))}",
        "function renderLists(){}function paintVoice(){}function banner(){}function appDown(e){CON.push('appDown '+e)}function paintDaylog(){}function paintRm(){}",
        "function paintForm(){}async function loadDaylog(){}async function loadRm(){}function agentUI(){}function agentFormUI(){}const PAGE='t';let stopped=false;",
        "let S=null,J=null,TODAY=null,C=null,V=null,P=null,DOC=null;",
        grab("const esc="), grab("const fmt="), grab("const MSG="), grab("const fill="), grab("function msg("), grab("const errSaid="),
        cut("const api=async", "let lastBanner="),
        cut("let formPainted=false;", "\n\n// ---------- data"),
        grab("async function loadState("), grab("async function loadCfg("), grab("const agentLabel="),
        grab("const running="), grab("const havePeople="), grab("const haveVoice="),
        cut("function stage(){", "\nasync function tick(){"), cut("async function tick(){", "\nfunction paintConnect("),
        cut("function paintConnect(", "\n// ---------- Setup buttons"),
        cut("// ---------- Setup buttons", "\n// The Mac's weekday"),
        cut("let schedSaid=''", "\n// The download button"), "paintSchedule=function(){};",
        cut("// ---------- Set-up view (#28)", "\nlet peopleRendered=''"),
        cut("let peopleRendered=''", "async function findPeople("),
        cut("async function stageAuto(", "// Update Slack needs Slack"),
        cut("// Update Slack needs Slack", "\n// ---------- lists"),
        cut("const counts=()=>", "\n// ---------- day log"),
        grab("async function openClaude("),
        cut("let docAt=0", "document.addEventListener('visibilitychange'"),
        """const CALLS=[];const realFetch=global.fetch;
const FAKE={};   // url -> [answer to a POST, answer to a GET]: a setup step the app itself never runs (no browser opens)
global.fetch=(u,o)=>{const post=!!(o&&o.method==='POST');if(post)CALLS.push(u+' '+(o.body||''));
 if(FAKE[u])return Promise.resolve({ok:true,status:200,json:async()=>FAKE[u][post?0:1],text:async()=>JSON.stringify(FAKE[u][post?0:1])});
 return realFetch(BASE+u,o)};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(f,ms=60000){const end=Date.now()+ms;while(!f()){if(Date.now()>end)throw new Error('timed out');await sleep(100)}}
const jobCalls=()=>CALLS.filter(c=>/^\\/api\\/(people|voice|refresh) /.test(c));
async function boot(){await loadCfg();await loadState();DOC=await api('/api/doctor',{force:true,detect:true});docAt=Date.now()}
const card=k=>({state:$('#su_'+k+'_state').textContent,cls:$('#su_'+k+'_state').className,rows:$('#su_'+k+'_rows').innerHTML,act:($('#su_'+k+'_act')||{}).innerHTML});
const view=()=>({stage:stage(),setup:$('#setup').style.display,lists:$('#lists').style.display,back:$('#setup_back').style.display,
  ai:card('ai'),src:card('src'),sched:card('sched'),pick:$('#su_ai_pick').innerHTML,scan:$('#su_scan').textContent,time:$('#su_time').innerHTML,
  start:$('#st_start').style.display,jobs:jobCalls()});
(async()=>{const out={};try{""" + scenario + """}catch(e){out.error=String(e&&e.stack||e)}
 stopped=true;clearTimeout(loopT);clearInterval(allowT);console.log(JSON.stringify(out));process.exit(0)})();""",
    ]
    r = subprocess.run([NODE, "-e", "\n".join(parts)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"FAIL: node could not run the page's Set-up code: {r.stderr.strip()[-800:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    if out.get("error"):
        raise SystemExit(f"FAIL: the page's Set-up code threw: {out['error'][-800:]}")
    return out


if NODE:
    say("7. the Set-up view (#28): three cards, the AI picker, the pop-up for Allow, ⚙ → Set-up, the first scan last")
    page = (REPO / "openloops" / "index.html").read_text(encoding="utf-8")
    order = [page.index(x) for x in ('id="su_ai"', 'id="su_src"', 'id="su_sched"', 'id="su_start"', 'id="su_all"')]
    check(order == sorted(order) and "function setupEmbed(){$('#su_all_body').appendChild($('#st_connect'));$('#su_start').appendChild($('#st_start'))}" in page
          and "\nsetupEmbed();" in page,
          "the cards run AI, sources, schedule; the first-scan box is moved into the last card, the checklist into 'Every check'")
    check(page.index('id="setup_open" onclick="openSetup()"') > page.index('id="page_settings"') and ">Open Set-up</button>" in page,
          "⚙ Settings has a Set-up row that opens the view")

    # 7a. nothing installed at all: the real check on a machine with no AI CLI
    tmp = setup_install("openloops-setup-none-", fake=False)
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, "await boot();await tick();out.v=view();", tmp)
        v = out["v"]
        check(v["stage"] == "connect" and v["setup"] == "" and v["lists"] == "none" and v["back"] == "none",
              "no AI installed: the Set-up view is the page, before the loops")
        check(v["ai"]["state"] == "Needs you" and ">Install Claude</button>" in v["ai"]["rows"] and "Show the exact command" in v["ai"]["rows"]
              and v["ai"]["act"] == "", f"'Your AI' needs you, with Install Claude (its command one click away) as its one button ({v['ai']['state']})")
        check(v["pick"].count('role="radio"') == 3 and 'aria-checked="true" onclick="chooseAI(\'claude\')"' in v["pick"]
              and all(x in v["pick"] for x in ("ChatGPT (Codex)", "Miro only if you added a Miro server", "Free gets Gmail and Slack isn&#39;t confirmed", "Google Cloud set-up", "paid Claude plan")),
              "three choices, Claude picked, each saying what it needs and what it can't do")
        check(v["src"]["state"] == "Not started" and messages.say("needs_install", ai="Claude") in v["src"]["rows"]
              and "<button" not in v["src"]["rows"] and v["src"]["act"] == "",
              "'Your sources' has not started: each row says install Claude first, and offers no button yet")
        check(v["sched"]["state"] == "Not started" and "nothing runs by itself" in v["scan"] and "09:15" in v["time"],
              f"'Your schedule' has not started: the morning time, and the first scan comes last ({v['scan']!r})")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7b. mixed states from a doctor payload, as the check reports them
    tmp = setup_install("openloops-setup-mixed-")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();const row=(id,ok,x)=>Object.assign({id,ok,optional:!['claude','login','channel'].includes(id),title:id+' title',fix:ok?'':id+' fix'},x||{});
 DOC={agent:'claude',all_ok:true,steps:[row('claude',true),row('login',true),row('slack',false,{connect:'slack'}),row('gmail',true),
  row('miro',false,{connect:'miro'}),row('channel',true),row('self',false),
  row('schedule',false,{alert:true,kind:'failed',link:'https://github.com/OscarC178/Open-Loops/releases/latest'})]};
 await tick();out.a=view();
 CONN.slack={busy:true,msg:'Waiting for you in the browser: click Allow there.'};paintSetup(stage());out.b=view();delete CONN.slack;
 DOC={agent:'claude',all_ok:false,steps:[row('claude',true),row('login',false,{connect:'login'}),row('slack',false),row('gmail',false),row('miro',false),row('channel',false),row('self',false)]};
 await tick();out.c=view();
 C.agent='grok';const gfix='Gmail needs a one-off Google sign-in of its own: follow Gmail with Grok in INSTALL.md.';
 DOC={agent:'grok',all_ok:false,steps:[row('claude',true),row('login',false,{fix:"Click 'Open Grok' below and follow the sign-in link it shows."}),
  row('gmail',false,{fix:gfix}),row('channel',false),row('self',false)]};await tick();out.d=view();""", tmp)
        a, b, c, d = out["a"], out["b"], out["c"], out["d"]
        check(a["setup"] == "" and a["ai"]["state"] == "Done" and "su-state done" == a["ai"]["cls"] and ">Check again</button>" in a["ai"]["act"],
              "AI installed and signed in: 'Your AI' is done, and its one button is Check again")
        check(a["src"]["state"] == "Done" and 'class="" onclick="connectStep(\'slack\')">Connect Slack</button>' in a["src"]["rows"]
              and 'class="" onclick="connectStep(\'miro\')">Connect Miro</button>' in a["src"]["rows"] and a["src"]["act"] == "",
              "Gmail ticked is enough for 'Your sources' to be done; Slack and Miro keep a Connect each, as extras (not primary)")
        check("self title" not in a["src"]["rows"], "an optional 'Knows who you are on Slack' does not clutter the card")
        check(a["sched"]["state"] == "Needs you" and "schedule fix" in a["sched"]["rows"] and "Open the download page" in a["sched"]["rows"]
              and a["start"] == "" and a["scan"] == "",
              "a red morning-refresh row puts 'Your schedule' on Needs you, with its fix and the download page; the first-scan box shows")
        check(b["src"]["state"] == "In progress" and "Waiting for you in the browser" in b["src"]["rows"],
              "a Connect under way: 'Your sources' is in progress, the row says what it waits for")
        check(c["ai"]["state"] == "Needs you" and 'class="primary" onclick="connectStep(\'login\')">Sign in</button>' in c["ai"]["rows"]
              and c["src"]["state"] == "Not started" and c["sched"]["state"] == "Not started" and c["stage"] == "connect",
              "signed out: 'Your AI' needs you with Sign in; sources and schedule wait their turn")
        check('class="primary" onclick="suOpenAgent()">Open Grok</button>' in d["ai"]["act"] and "Open Grok" in d["ai"]["rows"]
              and "Gmail with Grok in INSTALL.md" in d["src"]["rows"] and "<button" not in d["src"]["rows"]
              and 'aria-checked="true" onclick="chooseAI(\'grok\')"' in d["pick"],
              "Grok: sign-in is Open Grok, and the Gmail row keeps its own instruction, with no button")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7c. choosing an AI saves it and re-checks; 7d. Connect opens the pop-up, which closes itself when the row turns green;
    # 7e. then the first-scan box is the last step, and nothing starts by itself
    tmp = setup_install("openloops-setup-flow-")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();await tick();out.before={agent:C.agent,doc:DOC.agent,pick:$('#su_ai_pick').innerHTML};
 let n=CALLS.length;await chooseAI('codex');out.codex={calls:CALLS.slice(n),agent:C.agent,doc:DOC.agent,v:view()};
 out.cfgCodex=JSON.parse(fs.readFileSync(BIN+'/../config.json','utf8')).agent;
 n=CALLS.length;await chooseAI('claude');out.claude={calls:CALLS.slice(n),doc:DOC.agent,v:view()};
 n=CALLS.length;const p=connectStep('slack');
 out.pressed={open:$('#allow_dlg').open,title:$('#allow_title').textContent,sub:$('#allow_sub').textContent,src:$('#su_src_state').textContent};
 await until(()=>$('#allow_link').innerHTML.includes('example.invalid'),15000);
 out.link={html:$('#allow_link').innerHTML,open:$('#allow_dlg').open,check:$('#allow_check').style.display};
 await sleep(3500);out.waiting={open:$('#allow_dlg').open,slack:DOC.steps.find(x=>x.id==='slack').ok};
 fs.writeFileSync(BIN+'/allow','');const t0=Date.now();
 await until(()=>!$('#allow_dlg').open,30000);out.closed={ms:Date.now()-t0,slack:DOC.steps.find(x=>x.id==='slack').ok,toasts:TOASTS.slice(),calls:CALLS.slice(n)};
 await p;for(let i=0;i<3;i++){await tick();await sleep(50)}
 out.end=view();out.seen=seen();setupEmbed();out.embed={start:els['#st_start'].parent.id,connect:els['#st_connect'].parent.id};""", tmp)
        check(out["before"]["agent"] == "claude" and out["before"]["doc"] == "claude", "starts on Claude")
        cz = out["codex"]
        check(cz["calls"][:2] == ['/api/config {"agent":"codex"}', '/api/doctor {"force":true,"detect":true}'],
              f"choosing ChatGPT (Codex) POSTs agent to /api/config, then re-checks ({cz['calls']})")
        check(out["cfgCodex"] == "codex" and cz["agent"] == "codex" and cz["doc"] == "codex"
              and "Codex is installed" in cz["v"]["ai"]["rows"] and "Signed in to ChatGPT" in cz["v"]["ai"]["rows"]
              and 'aria-checked="true" onclick="chooseAI(\'codex\')"' in cz["v"]["pick"],
              "...config.json says codex, and the card shows Codex's own rows with Codex picked")
        check(out["claude"]["calls"][:2] == ['/api/config {"agent":"claude"}', '/api/doctor {"force":true,"detect":true}']
              and out["claude"]["doc"] == "claude" and out["claude"]["v"]["ai"]["state"] == "Done",
              "choosing Claude again: saved, re-checked, 'Your AI' done")
        pr = out["pressed"]
        check(pr["open"] and pr["title"] == "Click Allow in the tab that just opened, then come back" and "Slack" in pr["sub"]
              and pr["src"] == "In progress", f"Connect Slack opens the pop-up at once, and the card says in progress ({pr})")
        check('Nothing opened? <a href="https://example.invalid/authorize?state=abc" target="_blank" rel="noopener">Open the sign-in page</a>' in out["link"]["html"]
              and out["link"]["open"] and out["link"]["check"] == "none",
              "the pop-up shows the sign-in link from the step's own status, in case no tab opened")
        opened = (tmp / "opened.txt").read_text().split() if (tmp / "opened.txt").exists() else []
        check(opened == ["https://example.invalid/authorize?state=abc"], f"...and the app opened that link in the browser, once ({opened})")
        check(out["waiting"] == {"open": True, "slack": False}, "while nobody has clicked Allow it stays open")
        cl = out["closed"]
        check(cl["slack"] is True and cl["ms"] < 12000 and "Slack connected." in cl["toasts"] and "/api/connect/slack {}" in cl["calls"],
              f"after Allow the row turns green and the pop-up closes itself on the next poll ({cl['ms']} ms, {cl['toasts']})")
        e = out["end"]
        check(e["stage"] == "people" and e["setup"] == "" and e["start"] == "" and e["sched"]["state"] == "Needs you"
              and e["ai"]["state"] == "Done" and e["src"]["state"] == "Done" and e["lists"] == "none",
              "AI and a source ready: the view stays, and its last step is the first-scan box, waiting for the press")
        check(e["jobs"] == [] and out["seen"] == ["slack-id"], f"nothing started by itself: no job, the AI asked only for the Slack id ({out['seen']})")
        check(out["embed"] == {"start": "#su_start", "connect": "#su_all_body"}, "setupEmbed puts the first-scan box in the schedule card")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7g. Codex: a Connect page that would not open, and a Check again that finds the row still red, are said in the pop-up
    tmp = setup_install("openloops-setup-codexfail-", config={"agent": "codex"})
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 FAKE['/api/connect/gmail']=[{started:true},{running:false,rc:1,url:'https://chatgpt.com/apps',opened:true,last:'',step:'gmail'}];   // the page did not open
 await boot();await tick();const p=connectStep('gmail');out.opened={open:$('#allow_dlg').open,title:$('#allow_title').textContent};
 await until(()=>$('#allow_msg').textContent!==''&&$('#allow_link').innerHTML.includes('chatgpt.com'),15000);
 out.fail={msg:$('#allow_msg').textContent,link:$('#allow_link').innerHTML,check:$('#allow_check').style.display};await p;
 await allowCheck();out.checked={msg:$('#allow_msg').textContent,open:$('#allow_dlg').open};""", tmp)
        check(out["opened"] == {"open": True, "title": "Connect Gmail on the ChatGPT page that just opened, then come back"},
              "Codex: Connect Gmail opens the pop-up for ChatGPT's apps page")
        check(out["fail"]["msg"] == messages.say("codex_browser_failed", service="Gmail") and "chatgpt.com" in out["fail"]["link"]
              and out["fail"]["check"] == "", f"...the page would not open: the pop-up says so, with the link and Check again ({out['fail']['msg']!r})")
        check(out["checked"] == {"msg": messages.say("needs_signin", ai="ChatGPT"), "open": True},
              f"...Check again that finds Gmail still red says the row's own sentence in the pop-up ({out['checked']['msg']!r})")
        check(not (tmp / "opened.txt").exists(), "...and no browser was asked to open anything")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7f. once set up, ⚙ → Set-up brings the same view back, and Back returns to the loops
    tmp = setup_install("openloops-setup-later-", config={"slack_self_id": "U0TEST12345", "first_scan": "go",
                                                          "people": {"Sam Lee": {"level": "peer", "aliases": ["Sam"], "email": None}},
                                                          "voice_sample_people": ["Sam Lee"]})
    (tmp / "bin" / "slack_ok").write_text("")
    (tmp / "voice.json").write_text(json.dumps({"general": "x", "people": {"Sam Lee": {"level": "peer"}}}), encoding="utf-8")
    (tmp / "state.json").write_text(json.dumps({"cursor": "2026-09-01T00:00+00:00", "last_refresh": "2026-09-22T09:00+00:00",
                                                "setup_done": True, "loops": []}), encoding="utf-8")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();await tick();out.ready=view();await openSetup();out.open=view();out.home=$('#page_home').style.display;
 await closeSetup();out.closed=view();""", tmp)
        r, o, c = out["ready"], out["open"], out["closed"]
        check(r["stage"] == "ready" and r["setup"] == "none" and r["lists"] == "", "set up: the loops are the page, Set-up is out of the way")
        check(o["setup"] == "" and o["lists"] == "none" and o["back"] == "" and out["home"] == ""
              and [o[k]["state"] for k in ("ai", "src", "sched")] == ["Done", "Done", "Done"] and o["scan"] == "The first scan is done.",
              f"⚙ → Set-up opens the view on Home, every card done, with Back to your loops ({[o[k]['state'] for k in ('ai', 'src', 'sched')]})")
        check(c["setup"] == "none" and c["lists"] == "" and c["jobs"] == [], "Back to your loops closes it again; nothing started")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 4. the installers
say("4. install.sh --isolated, and setup.ps1 -Isolated (static)")
ps = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
code = "\n".join(l for l in ps.splitlines() if not l.lstrip().startswith("#"))
check("[switch]$Isolated" in code and "if ($Isolated) { $NoApp = [switch]$true; $NoTask = [switch]$true }" in code,
      "setup.ps1 takes -Isolated, which implies -NoApp and -NoTask")
check(code.count("-NotePropertyName isolated -NotePropertyValue $true") == 2 and "PSObject.Properties.Remove('isolated')" in code
      and "-or $Isolated" in code, "setup.ps1 writes isolated (and test_copy) on a new and an existing config.json, and takes it off without")
check('-NotePropertyName first_scan -NotePropertyValue "later"' in code and 'Get-ScheduledTask -TaskName "Claude Open Loops Refresh"' in code
      and 'register-task.ps1") -Remove' in code, "setup.ps1: a new config.json waits for the page; -Isolated removes this folder's own task")
check("AddDays(-7)" not in code and "history_days" in code and "AddDays(-$days)" in code,
      "setup.ps1: the first-scan cursor is history_days back, not a week")
if sys.platform == "win32":
    say("SKIP install.sh on Windows")
    say("PASS")
    raise SystemExit(0)

tmp = Path(tempfile.mkdtemp(prefix="openloops-firstrun-install-"))
try:
    fakebin = tmp / "bin"
    fakebin.mkdir()
    for name, body in (("claude", "#!/bin/bash\nexit 0\n"),
                       ("launchctl", f"#!/bin/bash\necho \"$*\" >> '{tmp / 'launchctl.log'}'\n[ \"$1\" = print ] && exit 113\nexit 0\n"),
                       ("curl", f"#!/bin/bash\necho \"$*\" >> '{tmp / 'curl.log'}'\nexit 7\n")):
        (fakebin / name).write_text(body)
        (fakebin / name).chmod(0o755)

    def install(home, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENLOOPS_PORT", "OPENLOOPS_DEST", "OPENLOOPS_ISOLATED")}
        env.update(HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}")
        return subprocess.run(["bash", str(REPO / "install.sh"), *args], env=env, capture_output=True, text=True,
                              timeout=300, stdin=subprocess.DEVNULL)

    home = tmp / "home"
    (home / "Desktop").mkdir(parents=True)
    dest = tmp / "OpenLoops-test"
    r = install(home, "--dest", str(dest), "--isolated", "--no-launch", "--port", "8790", "--name", "Test")
    check(r.returncode == 0, f"install.sh --isolated finished ({(r.stdout + r.stderr)[-300:].strip() if r.returncode else 'ok'})")
    cfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    check(cfg.get("first_scan") == "later", "a new config.json starts with first_scan \"later\": the weekday task waits for the page")
    check(cfg.get("isolated") is True and cfg.get("test_copy") is True and cfg.get("port") == 8790,
          "--isolated writes \"isolated\": true and \"test_copy\": true into config.json")
    check(not (home / "Applications").exists() and not (home / "Desktop" / "Open Loops.app").exists()
          and not (home / "Library" / "LaunchAgents").exists() and not (tmp / "launchctl.log").exists(),
          "--isolated implies --no-app and --no-task: no app, no launchd job, launchctl never called")
    check("Isolated test copy" in r.stdout, "the installer says what kind of copy it made")
    cursor = datetime.fromisoformat(json.loads((dest / "state.json").read_text(encoding="utf-8"))["cursor"])
    want = datetime.now().astimezone() - timedelta(days=30)
    check(abs((cursor - want).total_seconds()) < 600, f"the first-scan cursor is history_days (30) back, as the page says, not a week ({cursor})")
    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch")
    cfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    check(r.returncode == 0 and "isolated" not in cfg and cfg.get("test_copy") is True and cfg.get("owner_name") == "Test",
          "a re-run without --isolated takes the mark off (still a test copy by its flags), keeping the rest")
    # a copy that has the weekday job, then made isolated: its job goes
    dest3 = tmp / "OpenLoops-scheduled"
    r = install(home, "--dest", str(dest3), "--no-app", "--no-launch", "--name", "Sched")
    plist = home / "Library" / "LaunchAgents" / "com.openloops.refresh.plist"
    check(r.returncode == 0 and plist.exists() and str(dest3) in plist.read_text(errors="replace"),
          f"a copy installed with its weekday job ({(r.stdout + r.stderr)[-200:].strip() if r.returncode else 'ok'})")
    r = install(home, "--dest", str(dest3), "--isolated", "--no-launch")
    check(r.returncode == 0 and not plist.exists() and "Removed this copy's weekday refresh" in r.stdout,
          "install.sh --isolated over it removes that copy's weekday job, so the pill is true")
    r = install(home, "--dest", str(dest3), "--no-app", "--no-launch")
    install(home, "--dest", str(dest), "--isolated", "--no-launch")
    check(plist.exists(), "...and --isolated on another copy leaves a job that runs a different copy alone")

    # over an old ~/Documents install, at the default place: copied, and no port probed
    home2 = tmp / "home2"
    old = home2 / "Documents" / "OpenLoops"
    shutil.copytree(REPO / "openloops", old / "openloops")
    (old / "config.json").write_text(json.dumps({"owner_name": "Old", "refresh_time": "08:30"}), encoding="utf-8")
    (old / "state.json").write_text(json.dumps({"cursor": "2026-09-01T00:00", "last_refresh": None, "loops": [{"id": "keep-me"}]}),
                                    encoding="utf-8")
    (home2 / "Desktop").mkdir(parents=True)
    # a process naming the old copy (as `python3 <old>/openloops/app.py` would), started from elsewhere: no port is
    # probed under --isolated, so its command line is what stops the copy
    busy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(old / "openloops" / "app.py")], cwd=str(tmp))
    try:
        r = install(home2, "--isolated", "--no-launch")
        check(r.returncode == 1 and "Something is still working in the old Open Loops folder." in r.stderr
              and not (home2 / "Library" / "Application Support" / "OpenLoops" / "state.json").exists(),
              f"--isolated over an old install that a process still names: refused, nothing copied ({r.stderr.strip()[-160:]!r})")
    finally:
        busy.kill()
        busy.wait()
    r = install(home2, "--isolated", "--no-launch")
    new = home2 / "Library" / "Application Support" / "OpenLoops"
    check(r.returncode == 0 and json.loads((new / "state.json").read_text())["loops"] == [{"id": "keep-me"}],
          f"--isolated over an old install still copies its list ({(r.stdout + r.stderr)[-300:].strip() if r.returncode else 'ok'})")
    check(not (tmp / "curl.log").exists(), "...and sends nothing to any port: the old-install port probe is skipped")
    check(json.loads((new / "config.json").read_text()).get("isolated") is True, "...and the copy is marked isolated")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
say("PASS")
