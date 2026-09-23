"""Open Loops - tiny local web app. stdlib only.

    python3 -m openloops.app            -> http://localhost:8765
                                           (or the next free port if 8765 is taken; OPENLOOPS_PORT overrides)
"""
import json, re, shlex, shutil, socket, subprocess, sys, threading, time, webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import messages
from .paths import PKG, ROOT
from .store import load_cfg, norm_date, read_json, update_json, write_json
STATE = ROOT / "state.json"
INDEX = PKG / "index.html"
CONFIG = ROOT / "config.json"
VOICEF = ROOT / "voice.json"
EDITABLE = ("agent", "model", "effort", "codex_model", "codex_effort", "use_slack", "history_days", "owner_name", "chase_external_email", "send_internal", "send_external", "internal_domains", "auto_chase", "tone", "people", "exclude_people", "exclude_topics", "voice_sample_people", "escalation", "vault_path", "standing_file", "pinned_links", "slack_source", "miro_source", "roadmap_board", "roadmap_frame")
import os
def _port_arg():
    """`--port N` (or `--port=N`) beats OPENLOOPS_PORT beats config.json "port" beats 8765. `npm run dev` uses 8766
    so a checkout never collides with, or is mistaken for, the installed copy on 8765; a test install
    (`install.sh --dest … --port 8790`) keeps its port in its own config.json so every launch uses it."""
    a = sys.argv
    for i, x in enumerate(a):
        if x.startswith("--port="):
            return int(x.split("=", 1)[1])
        if x == "--port" and i + 1 < len(a):
            return int(a[i + 1])
    if os.environ.get("OPENLOOPS_PORT"):
        return int(os.environ["OPENLOOPS_PORT"])
    try:
        p = int(load_cfg().get("port") or 8765)
    except (TypeError, ValueError):  # a hand-edited "port": "abc" falls back to the default rather than not starting
        return 8765
    return p if 1024 <= p <= 65535 else 8765  # so does one the app could never listen on


PREFERRED = _port_arg()
PORT = PREFERRED
WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"

IDLE_EXIT_S = 3 * 3600  # backstop: server quits after 3h with no page activity
last_seen = time.time()
# Pages that are open right now: page id -> last request time. Each page invents an id, sends it on
# every request, and says goodbye (sendBeacon) when it closes. Once no page is left, the server quits
# after a short grace (a reload is a goodbye followed by a hello within a second). Pages that vanish
# without a goodbye (browser crash, laptop closed) are forgotten after PAGE_STALE_S.
pages = {}
bye_at = 0.0
STARTED = datetime.now().isoformat(timespec="seconds")
quit_requested = False
quit_now = False  # `--stop --now`: do not wait for a running job, cut it short
PAGE_GRACE_S = 4
PAGE_STALE_S = 15 * 60
PEOPLEF = ROOT / "people_suggested.json"

def cfg():
    return load_cfg()


def history_days():
    """How far back the AI reads (Settings > History). Drives the first-scan cursor and who's-who."""
    try:
        return min(int(cfg().get("history_days") or 30), 365)
    except Exception:
        return 30


def fresh_state():
    from datetime import timedelta
    return json.dumps({"cursor": (datetime.now().astimezone() - timedelta(days=history_days())).isoformat(timespec="minutes"),
                       "last_refresh": None, "loops": []}, indent=2)


# First run on a new machine: make sure config.json and state.json exist so nothing 500s.
if not CONFIG.exists():
    tpl = ROOT / "config.template.json"
    CONFIG.write_text(tpl.read_text(encoding="utf-8-sig") if tpl.exists() else "{}", encoding="utf-8")
if not STATE.exists():
    STATE.write_text(fresh_state(), encoding="utf-8")
(ROOT / "state" / "logs").mkdir(parents=True, exist_ok=True)

doctor_cache = {"at": 0, "result": None}
# Bumped whenever the answer may have changed under a check already running (a setup step finished, Start over):
# such a check still answers its caller but is never cached, so it cannot bring back a row the user just fixed.
doctor_gen = {"n": 0}
JOB_MOD = {"refresh": "refresh", "chase": "chase", "voice": "voice", "people": "people", "standing": "close_standing",
           "daylog": "daylog", "roadmap": "roadmap"}
jobs = {k: {"running": False, "log": ""} for k in JOB_MOD}
procs = {}  # name -> Popen while a job runs, so `--stop --now` can cut it short (jobs itself is sent as JSON)


def load():
    return read_json(STATE, {"cursor": None, "last_refresh": None, "loops": []})


def save(s):
    write_json(STATE, s)


