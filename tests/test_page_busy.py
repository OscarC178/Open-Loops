"""The page while the app is busy saving, a failed click's wording, and two setup sentences (#61, #64).

    python3 tests/test_page_busy.py    # fast; no Slack/Gmail/Claude, no server. Node for parts 1-2 and 4.

The page's own code is cut from index.html as the app serves it (app.index_bytes(), in a throwaway install) and run in
node with a stub DOM and a stub fetch. Checks:
  1. the poll loop (#61): a 503 whose body is the app_busy sentence leaves the last state up, raises no banner and no
     offline flag, logs one Console line, and the next poll comes at the usual pace, not the 60 s offline backoff;
     the app not answering at all, or a 500, still raises the banner and backs off.
  2. a card click (#64): a failed one says "Couldn't snooze that: <the app's sentence>" (messages.ACTION_FAILED, a
     verb per action), never "Snoozed failed"; one that waits more than about a second shows "Saving…" on the card
     until the answer, and a quick one never does.
  3. every action the page sends from a card has its ACTION_FAILED line, and the table follows the plain-words rules.
  4. Codex and Miro (#64): the AI picker's Codex description and the Settings note end with the Miro row's own
     sentence (messages.py codex_no_miro), which names the custom-server exception; Codex's check says it can take
     about half a minute.
"""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

from _helpers import isolate_this_process
isolate_this_process("openloops-pagebusy-")   # importing app writes config/state into a throwaway copy
from openloops import app, doctor, messages  # noqa: E402

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


html = app.index_bytes().decode("utf-8")
lines = html.splitlines()
grab = lambda start: next(ln for ln in lines if ln.startswith(start))
cut = lambda a, b: html[html.index(a):html.index(b, html.index(a))]
BUSY = messages.say("app_busy")

NODE = shutil.which("node")
if not NODE and os.environ.get("GITHUB_ACTIONS"):
    raise SystemExit("FAIL: node is not on PATH in CI, so the page's busy handling was not tested")


def node(parts, scenario):
    """Run the page parts plus a scenario in node -> the scenario's `out` object."""
    js = "\n".join(parts + ["(async()=>{const out={};try{" + scenario + "}catch(e){out.error=String(e&&e.stack||e)}"
                            "console.log(JSON.stringify(out));process.exit(0)})();"])
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"FAIL: node could not run the page's code: {r.stderr.strip()[-800:]}")
    out = json.loads(r.stdout.strip().split("\n")[-1])   # split on newlines only: the output may hold U+2028
    if out.get("error"):
        raise SystemExit(f"FAIL: the page's code threw: {out['error'][-800:]}")
    return out


# A stub DOM: every #id is a plain object; the banner is read back from it. REPLY decides what fetch answers.
STUBS = """
const els={};const $=s=>els[s]||(els[s]={style:{},textContent:'',innerHTML:'',classList:{add(){},remove(){},toggle(){}}});
let CON=[];function clog(m){CON.push(String(m))}const TOASTS=[];function toast(m,o){TOASTS.push({m:String(m),err:!!(o&&o.err)})}
const PAGE='t';let stopped=false;let S=null,J=null,TODAY=null,C=null,V=null,P=null,DOC=null,ISO=false;
const STATE={state:{loops:[{id:'L1',owner:'Sam',ask:'the budget',status:'waiting'}]},jobs:{},today:'2026-09-24',instance:'i1'};
// REPLY / DELAY: how every request is answered; ON[url]: a queue of {ms, mode} for the next requests to that url
let REPLY='ok';const DELAY={},ON={};
global.fetch=(u,o)=>{const q=ON[u]&&ON[u].shift(),mode=q?q.mode:REPLY;return new Promise((res,rej)=>setTimeout(()=>{
 if(mode==='down')return rej(new TypeError('Failed to fetch'));
 if(mode==='busy')return res({ok:false,status:503,text:async()=>JSON.stringify({error:BUSY,code:'app_busy'})});
 if(mode==='busy_nocode')return res({ok:false,status:503,text:async()=>JSON.stringify({error:BUSY})});   // a 503 of another kind
 if(mode==='boom')return res({ok:false,status:500,text:async()=>'{"error":"boom"}'});
 res({ok:true,status:200,json:async()=>u==='/api/state'?JSON.parse(JSON.stringify(STATE)):{ok:true}})},q?q.ms:DELAY[u]||0))};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
"""
BASE = [grab("const esc="), grab("const MSG="), grab("const LABEL="), grab("const ACT_FAILED="), grab("const fill="), grab("function msg("),
        grab("const errSaid="), cut("const api=async", "// The page could not talk to the app"), grab("const appDown="),
        cut("async function loadState(", "async function loadCfg(")]

