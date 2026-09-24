"""First run (#38) and isolated test copies (#36): setup finishes with Slack only, nothing scans before a press, and
an isolated copy reads no to-do file and starts nothing by itself.

    python3 tests/test_first_run.py    # no Slack/Gmail/Claude: a fake `claude` on PATH, temp installs, spare ports.

The page's own setup code (stage machine, tick, stagePeople, stageAuto, paintButtons, the Start the first scan gate)
is cut out of the page as the app serves it and run in node against the real app, whose jobs run the fake `claude`.
So a pass means the served page, the app and the jobs agree, not that a copy of the logic does. Checks:
  1. Slack only (Gmail not connected): the checklist is green; before the press nothing runs and the page says what
     the first scan reads and how long ("Looking back 30 days across Slack. ..."), Update Slack hidden until the first
     scan is done (#56) and the steps bar lighting a part only while it runs or waits for you (#54); after
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
     step and nothing starts by itself; once set up, ⚙ Settings → Set-up brings the view back. After a reload (#27) a
     sign-in the app is still running gets its spinner, link and pop-up back; a finished one leaves the row as it is.
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
    with open(os.path.join(here, "efforts.txt"), "a") as f:   # #50: which effort each kind of run was given
        f.write(kind + ":" + (a[a.index("--effort") + 1] if "--effort" in a else "-") + "\n")
    if kind == "slack-id":
        print("SLACK_ID: U0TEST12345\nSLACK_NAME: Test Person")
    elif kind == "people":
        import time   # a test barrier: while hold_people exists the job stays running (up to 60 s), so its running state can be seen
        end = time.time() + 60
        while os.path.exists(os.path.join(here, "hold_people")) and time.time() < end:
            time.sleep(0.1)
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
        "async function connectReattach(){return true}",   # the Setup-buttons section is not cut in here (#27 is tested in 7k)
        "let S=null,J=null,TODAY=null,C=null,V=null,P=null,DOC=null;",
        grab("const esc="), grab("const fmt="), grab("const MSG="), grab("const fill="), grab("function msg("), grab("const errSaid="),
        cut("const api=async", "let lastBanner="),
        cut("async function loadState(", "async function loadCfg("), grab("async function loadCfg("), grab("const agentLabel="), grab("function msgFollow("),
        grab("const CONNECT_LABEL="), grab("const AI_NAME="), grab("function setupBtn("), grab("function msgBusy("), "const CONN={};",
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
  later:$('#start_msg').textContent,uslack:$('#uslack').style.display,uslack_label:$('#uslack').textContent,uslack_disabled:$('#uslack').disabled,pill:$('#isolated_pill').style.display,lists:$('#lists').style.display,
  bar:$('#steps').innerHTML,save:$('#people_save').disabled,plist:$('#people_list').innerHTML});
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
 const me=DOC.steps.find(x=>x.id==='self');out.self={title:me.title,detail:me.detail};
 const HOLD=PF.replace('prompts.txt','hold_people');fs.writeFileSync(HOLD,'');   // the people job waits until the snapshot is taken
 startScan();await until(()=>jobCalls().length>0);await tick();out.peopleRun=snap();fs.unlinkSync(HOLD);await waitJob('people');await loadCfg();await tick();out.people=snap();out.P=P&&P.people.length;
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
        check(b["ask"] == messages.say("first_scan_ask_slack"), "with Slack connected, the box mentions the one quick Slack-id check (#50)")
        check(b["uslack"] == "none" and out["scan"]["uslack"] == "none",
              "#56: Update Slack is hidden during set-up, before and during the first scan (it would run a real Slack pass)")
        check(out["end"]["uslack"] == "" and out["end"]["uslack_label"] == "Update Slack" and out["end"]["uslack_disabled"] is False,
              "...and on show, pressable, once the first scan is done")
        # #54 / #56: one numbering, the Set-up cards'; the Who's who part is lit only while it runs or waits for you
        check("3 · First scan</span>" in b["bar"] and 'class="now"' not in b["bar"] and "Who's who" not in b["bar"]
              and b["bar"].count(" ✓") == 2 and "1 · Your AI" in b["bar"] and "2 · Your sources" in b["bar"],
              f"before the press the bar is the cards' 1 · Your AI, 2 · Your sources, 3 · First scan, nothing lit ({b['bar']!r})")
        pr = out["peopleRun"]
        check('class="now">3 · First scan: Who\'s who</span>' in pr["bar"] and pr["save"] is True
              and messages.say("people_running", sources="Slack") in pr["plist"] and "about a minute" not in pr["plist"],
              f"Who's who running: lit, Save disabled, and 'a few minutes' ({pr['plist'][-90:]!r})")
        check('class="now">3 · First scan: Who\'s who</span>' in out["people"]["bar"] and out["people"]["save"] is False,
              "its list up: still lit (it waits for you), and Save can be pressed")
        check('class="now">3 · First scan: learning your tone</span>' in out["voice"]["bar"]
              and 'class="now">3 · First scan: reading your messages</span>' in out["scan"]["bar"], f"then your tone, then the scan, under the same 3 ({out['voice']['bar']!r}, {out['scan']['bar']!r})")
        check(out["seen"] == ["slack-id"], f"...and the fake claude was asked nothing but the Slack id before the press ({out['seen']})")
        check(out["self"] == {"title": "Knows who you are on Slack (Test Person)", "detail": "U0TEST12345"},
              f"the Slack row names you by your display name; the id is only in its detail (#50) ({out['self']})")
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
        eff = (tmp / "bin" / "efforts.txt").read_text().split()
        check("slack-id:low" in eff and "people:low" in eff and "voice:high" in eff and "refresh-slack-only:high" in eff,
              f"#50: the Slack lookup and who's who run at low effort, the other jobs at the template's high ({eff})")
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
        check('class="now"' not in out["before"]["bar"] and 'class="now"' not in out["after"]["bar"],
              f"#54: nothing in the steps bar is lit while the first scan waits for its press ({out['before']['bar']!r})")
        check(out["before"]["shown"] == ["start"] and out["before"]["jobs"] == [] and out["after"]["jobs"] == []
              and not any(j["running"] or "rc" in j for j in st["jobs"].values()) and "people" not in prompts(tmp),
              "five ticks and Not now: no job started, by the page or on the server")
        check(out["after"]["later"] == "Not started; Open Loops remembers that, so nothing reads your messages until you choose. Press Start the first scan when you're ready." and out["SS"] == {"ol.firstscan": "later"},
              f"Not now says so and is remembered for the tab ({out['after']['later']!r}, {out['SS']})")
        check(json.loads((tmp / "config.json").read_text(encoding="utf-8")).get("first_scan") == "later",
              "...and saved as first_scan \"later\" in config.json, for the weekday task")
        out = page_js(port, {"ol.firstscan": "later"}, "await boot();await tick();await tick();out.reload=snap();", tmp)
        check(out["reload"]["shown"] == ["start"] and out["reload"]["jobs"] == [] and out["reload"]["later"] == messages.say("first_scan_later"),
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
        api(port, "/api/refresh", {"slack_only": True})   # #50: Update Slack, signed out
        t1 = time.time()
        while time.time() - t1 < 5 and api(port, "/api/state")["jobs"]["refresh"].get("seq") != 3:
            time.sleep(0.1)
        j = api(port, "/api/state")["jobs"]["refresh"]
        check(j.get("said") == messages.say("job_signed_out", job="The Slack update", ai="Claude"),
              f"app: a failed Update Slack is named as the button pressed ({j.get('said')!r})")
        (tmp / "bin" / "signed_out").unlink()
        if NODE:
            out = page_js(port, {}, """
     await boot();await loop();out.idle={stage:stage(),seen:SEEN.refresh};
     fs.writeFileSync(PF.replace('prompts.txt','signed_out'),'');
     const t0=Date.now();await refresh();await until(()=>TOASTS.length>0,3000);out.ms=Date.now()-t0;
     out.toasts=TOASTS.slice();out.said=J.refresh.said;out.con=CON.filter(l=>/refresh/.test(l));
     await until(()=>DOC&&DOC.steps.some(x=>x.id==='login'&&!x.ok),10000);await until(()=>S&&S.setup_done,5000);await tick();
     out.after={stage:stage(),rows:Object.fromEntries(DOC.steps.map(x=>[x.id,{ok:x.ok,fix:x.fix,connect:x.connect||''}]))};
     const h=$('#st_connect_h');h.dataset={};h.textContent="1 · Let's get you connected";
     out.bar=$('#steps').innerHTML;paintSetupDone();out.head=h.textContent;
     S.setup_done=false;const lr_=[S.last_refresh,S.last_slack_refresh];S.last_refresh=S.last_slack_refresh=null;   // never finished: no first scan either
     paintSetupDone();out.headFresh=h.textContent;await tick();out.barFresh=$('#steps').innerHTML;S.setup_done=true;S.last_refresh=lr_[0];S.last_slack_refresh=lr_[1];
     out.watch=Object.keys(WATCH);""", tmp)
            check(out["idle"]["stage"] == "ready" and out["idle"]["seen"] == 3, "page: set up and idle, earlier job ends noted but not announced")
            check(out["ms"] < 3000 and out["toasts"] and out["toasts"][0] == out["said"] and out["said"],
                  f"page: the sentence is shown within 3 s of pressing Refresh ({out['ms']} ms: {out['toasts'][:1]})")
            check(any(l.startswith("refresh finished rc=1") for l in out["con"]), f"page: a Console line says the refresh ended ({out['con']})")
            rows = out["after"]["rows"]
            signin = messages.say("needs_signin", ai="Claude")
            check(out["after"]["stage"] == "connect" and rows["login"]["connect"] == "login" and not rows["login"]["ok"],
                  "page: the re-check brings the checklist back with its Sign in button")
            check(not rows["self"]["ok"] and rows["self"]["fix"] == signin, f"'Knows who you are on Slack' is not ticked while signed out ({rows['self']})")
            check(not rows["channel"]["ok"] and rows["channel"]["fix"] == signin, f"'At least one source' says sign in first ({rows['channel']['fix']!r})")
            check(out["bar"] == "" and out["head"] == messages.say("setup_done_signin", ai="Claude", button="Sign in"),
                  f"#50: set up once, a sign-out shows the checklist under one line, not the numbered setup again ({out['head']!r}, {out['bar'][:60]!r})")
            check(out["head"] == "Setup is done; Claude just needs signing in again. Press Sign in below."
                  and out["headFresh"] == "1 · Let's get you connected" and '<span class="now">1 · Your AI</span>' in out["barFresh"],
                  "...while a setup that never finished still shows '1 · Let's get you connected' and the numbered steps")
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
    if os.path.exists(os.path.join(here, "slow")):   # a check that takes a while: the page must wait for it
        import time; time.sleep(3)
    print("codex-cli 0.156.1"); sys.exit(0)
if a[:2] == ["login", "status"]:
    print("Not logged in"); sys.exit(1)
print("fake codex: unexpected " + " ".join(a)); sys.exit(9)
'''.replace("PYTHON", sys.executable)
# A fake Grok: installed, not signed in (no ~/.grok/auth.json in the test HOME). Calls go to calls-grok.txt.
FAKE7_GROK = r'''#!PYTHON
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls-grok.txt"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1:] == ["--version"]:
    print("grok 1.0.30"); sys.exit(0)
print("fake grok: unexpected " + " ".join(sys.argv[1:])); sys.exit(9)
'''.replace("PYTHON", sys.executable)
# Only the system folders on PATH: no real claude, codex or grok of the developer's is ever run (the fake is added in front)
BARE = os.pathsep.join(d for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin") if os.path.isdir(d))


def setup_install(prefix, fake=True, config=None):
    tmp = fresh_install(prefix, dict({"agent": "claude", "owner_name": "Test", "slack_source": "plugin"}, **(config or {})))
    (tmp / "bin").mkdir()
    if fake:
        (tmp / "bin" / "claude").write_text(FAKE7, encoding="utf-8")
        (tmp / "bin" / "codex").write_text(FAKE7_CODEX, encoding="utf-8")
        (tmp / "bin" / "grok").write_text(FAKE7_GROK, encoding="utf-8")
    # the browser: only writes down what it was asked to open. It never fails: Python's webbrowser would then go on to
    # the real default browser, so a failed browser step is faked on the page side instead (FAKE in setup_js).
    (tmp / "bin" / "browser").write_text(f"#!/bin/sh\necho \"$1\" >> '{tmp / 'opened.txt'}'\n", encoding="utf-8")
    for f in (tmp / "bin").iterdir():
        os.chmod(f, 0o755)
    return tmp


def setup_env(tmp):
    """PATH = the fakes, then system folders only; every AI's own folder in the temp install; and agent.py told not to look
    in the fixed places a real Codex lives (/opt/homebrew/bin, /Applications/Codex.app), so only the fakes can run."""
    homes = {k: str(tmp / "ai-homes" / k.lower()) for k in ("CODEX_HOME", "GROK_HOME", "CLAUDE_CONFIG_DIR")}
    for h in homes.values():
        os.makedirs(h, exist_ok=True)
    return isolated_env(tmp, PATH=str(tmp / "bin") + os.pathsep + BARE, BROWSER=str(tmp / "bin" / "browser"),
                        OPENLOOPS_NO_FALLBACK_PATHS="1", **homes)


def real_clis_ran(tmp):
    """Which AI CLIs, by the path they were found at, the app would run with this env: each must be a fake in bin/.
    -> a list of the ones that are not (empty when isolated)."""
    code = ("import json, shutil, sys; sys.path.insert(0, '.'); from openloops import agent; import openloops.agent as A\n"
            "out = {n: shutil.which(n) for n in ('claude', 'codex', 'grok')}\n"
            "A.name = lambda: 'codex'; out['codex-cli'] = shutil.which(A.cli()) or A.cli()\n"
            "print(json.dumps(out))")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=setup_env(tmp), capture_output=True, text=True, timeout=60)
    found = json.loads(r.stdout.strip().splitlines()[-1])
    return [f"{k}={v}" for k, v in found.items() if not (v and str(v).startswith(str(tmp / "bin")))]


def quit_app(port, srv, secs=10):
    """Quit an app through /api/quit, as its Settings button does: that stops any setup step it is running (its fake
    `claude mcp login` included), which terminating the server may not. Best effort; stop() still follows."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", data=b"{}", method="POST",
                                                      headers={"Content-Type": "application/json"}), timeout=5).read()
        srv.wait(secs)
    except Exception:
        pass


