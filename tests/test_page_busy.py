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
    out = json.loads(r.stdout.strip().splitlines()[-1])
    if out.get("error"):
        raise SystemExit(f"FAIL: the page's code threw: {out['error'][-800:]}")
    return out


# A stub DOM: every #id is a plain object; the banner is read back from it. REPLY decides what fetch answers.
STUBS = """
const els={};const $=s=>els[s]||(els[s]={style:{},textContent:'',innerHTML:'',classList:{add(){},remove(){},toggle(){}}});
let CON=[];function clog(m){CON.push(String(m))}const TOASTS=[];function toast(m,o){TOASTS.push({m:String(m),err:!!(o&&o.err)})}
const PAGE='t';let stopped=false;let S=null,J=null,TODAY=null,C=null,V=null,P=null,DOC=null,ISO=false;
const STATE={state:{loops:[{id:'L1',owner:'Sam',ask:'the budget',status:'waiting'}]},jobs:{},today:'2026-09-24',instance:'i1'};
let REPLY='ok';const DELAY={};
global.fetch=(u,o)=>new Promise((res,rej)=>setTimeout(()=>{
 if(REPLY==='down')return rej(new TypeError('Failed to fetch'));
 if(REPLY==='busy')return res({ok:false,status:503,text:async()=>JSON.stringify({error:BUSY})});
 if(REPLY==='boom')return res({ok:false,status:500,text:async()=>'{"error":"boom"}'});
 res({ok:true,status:200,json:async()=>u==='/api/state'?JSON.parse(JSON.stringify(STATE)):{ok:true}})},DELAY[u]||0));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
"""
BASE = [grab("const esc="), grab("const MSG="), grab("const ACT_FAILED="), grab("const fill="), grab("function msg("),
        grab("const errSaid="), cut("const api=async", "// The page could not talk to the app"), grab("const appDown="),
        cut("async function loadState(", "async function loadCfg(")]

if NODE:
    # ------------------------------------------------------------ 1. the poll loop (#61)
    say("1. a poll answered 'busy saving' is not the app being down")
    loop_parts = [f"const BUSY={json.dumps(BUSY)};", STUBS, *BASE,
                  # what the loop calls that is not under test here: the stage is not 'ready', so the usual pace is 4 s
                  "function stage(){return 'people'}async function tick(){}async function doctor(){}let docAt=Date.now(),docFails=0;",
                  "async function connectReattach(){return true}async function loadCfg(){}async function loadDaylog(){}async function loadRm(){}",
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

    # ------------------------------------------------------------ 2. a card click (#64)
    say("2. a failed click says what it could not do; a slow one says Saving…")
    act_parts = [f"const BUSY={json.dumps(BUSY)};", STUBS, *BASE,
                 # the card: its classes are what the test reads
                 "const CARD={cls:new Set()};CARD.classList={add:(...c)=>c.forEach(x=>CARD.cls.add(x)),remove:(...c)=>c.forEach(x=>CARD.cls.delete(x))};",
                 "let INST='';const document={querySelector:()=>({closest:()=>CARD})};const CSS={escape:x=>x};function renderLists(){CARD.cls.clear()}",
                 cut("const who=l=>", "\nfunction snooze(")]
    out = node(act_parts, """
      await loadState();
      REPLY='busy';DELAY['/api/action']=1500;const p=act('L1','snooze',{until:'2026-09-30'}).catch(e=>'threw');
      await sleep(400);out.early=[...CARD.cls];await sleep(900);out.waiting=[...CARD.cls];out.r=await p;out.after=[...CARD.cls];out.toasts=TOASTS.slice();
      TOASTS.length=0;REPLY='busy';DELAY['/api/action']=0;
      for(const a of ['done','reopen','priority','note','add_link','drop_link','auto_off','auto_on','unsnooze','mystery'])await act('L1',a,{}).catch(()=>{});
      out.each=TOASTS.map(t=>t.m);
      TOASTS.length=0;REPLY='ok';DELAY['/api/action']=200;CARD.cls.clear();await act('L1','done');await sleep(1200);out.quick=[...CARD.cls];out.okToast=TOASTS.map(t=>t.m);
    """)
    check(out["early"] == ["busy"], f"a click dims the card at once, no note yet ({out['early']})")
    check(sorted(out["waiting"]) == ["busy", "saving"], f"...still waiting after a second: the card says Saving… ({out['waiting']})")
    check(out["r"] == "threw" and out["after"] == [], f"...and once the answer comes, both go ({out['after']})")
    t = out["toasts"]
    check(len(t) == 1 and t[0]["err"] and t[0]["m"] == f"Couldn't snooze that: {BUSY}", f"a failed snooze: \"Couldn't snooze that: <why>\" ({t})")
    want = [messages.ACTION_FAILED[a] + ": " + BUSY for a in ("done", "reopen", "priority", "note", "add_link", "drop_link",
                                                              "auto_off", "auto_on", "unsnooze", "other")]
    check(out["each"] == want, f"every action has its own verb, and one the table does not know gets the plain one ({out['each']})")
    check(not any(" failed:" in m for m in out["each"] + [t[0]["m"]]), "no past-tense label used as a verb ('Snoozed failed')")
    check(out["quick"] == [] and out["okToast"] and out["okToast"][0].startswith("Marked done"), f"a quick click never shows Saving… ({out['quick']})")
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
say("all passed")