def kill_tree(p):
    """Stop a job and whatever it spawned (the claude CLI). Windows has no process groups: taskkill /T."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            import os, signal
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:  # a child that shrugs off TERM
                os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        pass  # already gone, or never ours: nothing left to stop


def _ai_now():
    """The AI chosen now, by name for a sentence ("Claude"); read when a job STARTS, so a switch in Settings while it
    runs never blames the other AI (#25 review)."""
    from . import agent
    try:
        return agent.display_name()
    except Exception:  # an unreadable config.json must not lose the job's result
        return "Claude"


def _ended(name, rc, log, ai="Claude", last=None):
    """A finished job's entry in `jobs`. A failure (not 0, not 2 = SKIPPED) also carries "failure" (a messages.py id)
    and "said", the plain sentence the page shows (#25), read from the job's own OPENLOOPS_FAILURE line only; the log
    itself stays for the Console and Settings."""
    j = {"running": False, "log": log, "rc": rc}
    if rc not in (0, 2):
        j["failure"], j["said"] = messages.job_failure(name, rc, log, ai=ai, last=last)
    return j


def run_job(name, extra=None):
    if jobs[name]["running"]:
        return False
    args = [sys.executable, "-m", f"openloops.{JOB_MOD[name]}", *(extra or [])]
    ai = _ai_now()  # the AI this run uses, for its sentence if it fails
    try:  # started here, not in the thread: a job the page sees as running always has a process /api/quit can stop
        p = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", start_new_session=sys.platform != "win32")
    except Exception as e:  # no interpreter, no permission: say so in the job log rather than hang as "running"
        jobs[name] = _ended(name, -1, f"could not start {name}: {type(e).__name__}: {e}", ai)
        return False
    procs[name] = p
    jobs[name] = {"running": True, "log": ""}

    def go():
        try:
            out, err = p.communicate()
            last = (out.strip().splitlines() or [""])[-1]   # the job's own last stdout line: its OPENLOOPS_FAILURE, if any
            jobs[name] = _ended(name, p.returncode, (out + err)[-4000:], ai, last)
        except Exception as e:
            jobs[name] = _ended(name, -1, f"{name} broke off: {type(e).__name__}: {e}", ai)
        finally:
            procs.pop(name, None)

    threading.Thread(target=go, daemon=True).start()
    return True


# ---- Setup buttons: /api/connect/<step> runs agent.login_cmd(step) (Claude's sign-ins, Codex's `codex login`), opens
# agent.connect_url(step) in the browser (Codex's Gmail / Slack: connected on ChatGPT's apps page), or, for "install",
# runs the selected AI's installer (agent.install_cmd, any agent) in the background ----
# Nothing here keeps a token: the Claude CLI stores whatever the sign-in gives it, as it does from a terminal.
# The log (state/connect-<step>.log) holds what the CLI printed, minus the sign-in link's query, for the page and Console.
CONNECT_TIMEOUT_S = 5 * 60  # a sign-in nobody finishes is stopped, so a later click can start afresh
# an install downloads a few hundred MB: longer, but still not for ever (the tests shorten it)
INSTALL_TIMEOUT_S = int(os.environ.get("OPENLOOPS_INSTALL_TIMEOUT_S") or 10 * 60)
URL_RE = re.compile(r"https://[^\s\x1b\x07]+")
# On disk a link keeps its address but not its query: that is where an authorisation request's state and
# challenge live. The full link stays in memory only (connects[step]["url"]), for the page's fallback link.
REDACT_RE = re.compile(r"(https://[^\s?\x1b\x07]+)\?[^\s\x1b\x07]+")
ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]|\r")
connects = {}  # step -> {"running", "rc", "url", "started"}
connect_lock = threading.Lock()  # two clicks (two tabs) at once must still start one run
connect_procs = {}  # step -> Popen of the command running now, so quitting the app stops it


def stop_connects():
    """Quit or exit: stop every setup step still waiting (Windows: its console window too). quit_requested is set
    first, and _launch checks it under the same lock, so a step about to start either is in this list or never starts."""
    with connect_lock:
        running = list(connect_procs.values())
    for p in running:
        kill_tree(p)


def _launch(step, *args, **kw):
    """Popen for a setup step, registered for stop_connects() in the same breath -> Popen, or None once quitting."""
    with connect_lock:
        if quit_requested:
            return None
        p = connect_procs[step] = subprocess.Popen(*args, **kw)
        return p


def _reap(step, p, secs, on_timeout=None):
    """Wait for a setup step's process (killing it if it outstays secs) -> exit code, then stop tracking it.
    Tracked until here, so a child that closed its terminal but lives on can still be stopped by Quit.
    on_timeout, if given, is called once the outstayer has been killed (the install says why it stopped)."""
    try:
        return p.wait(max(1, secs))
    except subprocess.TimeoutExpired:
        kill_tree(p)
        if on_timeout:
            on_timeout()
        return -1
    finally:
        with connect_lock:
            if connect_procs.get(step) is p:
                connect_procs.pop(step)


def connect_log(step):
    return ROOT / "state" / f"connect-{step}.log"


def add_install_dirs():
    """Append the folders the CLI installers use (agent.install_dirs) that exist to this process's PATH; the checks and
    jobs it starts inherit it. Run at start (an app opened from a terminal that predates the install) and after an
    install (the installer adds the folder to the user's shell profile, which this process never reads)."""
    from . import agent
    parts = os.environ.get("PATH", "").split(os.pathsep)
    extra = [str(d) for d in agent.install_dirs() if d.is_dir() and str(d) not in parts]
    if extra:
        os.environ["PATH"] = os.pathsep.join(parts + extra)