def setup_js(port, scenario, tmp, session=None):
    """The served page's Set-up view with everything it calls (stage machine, tick, checklist rows and buttons,
    connectStep, doctor), a stub DOM whose elements keep what they are given, talking to the real app on `port`."""
    html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode("utf-8")
    lines = html.splitlines()
    grab = lambda start: next(l for l in lines if l.startswith(start))
    cut = lambda a, b: html[html.index(a):html.index(b, html.index(a))]
    parts = [
        f"const BASE='http://127.0.0.1:{port}';const SS={json.dumps(session or {})};const BIN={json.dumps(str(tmp / 'bin'))};",
        "const fs=require('fs');const seen=()=>{const f=BIN+'/prompts.txt';return fs.existsSync(f)?fs.readFileSync(f,'utf8').split(/\\s+/).filter(Boolean):[]};",
        "const sessionStorage={getItem:k=>k in SS?SS[k]:null,setItem:(k,v)=>{SS[k]=String(v)},removeItem:k=>{delete SS[k]}};",
        # elements keep what they are given; a container painted row by row (paintRows, #62) holds its row boxes as child
        # nodes, and reads back as their HTML, as a browser's innerHTML would
        "const els={};const mk=id=>({id,style:{},dataset:{},textContent:'',_html:'',disabled:false,title:'',open:false,className:'',kids:[],"
        "get innerHTML(){return this.kids.length?this.kids.map(k=>'<div>'+k.innerHTML+'</div>').join(''):this._html},set innerHTML(v){this._html=String(v);this.kids=[]},"
        "get children(){return this.kids},replaceChild(a,b){this.kids[this.kids.indexOf(b)]=a;a.parent=this;return b},removeChild(c){this.kids.splice(this.kids.indexOf(c),1);return c},"
        "contains(x){return x===this||this.kids.some(k=>k.contains(x))},"
        "classList:{toggle(){}},appendChild(c){this.kids.push(c);c.parent=this;return c},showModal(){this.open=true},close(){this.open=false},addEventListener(){}});",
        "const $=s=>els[s]||(els[s]=mk(s));const document={getElementById:id=>$('#'+id),createElement:()=>mk('new')};",
        "const CON=[];function clog(m){CON.push(String(m))}const TOASTS=[];function toast(m){TOASTS.push(String(m))}",
        "function renderLists(){}function paintVoice(){}function banner(){}function appDown(e){CON.push('appDown '+e)}function paintDaylog(){}function paintRm(){}",
        "function paintForm(){}async function loadDaylog(){}async function loadRm(){}function agentUI(){}function agentFormUI(){}const PAGE='t';let stopped=false;",
        "let S=null,J=null,TODAY=null,C=null,V=null,P=null,DOC=null;",
        grab("const esc="), grab("const fmt="), grab("const MSG="), grab("const fill="), grab("function msg("), grab("const errSaid="),
        cut("const api=async", "let lastBanner="),
        cut("let formPainted=false;", "\n\n// ---------- data"),
        cut("async function loadState(", "async function loadCfg("), grab("async function loadCfg("), grab("const agentLabel="),
        grab("function msgFollow("), grab("function msgBusy("),
        grab("const running="), grab("const havePeople="), grab("const haveVoice="),
        cut("function stage(){", "\nasync function tick(){"), cut("async function tick(){", "\nfunction paintConnect("),
        cut("function paintConnect(", "\n// ---------- Setup buttons"),
        cut("// ---------- Setup buttons", "\n// The Mac's weekday"),
        cut("let schedSaid=''", "\n// What goes with the checklist"), "paintSchedule=function(){};",
        cut("// ---------- Set-up view (#28)", "\nlet peopleRendered=''"),
        cut("let peopleRendered=''", "async function findPeople("),
        cut("async function stageAuto(", "// Update Slack needs Slack"),
        cut("// Update Slack needs Slack", "\n// ---------- lists"),
        cut("const counts=()=>", "\n// ---------- day log"),
        grab("async function openClaude("), grab("async function learnVoice("),
        cut("let docAt=0", "document.addEventListener('visibilitychange'"),
        """const CALLS=[];const realFetch=global.fetch;
const FAKE={};   // url -> [answer to a POST, answer to a GET]: a setup step the app itself never runs (no browser opens)
const FAIL_ONCE=new Set(),FAIL_GET=new Set(),HANG=new Set(),HOLD={};
global.fetch=(u,o)=>{const post=!!(o&&o.method==='POST');if(post)CALLS.push(u+' '+(o.body||''));
 if(FAIL_ONCE.has(u)||(!post&&FAIL_GET.has(u))){FAIL_ONCE.delete(u);FAIL_GET.delete(u);return Promise.resolve({ok:false,status:500,text:async()=>'{"error":"boom"}'})}
 const hd=!post&&HOLD[u];if(hd&&!hd.used){hd.used=true;return new Promise(res=>{hd.release=()=>res({ok:true,status:200,json:async()=>hd.body,text:async()=>JSON.stringify(hd.body)})})}   // answers when released
 if(HANG.has(u)){HANG.delete(u);return new Promise((res,rej)=>{const sg=o&&o.signal;if(sg)sg.addEventListener('abort',()=>rej(Object.assign(new Error('aborted'),{name:'AbortError'})))})}   // never answers
 if(FAKE[u])return Promise.resolve({ok:true,status:200,json:async()=>FAKE[u][post?0:1],text:async()=>JSON.stringify(FAKE[u][post?0:1])});
 return realFetch(BASE+u,o)};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(f,ms=60000){const end=Date.now()+ms;while(!f()){if(Date.now()>end)throw new Error('timed out');await sleep(100)}}
const jobCalls=()=>CALLS.filter(c=>/^\\/api\\/(people|voice|refresh) /.test(c));
async function boot(){await loadCfg();await loadState();DOC=await api('/api/doctor',{force:true,detect:true});docAt=Date.now()}
const card=k=>({state:$('#su_'+k+'_state').textContent,cls:$('#su_'+k+'_state').className,rows:$('#su_'+k+'_rows').innerHTML,act:($('#su_'+k+'_act')||{}).innerHTML});
const view=()=>({stage:stage(),setup:$('#setup').style.display,lists:$('#lists').style.display,back:$('#setup_back').style.display,
  ai:card('ai'),src:card('src'),sched:card('sched'),pick:$('#su_ai_pick').innerHTML,scan:$('#su_scan').textContent,time:$('#su_time').innerHTML,
  start:$('#st_start').style.display,jobs:jobCalls(),every:$('#st_connect').style.display,steps:$('#setup_steps').innerHTML,
  bar:$('#steps').style.display,srchelp:$('#su_src_help').textContent});
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
    check('role="radiogroup" aria-labelledby="su_ai_h" onkeydown="suPickKey(event)"' in page and 'tabindex="${k===a?0:-1}" data-fk="ai-${k}"' in page
          and all(f'id="su_{k}" tabindex="-1"' in page for k in ("ai", "src", "sched")),
          "the picker is a keyboard radio group with a roving tab stop; the cards can take focus back after a repaint")
    check("both optional" not in page and page.count("esc(msg('sources_needed'))+'") == 2 and "msg('sources_needed')+(a==='codex'" in page
          and "#st_connect_h,#st_connect_intro{display:none!important}" in page and "<h3>2 · Who's who?</h3>" not in page,
          "#56: the fold and the card state sources the same way; the fold's old heading is hidden; the stage headings carry no old numbers")
    check(messages.say("people_running", sources="Slack") == "Looking at who you talk to on Slack. This usually takes a few minutes."
          and "msg('people_running',{sources:scanSources()})" in page, "#54: Who's who says 'a few minutes', from messages.py")

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
        check(v["pick"].count('role="radio"') == 3 and 'aria-checked="true" tabindex="0" data-fk="ai-claude" onclick="chooseAI(\'claude\')"' in v["pick"]
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
 await tick();out.a=view();ISO=true;paintSetup(stage());out.iso=$('#su_time').innerHTML;ISO=false;paintSetup(stage());
 CONN.slack={busy:true,msg:'Waiting for you in the browser: click Allow there.'};paintSetup(stage());out.b=view();delete CONN.slack;
 DOC={agent:'claude',all_ok:false,steps:[row('claude',true),row('login',false,{connect:'login'}),row('slack',false),row('gmail',false),row('miro',false),row('channel',false),row('self',false)]};
 await tick();out.c=view();
 C.agent='grok';const gfix='Gmail needs a one-off Google sign-in of its own: follow Gmail with Grok in INSTALL.md.';
 DOC={agent:'grok',all_ok:false,steps:[row('claude',true),row('login',false,{fix:"Click 'Open Grok' below and follow the sign-in link it shows."}),
  row('gmail',false,{fix:gfix}),row('channel',false),row('self',false)]};await tick();out.d=view();C.agent='claude';
 // #52: setup was done once and the sign-in went; its checklist heading says so, and Set-up puts that line on top
 S.setup_done=true;const hh=$('#st_connect_h');hh.dataset={first:"1 · Let's get you connected"};hh.textContent='Setup is done; Claude just needs signing in again. Press Sign in below.';
 DOC={agent:'claude',all_ok:false,steps:[row('claude',true),row('login',false,{connect:'login',title:'Signed in to <i>Claude</i>'}),row('slack',false),row('gmail',false),row('channel',false)]};
 await tick();out.e={repair:$('#su_repair').textContent,shown:$('#su_repair').style.display,intro:$('#su_intro').style.display,rows:$('#su_ai_rows').innerHTML};
 S.setup_done=false;paintSetup(stage());out.e.after=$('#su_repair').style.display;
 // #52 post-rebase review: Set-up reopened from Settings on an all-green install says nothing is wrong (no repair line),
 // and a line shown while a sign-in was under way goes once the connection is back
 const green=()=>({agent:'claude',all_ok:true,steps:[row('claude',true),row('login',true),row('slack',true),row('gmail',true),row('channel',true),row('self',true)]});
 const rep=()=>({repair:$('#su_repair').textContent,shown:$('#su_repair').style.display,head:$('#st_connect_h').textContent});
 S.setup_done=true;SETUP_OPEN=true;docAt=Date.now();DOC=green();paintSetup('ready');out.green=rep();
 DOC={agent:'claude',all_ok:false,steps:[row('claude',true),row('login',false,{connect:'login'}),row('slack',true),row('gmail',true),row('channel',true)]};
 CONN.login={busy:true,msg:'Waiting for you in the browser: click Allow there.'};paintSetup('connect');out.busy=rep();
 delete CONN.login;DOC=green();paintSetup('ready');out.recovered=rep();SETUP_OPEN=false;
 // keyboard: arrows move along the three choices (wrapping), Home / End, and the one that can be tabbed to follows
 const bs=[0,1,2].map(i=>({i,tabIndex:-1,focus(){document.activeElement=this}}));$('#su_ai_pick').querySelectorAll=()=>bs;bs[0].focus();
 const key=k=>{suPickKey({key:k,preventDefault(){}});return document.activeElement.i+':'+bs.map(b=>b.tabIndex===0?1:0).join('')};
 out.keys=[key('ArrowRight'),key('ArrowDown'),key('ArrowRight'),key('ArrowLeft'),key('Home'),key('End'),key('Tab')];
 // the pop-up gives focus back to that step's button, or to its card once the button has gone
 const asked=[];document.querySelector=sel=>{asked.push(sel);return sel.includes('connectStep')&&!out.gone?{focus(){out.focused=sel}}:sel.startsWith('#su_')?{focus(){out.focused=sel}}:null};
 ALLOW={step:'slack'};allowClose();out.focus1=out.focused;out.gone=true;ALLOW={step:'login'};allowClose();out.focus2=out.focused;""", tmp)
        a, b, c, d, e = out["a"], out["b"], out["c"], out["d"], out["e"]
        check(a["setup"] == "" and a["ai"]["state"] == "Done" and "su-state done" == a["ai"]["cls"] and ">Check again</button>" in a["ai"]["act"],
              "AI installed and signed in: 'Your AI' is done, and its one button is Check again")
        check(a["src"]["state"] == "Done" and 'class="" onclick="connectStep(\'slack\')">Connect Slack</button>' in a["src"]["rows"]
              and 'class="" onclick="connectStep(\'miro\')">Connect Miro</button>' in a["src"]["rows"] and a["src"]["act"] == "",
              "Gmail ticked is enough for 'Your sources' to be done; Slack and Miro keep a Connect each, as extras (not primary)")
        check("self title" not in a["src"]["rows"], "an optional 'Knows who you are on Slack' does not clutter the card")
        check("onclick=\"connectStep('slack')\">Connect Slack</button>" in a["steps"] and 'class="primary"' not in a["steps"],
              "#56: Every check's Connect buttons are plain; the cards above hold the one primary action")
        check(a["bar"] == "none" and a["srchelp"] == messages.say("sources_needed") + " Each Connect opens your browser, where you click Allow.",
              f"#56: while Set-up is up the steps bar is hidden; the sources card says one thing about sources ({a['srchelp']!r})")
        check(out["iso"] == messages.say("sched_test_copy").replace("'", "&#39;") and "Press Refresh when you want a pass" not in page,
              f"#56: a test copy's schedule card says what happens there, not a Refresh button that isn't there yet ({out['iso']!r})")
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
              and 'aria-checked="true" tabindex="0" data-fk="ai-grok" onclick="chooseAI(\'grok\')"' in d["pick"],
              "Grok: sign-in is Open Grok, and the Gmail row keeps its own instruction, with no button")
        check(e["repair"] == "Setup is done; Claude just needs signing in again. Press Sign in below." and e["shown"] == "" and e["intro"] == "none"
              and e["after"] == "none", "setup done once and the checklist back: its one-line repair sentence sits above the cards (#52)")
        check(out["green"] == {"repair": "", "shown": "none", "head": ""},
              f"all green, Set-up reopened from Settings: no repair line (not 'one connection just needs attention') ({out['green']})")
        check(out["busy"]["repair"] == "Setup is done; Claude just needs signing in again. Please wait while that finishes." and out["busy"]["shown"] == ""
              and out["recovered"] == {"repair": "", "shown": "none", "head": ""},
              f"a sign-in under way says please wait; once it is back the line goes ({out['busy']}, {out['recovered']})")
        check("Signed in to &lt;i&gt;Claude&lt;/i&gt;" in e["rows"], "row titles are escaped (#52 puts a Slack display name in one)")
        check(out["keys"] == ["1:010", "2:001", "0:100", "2:001", "0:100", "2:001", "2:001"],
              f"the AI picker: arrows and Home / End move the focus and the tab stop; other keys are left alone ({out['keys']})")
        check(out["focus1"] == "#setup button[onclick=\"connectStep('slack')\"]" and out["focus2"] == "#su_ai",
              f"closing the pop-up puts focus on that step's button, or on its card when the button is gone ({out['focus1']}, {out['focus2']})")
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
 n=CALLS.length;const p=connectStep('slack');await until(()=>$('#allow_dlg').open,5000);   // once the app has named the run
 out.pressed={open:$('#allow_dlg').open,title:$('#allow_title').textContent,sub:$('#allow_sub').textContent,src:$('#su_src_state').textContent};
 await until(()=>$('#allow_link').innerHTML.includes('example.invalid'),15000);
 out.link={html:$('#allow_link').innerHTML,open:$('#allow_dlg').open,check:$('#allow_check').style.display};
 await sleep(3500);out.waiting={open:$('#allow_dlg').open,slack:DOC.steps.find(x=>x.id==='slack').ok};
 fs.writeFileSync(BIN+'/allow','');const t0=Date.now();
 await until(()=>!$('#allow_dlg').open,30000);out.closed={ms:Date.now()-t0,slack:DOC.steps.find(x=>x.id==='slack').ok,toasts:TOASTS.slice(),calls:CALLS.slice(n)};
 await p;for(let i=0;i<3;i++){await tick();await sleep(50)}
 out.end=view();out.seen=seen();setupEmbed();out.embed={start:els['#st_start'].parent.id,connect:els['#st_connect'].parent.id};""", tmp)
        check(out["before"]["agent"] == "claude" and out["before"]["doc"] == "claude", "starts on Claude")
        check(real_clis_ran(tmp) == [], f"isolated: claude, codex and grok all resolve to the fakes, Codex's fixed paths included ({real_clis_ran(tmp)})")
        cc = (tmp / "bin" / "calls-codex.txt").read_text().splitlines() if (tmp / "bin" / "calls-codex.txt").exists() else []
        check("--version" in cc and "login status" in cc, f"...and choosing Codex ran the fake codex, not a real one ({cc})")
        env0 = dict(setup_env(tmp), PATH=BARE)   # no fake at all: the override must still keep the fixed paths out
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import openloops.agent as A; A.name = lambda: 'codex'; print(A.cli())"],
                           cwd=tmp, env=env0, capture_output=True, text=True, timeout=60)
        check(r.stdout.strip() == "codex", f"OPENLOOPS_NO_FALLBACK_PATHS=1: with no codex on PATH, agent.cli() is plain 'codex', never a fixed path ({r.stdout.strip()!r})")
        cz = out["codex"]
        check(cz["calls"][:2] == ['/api/config {"agent":"codex"}', '/api/doctor {"force":true,"detect":true}'],
              f"choosing ChatGPT (Codex) POSTs agent to /api/config, then re-checks ({cz['calls']})")
        check(out["cfgCodex"] == "codex" and cz["agent"] == "codex" and cz["doc"] == "codex"
              and "Codex is installed" in cz["v"]["ai"]["rows"] and "Signed in to ChatGPT" in cz["v"]["ai"]["rows"]
              and 'aria-checked="true" tabindex="0" data-fk="ai-codex" onclick="chooseAI(\'codex\')"' in cz["v"]["pick"],
              "...config.json says codex, and the card shows Codex's own rows with Codex picked")
        check(cz["v"]["srchelp"].startswith(messages.say("sources_needed") + " Gmail and Slack are connected in your ChatGPT account"),
              "...its sources card opens with the same statement of which sources are needed")
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
        check(e["every"] == "" and "Slack connected" in e["steps"] and "(optional)" not in e["steps"].split("Miro")[0]
              and "At least one source connected" in e["steps"],
              "past the connect stage, Every check still holds the whole checklist, from the latest check")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7g. Codex: a Connect page that would not open, and a Check again that finds the row still red, are said in the pop-up
    tmp = setup_install("openloops-setup-codexfail-", config={"agent": "codex"})
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 FAKE['/api/connect/gmail']=[{started:true},{running:false,rc:1,url:'https://chatgpt.com/apps',opened:true,last:'',step:'gmail'}];   // the page did not open
 await boot();await tick();const p=connectStep('gmail');await until(()=>$('#allow_dlg').open,5000);out.opened={open:$('#allow_dlg').open,title:$('#allow_title').textContent};
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

    # 7h. changing the AI: nothing starts until the new AI's check is in; not while a job runs; a failed check is not green
    tmp = setup_install("openloops-setup-switch-", config={"slack_self_id": "U0TEST12345"})
    (tmp / "bin" / "slack_ok").write_text("")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();await tick();out.before=view();
 fs.writeFileSync(BIN+'/slow','');const p=chooseAI('codex');await sleep(400);
 out.pending={pend:aiPending(),v:view(),uslack:$('#uslack').style.display};
 let n=CALLS.length;await startScan();await refresh();await learnVoice();out.pendCalls=CALLS.slice(n);out.pendToasts=TOASTS.slice();
 await p;fs.unlinkSync(BIN+'/slow');out.after={pend:aiPending(),doc:DOC.agent,stage:stage()};
 // #56: doctor(true) has resolved (suBusy clear) but the answer is still another AI's: the picker stays disabled
 const dis=()=>($('#su_ai_pick').innerHTML.match(/" disabled onclick=/g)||[]).length;
 const d0=DOC;DOC=Object.assign({},d0,{agent:'claude'});paintSetup(stage());
 out.gap={pend:aiPending(),busy:suBusy,disabled:dis()};n=CALLS.length;await chooseAI('grok');out.gap.calls=CALLS.slice(n);out.gap.agent=C.agent;
 DOC=d0;paintSetup(stage());out.gap.after=dis();
 // review of #59: another tab switched to Codex; this one still caches Claude. A good Codex answer re-reads settings once
 C.agent='claude';paintSetup(stage());out.stale={pend:aiPending(),disabled:dis()};await doctor(true);paintSetup(stage());
 out.stale.after={pend:aiPending(),agent:C.agent,disabled:dis(),reread:CON.filter(l=>l.includes('settings re-read')).length};
 // ...and a re-read that fails once is tried again on the next good check, instead of leaving the picker disabled
 C.agent='claude';docReread='';FAIL_GET.add('/api/config');await doctor(true);paintSetup(stage());out.rereadFail={pend:aiPending(),disabled:dis()};
 await doctor(true);paintSetup(stage());out.retried={pend:aiPending(),agent:C.agent,disabled:dis()};
 J.refresh=Object.assign({},J.refresh,{running:true});n=CALLS.length;TOASTS.length=0;await chooseAI('claude');
 out.job={calls:CALLS.slice(n),toasts:TOASTS.slice(),agent:C.agent};J.refresh.running=false;
 FAIL_ONCE.add('/api/doctor');n=CALLS.length;await chooseAI('claude');
 out.failed={calls:CALLS.slice(n),stage:stage(),all_ok:DOC.all_ok,error:!!DOC.error,agent:C.agent,setup:$('#setup').style.display,shown:$('#st_checkfail').style.display};""", tmp)
        b, pd = out["before"], out["pending"]
        check(b["stage"] == "people" and b["start"] == "", "before: Claude ready, the first-scan box waits for its press")
        check(pd["pend"] and pd["v"]["stage"] == "checking" and pd["v"]["setup"] == "" and "Checking Codex on this computer" in pd["v"]["ai"]["rows"]
              and pd["v"]["ai"]["state"] == "In progress" and pd["v"]["start"] == "none" and pd["uslack"] == "none"
              and "Checking Codex first" in pd["v"]["scan"],
              f"while Codex's check is pending: Set-up says Checking Codex, and Start and Update Slack are gone ({pd['v']['stage']})")
        check(out["pendCalls"] == [] and any("still checking Codex" in t for t in out["pendToasts"]),
              f"...Start, Refresh and Learn my tone start nothing then, and say why ({out['pendCalls']}, {out['pendToasts'][-1:]})")
        check(out["after"] == {"pend": False, "doc": "codex", "stage": "connect"}, f"once the check is in, the page follows Codex's own rows ({out['after']})")
        g = out["gap"]
        check(g["pend"] is True and g["busy"] == "" and g["disabled"] == 3 and g["calls"] == [] and g["agent"] == "codex" and g["after"] == 0,
              f"#56: check resolved but not yet for the new AI: all three choices disabled and a press starts nothing ({g})")
        st_ = out["stale"]
        check(st_["pend"] is True and st_["disabled"] == 3 and st_["after"] == {"pend": False, "agent": "codex", "disabled": 0, "reread": 1},
              f"another tab switched the AI: the next good check re-reads settings once and the picker is usable again ({st_})")
        check(out["rereadFail"] == {"pend": True, "disabled": 3} and out["retried"] == {"pend": False, "agent": "codex", "disabled": 0},
              f"...a failed settings re-read is retried on the next good check, and the picker comes back ({out['rereadFail']}, {out['retried']})")
        check(out["job"]["calls"] == [] and out["job"]["agent"] == "codex"
              and out["job"]["toasts"] == ["Open Loops is still running a job with Codex. Change your AI once it has finished."],
              f"changing the AI while a job runs is refused, in a sentence ({out['job']['toasts']})")
        f = out["failed"]
        check(f["calls"][:2] == ['/api/config {"agent":"claude"}', '/api/doctor {"force":true,"detect":true}'] and f["error"] and f["all_ok"] is False
              and f["stage"] == "checkfail" and f["shown"] == "" and f["setup"] == "none",
              f"a failed check after the change keeps nothing of Codex's answer: not green, the 'couldn't run the check' box instead ({f['stage']})")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7i. the AI-change gate always lets go: a check that never answers ends at the page's deadline; a later good answer
    # for the new AI clears a stray gate; a saved change whose settings re-read fails once is read again
    tmp = setup_install("openloops-setup-hang-", config={"slack_self_id": "U0TEST12345"})
    (tmp / "bin" / "slack_ok").write_text("")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();await tick();DOC_DEADLINE_MS=800;HANG.add('/api/doctor');const t0=Date.now();
 const p=chooseAI('codex');await sleep(300);out.during={pend:aiPending(),stage:stage()};await p;
 out.hung={ms:Date.now()-t0,pend:aiPending(),stage:stage(),error:DOC.error,box:$('#st_checkfail').style.display,said:$('#checkfail_said').textContent,agent:C.agent};
 DOC_DEADLINE_MS=250000;clearTimeout(docT);aiSwitching='codex';out.stray=aiPending();await doctor(true);out.later={pend:aiPending(),doc:DOC.agent,stage:stage()};
 FAIL_GET.add('/api/config');let n=CALLS.length;await chooseAI('claude');
 out.reread={calls:CALLS.slice(n),agent:C.agent,doc:DOC.agent,pend:aiPending(),stage:stage(),toasts:TOASTS.filter(t=>/not changed/.test(t))};
 const AC=global.AbortController;global.AbortController=undefined;DOC_DEADLINE_MS=900;HANG.add('/api/doctor');const t1=Date.now();
 await doctor(true);out.noac={ms:Date.now()-t1,said:CON.filter(l=>l.includes('no answer within 1 s')).length>0};global.AbortController=AC;DOC_DEADLINE_MS=500000;clearTimeout(docT);""", tmp)
        check(out["during"] == {"pend": True, "stage": "checking"}, "a check that hangs after an AI change: the page waits in 'checking' meanwhile")
        h = out["hung"]
        check(800 <= h["ms"] < 5000 and h["pend"] is False and h["stage"] == "checkfail" and h["box"] == ""
              and h["said"] == messages.say("check_failed") and "no answer within" in h["error"] and h["agent"] == "codex",
              f"...at the page's deadline the gate lets go, and the page says the check didn't run, with Retry ({h['ms']} ms, {h['stage']})")
        check(out["stray"] is True and out["later"] == {"pend": False, "doc": "codex", "stage": "connect"},
              f"a stray gate is cleared by the next good answer for the new AI ({out['later']})")
        r = out["reread"]
        check(r["calls"][:2] == ['/api/config {"agent":"claude"}', '/api/doctor {"force":true,"detect":true}'] and r["agent"] == "claude"
              and r["doc"] == "claude" and r["pend"] is False and r["toasts"] == [],
              f"a saved change whose settings re-read fails once is read again, then checked ({r['stage']})")
        check("DOC_DEADLINE_MS=500000" in page and "two attempts of 240 s" in page[page.index("let DOC_DEADLINE_MS") - 400:page.index("let DOC_DEADLINE_MS")],
              "the deadline is 500 s, past the app's two 240 s attempts")
        check(out["noac"]["said"] is True and 800 <= out["noac"]["ms"] < 5000,
              f"...and without AbortController a hung check is still ended by the timer ({out['noac']})")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7j. /api/state answers out of order: an older one is dropped, and one sent before a job started cannot clear it
    tmp = setup_install("openloops-setup-stale-", config={"slack_self_id": "U0TEST12345"})
    (tmp / "bin" / "slack_ok").write_text("")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();await tick();stopped=true;   // no polls of its own: the test decides the order
 const held=jobs=>({state:S,jobs,today:TODAY,isolated:ISO,instance:INST});
 HOLD['/api/state']={body:held({refresh:{running:false,seq:0,marker:'old'}})};
 const s0=ST_SEQ,old=loadState();await sleep(50);await loadState();HOLD['/api/state'].release();await old;
 out.order={marker:(J.refresh||{}).marker||'',applied:ST_DONE-s0,asked:ST_SEQ-s0};
 HOLD['/api/state']={body:held({refresh:{running:false,seq:(J.refresh&&J.refresh.seq)||0}})};
 const early=loadState();await sleep(50);jobStarted('refresh');HOLD['/api/state'].release();await early;
 out.early={running:J.refresh.running,pending:!!PENDING_START.refresh};await loadState();out.current=J.refresh.running;
 let n=CALLS.length;TOASTS.length=0;await chooseAI('codex');out.refused={calls:CALLS.slice(n),toasts:TOASTS.slice(),agent:C.agent};
 await realFetch(BASE+'/api/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
 for(let i=0;i<150&&J.refresh.running;i++){await sleep(100);await loadState()}
 out.ended={running:J.refresh.running,pending:!!PENDING_START.refresh,seq:J.refresh.seq};
 J.refresh={running:false,seq:10,rc:0,finished_at:'2026-09-23T08:00:00'};   // the page has seen run 10 end
 HOLD['/api/state']={body:held({refresh:{running:false,seq:11,rc:0,finished_at:new Date(Date.now()-60000).toISOString()}})};   // run 11 ended a minute ago
 const before=loadState();await sleep(50);jobStarted('refresh');HOLD['/api/state'].release();await before;
 out.prev={running:J.refresh.running,pending:!!PENDING_START.refresh};n=CALLS.length;TOASTS.length=0;await chooseAI('codex');
 out.prevRefused={calls:CALLS.slice(n),toasts:TOASTS.slice()};""", tmp)
        check(out["order"] == {"marker": "", "applied": 2, "asked": 2}, f"an older /api/state answer arriving after a newer one is dropped ({out['order']})")
        check(out["early"] == {"running": True, "pending": True} and out["current"] is True,
              "an answer sent before jobStarted() and arriving after it leaves the job running (so does a current one not yet showing it)")
        check(out["refused"]["calls"] == [] and out["refused"]["agent"] == "claude"
              and out["refused"]["toasts"] == ["Open Loops is still running a job with Claude. Change your AI once it has finished."],
              "...so chooseAI() refuses, in a sentence")
        check(out["prev"] == {"running": True, "pending": True} and out["prevRefused"]["calls"] == []
              and out["prevRefused"]["toasts"] == ["Open Loops is still running a job with Claude. Change your AI once it has finished."],
              f"a poll asked before the start that carries the previous run's end (seq 11 > 10, ended before) does not release it; chooseAI() refuses ({out['prev']})")
        check(out["ended"]["running"] is False and out["ended"]["pending"] is False and (out["ended"]["seq"] or 0) >= 1,
              f"once an answer shows the job ended after the start, it is no longer running ({out['ended']})")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7k. #27: the page reloads while a sign-in is waiting in the browser. The app started it for an earlier load of the
    # page (a POST this page never made); the new page's first poll asks the app, finds it running and shows the spinner,
    # the fallback link and the Allow pop-up again; a step that has finished leaves its row as the check paints it.
    # After the offline banner clears it asks again, without watching the same run twice. Closing the pop-up is
    # remembered for that run: a second reload (a new page, with this tab's sessionStorage) brings back the busy row but
    # not the pop-up. Then Allow finishes it.
    tmp = setup_install("openloops-setup-reattach-")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();await tick();
 await realFetch(BASE+'/api/connect/slack',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});   // pressed on the page before the reload
 let link='';for(let i=0;i<150&&!link;i++){const s=await (await realFetch(BASE+'/api/connect/slack')).json();link=s.url||'';if(!link)await sleep(100)}
 if(!link)throw new Error('the fake sign-in never printed its link');
 FAKE['/api/connect/gmail']=[{},{running:false,rc:1,url:'',step:'gmail',last:'Login cancelled'}];   // ran and finished before the reload
 out.before={slack:CONN.slack||null,open:$('#allow_dlg').open};
 const real=connectReattach;let n=0,sweep=null;connectReattach=()=>{n++;return sweep=real()};   // sweep: the latest one, to wait for
 await loop();await until(()=>CONN.slack&&CONN.slack.busy,10000);out.first=await sweep;   // the page load's first pass, answered in full
 out.after={n,busy:CONN.slack.busy,url:CONN.slack.url,msg:CONN.slack.msg,gmail:CONN.gmail===undefined,open:$('#allow_dlg').open,step:ALLOW&&ALLOW.step,
  link:$('#allow_link').innerHTML,rows:$('#su_src_rows').innerHTML,state:$('#su_src_state').textContent};
 await loop();out.n2=n;offline=true;await loop();await until(()=>n===2,5000);out.second=await sweep;out.n3=n;out.picked=CON.filter(l=>l.includes('slack still running')).length;
 allowDismiss();out.dismissed={open:$('#allow_dlg').open,busy:CONN.slack.busy,run:CONN.slack.run};out.SS=SS;
 // review of #58: an answer that arrives while the AI is being changed, or after it changed, restores nothing
 const run={running:true,rc:null,url:'https://example.invalid/authorize?state=old',started:'2026-09-24T10:00:00',step:'login',agent:'claude',run_id:'r-login-1'};
 const quiet=()=>({busy:!!(CONN.login&&CONN.login.busy),open:$('#allow_dlg').open});
 HOLD['/api/connect/login']={body:run};let sw=real();await until(()=>HOLD['/api/connect/login'].release,5000);
 aiSwitching='codex';HOLD['/api/connect/login'].release();out.gated={ok:await sw,...quiet()};aiSwitching='';
 HOLD['/api/connect/login']={body:run};sw=real();await until(()=>HOLD['/api/connect/login'].release,5000);
 C.agent='codex';HOLD['/api/connect/login'].release();out.changed={ok:await sw,...quiet()};C.agent='claude';
 aiSwitching='codex';out.during=await real();aiSwitching='';
 // review of #58: one step's status fails and another never answers; the rest still restore, and the sweep ends in time
 FAIL_GET.add('/api/connect/install');HANG.add('/api/connect/slack_install');REATTACH_MS=1500;
 FAKE['/api/connect/miro']=[{},{running:true,rc:null,url:'https://example.invalid/authorize?state=miro',started:'2026-09-24T10:01:00',step:'miro',agent:'claude',run_id:'r-miro-1'}];
 const t2=Date.now();out.sweep={ok:await real(),ms:Date.now()-t2,miro:!!(CONN.miro&&CONN.miro.busy),url:CONN.miro&&CONN.miro.url};REATTACH_MS=15000;""", tmp)
        check(out["before"] == {"slack": None, "open": False}, "a fresh page knows nothing of the sign-in the app is running")
        a = out["after"]
        check(a["n"] == 1 and a["busy"] is True and a["url"] == "https://example.invalid/authorize?state=abc"
              and a["msg"] == "Waiting for you in the browser: click Allow there.",
              f"its first poll asks the app, which says Slack's sign-in is still running: the row is busy again, with the run's link ({a})")
        check('<span class="spin"></span>Waiting for you in the browser' in a["rows"] and "Open the sign-in page" in a["rows"]
              and "connectStep('slack')" not in a["rows"] and a["state"] == "In progress",
              "...the Slack row shows the spinner and the fallback link instead of its button; the card says in progress")
        check(a["open"] and a["step"] == "slack" and "example.invalid/authorize" in a["link"],
              "...and the Allow pop-up is back, with the sign-in link in case no tab opened")
        check(out["first"] is True and a["gmail"], "a step that finished before the reload leaves its row as the check paints it (no spinner, no old message)")
        check(out["n2"] == 1 and out["n3"] == 2 and out["second"] is True and out["picked"] == 1,
              f"later polls do not ask again; once the offline banner clears they do, without watching the same run twice ({out['n2']}, {out['n3']}, {out['picked']})")
        ds = out["dismissed"]
        gone = json.loads(out["SS"].get("ol.allowDismissed") or "{}")
        check(not ds["open"] and ds["busy"] and ds["run"] and gone == {"slack": ds["run"]},
              f"Close on the pop-up: closed, the row still busy, and that run remembered for this tab ({gone})")
        check(out["gated"] == {"ok": False, "busy": False, "open": False} and out["changed"] == {"ok": False, "busy": False, "open": False}
              and out["during"] is False,
              f"a reattach answer arriving during an AI change, or after one, restores no busy row and no pop-up, and is asked again later ({out['gated']}, {out['changed']})")
        sw = out["sweep"]
        check(sw["ok"] is False and sw["miro"] and sw["url"] == "https://example.invalid/authorize?state=miro" and 1400 <= sw["ms"] < 5000,
              f"one step's status failing and another's never answering stop neither the rest (Miro restored) nor the sweep's deadline ({sw})")
        out = setup_js(port, """
 await boot();await tick();await loop();await until(()=>CONN.slack&&CONN.slack.busy,10000);
 out.again={busy:CONN.slack.busy,open:$('#allow_dlg').open,rows:$('#su_src_rows').innerHTML};
 fs.writeFileSync(BIN+'/allow','');await until(()=>!(CONN.slack&&CONN.slack.busy)&&DOC.steps.find(x=>x.id==='slack').ok,30000);
 out.done={ok:DOC.steps.find(x=>x.id==='slack').ok,open:$('#allow_dlg').open,rows:$('#su_src_rows').innerHTML,gone:SS['ol.allowDismissed']};
 // run ids (review of #58): every fake run below starts in the same second, so only the id tells them apart
 const G=()=>JSON.parse(SS['ol.allowDismissed']||'{}'),S='2026-09-24T12:00:00';
 const run=(step,id,running,extra)=>[{},Object.assign({running,rc:running?null:0,url:'https://example.invalid/'+step,started:S,step,agent:'claude'},id?{run_id:id}:{},extra||{})];
 // (a) + (c): A's pop-up closed, the page reloads, B (same second, another id) is running: B's pop-up opens
 CONN.gmail={busy:true,msg:'x',run:'gA'};allowOpen('gmail','gA');allowDismiss();delete CONN.gmail;out.noteA=G().gmail;
 FAKE['/api/connect/gmail']=run('gmail','gB',true);await connectReattach();
 out.a={open:$('#allow_dlg').open,step:ALLOW&&ALLOW.step,run:ALLOW&&ALLOW.run,busy:CONN.gmail.busy};
 allowDismiss();out.noteB=G().gmail;
 // ...and the same run after another reload stays closed
 delete CONN.gmail;await connectReattach();out.sameRun={open:$('#allow_dlg').open,busy:CONN.gmail.busy};
 // (b): run A's watcher ends while B's note is kept: A's cleanup removes only A's own note, never B's
 allowGoneSet('miro','mB');CONN.miro={busy:true,msg:'x',run:'mA'};FAKE['/api/connect/miro']=run('miro','mA',false);
 await connectWatch('miro',{});out.b={miro:G().miro};
 allowGoneSet('miro','mA');CONN.miro={busy:true,msg:'x',run:'mA'};await connectWatch('miro',{});out.bOwn=G().miro===undefined;
 // (d): a running status from an older app, with no run_id: skipped, like a run with no agent
 delete CONN.login;FAKE['/api/connect/login']=run('login','',true);await connectReattach();
 out.d={busy:!!(CONN.login&&CONN.login.busy),open:$('#allow_dlg').open,said:CON.filter(l=>l.includes('login is running with no run id')).length};