if NODE:
    # ------------------------------------------------------------ 1. the poll loop (#61)
    say("1. a poll answered 'busy saving' is not the app being down")
    loop_parts = [f"const BUSY={json.dumps(BUSY)};", STUBS, *BASE,
                  # what the loop calls that is not under test here: the stage is not 'ready', so the usual pace is 4 s
                  "function stage(){return 'people'}async function tick(){}async function doctor(){}let docAt=Date.now(),docFails=0;",
                  "async function connectReattach(){return true}function reattachSweep(){}async function loadCfg(){}async function loadDaylog(){}async function loadRm(){}",
                  "const running=k=>!!(J&&J[k]&&J[k].running);function counts(){return {ids:new Set()}}let before=null;",
                  cut("let loopT=null,", "document.addEventListener('visibilitychange'"),
                  # the timer the loop sets for its next pass: recorded, not run
                  "const NEXT=[];const _st=setTimeout;global.setTimeout=(f,ms)=>f===loop?(NEXT.push(ms),0):_st(f,ms);"]
    out = node(loop_parts, """
      await loop();out.first={S:!!S,banner:$('#banner').style.display||'',offline,next:NEXT.pop()};
      REPLY='busy';const before=S;CON=[];await loop();
      out.busy={same:S===before,loops:S&&S.loops.length,banner:$('#banner').style.display||'',text:$('#banner').textContent,offline,next:NEXT.pop(),con:CON.slice()};
      REPLY='ok';await loop();out.back={banner:$('#banner').style.display||'',offline,next:NEXT.pop()};
      REPLY='down';CON=[];await loop();out.down={banner:$('#banner').style.display,text:$('#banner').textContent,offline,next:NEXT.pop()};
      REPLY='ok';await loop();REPLY='boom';await loop();out.boom={banner:$('#banner').style.display,text:$('#banner').textContent,offline,next:NEXT.pop()};
      REPLY='ok';await loop();REPLY='down';await loop();REPLY='busy';const kept=S;await loop();
      out.back_busy={banner:$('#banner').style.display,offline,next:NEXT.pop(),same:S===kept};
      REPLY='busy_nocode';await loop();out.nocode={banner:$('#banner').style.display,offline};
    """)
    check(out["first"] == {"S": True, "banner": "none", "offline": False, "next": 4000}, f"a normal poll: state up, no banner, next in 4 s ({out['first']})")
    b = out["busy"]
    check(b["same"] and b["loops"] == 1, "a 503 'busy saving' poll keeps the last state on the page")
    check(b["banner"] == "none" and not b["text"] and b["offline"] is False, f"...raises no banner and no offline flag ({b})")
    check(b["next"] == 4000, f"...and the next poll comes at the usual pace, not the 60 s offline backoff ({b['next']})")
    check(len(b["con"]) == 1 and "503" in b["con"][0] and BUSY in b["con"][0], f"...with one Console line saying so ({b['con']})")
    check(out["back"]["next"] == 4000 and out["back"]["offline"] is False, "the next good poll carries on as before")
    d = out["down"]
    check(d["banner"] == "block" and d["text"].startswith(messages.part("server_offline", "what")),
          f"the app not answering still raises the 'isn't running' banner ({d['text']!r})")
    check(d["offline"] is True and d["next"] == 60000, f"...and backs off to a poll a minute ({d})")
    e = out["boom"]
    check(e["banner"] == "block" and e["text"] == messages.say("server_error") and e["next"] == 60000,
          f"a 500 is still an error: banner up, backed off ({e})")
    b = out["back_busy"]
    check(b == {"banner": "none", "offline": False, "next": 4000, "same": True},
          f"a failed poll then a busy one: the app is back, so the banner and the 60 s backoff go; the state stays ({b})")
    check(out["nocode"]["banner"] == "block" and out["nocode"]["offline"] is True,
          f"a 503 without code app_busy is still an error, whatever its sentence ({out['nocode']})")
    # the first load, when the page opens
    boot = [f"const BUSY={json.dumps(BUSY)};", STUBS, *BASE,
            "let LOOPED=0,LOADS=0;function loop(){LOOPED++}async function loadCfg(){}async function doctor(){}async function loadDaylog(){}async function loadRm(){}",
            "const _f=global.fetch;global.fetch=(u,o)=>{if(u==='/api/state')LOADS++;return _f(u,o)};",
            cut("const appBusy=", "\nasync function loop()"),
            cut("// Busy saving at the first load", "</script>")]
    for mode, want_banner in (("busy", "none"), ("down", "block")):
        out = node(boot[:-1] + [f"REPLY={json.dumps(mode)};", boot[-1]], "await sleep(300);$('#banner');"
                   "out.r={banner:$('#banner').style.display||'none',looped:LOOPED,loads:LOADS};")
        r = out["r"]
        check(r["banner"] == want_banner and r["looped"] == 1 and r["loads"] == (2 if mode == "busy" else 1),
              f"first load answered {mode}: banner {want_banner}, " + ("tried once more, " if mode == "busy" else "") + f"then the poll loop ({r})")

    # #67 (Codex check on #66): a first load that failed (not busy) put the banner up without setting offline, so a
    # later busy poll, which clears the banner only when coming back from offline, left it up. The real first load
    # and the real poll loop: the first load's request not answered, the loop's first poll (straight after) busy.
    out = node(loop_parts + ["let SAID=[];const _bn=banner;banner=m=>{if(m)SAID.push(m);_bn(m)};",   # every banner raised
                             "REPLY='busy';ON['/api/state']=[{ms:0,mode:'down'}];", cut("// Busy saving at the first load", "</script>")], """
      await sleep(300);out.r={said:SAID.slice(),banner:$('#banner').style.display,offline,next:NEXT.pop()};""")
    r = out["r"]
    check(len(r["said"]) == 1 and r["said"][0].startswith(messages.part("server_offline", "what")),
          f"#67: a first load the app did not answer puts the 'isn't running' banner up ({r['said']})")
    check({k: r[k] for k in ("banner", "offline", "next")} == {"banner": "none", "offline": False, "next": 4000},
          f"#67: ...and counts as offline, so the busy poll that follows clears that banner at the usual pace ({r})")

    # ------------------------------------------------------------ 2. a card click (#64)
    say("2. a failed click says what it could not do; a slow one says Saving…")
    act_parts = [f"const BUSY={json.dumps(BUSY)};", STUBS, *BASE,
                 # the card for L1, as the page draws it: renderLists() replaces it with a new element whose classes
                 # come from the card markup's actCls(), as the real renderLists does; the test reads CARD's classes
                 "function mkCard(cls){const c={cls:new Set(cls.split(' ').filter(Boolean))};c.classList={add:(...x)=>x.forEach(k=>c.cls.add(k)),"
                 "remove:(...x)=>x.forEach(k=>c.cls.delete(k)),toggle:(k,on)=>on?c.cls.add(k):c.cls.delete(k)};return c}",
                 "let CARD=mkCard(''),DRAWS=0;function renderLists(){DRAWS++;CARD=mkCard(actCls('L1'))}",
                 "let INST='';const document={querySelector:()=>({closest:()=>CARD})};const CSS={escape:x=>x};",
                 "const cls=()=>[...CARD.cls].sort();",
                 cut("const who=l=>", "\nfunction snooze(")]
    out = node(act_parts, """
      await loadState();
      ON['/api/action']=[{ms:1500,mode:'busy'}];const p=act('L1','snooze',{until:'2026-09-30'}).catch(e=>'threw');
      await sleep(400);out.early=cls();await sleep(900);out.waiting=cls();out.r=await p;out.after=cls();out.toasts=TOASTS.slice();
      // saved (slowly), then the refresh after it fails: the card must not keep Saving…
      TOASTS.length=0;ON['/api/action']=[{ms:1300,mode:'ok'}];ON['/api/state']=[{ms:0,mode:'busy'}];
      out.refresh={r:await act('L1','done').catch(e=>'threw'),cls:cls(),pending:PENDING_ACT.size,toasts:TOASTS.length};
      // two clicks on one card: the first fails while the second still waits; the second keeps its look
      ON['/api/action']=[{ms:1200,mode:'busy'},{ms:2500,mode:'ok'}];
      const p1=act('L1','snooze',{until:'2026-09-30'}).catch(()=>'threw');await sleep(100);const p2=act('L1','note',{notes:'x'});
      await p1;out.overlap={afterFirst:cls()};await p2;out.overlap.afterBoth=cls();out.overlap.pending=PENDING_ACT.size;
      // a redraw (a poll) while a click waits: the new card is drawn busy and Saving…, and loses it once it is over
      ON['/api/action']=[{ms:1600,mode:'ok'}];const p3=act('L1','done');await sleep(1200);renderLists();out.redraw={during:cls()};
      await p3;out.redraw.after=cls();
      TOASTS.length=0;REPLY='busy';DELAY['/api/action']=0;
      for(const a of ['done','reopen','priority','note','add_link','drop_link','auto_off','auto_on','unsnooze','mystery'])await act('L1',a,{}).catch(()=>{});
      out.each=TOASTS.map(t=>t.m);
      TOASTS.length=0;REPLY='ok';DELAY['/api/action']=200;await act('L1','done');await sleep(1200);out.quick=cls();out.okToast=TOASTS.map(t=>t.m);
    """)
    check(out["early"] == ["busy"], f"a click dims the card at once, no note yet ({out['early']})")
    check(out["waiting"] == ["busy", "saving"], f"...still waiting after a second: the card says Saving… ({out['waiting']})")
    check(out["r"] == "threw" and out["after"] == [], f"...and once the answer comes, both go ({out['after']})")
    t = out["toasts"]
    check(len(t) == 1 and t[0]["err"] and t[0]["m"] == f"Couldn't snooze that: {BUSY}", f"a failed snooze: \"Couldn't snooze that: <why>\" ({t})")
    r = out["refresh"]
    check(r == {"r": "threw", "cls": [], "pending": 0, "toasts": 0},
          f"saved after a second, then the refresh fails: no Saving… or dimming left behind, no 'couldn't' toast ({r})")
    o = out["overlap"]
    check(o["afterFirst"] == ["busy", "saving"], f"two clicks on one card: the first failing leaves the second's look alone ({o})")
    check(o["afterBoth"] == [] and o["pending"] == 0, f"...which goes when the second is over ({o})")
    d = out["redraw"]
    check(d["during"] == ["busy", "saving"] and d["after"] == [], f"a redraw while a click waits keeps it busy and Saving… ({d})")
    want = [messages.ACTION_FAILED[a] + ": " + BUSY for a in ("done", "reopen", "priority", "note", "add_link", "drop_link",
                                                              "auto_off", "auto_on", "unsnooze", "other")]
    check(out["each"] == want, f"every action has its own verb, and one the table does not know gets the plain one ({out['each']})")
    check(not any(" failed:" in m for m in out["each"] + [t[0]["m"]]), "no past-tense label used as a verb ('Snoozed failed')")
    check(out["quick"] == [] and out["okToast"] and out["okToast"][0].startswith("Marked done"), f"a quick click never shows Saving… ({out['quick']})")
    check("${actCls(l.id)}" in html, "the card markup takes its busy / Saving… look from the pending clicks")