def _connect_one(step, argv, log, deadline):
    """Run one command of a setup step -> exit code. Off Windows it runs on a pseudo-terminal (stdlib pty):
    `claude mcp login` gives up at once when stdin is not a terminal, but on one it waits for the browser's
    callback. With --no-browser it prints the sign-in link instead of opening it; we open it here, and the
    page shows it too in case no browser window came up. Windows has no stdlib pty: the command gets a
    console window of its own (a real terminal) and opens the browser itself; its output stays in that window."""
    if WIN:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"$ {subprocess.list2cmdline(argv)}\n(running in its own window)\n")
        p = _launch(step, subprocess.list2cmdline(argv), cwd=ROOT, shell=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE)
        return -1 if p is None else _reap(step, p, deadline - time.time())
    import os, pty, select
    m, s = pty.openpty()
    try:
        p = _launch(step, argv, cwd=ROOT, stdin=s, stdout=s, stderr=s, start_new_session=True, close_fds=True)
    finally:
        os.close(s)
    if p is None:  # Open Loops is closing
        os.close(m)
        return -1
    raw, tail, rc = b"", "", -1
    try:
        before = log.read_text(encoding="utf-8") + "$ " + " ".join(shlex.quote(a) for a in argv) + "\n"
        while True:
            if time.time() > deadline:
                kill_tree(p)
                tail = "\nstopped: no answer from the browser within 5 minutes\n"
                break
            if select.select([m], [], [], 0.5)[0]:
                try:
                    chunk = os.read(m, 4096)
                except OSError:  # the command exited and closed its end (Linux says so with EIO)
                    chunk = b""
                if not chunk:
                    break
                raw = (raw + chunk)[-64000:]
                # the whole buffer each time: an escape code or the link can straddle two reads
                text = ANSI_RE.sub("", raw.decode("utf-8", "replace"))
                log.write_text(before + REDACT_RE.sub(r"\1?(rest of the link not saved)", text), encoding="utf-8")
                u = URL_RE.search(text)
                if u and not connects[step].get("url") and text[u.end():u.end() + 1].isspace():  # the whole link is in
                    connects[step]["url"] = u.group(0)
                    if "--no-browser" in argv:
                        webbrowser.open(u.group(0))
            elif p.poll() is not None:
                break
    finally:  # however the loop ended, the process is waited for (or killed) before it stops being tracked
        os.close(m)
        rc = _reap(step, p, 5)
    if tail:
        with open(log, "a", encoding="utf-8") as f:
            f.write(tail)
    return rc


def run_connect(step):
    """Start a Claude or Codex setup step in the background -> (started, error). One run per step at a time."""
    from . import agent
    if step not in agent.connect_steps():
        return False, "no such setup step for " + agent.display_name()
    with connect_lock:  # check and claim in one go
        if quit_requested:
            return False, "Open Loops is closing"
        if (connects.get(step) or {}).get("running"):
            return False, "already running"
        connects[step] = {"running": True, "rc": None, "url": "", "started": datetime.now().isoformat(timespec="seconds")}
    log = connect_log(step)

    def go():
        rc, deadline = -1, time.time() + CONNECT_TIMEOUT_S
        try:  # login_cmd may ask the CLI a question itself (is the marketplace known?), so not on the request
            url = agent.connect_url(step)
            if url:  # a page to open, not a command: done once the browser has it; the user presses Check again after
                with open(log, "a", encoding="utf-8") as f:
                    f.write(f"opened {url} in the browser\n")
                connects[step].update(url=url, opened=True)
                rc = 0 if webbrowser.open(url) else 1
                return
            for argv in agent.login_cmd(step) or []:
                rc = _connect_one(step, argv, log, deadline)
                if rc != 0:
                    break
        except Exception as e:  # CLI missing, pty refused: say so in the log rather than hang as "running"
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\ncould not run {step}: {type(e).__name__}: {e}\n")
        finally:
            doctor_gen["n"] += 1  # a check already running started before this sign-in: do not cache what it says
            doctor_cache["at"] = 0  # the next check asks the CLI again rather than answer from before the sign-in
            connects[step].update(running=False, rc=rc)

    try:  # anything failing between the claim and the worker would leave the step "already running" for good
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        threading.Thread(target=go, daemon=True).start()
    except Exception as e:
        connects[step].update(running=False, rc=-1)
        return False, f"could not start {step}: {type(e).__name__}: {e}"
    return True, ""


def _install_one(step, argv, log, deadline):
    """Run one command of an install -> (exit code, stopped at the deadline?), its output appended to the log as it comes (Windows too: no console
    window, so the page and Console see what went wrong). No terminal: stdin is empty, so an installer that stops to ask
    a question reads end-of-input and fails at once instead of waiting for an answer nobody can type, and in a session
    of its own it has no terminal to open either. Stopped at the deadline, and the log says why."""
    with open(log, "a", encoding="utf-8") as f:
        f.write("$ " + (subprocess.list2cmdline(argv) if WIN else shlex.join(argv)) + "\n")
        f.flush()
        how = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if WIN else {}
        if sys.platform != "win32":  # a process group of its own, which is what kill_tree stops off Windows
            how["start_new_session"] = True
        # started and registered through _launch, stopped and untracked through _reap, as the sign-ins are
        p = _launch(step, argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, **how)
        if p is None:
            f.write("\nnot started: Open Loops is closing\n")
            return -1, False
        late = []
        rc = _reap(step, p, deadline - time.time(), on_timeout=lambda: late.append(True))
        if late:
            f.write(f"\nstopped: the install did not finish within {INSTALL_TIMEOUT_S} seconds\n")
        return rc, bool(late)