""", tmp, session=out["SS"])
        ag = out["again"]
        check(ag["busy"] and not ag["open"] and '<span class="spin"></span>' in ag["rows"] and "Open the sign-in page" in ag["rows"],
              "a reload after closing the pop-up: the row is busy again with its link, but the pop-up stays closed for that run")
        d = out["done"]
        check(d["ok"] is True and not d["open"] and "spin" not in d["rows"] and d["gone"] == "{}",
              "clicking Allow finishes it as if pressed on this page: the row turns green, and the closed pop-up's note goes with the run")
        check(out["noteA"] == "gA" and out["a"] == {"open": True, "step": "gmail", "run": "gB", "busy": True},
              f"(a) A's pop-up closed, then a reload finds B running: B is another run (same second, other id) and its pop-up opens ({out['a']})")
        check(out["noteB"] == "gB" and out["sameRun"] == {"open": False, "busy": True},
              "(c) runs started in the same second stay distinct: B's own close is kept for B, and a reload during B stays closed")
        check(out["b"] == {"miro": "mB"} and out["bOwn"],
              f"(b) run A's watcher ending removes only A's note: B's survives, A's own goes ({out['b']})")
        check(out["d"] == {"busy": False, "open": False, "said": 1},
              f"(d) a running status with no run_id (an older Open Loops) is not picked up, like one with no agent ({out['d']})")
    finally:
        quit_app(port, srv)   # a failure part-way can leave the fake sign-in waiting: the app's own quit stops it
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

    # 7l. #27 review: a Claude sign-in still running when the AI is changed to Codex. Once Codex's check is in, a sweep
    # (a reload, the offline banner clearing) must not show that run as Codex's: no busy row, no pop-up. Back on Claude
    # it is Claude's again.
    tmp = setup_install("openloops-setup-reattach-ai-")
    srv, port = start_app(tmp, setup_env(tmp))
    try:
        out = setup_js(port, """
 await boot();await tick();
 await realFetch(BASE+'/api/connect/slack',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});   // pressed for Claude before the reload
 let st={};for(let i=0;i<150&&!st.url;i++){st=await (await realFetch(BASE+'/api/connect/slack')).json();if(!st.url)await sleep(100)}
 if(!st.url)throw new Error('the fake sign-in never printed its link');out.status={agent:st.agent,running:st.running};
 await chooseAI('codex');out.switched={agent:C.agent,doc:DOC.agent,pend:aiPending()};
 out.sweep=await connectReattach();out.codex={busy:!!(CONN.slack&&CONN.slack.busy),open:$('#allow_dlg').open,said:CON.filter(l=>l.includes('running for Claude')).length};
 await chooseAI('claude');out.back=await connectReattach();out.claude={busy:!!(CONN.slack&&CONN.slack.busy),open:$('#allow_dlg').open};""", tmp)
        check(out["status"] == {"agent": "claude", "running": True}, f"the app's status says which AI a running sign-in is for ({out['status']})")
        check(out["switched"] == {"agent": "codex", "doc": "codex", "pend": False}, "the change to Codex is saved and checked")
        check(out["sweep"] is True and out["codex"] == {"busy": False, "open": False, "said": 1},
              f"a sweep after it does not pick up Claude's sign-in as Codex's: no busy row, no pop-up, a Console line ({out['codex']})")
        check(out["back"] is True and out["claude"] == {"busy": True, "open": True}, "back on Claude, the same run is picked up again")
    finally:
        quit_app(port, srv)
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
 await closeSetup();out.closed=view();
 kicked.people=true;const s0_=stage;stage=()=>'people';await tick();out.reopen=$('#steps').innerHTML;stage=s0_;await tick();PAGE_WIN=true;await openSetup();out.win={state:$('#su_sched_state').textContent,time:$('#su_time').innerHTML};""", tmp)
        r, o, c = out["ready"], out["open"], out["closed"]
        check(r["stage"] == "ready" and r["setup"] == "none" and r["lists"] == "", "set up: the loops are the page, Set-up is out of the way")
        check(o["setup"] == "" and o["lists"] == "none" and o["back"] == "" and out["home"] == ""
              and [o[k]["state"] for k in ("ai", "src", "sched")] == ["Done", "Done", "Done"] and o["scan"] == "The first scan is done.",
              f"⚙ → Set-up opens the view on Home, every card done, with Back to your loops ({[o[k]['state'] for k in ('ai', 'src', 'sched')]})")
        check(o["every"] == "" and "Signed in to Claude" in o["steps"], "...and Every check is filled in there too")
        check(c["setup"] == "none" and c["lists"] == "" and c["jobs"] == [], "Back to your loops closes it again; nothing started")
        check(out["reopen"] == "", f"Who's who reopened after the first scan shows no '3 · First scan' bar (review of #59) ({out['reopen']!r})")
        check(out["win"]["state"] == "Not checked" and "can&#39;t yet see from here whether Windows started it" not in out["win"]["time"]
              and "can't yet see from here whether Windows started it" in out["win"]["time"],
              "on Windows, with no morning-refresh row to go by, the schedule card says Not checked, never Done")
    finally:
        stop(srv)
        shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 4. the installers