else:
    say("SKIP parts 1-2: node not installed")

# ---------------------------------------------------------------- 3. the ACTION_FAILED table
say("3. ACTION_FAILED covers every card action, in plain words")
said = set(re.findall(r"(\w+):'", re.search(r"const SAID=\{([^}]*)\}", html).group(1)))
check(said and said <= set(messages.ACTION_FAILED) and "other" in messages.ACTION_FAILED,
      f"every action with a toast label has a failure line ({sorted(said - set(messages.ACTION_FAILED))} missing)")
for k, v in messages.ACTION_FAILED.items():
    check(v.startswith("Couldn't ") and not v.endswith((".", ":")) and "failed" not in v.lower()
          and not re.search(r"\b(color|behavior|canceled)\b", v), f"ACTION_FAILED[{k}]: a verb phrase, no end stop ({v!r})")
check("const ACT_FAILED=" + messages.page_action_failed_json() + ";" in html, "the served page carries the table")
# the second placeholder is filled exactly once and survives hostile text: "</script>", "<!--", quotes, U+2028
raw = (Path(app.__file__).parent / "index.html").read_text(encoding="utf-8")
check(raw.count("/*OL_MESSAGES*/{}") == 1 and raw.count("/*OL_ACTION_FAILED*/{}") == 1 and "/*OL_ACTION_FAILED*/" not in html,
      "index.html has each placeholder once, and the served page has none left")