def run_install(body):
    """Start the selected AI's installer in the background -> (started, error, HTTP code). Works for any AI; shares the
    sign-in steps' one-run-at-a-time claim, process tracking and log. Runs only what the page showed: the press sends the
    "agent" and "command_id" from the checklist row, and if either no longer matches (another tab changed the AI in
    Settings since) nothing runs. The run keeps its own agent and command, which the status reports while it runs."""
    from . import agent
    step, ic = agent.INSTALL_STEP, agent.install_cmd()
    if not ic:
        return False, "no installer known for " + agent.display_name(), 400
    if body.get("agent") != ic["agent"]:  # another tab chose a different AI in Settings since this row was shown
        return False, "changed", 409
    if body.get("command_id") != ic["id"]:  # same AI, other command: Open Loops was updated or moved since the page loaded
        return False, "updated", 409
    with connect_lock:  # check and claim in one go
        if quit_requested:
            return False, "Open Loops is closing", 400
        if (connects.get(step) or {}).get("running"):
            return False, "already running", 200
        connects[step] = {"running": True, "rc": None, "url": "", "started": datetime.now().isoformat(timespec="seconds"),
                          "why": "", "agent": ic["agent"], "command": ic["command"], "command_id": ic["id"],
                          "vendor": ic.get("vendor", "")}
    log = connect_log(step)

    def go():
        rc, deadline, why, kind = -1, time.time() + INSTALL_TIMEOUT_S, "", "download"
        script = Path(ic["script"])
        try:
            script.parent.mkdir(parents=True, exist_ok=True)
            script.unlink(missing_ok=True)  # never run a script left over from an earlier try
            for kind, argv in ic["steps"]:
                if kind == "install" and not (script.is_file() and script.stat().st_size):  # Windows has no test -s step
                    rc, why = 1, "vendor"  # the vendor's server sent an empty file
                    break
                if kind == "check":  # the installer exited 0: find the new CLI in its folders, PATH untouched for now
                    look = os.pathsep.join([os.environ.get("PATH", "")] + [str(d) for d in agent.install_dirs()])
                    argv = [shutil.which(argv[0], path=look) or argv[0]] + argv[1:]
                rc, late = _install_one(step, argv, log, deadline)
                if rc != 0:
                    why = "timeout" if late else download_why(argv, rc, log) if kind == "download" else kind
                    break
            else:  # every step worked, the CLI answered --version: only now its folder goes on this process's PATH
                add_install_dirs()
        except Exception as e:  # bash or PowerShell missing, say: in the log and as "start", rather than hang as "running"
            rc, why = -1, why or ("check" if kind == "check" else "start")  # no CLI to run after the install: "check"
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\ncould not run the installer: {type(e).__name__}: {e}\n")
        finally:
            try:
                script.unlink(missing_ok=True)
            except OSError:
                pass
            doctor_gen["n"] += 1  # a check already running started before this install: do not cache what it says
            doctor_cache["at"] = 0
            connects[step].update(running=False, rc=rc, why=why)

    try:  # as run_connect: anything failing between the claim and the worker would leave the install "running" for good
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        threading.Thread(target=go, daemon=True).start()
    except Exception as e:
        connects[step].update(running=False, rc=-1, why="start")
        return False, f"could not start the install: {type(e).__name__}: {e}", 500
    return True, "", 200


# curl's exit codes (curl -f): 22 = the server answered with an HTTP error (a vendor 404 / 500, not the user's
# internet); these = could not reach it at all (DNS, refused, timed out, TLS handshake, nothing received).
CURL_NETWORK = {5, 6, 7, 28, 35, 52, 56}
# Windows: Invoke-WebRequest's messages, as they land in the install log
PS_VENDOR = re.compile(r"\((?:4|5)\d\d\)|returned an error|The download was empty", re.I)
PS_NETWORK = re.compile(r"remote name could not be resolved|No such host is known|Unable to connect|actively refused|"
                        r"timed out|could not be established", re.I)


def download_why(argv, rc, log):
    """Why a download step failed -> "vendor" (their site answered with an error or an empty file), "network" (this
    computer could not reach it) or "download" (can't tell). The page's sentence depends on it (#25): a vendor 404 must
    not be blamed on the user's internet."""
    name = Path(argv[0]).name.lower() if argv else ""
    if name.startswith("curl"):
        return "vendor" if rc == 22 else "network" if rc in CURL_NETWORK else "download"
    if name == "test":  # test -s: the file arrived empty
        return "vendor"
    if name.startswith("powershell"):
        try:
            tail = log.read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError:
            tail = ""
        return "vendor" if PS_VENDOR.search(tail) else "network" if PS_NETWORK.search(tail) else "download"
    return "download"


# how an install ended (connects["install"]["why"]) -> the messages.py sentence the page shows
INSTALL_WHY = {"download": "install_download", "network": "install_network", "vendor": "install_vendor",
               "check": "install_check", "install": "install_error", "timeout": "install_timeout", "start": "install_start"}