say("4. install.sh --isolated, and setup.ps1 -Isolated (static)")
ps = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
code = "\n".join(l for l in ps.splitlines() if not l.lstrip().startswith("#"))
check('python -m openloops.app$(if ($Port) { " --port $Port" })' in code and "Start this copy with: $manual" in code,
      "setup.ps1: the printed start command carries -Port, as its launch does")
# ...and that line as PowerShell evaluates it (review of #59): pwsh is on GitHub's Ubuntu and macOS runners; the whole
# script needs Windows (Scheduled Tasks, shortcuts), which the test matrix does not have, so only this line is run
pwsh = shutil.which("pwsh")
if pwsh:
    line = next(l.strip() for l in code.splitlines() if l.strip().startswith("$manual = "))
    for port_, want_ in ((8790, 'cd "C:\\OL test"; python -m openloops.app --port 8790'), (0, 'cd "C:\\OL test"; python -m openloops.app')):
        r_ = subprocess.run([pwsh, "-NoProfile", "-Command", f'$Dest = "C:\\OL test"; $Port = {port_}; $env:OPENLOOPS_PORT = "8791"; {line}; $manual'],
                            capture_output=True, text=True, timeout=60)
        check(r_.returncode == 0 and r_.stdout.strip() == want_,
              f"setup.ps1's printed command, evaluated by PowerShell with -Port {port_} and OPENLOOPS_PORT=8791: {r_.stdout.strip()!r} {r_.stderr.strip()[-200:]}")
