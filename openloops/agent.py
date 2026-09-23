"""Which AI runs the headless jobs. config.json "agent": "claude" (default), "grok" or "codex".

The job scripts (refresh/chase/voice/people) name tools logically - "slack.read_channel",
"gmail.search_threads" - and call run(prompt, tools). This module maps those names to the
agent's own tool ids and invokes the right CLI.

Grok: Slack is opt-in (config.json "use_slack"). Off, the Slack plugin is not started and
doctor does not probe it. Vercel is never started. Jobs pass --effort low because the Grok
CLI defaults to xhigh. Gmail is the bundled gmail_mcp.py server (not Claude's connector).

Codex (OpenAI's CLI, ChatGPT sign-in): Gmail and Slack are ChatGPT connectors that ride on the ChatGPT account
(server "codex_apps", tools gmail.<tool> / slack.slack_<tool>), not MCP servers on this computer. `codex exec` has
no tool allow-list (the apps.* enable/disable keys are ignored there, #18), so a run is scoped by a read-only
sandbox, shell tools switched off, and a prompt preamble naming the only tools it may use. Jobs run under a
job-local CODEX_HOME (state/codex-home) so the user's plugins, skills, memories and AGENTS.md are not loaded.
"""
import hashlib, json, os, re, shlex, shutil, subprocess, sys
from pathlib import Path

from .paths import ROOT
WIN = sys.platform == "win32"
_GROK_JOB_HOME = ROOT / "state" / "grok-home"
_CODEX_JOB_HOME = ROOT / "state" / "codex-home"

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
        # Homebrew, the install script (~/.local/bin, Windows %LOCALAPPDATA%), or Codex's own folder: a Finder- or
        # launchd-started app has none of them on PATH
        home = Path.home()
        spots = [Path("/opt/homebrew/bin/codex"), home / ".local" / "bin" / "codex", home / ".codex" / "bin" / "codex"]
        if WIN and os.environ.get("LOCALAPPDATA"):
            spots.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
        return shutil.which("codex") or next((str(p) for p in spots if p.exists()), "codex")
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
def codex_user_home():
    """The user's own Codex folder: $CODEX_HOME if set (and not our job home), else ~/.codex."""
    h = os.environ.get("CODEX_HOME")
    if h and os.path.realpath(h) != os.path.realpath(_CODEX_JOB_HOME):
        return Path(h)
    return Path.home() / ".codex"