def connect_status(step):
    c = dict(connects.get(step) or {"running": False, "rc": None, "url": "", "started": None})
    log = connect_log(step)
    try:
        lines = [x.strip() for x in log.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:  # not written yet, or not writable at all
        lines = []
    c["last"] = next((x for x in reversed(lines) if x), "")[:300]
    c["step"] = step
    from . import agent
    if step == agent.INSTALL_STEP:
        ran = c.get("agent")  # the AI the last run installed, if there was one
        if not c.get("running"):  # idle: what the button would run now, so the page can show it before the press.
            ic = agent.install_cmd() or {}  # A running install keeps reporting its own agent and command instead.
            c.update(agent=ic.get("agent", ""), command=ic.get("command", ""), command_id=ic.get("id", ""))
        if c.get("why"):  # the page shows this sentence, never the installer's raw last line ("last", for the Console)
            n = INSTALL_TIMEOUT_S  # the limit actually in force, in the unit a person would say it
            limit = f"{n // 60} minutes" if n >= 120 and n % 60 == 0 else "1 minute" if n == 60 else f"{n} seconds"
            vendor = c.get("vendor") or (agent.install_cmd(ran or c.get("agent")) or {}).get("vendor", "")
            c["said"] = messages.say(INSTALL_WHY.get(c["why"], "install_error"), ai=agent.display_name(ran or c["agent"]),
                                     limit=limit, vendor=vendor or "the download site")
    return c


def index_bytes(table=None):
    """index.html with the page's copy of messages.py filled in, for this platform (escaped for an inline <script>)."""
    return INDEX.read_bytes().replace(b"/*OL_MESSAGES*/{}", messages.page_json(WIN, table).encode("utf-8"), 1)


class H(BaseHTTPRequestHandler):
    server_version = "OpenLoops/1"  # sent as the Server: header - how the launcher recognises itself

    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        global last_seen
        last_seen = time.time()
        pid = self.headers.get("X-OL-Page")
        if pid:
            pages[pid] = last_seen
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            # the page's copy of messages.py, for this platform: it can still say "not running" once the server is gone
            b = index_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")  # a restored or cached page would keep an older copy of the wording
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/api/state":
            from . import standing
            s = load()
            s = dict(s)
            vault_loops, dirty = standing.as_loops(s)
            if dirty:
                save(s)
            s["loops"] = list(s.get("loops") or []) + vault_loops
            self._json({"state": s, "jobs": jobs, "today": date.today().isoformat(),
                        "pages": len(pages), "quitting": quit_requested})  # who is holding the server up
        elif self.path == "/api/config":
            self._json({"config": cfg(), "voice": read_json(VOICEF), "people_suggested": read_json(PEOPLEF)})
        elif self.path == "/api/diag":  # what the Console's "Copy all" pastes: enough to debug from a screenshot-free report
            c = cfg()
            stamp = ROOT / "INSTALLED.txt"
            dl = ROOT / "state" / "logs" / "doctor-last.log"
            le = ROOT / "state" / "logs" / "launchd.err.log"  # Mac: why the weekday morning refresh did not start (#24)
            self._json({"app": "openloops",   # identity: install.sh only asks a server to quit if this is here and root matches
                        "python": sys.version.split()[0], "platform": sys.platform, "port": PORT, "root": str(ROOT),
                        "build": stamp.read_text(encoding="utf-8").strip() if stamp.exists() else "checkout",
                        "up_since": STARTED, "agent": c.get("agent") or "claude", "model": c.get("model") or "",
                        "pages": len(pages), "jobs": {k: {"running": j["running"], "rc": j.get("rc"), "tail": (j.get("log") or "")[-1200:]} for k, j in jobs.items()},
                        "doctor": doctor_cache["result"], "doctor_log": dl.read_text(encoding="utf-8", errors="replace")[-2000:] if dl.exists() else "",
                        "launchd_err_log": le.read_text(encoding="utf-8", errors="replace")[-2000:] if le.exists() else ""})
        elif self.path == "/api/schedule/status":  # the morning refresh's last start, from its logs only (#24)
            from . import doctor
            self._json({"step": doctor.schedule_step() if MAC else None})
        elif self.path.split("?")[0] == "/api/daylog":
            from . import daylog
            q = self._query()
            self._json(daylog.status(q.get("date") or None))
        elif self.path.split("?")[0] == "/api/daylog/page":
            # served through the app: a file:// link from an http:// page is blocked by browsers
            from . import daylog
            q = self._query()
            day = q.get("date") or date.today().isoformat()
            pg = daylog.page_path(day)
            if not pg.exists():
                return self._json({"error": f"no day log for {day} yet - press Write it up"}, 404)
            b = pg.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path.split("?")[0] == "/api/standing":
            from . import standing
            self._json(standing.status(self._query().get("path") or None))
        elif self.path.split("?")[0].startswith("/api/connect/"):
            from . import agent
            step = self.path.split("?")[0].rsplit("/", 1)[1]
            if step not in agent.CONNECT_STEPS + (agent.INSTALL_STEP,):
                return self._json({"error": "unknown setup step"}, 404)
            self._json(connect_status(step))
        elif self.path == "/api/roadmap":
            from . import roadmap
            st, conf = roadmap.load(), roadmap.configured()
            # the board may be known by link from Settings before it has ever been read
            embed = roadmap.embed_url(st["board"].get("url") or conf["board"], st["board"].get("frame_id"))
            self._json({"store": st, "configured": conf, "embed": embed,
                        "miro": bool((doctor_cache.get("result") or {}).get("miro"))})
        else:
            self._json({"error": "not found"}, 404)

    def _query(self):
        from urllib.parse import parse_qs, urlsplit
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def do_POST(self):
        global doctor_cache
        # Every POST changes something (a job, a sign-in, a file), and any web page open in the browser can send one
        # to localhost. Browsers always say where a POST comes from (Origin), so one from anywhere but this page is
        # refused. The page's own requests and its close-tab beacon carry this page's origin; `--stop`, the tests
        # and other local scripts send no Origin at all and are let through.
        origin = self.headers.get("Origin")
        if origin is not None and origin not in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"):
            return self._json({"error": "refused: request from another site"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/bye":  # a page closed (or reloaded: its successor says hello within a second)
            global bye_at
            pages.pop(str(body.get("page") or ""), None)
            bye_at = time.time()
            return self._json({"ok": True, "pages": len(pages)})
        if self.path == "/api/quit":  # Settings button or `python -m openloops.app --stop [--now]`
            global quit_requested, quit_now
            quit_requested = True
            stop_connects()  # a sign-in still waiting in the browser is not worth holding a quit for
            busy = [k for k, j in jobs.items() if j["running"]]
            if body.get("now") and busy:  # `npm run dev` restarting a dev session: a half-done refresh is not worth waiting for
                for k in busy:
                    if k in procs:
                        kill_tree(procs[k])
                self._json({"ok": True, "after_jobs": [], "cut_short": busy})
                quit_now = True  # only once the answer is out: the reaper stops the server the moment it sees this
                return
            return self._json({"ok": True, "after_jobs": busy})
        if self.path == "/api/refresh":
            return self._json({"started": run_job("refresh", ["--slack-only"] if body.get("slack_only") else None)})
        if self.path == "/api/daylog":
            return self._json({"started": run_job("daylog", ["--digest-only"] if body.get("digest_only") else None)})
        if self.path == "/api/roadmap":
            from . import roadmap
            mode = body.get("mode")
            if mode == "save":
                # staging rows live in state/roadmap.json, never in state.json (refresh rewrites that)
                roadmap.stage(rows=body.get("rows"), pasted=body.get("pasted"))
                return self._json({"ok": True})
            if mode not in roadmap.MODES:
                return self._json({"ok": False, "error": "unknown mode"}, 400)
            if not roadmap.configured()["ok"]:
                return self._json({"ok": False, "error": "set the board and frame in Settings first"}, 400)
            if mode == "parse" and body.get("pasted") is not None:
                roadmap.stage(pasted=body.get("pasted"))
            if mode == "build" and not body.get("confirm"):
                # adds cards to a board other people share - the page arms this for a few seconds after a preview
                return self._json({"ok": False, "error": "confirm required"}, 400)
            return self._json({"started": run_job("roadmap", [mode] + (["--confirm"] if mode == "build" else []))})
        if self.path == "/api/standing/create":
            from . import standing
            try:
                p = standing.create_starter(body.get("path") or None)
            except FileExistsError as e:
                return self._json({"ok": False, "error": messages.say("standing_exists"), "detail": str(e)}, 400)
            except ValueError as e:  # no path set: standing.py's own sentence (messages "standing_no_path")
                return self._json({"ok": False, "error": str(e)}, 400)
            except OSError as e:  # the OS's reason is developer detail
                return self._json({"ok": False, "error": messages.say("standing_write"), "detail": str(e)}, 400)
            return self._json({"ok": True, "path": str(p)})
        if self.path == "/api/doctor":
            import time as _t
            if body.get("force") or _t.time() - doctor_cache["at"] > 55:
                gen = doctor_gen["n"]
                # --recheck: a press of Check again (or a finished setup step) asks Codex afresh instead of reusing
                # its last answer about Gmail / Slack; Claude and Grok read their CLIs every time anyway
                args = ([sys.executable, "-m", "openloops.doctor"] + (["--detect"] if body.get("detect") else [])
                        + (["--recheck"] if body.get("force") else []))
                for attempt in (1, 2):  # a check that produced nothing gets one quiet retry before anyone hears about it
                    try:
                        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                           stdin=subprocess.DEVNULL, timeout=240)
                        out, err, rc = r.stdout, r.stderr, r.returncode
                    except Exception as e:  # timeout, or the interpreter could not be started
                        out, err, rc = "", f"{type(e).__name__}: {e}", -1
                    if out.strip():
                        break
                    _t.sleep(2)
                try:
                    (ROOT / "state" / "logs").mkdir(parents=True, exist_ok=True)
                    (ROOT / "state" / "logs" / "doctor-last.log").write_text(
                        chr(10).join(["$ " + " ".join(args), "rc=" + str(rc), "--- stdout ---", out, "--- stderr ---", err]), encoding="utf-8")
                except OSError:
                    pass
                try:
                    res = json.loads(out.strip().splitlines()[-1])
                except Exception:
                    why = (out + err).strip()[-300:] or f"the check produced no output (exit code {rc})"
                    # not a connection problem: the checker itself did not answer. The page keeps its last good answer.
                    res = {"all_ok": False, "error": why, "steps": [], "rc": rc}
                if gen == doctor_gen["n"]:  # a check that started before a sign-in finished answers, but is not kept
                    doctor_cache = {"at": _t.time(), "result": res}
                return self._json(res)
            return self._json(doctor_cache["result"])
        if self.path == "/api/connect/install":  # the Install button: {"agent", "command_id"} as the row showed them
            from . import agent
            started, why, code = run_install(body)
            busy = (connects.get(agent.INSTALL_STEP) or {}).get("agent") or agent.name()
            said = (messages.say("ai_changed") if why == "changed" else messages.say("app_updated") if why == "updated"
                    else messages.say("install_busy", ai=agent.display_name(busy)) if why == "already running" else "")
            return self._json({"started": started, **({"error": why} if why else {}), **({"said": said} if said else {})}, code)
        if self.path.startswith("/api/connect/"):  # a setup button: sign in, install Slack, connect a source
            step = self.path.rsplit("/", 1)[1]
            started, why = run_connect(step)
            said = messages.say("connect_busy") if why == "already running" else ""
            return self._json({"started": started, **({"error": why} if why else {}), **({"said": said} if said else {})},
                              200 if started or why == "already running" else 400)
        if self.path == "/api/open-claude":  # the fallback: a terminal running the agent, for anything the buttons can't do
            # opens a terminal running the configured agent so the user can sign in / connect
            from . import agent
            cli, title = agent.cli(), agent.display_name()
            if WIN:
                subprocess.Popen(f'start "{title}" cmd /k "{cli}"', shell=True, cwd=ROOT)
            elif MAC:
                script = f'cd {shlex.quote(str(ROOT))} && {shlex.quote(cli)}'
                subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                                  "-e", 'tell application "Terminal" to activate'])
            else:  # Linux desktop - best effort, terminal emulator varies
                subprocess.Popen(["x-terminal-emulator", "-e", cli], cwd=ROOT)
            return self._json({"ok": True})
        if self.path == "/api/schedule":
            t = str(body.get("time", "")).strip()
            if not (len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit()):
                return self._json({"ok": False, "error": "time must be HH:MM"}, 400)
            if WIN:
                r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                    str(ROOT / "scripts" / "register-task.ps1"), "-At", t],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
            else:
                r = subprocess.run(["bash", str(ROOT / "scripts" / "register-task.sh"), "--at", t],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and update_json(CONFIG, lambda c: c.update(refresh_time=t)) is False:
                return self._json({"ok": False, "error": "config.json could not be read, so the new time was not saved there"}, 500)
            return self._json({"ok": r.returncode == 0, "out": (r.stdout + r.stderr)[-500:]})
        if self.path == "/api/voice":
            return self._json({"started": run_job("voice")})
        if self.path == "/api/people":
            return self._json({"started": run_job("people")})
        if self.path == "/api/config":
            if "pinned_links" in body:  # http(s) only, one entry per url, label trimmed
                seen, clean = set(), []
                for p in body.get("pinned_links") or []:
                    u = str((p or {}).get("url") or "").strip()
                    if not u.lower().startswith(("http://", "https://")) or u in seen:
                        continue
                    seen.add(u)
                    clean.append({"url": u, "label": str((p or {}).get("label") or "").strip()[:60]})
                body["pinned_links"] = clean
            # applied to config.json as it is now, under the lock doctor.py takes too (its own process)
            if update_json(CONFIG, lambda c: c.update({k: v for k, v in body.items() if k in EDITABLE})) is False:
                return self._json({"ok": False, "error": messages.say("config_unreadable"),
                                   "detail": "config.json could not be read; nothing saved (fix or delete it)"}, 500)
            return self._json({"ok": True})
        if self.path == "/api/reset":
            # "Start over": back to the state a brand-new user sees, keeping only name/domains/tone settings.
            # Config first: if it cannot be read, refuse before deleting anything, so an unreadable
            # config.json never leaves the user with no list AND stale people/Slack id (Codex review, #21).
            if update_json(CONFIG, lambda c: c.update(people={}, voice_sample_people=[], slack_self_id="")) is False:
                return self._json({"ok": False, "error": messages.say("config_unreadable"),
                                   "detail": "config.json could not be read; nothing was reset (fix or delete it)"}, 500)
            for f in (STATE, VOICEF, PEOPLEF):
                if f.exists():
                    f.unlink()
            STATE.write_text(fresh_state(), encoding="utf-8")
            doctor_gen["n"] += 1
            doctor_cache = {"at": 0, "result": None}
            return self._json({"ok": True})
        if self.path == "/api/chase":
            return self._json({"started": run_job("chase", [body["id"]])})
        if self.path == "/api/action":
            s = load()
            act = body.get("action")
            vid = str(body.get("id") or "")
            if vid.startswith("vault-"):
                from . import standing
                item_id = vid.split("-", 1)[1].upper()
                if act != "done":
                    return self._json({"error": "vault items are closed with done only"}, 400)
                closure = (body.get("closure") or "").strip()
                try:
                    closed = standing.close_item(item_id, closure)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                (ROOT / "state").mkdir(parents=True, exist_ok=True)
                (ROOT / "state" / "standing-close.json").write_text(
                    json.dumps({"id": item_id, "closure": closure,
                                "project": closed["project"], "action": closed["action"]},
                               ensure_ascii=False), encoding="utf-8")
                started = run_job("standing")
                return self._json({"ok": True, "id": vid, "started": started})
            if act == "add":
                owner = (body.get("owner") or "").strip()[:80]
                ask = (body.get("ask") or "").strip()[:300]
                notes = (body.get("notes") or "").strip()[:2000]
                if not ask:
                    return self._json({"error": "need something to do"}, 400)
                email = None
                for name, p in (cfg().get("people") or {}).items():
                    if name.lower() == owner.lower():
                        owner = name
                        email = (p or {}).get("email")
                        break
                slug = lambda t: re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:32] or "x"
                now = datetime.now().astimezone()
                lid = f"note-{slug(owner or 'me')}-{slug(ask)}-{now.strftime('%Y%m%d%H%M%S')}"
                s["loops"].insert(0, {
                    "id": lid, "owner": owner, "owner_email": email, "ask": ask,
                    "channel": "note", "thread": None, "link": None,
                    "asked_at": now.isoformat(timespec="minutes"),
                    "status": "needs_me", "inbound": True, "manual": True,
                    "notes": notes, "links": [], "last_reply_at": None, "reply_snippet": None,
                    "chases": 0, "snooze_until": None,
                })
                save(s)
                return self._json({"ok": True, "id": lid})
            for lp in s["loops"]:
                if lp["id"] == body["id"]:
                    if act == "done":
                        lp["status"] = "done"
                        lp["closed_at"] = datetime.now().isoformat(timespec="minutes")
                    elif act == "reopen":
                        # typed reminders belong in Needs me, not Waiting on them
                        lp["status"] = "needs_me" if lp.get("channel") == "note" or lp.get("manual") else "waiting"
                        lp["snooze_until"] = None
                    elif act == "snooze":
                        try:
                            lp["snooze_until"] = norm_date(body.get("until"))
                        except ValueError as e:
                            return self._json({"error": str(e)}, 400)
                    elif act == "unsnooze":
                        lp["snooze_until"] = None
                    elif act == "priority":
                        pr = body.get("priority")
                        if pr not in ("high", "normal", "low"):
                            return self._json({"error": "priority is high, normal or low"}, 400)
                        lp["priority"], lp["priority_by"] = pr, "you"
                    elif act == "auto_off":
                        lp["auto_off"] = True
                    elif act == "auto_on":
                        lp["auto_off"] = False
                    elif act == "note":
                        lp["notes"] = (body.get("notes") or "").strip()[:2000]
                    elif act == "add_link":
                        url = (body.get("url") or "").strip()
                        if not url.startswith("http"):
                            return self._json({"error": "link must start with http"}, 400)
                        links = lp.setdefault("links", [])
                        if not any(x.get("url") == url for x in links):
                            links.append({"url": url, "label": (body.get("label") or "").strip()[:60]})
                    elif act == "drop_link":
                        lp["links"] = [x for x in lp.get("links") or [] if x.get("url") != body.get("url")]
            save(s)
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)


