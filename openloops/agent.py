"""Which AI runs the headless jobs. config.json "agent": "claude" (default), "grok" or "codex".

The job scripts (refresh/chase/voice/people) name tools logically - "slack.read_channel",
"gmail.search_threads" - and call run(prompt, tools). This module maps those names to the
agent's own tool ids and invokes the right CLI.

Grok: Slack is opt-in (config.json "use_slack"). Off, the Slack plugin is not started and
doctor does not probe it. Vercel is never started. Jobs pass --effort low because the Grok
CLI defaults to xhigh. Gmail is the bundled gmail_mcp.py server (not Claude's connector).

Codex (OpenAI's CLI, ChatGPT sign-in): Gmail and Slack are ChatGPT connectors that ride on the ChatGPT account
(server "codex_apps", tools gmail.<tool> / slack.slack_<tool>), not MCP servers on this computer. Each run gets an
allow-list in its config (every connector off, the job's own tools on; see the Codex section below), a read-only
sandbox and no shell tool, and fails if it calls anything else. Jobs run under a job-local CODEX_HOME per ChatGPT
account (state/codex-home/<hash>) so the user's plugins, skills, memories and AGENTS.md are not loaded.
"""
import hashlib, json, os, re, shlex, shutil, subprocess, sys
from pathlib import Path

from .paths import ROOT
WIN = sys.platform == "win32"
_GROK_JOB_HOME = ROOT / "state" / "grok-home"
_CODEX_JOBS = ROOT / "state" / "codex-home"  # one job home per ChatGPT account below it

# logical "service.tool" -> per-agent fully-qualified tool id.
# Claude can reach Slack two ways: the Slack *plugin* (plugin:slack:slack, default) or the
# claude.ai Slack *connector*. Same tools, different prefix; the wrong one makes a refresh
# silently find nothing. doctor.py detects which is connected and stores config "slack_source".
# Miro (Roadmap card) is the official Miro plugin; jobs allow the whole server ("miro.*").
_CLAUDE_SLACK = {"plugin": "mcp__plugin_slack_slack__slack_{}", "connector": "mcp__claude_ai_Slack__slack_{}"}
# Miro likewise: the Miro plugin (plugin:miro:miro) or the claude.ai Miro connector. Jobs allow the
# whole server either way; doctor.py stores which one is connected as config "miro_source".
_CLAUDE_MIRO = {"plugin": "mcp__plugin_miro_miro", "connector": "mcp__claude_ai_Miro",
                "server": "mcp__miro"}
# "server" = a user-added HTTP server literally named "miro":
#   claude mcp add --scope user --transport http miro https://mcp.miro.com/
# (--transport http is required: `claude mcp add` defaults to stdio and would try to run the URL.)
_FMT = {
    "claude": {"slack": _CLAUDE_SLACK["plugin"], "gmail": "mcp__claude_ai_Gmail__{}", "miro": _CLAUDE_MIRO["plugin"]},
    "grok":   {"slack": "slack__slack_{}",       "gmail": "gmail__{}",                "miro": "miro"},
    # ChatGPT connectors, as `codex exec --json` names them (verified in #18, Codex CLI 0.156.1). Miro has no
    # connector: "miro" is a user-added MCP server of that name, only there if the user set one up (see _codex_miro_toml).
    "codex":  {"slack": "slack.slack_{}",        "gmail": "gmail.{}",                 "miro": "miro"},
}
# The logical names the jobs pass where the Gmail connector's tool is called something else. gmail.reply is
# send_email with reply_message_id (see _CODEX_HINT), used only when a send switch in Settings is ticked (chase.py).
_CODEX_RENAME = {"gmail.search_threads": "search_emails", "gmail.get_thread": "read_email_thread",
                 "gmail.reply": "send_email"}
# Said next to a tool in the Codex preamble. Both take reply_message_id (seen in Codex's cached tool schemas, 0.156.1),
# which is what keeps a chase in the original email thread.
_CODEX_HINT = {"gmail.reply": "pass reply_message_id so it answers in the thread",
               "gmail.create_draft": "pass reply_message_id so the draft answers in the thread"}

# Grok CLI defaults to xhigh; Open Loops jobs are unattended JSON, not coding.
_GROK_DISALLOWED = (
    "Agent,run_terminal_cmd,search_replace,read_file,todo_write,write,"
    "grep,list_dir,web_search,web_fetch"
)


def _cfg():
    from .store import load_cfg  # config.json over config.template.json, so "model" has its default
    return load_cfg()


def name():
    return (_cfg().get("agent") or "claude").strip().lower()


def slack_source():
    """Claude only: "plugin" (default) or "connector" - see _CLAUDE_SLACK."""
    v = (_cfg().get("slack_source") or "plugin").strip().lower()
    return v if v in _CLAUDE_SLACK else "plugin"


def miro_source():
    """Claude only: "plugin" (default), "connector" or "server" - see _CLAUDE_MIRO."""
    v = (_cfg().get("miro_source") or "plugin").strip().lower()
    return v if v in _CLAUDE_MIRO else "plugin"


_exists = os.path.exists  # a seam for the tests: where cli() looks


def display_name(agent=None):
    """"Claude" / "Grok" for the selected agent, or for the one named."""
    n = (agent or name()).strip().lower()
    return {"claude": "Claude", "grok": "Grok", "codex": "Codex"}.get(n, n.capitalize())


def cli():
    """Path or command for the agent's CLI (also used to open its sign-in terminal)."""
    if name() == "grok":
        # grok installs to ~/.grok/bin, which Finder/launchd PATHs usually lack
        return shutil.which("grok") or str(Path.home() / ".grok" / "bin" / "grok")
    if name() == "codex":
        # Homebrew, the install script (~/.local/bin, Windows %LOCALAPPDATA%), Codex's own folder or the desktop app:
        # a Finder- or launchd-started app has none of them on PATH
        home = Path.home()
        spots = [Path("/opt/homebrew/bin/codex"), home / ".local" / "bin" / "codex", home / ".codex" / "bin" / "codex",
                 Path("/Applications/Codex.app/Contents/Resources/codex")]  # the desktop app alone ships one too
        if WIN and os.environ.get("LOCALAPPDATA"):
            spots.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
        return shutil.which("codex") or next((str(p) for p in spots if _exists(p)), "codex")
    return "claude"


def use_slack():
    """Grok only: Slack plugin is off unless the user ticks Use Slack in Settings."""
    return bool(_cfg().get("use_slack"))


