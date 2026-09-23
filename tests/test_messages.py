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
import json, re, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from _helpers import fresh_install, isolate_this_process, isolated_env, start_app, stop  # noqa: E402
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
show("2. every id used exists, and every entry is used")
SOURCES = {p: (REPO / "openloops" / p).read_text(encoding="utf-8") for p in ("doctor.py", "app.py", "agent.py", "standing.py", "index.html", "messages.py")}
used = set()
for name, text in SOURCES.items():
    used |= set(re.findall(r"""\b(?:say|part|msg)\(\s*["']([a-z_]+)["']""", text))
    used |= set(re.findall(r"""_F\[["']([a-z_]+)["']\]""", text))
from openloops import app  # noqa: E402  (importing app writes config/state into the throwaway install only)
used |= set(app.INSTALL_WHY.values()) | {i for i, _ in messages.JOB_SIGNS}
missing = sorted(i for i in used if i not in FAILURES)
for text in SOURCES.values():  # an id picked by a condition on the same line: say("ai_broken" if broken else "ai_missing")
    for line in text.splitlines():
        if re.search(r"\b(?:say|msg)\(", line):
            used |= set(re.findall(r"""["']([a-z_]+)["']""", line)) & set(FAILURES)
check(not missing, f"every id asked for is in FAILURES (missing: {missing})")
unused = sorted(set(FAILURES) - used)
check(not unused, f"every FAILURES entry is used somewhere (unused: {unused})")
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
      and all(set(v) == {"what", "fix", "button"} for v in page.values()), "for_page(): the whole table, fix chosen for the platform")

# ---------------------------------------------------------------- 4. job failures
show("4. job_failure() reads why a job stopped")
jf = messages.job_failure
check(jf("refresh", 1, "!! no OPENLOOPS block\nInvalid API key · Please run /login")[0] == "job_signed_out", "signed out -> job_signed_out")
check("Claude has signed you out" in jf("refresh", 1, "Not logged in")[1], "...said as Claude signing you out")
check(jf("chase", 1, "Claude AI usage limit reached|1760000000")[0] == "job_usage_limit", "usage limit")
check(jf("people", 1, "API Error: Connection error. getaddrinfo ENOTFOUND api.anthropic.com")[0] == "job_network", "no network")
i, said = jf("voice", 1, "!! no VOICE block. See log.")
check(i == "job_failed" and said.startswith("Learning your tone didn't finish."), f"anything else: the job, named, didn't finish ({said!r})")
i, said = jf("refresh", -1, "could not start refresh: FileNotFoundError: python")
check(i == "job_start_failed" and "couldn't start the refresh" in said, "a job that could not start")
check(jf("refresh", 1, "Not logged in", ai="Grok")[1].startswith("The refresh stopped because Grok"), "the AI is named as chosen")
line = agent.CODEX_REFUSE["timeout"].format(limit="15 minutes")
check(jf("refresh", 3, "[2026-09-23_0915] refresh: 3 open loops\n" + line) == ("codex_timeout", line),
      "a Codex job's own sentence (agent.CODEX_REFUSE) is what the page shows")
check(agent.CODEX_REFUSE["unlisted"] == say("codex_unlisted") and "{store}" in agent.CODEX_REFUSE["keyring"],
      "agent.CODEX_REFUSE is built from the table, placeholders left for the caller")

e = app._ended("refresh", 1, "Invalid API key · Please run /login")
check(e["failure"] == "job_signed_out" and e["said"].startswith("The refresh stopped because") and e["rc"] == 1,
      "app: a failed job carries its failure id and sentence for the page")
check("said" not in app._ended("refresh", 0, "done") and "said" not in app._ended("chase", 2, "SKIPPED: x"),
      "app: a job that worked, or was SKIPPED (exit 2, it says why itself), carries no failure")

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
finally:
    stop(srv)
    shutil.rmtree(tmp, ignore_errors=True)
m = re.search(r"^const MSG=(.*);$", html, re.M)
check(m and "/*OL_MESSAGES*/" not in html, "the app fills in the page's failure table as it serves it")
served = json.loads(m.group(1))
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

# ---------------------------------------------------------------- 6. offline banner, run in node
show("6. the offline banner, in node")
node = shutil.which("node")
if not node:
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
    "let lastBanner='';", grab("function banner("), grab("const appDown="),
    """(async()=>{const out={};
 global.fetch=async()=>{throw new TypeError('Failed to fetch')};
 try{await api('/api/state')}catch(e){appDown(e)}out.offline={display:$('#banner').style.display,text:$('#banner').textContent};
 global.fetch=async()=>({ok:false,status:500,text:async()=>'{"error":"boom"}'});
 try{await api('/api/state')}catch(e){out.status=e.status;out.body=e.body;appDown(e)}out.error={display:$('#banner').style.display,text:$('#banner').textContent};
 banner('');out.cleared=$('#banner').style.display;
 console.log(JSON.stringify(out))})();"""])
r = subprocess.run([node, "-e", JS], capture_output=True, text=True, timeout=30)
check(r.returncode == 0, f"node ran the page's own functions ({r.stderr.strip()[-300:]})")
out = json.loads(r.stdout.strip().splitlines()[-1])
want = say("server_offline")
check(out["offline"] == {"display": "block", "text": want},
      f"app not running: banner shown (display:block) saying {want!r} (got {out['offline']})")
check(out["error"]["display"] == "block" and out["error"]["text"] == say("server_error") and out["status"] == 500
      and out["body"] == {"error": "boom"}, "app answered with an error: a different sentence, and the error carries status and body")
check(out["cleared"] == "none", "banner('') hides it again")
show("PASS")
