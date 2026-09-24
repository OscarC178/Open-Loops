"""Stop this sign-in against runs that change under it (#67, reviews of #68): the page's own code, in node, with a fetch
whose every answer the test releases by hand and a clock the test moves. No waits on real time, no server.

    python3 tests/test_page_signin.py    # fast; no Slack/Gmail/Claude, no server. Needs node (skipped without it locally).

The page's code is cut from index.html as the app serves it (app.index_bytes(), in a throwaway install): the setup
buttons (connectStep / connectStop / connectAdopt / connectStopped / connectWatch) and the Allow pop-up. Checks, each an
interleaving Codex reproduced on #68:
  1. a Stop for R1 whose success arrives after the row has taken R2 (R1 ended, another tab started R2): R2's row is
     left alone (still waiting, its Stop ready, no check run, no toast).
  2. going backwards: the row has moved R1 -> R2 -> R3 and R3's pop-up was closed; a late 409 for the Stop of R1,
     naming R2, arrives: the row stays on R3 and R3's closed-pop-up note is kept. A late status naming R2 is not taken
     either.
  3. a 409 that makes the row take R2 just before R1's time limit: the watcher follows R2 with a limit of its own, so R2
     is still waiting past R1's deadline, and times out only at its own.
"""
import json, os, shutil, subprocess

from _helpers import isolate_this_process
isolate_this_process("openloops-pagesignin-")   # importing app writes config/state into a throwaway copy
from openloops import app, messages  # noqa: E402


def say(msg):
    print(msg, flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


NODE = shutil.which("node")
if not NODE:
    if os.environ.get("GITHUB_ACTIONS"):
        raise SystemExit("FAIL: node is not on PATH in CI, so the page's sign-in code was not tested")
    say("SKIP: node not installed")
    raise SystemExit(0)

html = app.index_bytes().decode("utf-8")
lines = html.splitlines()
grab = lambda start: next(ln for ln in lines if ln.startswith(start))
cut = lambda a, b: html[html.index(a):html.index(b, html.index(a))]

# A stub DOM and page, a fetch the test answers by hand (PENDING), and a clock the test moves (NOW, TIMERS).
STUBS = r"""
const els={};const mk=id=>({id,style:{},dataset:{},textContent:'',innerHTML:'',disabled:false,open:false,focus(){},showModal(){this.open=true},close(){this.open=false},addEventListener(){}});
const $=s=>els[s]||(els[s]=mk(s));const document={querySelector:()=>null,getElementById:id=>$('#'+id)};
const SS={};const sessionStorage={getItem:k=>k in SS?SS[k]:null,setItem:(k,v)=>{SS[k]=String(v)},removeItem:k=>{delete SS[k]}};
const CON=[];function clog(m){CON.push(String(m))}const TOASTS=[];function toast(m){TOASTS.push(String(m))}
const PAGE='t';let C={agent:'claude'},DOC=null;const agentLabel=()=>'Claude';function stage(){return 'ready'}function paintConnect(){}function paintSetup(){}
const DOCTOR=[];async function doctor(f){DOCTOR.push(!!f)}
let NOW=0;Date.now=()=>NOW;const TIMERS=[];
global.setTimeout=(f,ms)=>{TIMERS.push({at:NOW+(ms||0),f});return TIMERS.length};global.clearTimeout=()=>{};
global.setInterval=()=>0;global.clearInterval=()=>{};
const PENDING=[];
global.fetch=(u,o)=>new Promise(res=>PENDING.push({u,post:!!(o&&o.method==='POST'),body:(o&&o.body)||'',res}));
const flush=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setImmediate(r))};
// answer the first pending request to u (a POST if post) with status and body
async function answer(u,post,status,body){const i=PENDING.findIndex(p=>p.u===u&&p.post===post);if(i<0)throw new Error('nothing pending for '+u);
 const p=PENDING.splice(i,1)[0];p.res({ok:status<400,status,json:async()=>body,text:async()=>JSON.stringify(body)});await flush();return p}
// move the clock to `to`, firing timers as they fall due; every status poll the watcher sends meanwhile gets status(step)
async function runTo(to,status){for(;;){TIMERS.sort((a,b)=>a.at-b.at);const t=TIMERS[0];
  if(!t||t.at>to){NOW=to;return}TIMERS.shift();NOW=t.at;t.f();await flush();
  for(const p of PENDING.filter(p=>!p.post)){PENDING.splice(PENDING.indexOf(p),1);const s=status(p.u);p.res({ok:true,status:200,json:async()=>s,text:async()=>JSON.stringify(s)})}
  await flush()}}
"""
PARTS = [STUBS, grab("const esc="), grab("const MSG="), grab("const LABEL="), grab("const fill="), grab("function msg("),
         grab("const errSaid="), cut("const api=async", "let lastBanner="),
         cut("// ---------- Setup buttons", "\n// #27: the page reloaded"),
         cut("const ALLOW_ROW=", "// ---------- end of the Set-up view")]


def node(scenario):
    js = "\n".join(PARTS + ["(async()=>{const out={};try{" + scenario + "\n}catch(e){out.error=String(e&&e.stack||e)}"
                            "console.log(JSON.stringify(out));process.exit(0)})();"])
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"FAIL: node could not run the page's code: {r.stderr.strip()[-800:]}")
    out = json.loads(r.stdout.strip().split("\n")[-1])
    if out.get("error"):
        raise SystemExit(f"FAIL: the page's code threw: {out['error'][-800:]}")
    return out