def slack_enabled():
    """Whether jobs should pass Slack tools. Claude and Codex: yes (still gated on slack_self_id); Grok: use_slack."""
    return True if name() != "grok" else use_slack()


def grok_job_env():
    """Env for headless Grok: skip Vercel always, skip Slack unless use_slack.

    Grok loads Slack/Vercel from Claude plugins; project .grok/config.toml cannot
    disable those. disabled_mcp_servers in a job-local GROK_HOME does.
    """
    home = _GROK_JOB_HOME
    home.mkdir(parents=True, exist_ok=True)
    src = Path.home() / ".grok"
    for name_ in ("auth.json", "trusted_folders.toml", "trusted_folders.toml.lock"):
        origin, dest = src / name_, home / name_
        if origin.exists() or origin.is_symlink():
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            dest.symlink_to(origin)
    disabled = ["vercel"] if use_slack() else ["vercel", "slack"]
    (home / "config.toml").write_text(
        "# Generated by Open Loops for headless Grok jobs. Do not edit.\n"
        "disabled_mcp_servers = " + json.dumps(disabled) + "\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["GROK_HOME"] = str(home)
    env["GROK_CLAUDE_AGENTS_ENABLED"] = "0"
    env["GROK_CLAUDE_SKILLS_ENABLED"] = "0"
    env["GROK_CURSOR_SKILLS_ENABLED"] = "0"
    env["GROK_CURSOR_AGENTS_ENABLED"] = "0"
    return env


# ---- Codex (OpenAI's CLI, signed in with ChatGPT) ----
# The boundary, measured on 2026-09-23 against Codex CLI 0.156.1 with real runs (PR #40): in `codex exec`,
#   [apps._default] enabled = false                 switches every ChatGPT connector off,
#   [apps.<connector id>] enabled = true            switches one back on,
#   [apps.<connector id>.tools.<tool>] enabled = false   removes one tool from the session altogether,
# so a job gets exactly the connector tools it lists and the rest are not there to call. The id is the connector's
# own (connector_... for Gmail, asdk_app_... for Slack), read from Codex's cached tool list; "gmail" is not an app id,
# which is why #18's apps.gmail.* keys did nothing. Every approval_mode form ("approve", per app, per tool, _default,
# on the command line) was ignored: a draft was still created. Tools the connector marks destructive (delete_emails)
# were refused by Codex itself. As a second line, a run that calls any tool off the job's list fails and saves nothing.
# The allow-list must sit in config.toml itself: layered from a -p profile, Codex took [apps._default] but not the
# per-app tables (measured), so each run gets a short-lived home of its own (see codex_job_env).
CODEX_REFUSE = {  # the plain sentence a job prints (and the checklist shows) when Open Loops will not run Codex
    "keyring": ("Codex keeps your sign-in in {store}, which Open Loops can't share with its own settings yet. "
                "Run codex logout, then codex login again with file storage (see INSTALL.md, Codex (ChatGPT))."),
    "signin": "Codex isn't signed in with a ChatGPT account. Press Sign in on the connection checklist.",
    "link": ("Open Loops couldn't link Codex's sign-in into its own settings folder, so it didn't run Codex. "
             "Check that the Open Loops folder isn't read-only, then press Check again."),
    "cold": ("Codex is still getting ready (loading your ChatGPT connections), so Open Loops didn't run it. "
             "Press Check again on the connection checklist in a minute."),
    "limit": ("Your ChatGPT plan's Codex allowance is used up for now, so Open Loops couldn't run Codex. "
              "It comes back by itself, usually within a few hours."),
    "expired": "Your ChatGPT sign-in has run out, so Open Loops couldn't run Codex. Press Sign in on the connection checklist.",
    "failed": "Codex stopped with an error before it could start this job, so nothing was saved. Try again in a minute.",
    "unlisted": "Codex used a tool this job did not allow, so nothing was saved.",
    "notools": "Codex couldn't reach its Gmail or Slack tools this time, so nothing was saved. Try again in a minute.",
    "stale": ("Codex couldn't refresh its list of your ChatGPT connections (it is more than a day old), so Open Loops "
              "didn't run it. Press Check again on the connection checklist in a minute."),
    "nosources": ("Neither Gmail nor Slack is connected in this ChatGPT account, so there was nothing to read. "
                  "Connect one on chatgpt.com/apps, then press Check again."),
    "timeout": "Codex took longer than {limit}, so Open Loops stopped it and saved nothing.",
    "start": "Open Loops couldn't start Codex. Press Check again on the connection checklist; it offers Install Codex if it's missing.",
}
_CODEX_SAFE_BUILTINS = {"list_mcp_resources", "list_mcp_resource_templates"}  # Codex's own listing tools: harmless
# Connector tools that only read. A run is retried (see codex_run) only when its job lists nothing else, and never
# after a call to anything outside this set, so a retry can never repeat a draft, a send or any other change.
CODEX_READ_TOOLS = {"gmail.get_profile", "gmail.search_emails", "gmail.search_email_ids", "gmail.read_email",
                    "gmail.read_email_thread", "gmail.batch_read_email", "gmail.batch_read_email_threads",
                    "gmail.list_labels", "gmail.list_drafts", "slack.slack_read_channel", "slack.slack_read_thread",
                    "slack.slack_search_public_and_private", "slack.slack_search_users", "slack.slack_read_user_profile",
                    "slack.slack_list_user_channels"}
_CODEX_WROTE_RE = re.compile(r"^(?:DRAFT_CREATED|SENT):", re.M)  # a chase's own success markers
_CODEX_SKIP = {"gmail": "Gmail is not connected in this ChatGPT account; skip email.",
               "slack": "Slack is not connected in this ChatGPT account; skip Slack."}


_LIMIT_RE = re.compile(r"usage limit|rate limit|too many requests|\b429\b|quota", re.I)
_EXPIRED_RE = re.compile(r"\b401\b|unauthori[sz]ed|refresh token|token (?:is )?expired|sign in again|log in again|login again", re.I)
SNAPSHOT_MAX_AGE_S = 24 * 3600  # an older connector list is refreshed by a warm-up before a job runs on it


def codex_failure(rc, errors, codex_stderr):
    """Why a finished `codex exec` failed -> "timeout" | "limit" | "expired" | "failed" | "" (it did not). One order
    for every caller (jobs, the warm-up, doctor.py's probe): a timeout, then a spent allowance, then a sign-in that ran
    out, then any other non-zero exit. Read from Codex's own error events and stderr, never from the model's text."""
    said = "\n".join(errors or []) + "\n" + (codex_stderr or "")
    if rc == 124:
        return "timeout"
    if _LIMIT_RE.search(said):
        return "limit"
    if _EXPIRED_RE.search(said):
        return "expired"
    return "failed" if rc != 0 else ""


class CodexNotReady(Exception):
    """Open Loops will not run Codex: .why is a key of CODEX_REFUSE."""
    def __init__(self, why):
        super().__init__(why)
        self.why = why


def codex_user_home():
    """The user's own Codex folder: $CODEX_HOME if set (and not one of our job homes), else ~/.codex."""
    h = os.environ.get("CODEX_HOME")
    if h and not os.path.realpath(h).startswith(os.path.realpath(_CODEX_JOBS) + os.sep):
        return Path(h)
    return Path.home() / ".codex"


def codex_auth():
    """What the user's auth.json says -> {"mode": "chatgpt" | "apikey" | "", "email", "account", "sig"}. mode "" = no
    file (signed out, or the sign-in is kept in the system keychain). "account" is a short hash of the ChatGPT account
    id: it names the job home and tells doctor.py when the account changed. The email comes from the ID token's claims
    (decoded, not verified: it is only shown on the checklist). "sig" changes whenever the file does. No token leaves."""
    import base64
    f = codex_user_home() / "auth.json"
    out = {"mode": "", "email": "", "account": "", "sig": ""}
    try:
        st = f.stat()
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return out
    out["sig"] = f"{st.st_mtime_ns}:{st.st_size}"
    tokens = data.get("tokens") or {}
    mode = str(data.get("auth_mode") or "").strip().lower()
    if not mode:  # older files carry no auth_mode: tokens mean ChatGPT, a key alone means API key
        mode = "chatgpt" if tokens else "apikey" if data.get("OPENAI_API_KEY") else ""
    out["mode"] = mode
    claims = {}
    try:
        payload = str(tokens.get("id_token") or "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        pass
    out["email"] = str(claims.get("email") or (claims.get("https://api.openai.com/profile") or {}).get("email") or "")
    acct = str(tokens.get("account_id") or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id") or "")
    out["account"] = hashlib.sha256(acct.encode("utf-8")).hexdigest()[:16] if acct else ""
    return out


def codex_keyring_store():
    """What the system keychain is called here, for CODEX_REFUSE["keyring"]."""
    return "the Mac keychain" if sys.platform == "darwin" else "Windows Credential Manager" if WIN else "the system keyring"


def codex_home(account=None):
    """The job home for one ChatGPT account: state/codex-home/<hash>/. Switching account switches folder, so nothing
    (links, connector list, anything Codex writes) is ever shared between two accounts."""
    return _CODEX_JOBS / (account if account is not None else codex_auth()["account"])


def _codex_miro_toml():
    """The user's own [mcp_servers.miro] tables from their Codex config.toml, copied verbatim into the job config, or "".
    Codex has no Miro connector; this is only there if the user added a Miro MCP server to Codex themselves."""
    try:
        text = (codex_user_home() / "config.toml").read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    out, on = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("["):  # a table header: in the Miro server's tables or out of them
            on = bool(re.match(r'\[\s*mcp_servers\s*\.\s*(?:miro|"miro")\s*(?:\.|\])', s))
        if on:
            out.append(ln)
    return "\n".join(out).strip()


def codex_has_miro():
    return bool(_codex_miro_toml())


def codex_connectors(home=None):
    """Codex's cached list of connector tools in a folder (a run folder's snapshot, or an account home) -> {connector
    id: [tool name, ...]} (names as the runs see them, "gmail.create_draft"), from the newest file Codex wrote. A
    connector the list names without any tool is left out, i.e. treated as absent. {} when there is no list yet."""
    d = (home or codex_home()) / "cache" / "codex_apps_tools"
    try:
        files = sorted((f for f in d.iterdir() if f.is_file() and f.stat().st_size), key=lambda f: f.stat().st_mtime)
        data = json.loads(files[-1].read_text(encoding="utf-8")) if files else {}
    except (OSError, ValueError):
        return {}
    out = {}
    for t in data.get("tools") or []:
        tool = (t or {}).get("tool") or {}
        cid, nm = str((tool.get("_meta") or {}).get("connector_id") or ""), str(tool.get("name") or "")
        if cid and "." in nm and re.fullmatch(r"[A-Za-z0-9_]+", cid):
            out.setdefault(cid, []).append(nm)
    return out


def codex_snapshot_age(home):
    """Seconds since Codex wrote the newest connector list in a folder, or None when there is none."""
    try:
        newest = max(f.stat().st_mtime for f in (home / "cache" / "codex_apps_tools").iterdir() if f.is_file())
    except (OSError, ValueError):
        return None
    import time
    return time.time() - newest  # negative when the file claims to be from the future: treated as stale


def _toml_str(v):
    return json.dumps(str(v))  # a JSON string is a valid TOML basic string


def _link(origin, dest):
    """dest -> origin as a symlink (Windows without Developer Mode: a hard link), never a copy: Codex refreshes the
    token in place, and a copy's refresh would sign the user's own Codex out."""
    try:
        dest.symlink_to(origin)
    except OSError:
        os.link(origin, dest)


_CODEX_CACHE = ("codex_apps_tools", "codex_apps_server_info")  # the connector list: all a run needs from the cache


def _codex_keep_cache(run, acct):
    """After a run: whatever connector list Codex fetched or refreshed in the run home becomes the account's."""
    for sub in _CODEX_CACHE:
        for f in (run / "cache" / sub).glob("*.json"):
            dest = acct / "cache" / sub / f.name
            try:
                if f.stat().st_size and (not dest.exists() or f.stat().st_mtime > dest.stat().st_mtime):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
            except OSError:
                pass


def _codex_base_toml(src):
    lines = ["# Generated by Open Loops for one headless Codex run; the folder is deleted when the run ends."]
    if model():
        lines.append("model = " + _toml_str(model()))
    if effort():
        lines.append("model_reasoning_effort = " + _toml_str(effort()))
    lines += ["project_doc_max_bytes = 0", 'web_search = "disabled"', "",
              "[features]", "memories = false", "shell_tool = false",
              "image_generation = false", "multi_agent = false", "browser_use = false", "computer_use = false", "",
              "# every ChatGPT connector off; the run's own allow-list below switches on only what the job lists",
              "[apps._default]", "enabled = false"]
    miro = _codex_miro_toml()
    if miro:
        lines += ["", "# the user's own Miro MCP server, copied from " + str(src / "config.toml"), miro]
    return "\n".join(lines) + "\n"


def codex_job_env():
    """A home for one headless Codex run -> (env, run home, account home). Raises CodexNotReady rather than ever
    running in the user's own home.

    The account home (state/codex-home/<account hash>/) keeps only what is worth keeping between runs: Codex's cached
    connector list (cache/). Each run gets a fresh folder below it (run-<id>/) holding links to the user's auth.json
    (and .credentials.json, the MCP sign-ins a Miro server uses, when there is one), a copy of that list, an empty
    work folder and a config.toml: model and effort, memories and the shell tool off, no AGENTS.md, no web search,
    every connector off (the run adds its allow-list, _codex_exec). Nothing from an earlier run or another account is
    ever reused, and two jobs at once never share a config. The user's own config, plugins, skills, hooks and memories
    are never loaded (#18: ~190k -> ~27k input tokens)."""
    import uuid
    src, auth = codex_user_home(), codex_auth()
    if not (src / "auth.json").is_file():
        raise CodexNotReady("keyring" if _codex_logged_in() else "signin")
    if auth["mode"] != "chatgpt" or not auth["account"]:
        raise CodexNotReady("signin")  # API-key sign-in: no connectors (doctor.py says so on its row)
    acct = codex_home(auth["account"])
    run = acct / ("run-" + uuid.uuid4().hex[:12])
    try:
        (acct / "cache").mkdir(parents=True, exist_ok=True)
        (run / "work").mkdir(parents=True)  # the run's working folder (-C): empty, so nothing to read
        for name_ in ("auth.json", ".credentials.json"):
            if (src / name_).exists():
                _link(src / name_, run / name_)
        if not os.path.samefile(run / "auth.json", src / "auth.json"):
            raise OSError("auth.json link does not point at the user's file")
        for sub in _CODEX_CACHE:  # a copy: Codex does not read its connector list through a linked folder (measured)
            if (acct / "cache" / sub).is_dir():
                shutil.copytree(acct / "cache" / sub, run / "cache" / sub)
        (run / "config.toml").write_text(_codex_base_toml(src), encoding="utf-8")
    except OSError:
        shutil.rmtree(run, ignore_errors=True)
        raise CodexNotReady("link")
    env = dict(os.environ)
    env["CODEX_HOME"] = str(run)
    return env, run, acct


def _codex_logged_in():
    """`codex login status` says signed in (used only to tell a keychain sign-in from no sign-in)."""
    try:
        p = subprocess.run([cli(), "login", "status"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, stdin=subprocess.DEVNULL, shell=WIN)
        said = ((p.stdout or "") + (p.stderr or "")).lower()
        return p.returncode == 0 and "logged in" in said and "not logged in" not in said
    except Exception:
        return False


def codex_allow_toml(tools, connectors):
    """One run's allow-list, layered over the base config: the connectors this job's tools belong to switched on, every
    other tool of those connectors switched off. -> (toml text, set of allowed tool names)."""
    allowed = {q for q in _qualify(tools) if "." in q}
    lines = ["", "# this run's allow-list"]
    for cid, names in sorted(connectors.items()):
        if not allowed.intersection(names):
            continue  # stays off (apps._default)
        lines += ["", f"[apps.{cid}]", "enabled = true", 'default_tools_approval_mode = "auto"']
        for nm in sorted(set(names) - allowed):
            lines += ["", f"[apps.{cid}.tools.{nm.split('.', 1)[1]}]", "enabled = false"]
    return "\n".join(lines) + "\n", allowed


def codex_preamble(tools):
    """What a Codex run is told before the job's own prompt: its tools by exact connector name, with the short name the
    job's prompt may use. The config is what enforces the list; this keeps the model from hunting for others."""
    lines = []
    for t in tools:
        for q in _qualify([t]):
            if "." not in q:  # a whole MCP server ("miro.*")
                lines.append(f"- any tool of the {q} MCP server (only if it is there)")
                continue
            short = t.split(".", 1)[1]
            notes = ([] if q.endswith(short) else [f"the instructions below may call it {short}"]) + (
                [_CODEX_HINT[t]] if t in _CODEX_HINT else [])
            lines.append(f"- {q}" + (f" ({'; '.join(notes)})" if notes else ""))
    lines = list(dict.fromkeys(lines))
    head = ("[Open Loops: an unattended run. Nobody is watching, so nobody can answer a question or approve anything.]\n")
    if lines:
        # Wording matters (measured): "do not use any other tool" also kept the model from Codex's own tool search,
        # which is how app tools are found, so it then saw none. Finding tools is allowed; calling others is not.
        head += ("The only tools you may call are:\n" + "\n".join(lines) +
                 "\nThey may not be loaded at the start: search for them by name first. Do not call any other app or "
                 "connector tool, do not run shell commands, do not read or write files, do not search the web, do not "
                 "install anything. If a tool you need is missing or fails, carry on without it and say so where the "
                 "instructions ask.\n")
    else:
        head += ("Use no tools at all: no apps, no connectors, no shell commands, no files, no web. "
                 "Answer from the text below only.\n")
    return head + "[End of the Open Loops note. The job's instructions follow.]\n\n"


def codex_args(home, out_file, effort_=None):
    """argv for one `codex exec`: prompt on stdin ("-"), final message to out_file, events as JSON Lines on stdout.
    Read-only sandbox and the shell tool off. (unified_exec cannot be
    switched off on Codex 0.156: `codex features list` still shows it on; shell_tool = false does take.)"""
    e = effort() if effort_ is None else effort_
    args = [cli(), "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
            "-C", str(home / "work"), "-o", str(out_file), "-c", "features.shell_tool=false"]
    if model():
        args += ["-m", model()]
    if e:
        args += ["-c", f'model_reasoning_effort="{e}"']
    return args + ["-"]


def _codex_events(jsonl):
    """`codex exec --json` stdout -> dict: "msg" (last agent message), "used" (every tool called, "server/tool", and
    shell / file / web items by type), "ok" (tools whose call completed without an error), "usage", "errors".
    Only names and counts are kept: a tool's arguments and results (mail, messages) never reach the logs from here."""
    out = {"msg": "", "used": [], "ok": [], "usage": {}, "errors": []}
    for ln in (jsonl or "").splitlines():
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind, item = ev.get("type"), ev.get("item") or {}
        if kind == "item.completed" and item.get("type") == "agent_message":
            out["msg"] = str(item.get("text") or out["msg"])
        elif kind in ("item.started", "item.completed") and item.get("type") == "mcp_tool_call":
            out["used"].append(f"{item.get('server', '?')}/{item.get('tool', '?')}")
            if kind == "item.completed" and item.get("status") == "completed" and not item.get("error"):
                out["ok"].append(str(item.get("tool") or ""))
        elif kind in ("item.started", "item.completed") and item.get("type") in ("command_execution", "file_change", "web_search"):
            out["used"].append(str(item.get("type")))
        elif kind == "turn.completed":
            out["usage"] = ev.get("usage") or out["usage"]
        elif kind in ("turn.failed", "error"):
            e = ev.get("error") if isinstance(ev.get("error"), dict) else ev
            out["errors"].append(str(e.get("message") or e)[:500])
    out["used"], out["ok"] = list(dict.fromkeys(out["used"])), list(dict.fromkeys(out["ok"]))
    return out


def codex_timeout():
    """config.json "codex_timeout_s": how long one job's Codex run may take (default 900 s); 0 = no limit."""
    try:
        n = int(_cfg().get("codex_timeout_s", 900) or 0)
    except (TypeError, ValueError):
        n = 900
    return n if n > 0 else None


def _refused(args, why, detail=""):
    said = CODEX_REFUSE[why].format(store=codex_keyring_store(), limit="the time allowed")
    p = subprocess.CompletedProcess(args, 3, stdout=said + "\n", stderr=f"codex: not run ({why}){': ' + detail if detail else ''}\n")
    p.refused, p.tools_used, p.tools_ok, p.errors, p.codex_stderr, p.dropped = why, [], [], [], "", []
    return p


def _codex_exec(home, prompt, tools, allow_toml, timeout, effort_):
    """Run `codex exec` once in a run folder made by codex_job_env, with the allow-list appended to its config
    -> (rc, events dict, codex's own stderr, final message, args). The caller removes the folder (_codex_done)."""
    env, run, acct = home
    out = run / "last-message.txt"
    args = codex_args(run, out, effort_)
    with open(run / "config.toml", "a", encoding="utf-8") as f:
        f.write(allow_toml)
    try:
        p = subprocess.run(args, input=codex_preamble(tools) + prompt, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, cwd=str(ROOT), timeout=timeout, shell=WIN)
        rc, events, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        rc, err = 124, f"codex: stopped after {timeout} seconds without an answer\n"
        events = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
    try:
        final = out.read_text(encoding="utf-8", errors="replace")
    except OSError:
        final = ""
    return rc, _codex_events(events), err, final, args


def _codex_done(home):
    """A run folder's end: whatever connector list Codex fetched in it goes to the account; the folder goes."""
    _, run, acct = home
    try:
        _codex_keep_cache(run, acct)
    finally:
        shutil.rmtree(run, ignore_errors=True)


def _codex_warmup(budget):
    """One short run with every connector off, so Codex fetches (or refreshes) the account's connector list
    -> (rc, events dict, codex's own stderr). Measured: Codex fetches the list even with every connector off."""
    home = codex_job_env()
    try:
        rc, ev, err, _, _ = _codex_exec(home, "Reply with exactly: OK", [], "", max(1, int(budget)), "low")
    finally:
        _codex_done(home)
    return rc, ev, err


def _codex_once(prompt, tools, left, effort_):
    """One job run bound to one snapshot -> (rc, events, codex stderr, final message, args, allowed tool names, dropped
    sources). Raises CodexNotReady when the run cannot be made safely: the warm-up failed (its reason), the snapshot
    still lacks a tool the job lists after one warm-up ("cold"), it is still more than a day old ("stale"), or none of
    the job's sources is connected ("nosources")."""
    for attempt in (1, 2):
        # ONE snapshot binds policy and execution: the run folder gets its copy of the connector list first, and the
        # allow-list below is generated from that copy, never from the account's list read earlier (another job's
        # warm-up may replace that meanwhile). What Codex runs with is what the deny-list was made from.
        home, result = codex_job_env(), None
        try:
            snap = codex_connectors(home[1])
            have = {t for names in snap.values() for t in names}
            # A source with no tool at all in the list is not connected in this ChatGPT account: its tools are dropped
            # and the job told to skip it (the caller learns which, e.g. refresh.py holds that source's cursor). With
            # no list at all nothing is dropped: the warm-up below fetches one first.
            listed = {t.split(".", 1)[0] for t in have}
            asked = {q.split(".", 1)[0] for q in _qualify(tools) if "." in q}
            dropped = sorted(asked - listed) if have else []
            run_tools = [t for t in tools if t.split(".", 1)[0] not in dropped]
            needed = {q for q in _qualify(run_tools) if "." in q}
            age = codex_snapshot_age(home[1])
            fresh = age is not None and 0 <= age < SNAPSHOT_MAX_AGE_S
            if asked and have and not needed:
                raise CodexNotReady("nosources")
            if needed <= have and (fresh or not needed):
                # Fail closed: a job runs only on a complete, fresh snapshot. A tool OpenAI added to a connector after
                # the snapshot was fetched cannot be switched off in advance: it is refused only after it has run (the
                # unlisted check in codex_run, rc 3). Snapshots older than SNAPSHOT_MAX_AGE_S are refreshed or refused,
                # which bounds that window to a day.
                allow_toml, allowed = codex_allow_toml(run_tools, snap)
                skip = "".join(_CODEX_SKIP[x] + "\n" for x in dropped)
                result = _codex_exec(home, (skip + "\n" if skip else "") + prompt, run_tools, allow_toml, left(),
                                     effort_) + (allowed, dropped)
        finally:
            _codex_done(home)
        if result:
            return result
        if attempt == 2:
            raise CodexNotReady("stale" if needed <= have else "cold")  # a warm-up ran and did not help
        # Missing, incomplete or stale list: one warm-up within this run's time budget; its failure is this run's
        wrc, wev, werr = _codex_warmup(min(120, left() or 120))
        why = codex_failure(wrc, wev["errors"], werr)
        if why:
            raise CodexNotReady(why)


def codex_run(prompt, tools, timeout=None, effort_=None):
    """One unattended `codex exec` -> CompletedProcess. stdout is the final message only (what the jobs parse); stderr
    is Codex's own plus one summary line (tools used, tokens). Extra attributes for doctor.py: refused (a key of
    CODEX_REFUSE, or ""), tools_used, tools_ok, errors, codex_stderr.

    Not run at all (rc 3, the reason as stdout) when there is no file sign-in, the job home cannot be linked, the
    warm-up failed (its own reason: timeout, spent allowance, expired sign-in, error), or even after a warm-up the
    connector list lacks a tool the job lists ("cold"). Failed (rc 3, "nothing was saved") when the run called a tool
    off the job's list: the stdout then holds no result for the job to apply. The warm-up and the job share `timeout`."""
    import time
    timeout = timeout if timeout is not None else codex_timeout()
    deadline = time.time() + timeout if timeout else None
    left = lambda: None if deadline is None else max(1, deadline - time.time())
    blind_note, used_all, ok_all, errs_all, err_all = "", [], [], [], ""
    read_only_job = {q for q in _qualify(tools) if "." in q} <= CODEX_READ_TOOLS
    try:
        for tries in (1, 2):
            missed = []
            rc, ev, err, final, args, allowed, dropped = _codex_once(prompt, tools, left, effort_)
            # Everything every attempt did is kept: a forbidden call in the first attempt still fails the run.
            used_all += ev["used"]
            ok_all += ev["ok"]
            errs_all += ev["errors"]
            err_all += err
            if codex_failure(rc, ev["errors"], err):
                break  # a timeout, spent allowance or expired sign-in: retrying would not help
            # Measured on real runs: a session now and then starts without one connector's tools (in one of three
            # probes Gmail's were simply not there, with Slack's present). A refresh would then report "no Gmail"
            # and move its cursor past mail it never read. So a run that called no tool of a connector its job lists
            # fails and saves nothing - retried once first, but only for a job that can only read, and never after
            # the first attempt called anything that is not a read or reported a draft or a send.
            services = {q.split(".", 1)[0] for q in allowed}
            reached = {u.split("/", 1)[1].split(".", 1)[0] for u in ev["used"] if u.startswith("codex_apps/")}
            missed = sorted(services - reached)
            if not missed:
                break
            wrote = [u for u in ev["used"] if u.startswith("codex_apps/") and u.split("/", 1)[1] not in CODEX_READ_TOOLS]
            wrote += [u for u in ev["used"] if u in ("command_execution", "file_change", "web_search")]
            marked = _CODEX_WROTE_RE.search(final or ev["msg"] or "")
            wrote += [u for u in ev["used"] if u.startswith("codex_apps/") and u.split("/", 1)[1] not in allowed]
            blind_note = (f"; no {', '.join(missed)} tool was called in attempt {tries} (it said: "
                          + " ".join((final or ev["msg"]).split())[:200] + ")")
            if (tries == 2 or not read_only_job or wrote or marked
                    or (deadline is not None and deadline - time.time() < 30)):
                break
    except CodexNotReady as e:
        return _refused([cli(), "exec"], e.why)
    except OSError as e:  # the CLI missing or not runnable (the run folder is gone already)
        return _refused([cli(), "exec"], "start", f"{type(e).__name__}: {e}")
    used_all, ok_all = list(dict.fromkeys(used_all)), list(dict.fromkeys(ok_all))
    ev = dict(ev, used=used_all, errors=errs_all)
    err = err_all
    servers = {q for q in _qualify(tools) if "." not in q}  # a whole MCP server the job allows ("miro")
    extra = [u for u in ev["used"] if u.split("/", 1)[-1] not in allowed | _CODEX_SAFE_BUILTINS
             and u.split("/", 1)[0] not in servers]
    note = "codex: tools used: " + (", ".join(ev["used"]) or "none") + blind_note
    if ev["usage"]:
        u = ev["usage"]
        note += f"; tokens in {u.get('input_tokens', '?')} (cached {u.get('cached_input_tokens', '?')}), out {u.get('output_tokens', '?')}"
    stdout = final or ev["msg"]
    blind = bool(missed) and not codex_failure(rc, ev["errors"], err)
    if extra:
        note += "; REFUSED: used a tool the job did not list: " + ", ".join(extra)
        rc, stdout = 3, CODEX_REFUSE["unlisted"] + "\n"
    elif blind:  # the model's own words are in the note: with the tools not called, they hold no mail or messages
        note += "; REFUSED: no " + ", ".join(missed) + " tool was called"
        rc, stdout = 3, CODEX_REFUSE["notools"] + "\n"
    elif rc == 124:
        n = timeout or 0
        stdout = CODEX_REFUSE["timeout"].format(limit=f"{n // 60} minutes" if n >= 120 else f"{n} seconds") + "\n"
    out_err = err + ("\n" if err and not err.endswith("\n") else "") + note + "\n" + "".join(f"codex error: {x}\n" for x in ev["errors"])
    done = subprocess.CompletedProcess(args, rc, stdout=stdout, stderr=out_err)
    done.refused = "unlisted" if extra else "notools" if blind else ""
    done.tools_used, done.tools_ok, done.errors, done.codex_stderr = used_all, ok_all, errs_all, err
    done.dropped = dropped  # sources skipped because they are not connected in this ChatGPT account
    return done


def _qualify(tools):
    fmt = dict(_FMT.get(name()) or _FMT["claude"])
    codex = name() == "codex"
    if name() == "claude":  # from the server actually found, so a renamed server gets its real tool ids
        fmt["slack"] = tool_prefix(server_name("slack")) + "__slack_{}"
        fmt["gmail"] = tool_prefix(server_name("gmail")) + "__{}"
        fmt["miro"] = tool_prefix(server_name("miro"))
    out = []
    for t in tools:
        svc, tool = t.split(".", 1)
        if codex:
            tool = _CODEX_RENAME.get(t, tool)
        pat = fmt[svc]
        # "miro.*" -> the bare server id: Claude Code reads that as every tool on that server
        out.append(pat.format(tool) if "{}" in pat else pat)
    return list(dict.fromkeys(out))


# Claude setup steps the checklist can start from a button (doctor.py names them in each red row's
# "connect" key, app.py's /api/connect/<step> runs them). The server names are what `claude mcp list`
# prints on Claude Code 2.1.x; doctor.py matches them exactly and falls back to a looser match.
CLAUDE_SERVERS = {
    "slack": {"plugin": "plugin:slack:slack", "connector": "claude.ai Slack"},
    "gmail": {"connector": "claude.ai Gmail"},
    "miro":  {"plugin": "plugin:miro:miro", "connector": "claude.ai Miro", "server": "miro"},
}
CONNECT_STEPS = ("login", "slack_install", "slack", "gmail", "miro")
# Codex: sign in runs `codex login` (browser flow); Gmail and Slack are connected in the ChatGPT account itself, so
# their buttons open ChatGPT's apps page and the user comes back and presses Check again. No Miro connector exists.
CODEX_CONNECT_STEPS = ("login", "gmail", "slack")
CODEX_APPS_URL = "https://chatgpt.com/apps"


def connect_steps():
    """The setup steps the checklist's buttons can start for the selected AI (none for Grok)."""
    return {"claude": CONNECT_STEPS, "codex": CODEX_CONNECT_STEPS}.get(name(), ())


def connect_url(step):
    """A setup step that is a page to open rather than a command to run -> its URL, else None."""
    return CODEX_APPS_URL if name() == "codex" and step in ("gmail", "slack") else None


_MARKETPLACE = "claude-plugins-official"  # where the Slack plugin lives
_MARKETPLACE_SRC = "anthropics/claude-plugins-official"


def _has_marketplace():
    """Whether `claude plugin marketplace list` already knows the official marketplace."""
    try:
        p = subprocess.run(["claude", "plugin", "marketplace", "list"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, shell=WIN)
        return _MARKETPLACE in (p.stdout or "")
    except Exception:
        return False  # adding it again is harmless; failing to add it breaks the install


def login_cmd(step):
    """The commands for one Claude setup step, run in order -> [argv, ...], or None (unknown step, or not Claude).
    Codex has one command step, sign in (`codex login`, which opens the browser itself).

    Each opens the browser at most once and needs nothing typed: the user only clicks Allow. `mcp login`
    gets --no-browser off Windows because app.py runs it on a pseudo-terminal, reads the sign-in link it
    prints and opens that itself (the CLI refuses to wait for the browser when stdin is not a terminal).
    On Windows app.py gives it a console window of its own instead, and the CLI opens the browser."""
    if name() == "codex":  # the browser flow; Gmail / Slack are pages to open (connect_url), not commands
        return [[cli(), "login"]] if step == "login" else None
    if name() != "claude" or step not in CONNECT_STEPS:
        return None
    if step == "login":
        return [["claude", "auth", "login"]]
    if step == "slack_install":
        add = [] if _has_marketplace() else [["claude", "plugin", "marketplace", "add", _MARKETPLACE_SRC]]
        return add + [["claude", "plugin", "install", f"slack@{_MARKETPLACE}"]]
    return [["claude", "mcp", "login", server_name(step)] + ([] if WIN else ["--no-browser"])]


def server_name(svc):
    """The Claude server for "slack" / "gmail" / "miro" on the configured route. doctor.py saves the name
    `claude mcp list` actually printed (config "claude_servers"); that one is used when it is on the same route
    and a plain name (Windows passes it through cmd.exe), else today's usual name. Sign-in and tool ids both
    come from here, so a renamed server is never signed in to while jobs allow tools it does not have."""
    src = {"slack": slack_source, "miro": miro_source}.get(svc, lambda: "connector")()
    seen = str((_cfg().get("claude_servers") or {}).get(svc) or "")
    if seen and re.fullmatch(r"[\w .:@/-]{1,100}", seen) and _route_of(seen, svc) == src:
        return seen
    return CLAUDE_SERVERS[svc][src]


def tool_prefix(server):
    """Claude Code's tool-id prefix for a server: "mcp__" + its name with anything but letters, digits, _ and -
    made _ (plugin:slack:slack -> mcp__plugin_slack_slack, claude.ai Gmail -> mcp__claude_ai_Gmail)."""
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", server)


def _route_of(server, svc):
    """"plugin" / "connector" / "server" for a server name, by exact match or by the shape of the name."""
    exact = next((s for s, n in CLAUDE_SERVERS[svc].items() if n == server), None)
    if exact:
        return exact
    low = server.lower()
    return ("plugin" if low.startswith("plugin:") else "connector" if low.startswith("claude.ai") else None) if svc in low else None


# ---- Installing the agent's CLI from the checklist: the "install" setup step (doctor.py offers it on the
# "<AI> is installed" row when the CLI is missing, app.py runs it like the sign-in steps). Every vendor now ships
# a standalone installer script, so no route needs Node, npm or Homebrew: the scripts need only curl + bash on a
# Mac and PowerShell on Windows, which both come with the OS. Checked against the vendors' pages on 2026-09-23:
#   Claude Code  https://code.claude.com/docs/en/setup  (native install, "Recommended"; lands in ~/.local/bin)
#   Codex        https://github.com/openai/codex        (install script; lands in ~/.local/bin, Windows
#                %LOCALAPPDATA%\Programs\OpenAI\Codex\bin). npm i -g @openai/codex and brew install --cask codex
#                also work but need Node or Homebrew first, so they are the manual fallback in INSTALL.md.
#   Grok         https://docs.x.ai/build/overview        (install script; lands in ~/.grok/bin)
# Download first, run second: the script is saved whole to state/install/ and checked non-empty before anything runs,
# so a download cut off half-way never executes (piping curl into bash would run what had arrived so far). Then the
# new CLI must answer `<cli> --version` before the step counts as done. The page shows every command, one per line,
# exactly as they run, so pasting them by hand does the same.
INSTALL_STEP = "install"
_INSTALL = {  # agent -> (Mac/Linux script, what runs it, Windows script, the CLI, where it is documented, who makes it)
    "claude": ("https://claude.ai/install.sh", "bash", "https://claude.ai/install.ps1", "claude",
               "https://code.claude.com/docs/en/setup", "Anthropic"),
    "codex":  ("https://chatgpt.com/codex/install.sh", "sh", "https://chatgpt.com/codex/install.ps1", "codex",
               "https://github.com/openai/codex", "OpenAI"),
    "grok":   ("https://x.ai/cli/install.sh", "bash", "https://x.ai/cli/install.ps1", "grok",
               "https://docs.x.ai/build/overview", "xAI"),
}
_PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]


def prereq(win=None):
    """What is on this computer that an install route could use -> {tool: bool}. The installer scripts need only
    curl + bash (Mac/Linux) or PowerShell (Windows); node / npm / brew / winget are reported for the manual routes."""
    win = WIN if win is None else win
    tools = ["powershell", "node", "npm", "winget"] if win else ["curl", "bash", "node", "npm", "brew"]
    return {t: shutil.which(t) is not None for t in tools}


def install_cmd(agent=None, win=None):
    """How to install an agent's CLI (default: the selected one) -> dict, or None for an agent with no known installer:
      "steps"    [(kind, argv), ...] run in order, each only if the one before exited 0. kind is "download", "install"
                 or "check"; app.py names a failure by it. "download" saves the script to "script".
      "command"  what the page shows before the button is pressed, and what to paste by hand: on a Mac every step,
                 joined by && so each runs only if the last worked; on Windows one PowerShell block with the same gates
      "needs"    the tools the steps cannot run without (doctor.py says so, and offers no button, when one is missing)
      "agent", "id"  name exactly what the page showed; app.py refuses a press whose pair no longer matches
      "cli", "script", "source", "vendor"."""
    win = WIN if win is None else win
    who = (agent or name()).strip().lower()
    got = _INSTALL.get(who)
    if not got:
        return None
    url, shell, ps_url, cli_, src, vendor = got
    folder = ROOT / "state" / "install"
    if win:
        script = str(folder / f"{who}-install.ps1")
        pq = lambda x: "'" + str(x).replace("'", "''") + "'"  # PowerShell '...' quoting: a quote is doubled
        # The download fails as a whole (Stop), including an empty file, so a partial or empty script never runs
        get = ["$ErrorActionPreference = 'Stop'",
               f"New-Item -ItemType Directory -Force -Path {pq(folder)} | Out-Null",
               f"Invoke-WebRequest -UseBasicParsing -Uri {pq(ps_url)} -OutFile {pq(script)}",
               f"if ((Get-Item -LiteralPath {pq(script)}).Length -eq 0) {{ throw 'The download was empty.' }}"]
        steps = [("download", _PS + ["-Command", "; ".join(get)]),
                 ("install", _PS + ["-File", script]),
                 ("check", [cli_, "--version"])]
        # Shown (and pasted by hand) as one PowerShell block with the same gates: it stops at the first failure,
        # and throw ends only the block, not the PowerShell window it is pasted into.
        command = "\n".join(["& {"] + ["  " + g for g in get] + [
            f"  powershell -NoProfile -ExecutionPolicy Bypass -File {pq(script)}",
            "  if ($LASTEXITCODE -ne 0) { throw \"The installer stopped with exit code $LASTEXITCODE.\" }",
            f"  {cli_} --version", "}"])
        needs = ["powershell"]
    else:
        script = str(folder / f"{who}-install.sh")
        steps = [("download", ["mkdir", "-p", str(folder)]),
                 ("download", ["curl", "-fsSL", "-o", script, url]),
                 ("download", ["test", "-s", script]),  # an empty 200 is a failed download too
                 ("install", [shell, script]),
                 ("check", [cli_, "--version"])]
        # pasted as is, each runs only if the last worked
        command, needs = " &&\n".join(shlex.join(a) for _, a in steps), ["curl", "bash"]
    return {"steps": steps, "command": command, "needs": needs, "agent": who, "cli": cli_, "script": script,
            "source": src, "vendor": vendor,
            "id": hashlib.sha256((who + "\0" + command).encode("utf-8")).hexdigest()[:16]}


# What the checklist says when an install ends badly, by what went wrong (app.py records which). Plain words as
# #25 asks: what happened, then what to do; no exit codes or file paths. The installer's own output stays in
# state/connect-install.log and the page's Console.
INSTALL_SAID = {
    "download": "{ai}'s installer couldn't download. Check your internet connection and press Install {ai} again.",
    "check":   "{ai} was installed but won't start. Press Install {ai} to try again, or ask IT to install {ai}.",
    "install": "{ai}'s installer stopped with an error. Press Install {ai} to try again. If it fails again, paste the "
               "commands below into Terminal (Windows: PowerShell) and press Enter.",
    "timeout": "The install took longer than {limit}, so Open Loops stopped it. Press Install {ai} to try again.",
    "start":   "Open Loops couldn't start {ai}'s installer. Press Install {ai} to try again.",
    "changed": "The AI chosen in Settings changed since this page showed the Install button, so nothing was installed. "
               "Press Check again.",
}


def install_dirs():
    """Where the installer scripts put the CLIs. A Finder-launched app, or a terminal opened before the install, does
    not have them on PATH yet; app.py adds the ones that exist so the re-check and the jobs find the new CLI."""
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".grok" / "bin"]
    if WIN and os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin")
    return dirs


def model():
    """config.json "model": the Claude model the jobs run on. An alias (sonnet, haiku, opus) or a
    full id. Blank means whatever `claude` defaults to on this machine, which is usually the most
    expensive model the user has - so the template says sonnet: plenty for reading threads and
    writing JSON, at a fraction of the cost. Grok ignores it.
    Codex has keys of its own, "codex_model" (template gpt-5.6-sol; gpt-5.5 leaves Codex on 2026-10-14), so
    switching AI in Settings never hands a Claude alias to `codex -m`."""
    return str(_cfg().get("codex_model" if name() == "codex" else "model") or "").strip()


def effort():
    """config.json "effort": low | medium | high | xhigh | max, how hard the model thinks per turn.
    Template: xhigh with sonnet (opus at medium is the other sensible pairing). Blank = CLI default.
    Codex: "codex_effort" (low | medium | high | xhigh), template low: every run is metered against the
    ChatGPT plan's 5-hour and weekly Codex allowance."""
    return str(_cfg().get("codex_effort" if name() == "codex" else "effort") or "").strip().lower()


def claude_args(tools):
    args = ["claude", "-p", "--output-format", "text", "--allowedTools", ",".join(_qualify(tools))]
    if model():
        args += ["--model", model()]
    if effort():
        args += ["--effort", effort()]
    return args


def run(prompt, tools, timeout=None):
    """One unattended prompt with only the given MCP tools allowed -> CompletedProcess.
    timeout (seconds) is honoured by Codex only; unset, Codex jobs use config.json "codex_timeout_s" (900)."""
    if name() == "codex":
        return codex_run(prompt, tools, timeout=timeout)
    if name() == "grok":
        # --cwd matters: .grok/config.toml there defines the bundled Gmail MCP server
        # (gmail_mcp.py, which does its own Google auth via gmail_auth.py).
        args = [cli(), "--cwd", str(ROOT), "-p", prompt, "--verbatim",
                "--output-format", "plain", "--max-turns", "50", "--effort", "low",
                "--disable-web-search", "--no-plan", "--no-subagents", "--no-auto-update",
                "--tools", "search_tool,use_tool",
                "--disallowed-tools", _GROK_DISALLOWED]
        for t in _qualify(tools):
            args += ["--allow", f"MCPTool({t})"]
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=grok_job_env(), shell=WIN)
    # shell=True only on Windows, to resolve claude.cmd (npm shim) via PATH
    return subprocess.run(claude_args(tools), input=prompt, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", shell=WIN)