hostile = {"snooze": "Couldn't </script><script>alert(1)</script> <!-- \"q\" 'a' & \u2028 \u2029 \\ end"}
real, messages.ACTION_FAILED = messages.ACTION_FAILED, hostile
try:
    served = app.index_bytes().decode("utf-8")
finally:
    messages.ACTION_FAILED = real
line = next(ln for ln in served.splitlines() if ln.startswith("const ACT_FAILED="))
check("</script" not in line.lower() and "<!--" not in line and "\u2028" not in line and "\u2029" not in line,
      "a hostile ACTION_FAILED line cannot end or change the script element")
check(served.count("</script>") == html.count("</script>"), "...and the page has no more script ends than before")
if NODE:
    got = node([line], "out.v=ACT_FAILED;")
    check(got["v"] == hostile, "...and the page reads it back as the same text")
check("' failed: '" not in html and "SAID[action]||action)+' failed" not in html, "the page no longer builds '<label> failed:'")

# ---------------------------------------------------------------- 4. Codex and Miro; Codex's check time
say("4. one statement of Codex and Miro; Codex's check says about half a minute")
row = messages.FAILURES["codex_no_miro"]["what"]
check(row == "Miro isn't available with Codex unless you added a Miro server to Codex yourself.",
      f"the Miro row's sentence names the custom-server exception in one clause ({row!r})")