def port_busy(port=None):
    with socket.socket() as sk:
        sk.settimeout(0.3)  # loopback answers instantly when something listens; Windows takes ~2 s to give up otherwise
        return sk.connect_ex(("127.0.0.1", port or PORT)) == 0


def already_running(port=None):
    """True only if the thing listening on the port is Open Loops, not some other local server
    (e.g. a stray `python -m http.server 8765`, which would otherwise show a directory listing)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port or PORT}/", timeout=2) as r:
            return r.headers.get("Server", "").startswith("OpenLoops")
    except Exception:
        return False


def pick_port(start=None):
    """Scan from the preferred port. Returns (port, running):
    running=True  -> Open Loops already answers there (launched earlier), just open the page;
    running=False -> the port is free, start there.
    Ports held by other programs are skipped, so the app is never confused with a stray server."""
    start = start or PORT
    stop = min(start + 20, 65536)  # never past the last port: 65535 + 1 would crash the scan
    for p in range(start, stop):
        if not port_busy(p):
            return p, False
        if already_running(p):
            return p, True
    raise SystemExit(messages.say("no_free_port") + f"\n(detail: no free port between {start} and {stop - 1}; set OPENLOOPS_PORT)")


def stop_running(now=False):
    """`python -m openloops.app --stop`: ask the running instance (if any) to quit. Exit 0 if one was told.
    `--now` also cuts a running job short instead of waiting for it (what `npm run dev` uses to restart)."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(20) as ex:  # probe the whole range at once: closed ports take the full timeout each
        span = range(PORT, min(PORT + 20, 65536))
        busy = [p for p, b in zip(span, ex.map(port_busy, span)) if b]
    for p in busy:
        if already_running(p):
            req = urllib.request.Request(f"http://127.0.0.1:{p}/api/quit", data=json.dumps({"now": now}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                out = json.loads(r.read() or b"{}")
            after, cut = out.get("after_jobs") or [], out.get("cut_short") or []
            print(f"Open Loops on port {p}: stopping" + (f" once {', '.join(after)} finishes" if after else "")
                  + (f" ({', '.join(cut)} cut short)" if cut else ""))
            return 0
    print("Open Loops is not running")
    return 1


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer minus the socket.getfqdn() that HTTPServer.server_bind does: a reverse lookup of 127.0.0.1
    that some Macs (GitHub's macOS runners, for one) take 35 s to answer, before the app can print its address."""
    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


if __name__ == "__main__":
    if "--stop" in sys.argv:
        sys.exit(stop_running(now="--now" in sys.argv))
    PORT, running = pick_port()
    url = f"http://localhost:{PORT}"
    def open_browser():
        # os.startfile uses the Windows default-browser association, which works whether or not
        # a browser is already running; webbrowser.open is the cross-platform fallback.
        try:
            import os
            os.startfile(url)
        except Exception:
            webbrowser.open(url)

    if running:  # launched earlier today - just open the page
        print("Open Loops (already running) ->", url)
        if "--no-browser" not in sys.argv:
            open_browser()
        sys.exit(0)
    add_install_dirs()  # a CLI installed after this terminal (or Finder session) started is still found
    srv = Server(("127.0.0.1", PORT), H)
    print("Open Loops ->", url)
    try:  # Codex run folders a killed job left behind, with their link to the user's sign-in (#42)
        from .agent import codex_sweep
        codex_sweep()
    except Exception:
        pass
    if PORT != PREFERRED:
        print(messages.say("port_moved", port=PORT))
        print(f"(detail: port {PREFERRED} was taken by another program; use --port or OPENLOOPS_PORT to choose)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, open_browser).start()

    def reaper():
        while True:
            time.sleep(1)
            now = time.time()
            for pid, seen in list(pages.items()):
                if now - seen > PAGE_STALE_S:
                    pages.pop(pid, None)
            if any(j["running"] for j in jobs.values()) and not quit_now:
                continue  # never pull the rug from under a refresh/chase; check again once it is done
            if any(c.get("running") for c in connects.values()) and not quit_requested:
                continue  # a sign-in outlives its tab: the browser may still send Allow, up to the 5-minute deadline
            no_pages = bye_at and not pages and now - bye_at > PAGE_GRACE_S and now - last_seen > PAGE_GRACE_S
            if quit_requested or no_pages or now - last_seen > IDLE_EXIT_S:
                srv.shutdown()
                return

    threading.Thread(target=reaper, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_connects()