STOP = "/api/connect/slack/stop"
OTHER = messages.say("connect_stop_other")

# ---------------------------------------------------------------- 1. a late Stop success after the row took R2
say("1. a Stop for R1 whose success arrives after the row has taken R2")
out = node(f"""
 CONN.slack={{busy:true,msg:'Waiting',run:'R1',url:'https://example.invalid/r1'}};
 const p=connectStop('slack');await flush();out.sent=PENDING.map(x=>x.u+' '+x.body);
 connectAdopt('slack',{{running:true,run_id:'R2',url:'https://example.invalid/r2'}});   // the watcher: R1 ended, R2 waits
 await answer('{STOP}',true,200,{{ok:true,running:false,run_id:'R1'}});await p;
 const c=CONN.slack;out.row={{run:c.run,busy:!!c.busy,stopping:!!c.stopping,url:c.url}};out.doctor=DOCTOR.length;out.toasts=TOASTS.slice();
 out.btn=connectBtn({{connect:'slack'}},false).includes(`<button onclick="connectStop('slack')">`);""")
check(out["sent"] == [STOP + ' {"run_id":"R1"}'], f"the Stop posts R1, the run the row showed ({out['sent']})")
check(out["row"] == {"run": "R2", "busy": True, "stopping": False, "url": "https://example.invalid/r2"} and out["btn"],
      f"R1's late success leaves R2's row as it is: waiting, R2's link, its Stop ready ({out['row']})")
check(out["doctor"] == 0 and out["toasts"] == [], f"...and runs no check and says nothing ({out['doctor']}, {out['toasts']})")

# ---------------------------------------------------------------- 2. no going backwards
say("2. a late 409 naming R2 after the row moved on to R3")
out = node(f"""
 CONN.slack={{busy:true,msg:'Waiting',run:'R1'}};
 const p=connectStop('slack');await flush();
 connectAdopt('slack',{{running:true,run_id:'R2'}});connectAdopt('slack',{{running:true,run_id:'R3'}});   // the watcher, twice
 allowOpen('slack','R3');allowDismiss();out.note=allowGone().slack;
 await answer('{STOP}',true,409,{{ok:false,error:'other run',running:true,said:{json.dumps(OTHER)},run_id:'R2'}});await p;
 out.after409={{run:CONN.slack.run,busy:!!CONN.slack.busy,note:allowGone().slack,toasts:TOASTS.slice()}};
 out.status=connectAdopt('slack',{{running:true,run_id:'R2'}});   // a late status naming R2: not taken either
 out.afterStatus={{run:CONN.slack.run,note:allowGone().slack}};
 out.fresh=connectAdopt('slack',{{running:true,run_id:'R4'}})&&CONN.slack.run==='R4';   // a run not seen before still is""")
check(out["note"] == "R3", "R3's pop-up was closed, and that is remembered for R3")
check(out["after409"] == {"run": "R3", "busy": True, "note": "R3", "toasts": []},
      f"a late 409 for R1's Stop naming R2 leaves the row on R3, R3's closed-pop-up note kept, nothing said ({out['after409']})")
check(out["status"] is False and out["afterStatus"] == {"run": "R3", "note": "R3"},
      f"a late status naming R2 (already replaced) is not taken ({out['afterStatus']})")
check(out["fresh"] is True, "...while a run the page has not seen end is still taken")

# ---------------------------------------------------------------- 3. the watcher follows a 409 adoption
say("3. a 409 adoption just before R1's time limit")
out = node(f"""
 const LIM=CONNECT_WAIT_MS;let who='R1';const st=()=>({{running:true,run_id:who,url:'https://example.invalid/'+who,agent:'claude'}});
 CONN.slack={{busy:true,msg:'Waiting',run:'R1'}};const w=connectWatch('slack',{{}});let ended=false;w.then(()=>{{ended=true}});
 await runTo(LIM-1000,st);   // R1 waits almost to its limit
 const p=connectStop('slack');await flush();who='R2';   // meanwhile R1 ended and another tab started R2
 await answer('{STOP}',true,409,{{ok:false,error:'other run',running:true,said:{json.dumps(OTHER)},run_id:'R2',url:'https://example.invalid/R2'}});await p;
 out.adopted={{run:CONN.slack.run,busy:!!CONN.slack.busy,at:NOW}};
 await runTo(LIM+2000,st);   // past R1's deadline
 out.past={{run:CONN.slack.run,busy:!!CONN.slack.busy,msg:CONN.slack.msg,ended}};
 await runTo(2*LIM+10000,st);   // past R2's own
 out.own={{busy:!!(CONN.slack&&CONN.slack.busy),msg:(CONN.slack||{{}}).msg,ended}};""")
check(out["adopted"]["run"] == "R2" and out["adopted"]["busy"], f"the 409 makes the row take R2 ({out['adopted']})")
check(out["past"] == {"run": "R2", "busy": True, "msg": "Waiting", "ended": False},
      f"past R1's deadline R2 is still waiting and watched: the watcher took R2 with a limit of its own ({out['past']})")
check(out["own"]["busy"] is False and out["own"]["msg"] == messages.say("connect_timeout") and out["own"]["ended"],
      f"...and R2 times out only at its own limit ({out['own']})")
say("all passed")