else:
    say("SKIP running setup.ps1's start-command line: no pwsh here (CI's runners have it)")
check("$TaskRemoved = $true" in code and 'if ($NoTask -and $TaskRemoved) {' in code, "setup.ps1: a removed task is not then called unchanged")
check("(-NoApp)" not in code and "$(if ($Isolated) { 'test copy' } else { '-NoApp' })" in code
      and "$(if ($Isolated) { 'test copy' } else { '-NoTask' })" in code, "setup.ps1 -Isolated says 'test copy' too (review of #59)")
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
    check("Skipped Open Loops.app (test copy)" in r.stdout and "Skipped the weekday refresh (test copy)" in r.stdout
          and "(--no-app)" not in r.stdout and "(--no-task)" not in r.stdout,
          "#56: --isolated says 'test copy', not the flags it implies")
    check("Start this copy with:" in r.stdout and f'cd "{dest}" && python3 -m openloops.app --port 8790' in r.stdout
          and "http://localhost:8790 (the --port you gave" in r.stdout and "Your first name: Test (used so messages sound like you)" in r.stdout,
          f"#56: the output gives the start command, the address with its port, and the --name ({r.stdout[-260:]!r})")
    cursor = datetime.fromisoformat(json.loads((dest / "state.json").read_text(encoding="utf-8"))["cursor"])
    want = datetime.now().astimezone() - timedelta(days=30)
    check(abs((cursor - want).total_seconds()) < 600, f"the first-scan cursor is history_days (30) back, as the page says, not a week ({cursor})")
    env_ = {k: v for k, v in os.environ.items() if k not in ("OPENLOOPS_DEST", "OPENLOOPS_ISOLATED")}
    env_.update(HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}", OPENLOOPS_PORT="8791")
    rp = subprocess.run(["bash", str(REPO / "install.sh"), "--dest", str(dest), "--isolated", "--no-launch"], env=env_,
                        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    check(rp.returncode == 0 and "http://localhost:8791 (the OPENLOOPS_PORT in your environment" in rp.stdout
          and "python3 -m openloops.app\n" in rp.stdout,
          f"review of #59: no --port, OPENLOOPS_PORT set: the address is the one the app will use ({rp.stdout[-200:]!r})")
    rq = subprocess.run(["bash", str(REPO / "install.sh"), "--dest", str(dest), "--isolated", "--no-launch", "--port", "8792"], env=env_,
                        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    check(rq.returncode == 0 and "python3 -m openloops.app --port 8792" in rq.stdout and "http://localhost:8792 (the --port you gave" in rq.stdout,
          f"...--port beats OPENLOOPS_PORT, as in app.py: the command carries it and the address matches ({rq.stdout[-200:]!r})")
    install(home, "--dest", str(dest), "--isolated", "--no-launch", "--port", "8790")   # back to 8790 for the checks below
    r = install(home, "--dest", str(dest), "--no-app", "--no-task", "--no-launch", "--name", "Other")
    cfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    check("(--no-app)" in r.stdout and "(--no-task)" in r.stdout and "http://localhost:8790 (the port in its config.json" in r.stdout
          and "Kept the name this copy already has: Test (--name only names a new copy)" in r.stdout,
          "without --isolated the flags are named; the port comes from config.json; a later --name is said to be kept out")
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
    check("No new weekday refresh registered (test copy)" in r.stdout and "is unchanged" not in r.stdout,
          "...and does not then say the schedule is unchanged (review of #59)")
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