check('say("codex_no_miro")' in (Path(doctor.__file__).read_text(encoding="utf-8")) and messages.say("codex_no_miro").startswith(row),
      "the checklist's Miro row under Codex says it")
if NODE:
    out = node([grab("const MSG="), grab("const CODEX_MIRO="), cut("const AI_CHOICES=[", "\nconst SU_STATE=")],
               "out.codex=AI_CHOICES.find(c=>c[0]==='codex')[2];out.miro=CODEX_MIRO;")
    check(out["miro"] == row and out["codex"].endswith(" " + row), f"the picker's Codex description ends with the same sentence ({out['codex'][-120:]!r})")
    check("Miro only if" not in out["codex"], "...and the old, different wording is gone")
check("$('#cfg_codex_miro').textContent=CODEX_MIRO" in html and "and Miro isn't available with Codex.</div>" not in html,
      "the Settings note under Codex says the same sentence")
check(messages.say("codex_checking") == "Checking Codex on this computer. This can take about half a minute."
      and "a==='codex'?esc(msg('codex_checking'))" in html, "Codex's check tells you it can take about half a minute")
if NODE:   # Check again with Codex already chosen (review of #66): the same half-minute note, next to the button
    out = node(["const esc=s=>String(s);", grab("const MSG="), "let suBusy='';", grab("const codexWait=")],
               "suBusy='check';out.codex=codexWait('codex');out.claude=codexWait('claude');suBusy='';out.idle=codexWait('codex');")
    check(messages.part("codex_checking", "fix") in out["codex"] and out["claude"] == "" and out["idle"] == "",
          f"Check again with Codex says it can take about half a minute; not for Claude, not when idle ({out})")
check(html.count("'Check again'}</button>${codexWait(a)}") == 2, "...on both Check again buttons of the Set-up view")
say("all passed")