def codex_auth():
    """What ~/.codex/auth.json says -> {"mode": "chatgpt" | "apikey" | "", "email": "", "sig": ""}. mode "" = no
    file (not signed in, or the sign-in is kept in the system keyring instead). The email comes from the ID token's
    claims (decoded, not verified: it is only shown on the checklist). sig changes whenever the file does, so a new
    sign-in makes doctor.py ask Codex again. Never returns a token."""
    import base64
    f = codex_user_home() / "auth.json"
    out = {"mode": "", "email": "", "sig": ""}
    try:
        st = f.stat()
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return out
    out["sig"] = f"{st.st_mtime_ns}:{st.st_size}"
    mode = str(data.get("auth_mode") or "").strip().lower()
    if not mode:  # older files carry no auth_mode: tokens mean ChatGPT, a key alone means API key
        mode = "chatgpt" if data.get("tokens") else "apikey" if data.get("OPENAI_API_KEY") else ""
    out["mode"] = mode
    try:
        payload = str((data.get("tokens") or {}).get("id_token") or "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        out["email"] = str(claims.get("email") or (claims.get("https://api.openai.com/profile") or {}).get("email") or "")
    except Exception:
        pass
    return out


def _codex_miro_toml():
    """The user's own [mcp_servers.miro] tables from their Codex config.toml, copied verbatim into the job home, or "".
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


def codex_apps_cached(sub="codex_apps_tools"):
    """Whether the job home already holds Codex's list of connector tools (see codex_job_env)."""
    d = _CODEX_JOB_HOME / "cache" / sub
    try:
        return any(f.is_file() and f.stat().st_size > 0 for f in d.iterdir())
    except OSError:
        return False


def _toml_str(v):
    return json.dumps(str(v))  # a JSON string is a valid TOML basic string


def codex_job_env():
    """Env for headless Codex: a job-local CODEX_HOME (state/codex-home), rewritten before every run.

    It holds a link to the user's auth.json (Codex keeps it fresh in place, so the user's own sign-in stays the one
    in use) and a small config.toml: model and effort, memories and shell tools off, no AGENTS.md, no web search,
    and the Gmail / Slack apps set not to wait for an approval nobody can give. The user's own config, plugins,
    skills, hooks and memories are not loaded: that alone cut a run from ~190k to ~72k input tokens (#18).
    If there is no auth.json (sign-in kept in the keyring), the user's own home is used as it is."""
    src, home = codex_user_home(), _CODEX_JOB_HOME
    (home / "work").mkdir(parents=True, exist_ok=True)  # the run's working folder (-C): empty, so nothing to read
    env = dict(os.environ)
    if not (src / "auth.json").exists():
        return env
    for name_ in ("auth.json", ".credentials.json"):  # .credentials.json: MCP sign-ins (a Miro server), if kept on disk
        origin, dest = src / name_, home / name_
        if not origin.exists():
            continue
        if dest.is_symlink() and os.readlink(dest) == str(origin):
            continue
        try:
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            try:
                dest.symlink_to(origin)
            except OSError:  # Windows without Developer Mode may not make symlinks; a hard link is shared the same way
                os.link(origin, dest)
        except OSError:
            # Neither: never a copy, whose refreshed token would log the user's own Codex out. Use their home as it is.
            return env
    # Codex fetches the list of connector tools in the background and keeps it in cache/codex_apps_tools. In a fresh
    # home the first session starts before that list arrives, so it has no Gmail or Slack tools at all (seen on
    # 2026-09-23: zero tool calls, then the list cached mid-run and the next run worked). Seed the job home from the
    # user's own cache (same file names: they are keyed by account, not by folder) when it has none yet.
    for sub in ("codex_apps_tools", "codex_apps_server_info"):
        mine, theirs = home / "cache" / sub, src / "cache" / sub
        if not codex_apps_cached(sub) and theirs.is_dir():
            try:
                shutil.copytree(theirs, mine, dirs_exist_ok=True)
            except OSError:
                pass  # Codex fetches it itself; the first run may just see no connectors
    lines = ["# Generated by Open Loops for headless Codex jobs. Do not edit: it is rewritten before every run."]
    if model():
        lines.append("model = " + _toml_str(model()))
    if effort():
        lines.append("model_reasoning_effort = " + _toml_str(effort()))
    lines += ["project_doc_max_bytes = 0", 'web_search = "disabled"', "",
              "[features]", "memories = false", "shell_tool = false",
              "image_generation = false", "multi_agent = false", "browser_use = false", "computer_use = false", "",
              "[apps.gmail]", 'default_tools_approval_mode = "auto"', "",
              "[apps.slack]", 'default_tools_approval_mode = "auto"']
    miro = _codex_miro_toml()
    if miro:
        lines += ["", "# the user's own Miro MCP server, copied from " + str(src / "config.toml"), miro]
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    env["CODEX_HOME"] = str(home)
    return env


def codex_preamble(tools):
    """What a Codex run is told before the job's own prompt. There is no allow-list in `codex exec`, so this is where
    the job's tool list goes: by exact connector name, with the short name the job's prompt may use for it."""
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
        head += ("You may use ONLY these tools:\n" + "\n".join(lines) +
                 "\nDo not use any other tool, app or connector, even if one is available. Do not run shell commands, "
                 "do not read or write files, do not search the web. If a tool you need is missing or fails, carry on "
                 "without it and say so where the instructions ask.\n")
    else:
        head += ("Use no tools at all: no apps, no connectors, no shell commands, no files, no web. "
                 "Answer from the text below only.\n")
    return head + "[End of the Open Loops note. The job's instructions follow.]\n\n"


def codex_args(out_file, effort_=None):
    """argv for one `codex exec`: prompt on stdin ("-"), final message to out_file, events as JSON Lines on stdout.
    read-only sandbox + the shell tool off, so the model can only talk to the connectors. (unified_exec cannot be
    switched off on Codex 0.156: `codex features list` still shows it on; shell_tool = false does take.)"""
    e = effort() if effort_ is None else effort_
    args = [cli(), "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
            "-C", str(_CODEX_JOB_HOME / "work"), "-o", str(out_file),
            "-c", "features.shell_tool=false"]  # also in the job config; here too for a keyring sign-in (no job home)
    if model():
        args += ["-m", model()]
    if e:
        args += ["-c", f'model_reasoning_effort="{e}"']
    return args + ["-"]


def _codex_events(jsonl):
    """`codex exec --json` stdout -> (last agent message, tools called as "server/tool", usage dict, error messages).
    Only names and counts are kept: a tool's arguments and results (mail, messages) never reach the logs from here."""
    msg, used, usage, errs = "", [], {}, []
    for ln in (jsonl or "").splitlines():
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind, item = ev.get("type"), ev.get("item") or {}
        if kind == "item.completed" and item.get("type") == "agent_message":
            msg = str(item.get("text") or msg)
        elif kind in ("item.started", "item.completed") and item.get("type") == "mcp_tool_call":
            used.append(f"{item.get('server', '?')}/{item.get('tool', '?')}")
        elif kind in ("item.started", "item.completed") and item.get("type") in ("command_execution", "file_change", "web_search"):
            used.append(str(item.get("type")))
        elif kind == "turn.completed":
            usage = ev.get("usage") or usage
        elif kind in ("turn.failed", "error"):
            e = ev.get("error") if isinstance(ev.get("error"), dict) else ev
            errs.append(str(e.get("message") or e)[:500])
    return msg, list(dict.fromkeys(used)), usage, errs


def codex_run(prompt, tools, timeout=None, effort_=None):
    """One unattended `codex exec` -> CompletedProcess whose stdout is the final message only (what the jobs parse),
    and whose stderr is Codex's own stderr plus one summary line: tools used, tokens, and any tool used that was
    not on the job's list (which the preamble forbids but nothing can block)."""
    import tempfile
    env = codex_job_env()
    fd, out = tempfile.mkstemp(prefix="codex-last-", suffix=".txt", dir=str(_CODEX_JOB_HOME))
    os.close(fd)
    args = codex_args(out, effort_)
    try:
        p = subprocess.run(args, input=codex_preamble(tools) + prompt, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, cwd=str(ROOT), timeout=timeout, shell=WIN)
        rc, events, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        rc, err = 124, f"codex: stopped after {timeout} seconds without an answer\n"
        events = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
    try:
        final = Path(out).read_text(encoding="utf-8", errors="replace")
    except OSError:
        final = ""
    finally:
        Path(out).unlink(missing_ok=True)
    msg, used, usage, errs = _codex_events(events)
    final = final or msg
    allowed = set(_qualify(tools))  # "gmail.search_emails" (a connector tool) or "miro" (a whole MCP server)
    extra = [u for u in used if u.split("/", 1)[-1] not in allowed and u.split("/", 1)[0] not in allowed]
    note = "codex: tools used: " + (", ".join(used) or "none")
    if usage:
        note += (f"; tokens in {usage.get('input_tokens', '?')} (cached {usage.get('cached_input_tokens', '?')}),"
                 f" out {usage.get('output_tokens', '?')}")
    if extra:
        note += "; WARNING: used a tool the job did not list: " + ", ".join(extra)
    err = err + ("\n" if err and not err.endswith("\n") else "") + note + "\n" + "".join(f"codex error: {x}\n" for x in errs)
    done = subprocess.CompletedProcess(args, rc, stdout=final, stderr=err)
    done.tools_used = used  # "server/tool" names, for doctor.py's probe
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
    timeout (seconds) is honoured by Codex only (doctor.py's probe); the jobs never set one."""
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
