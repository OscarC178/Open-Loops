"""Connection check. Prints JSON describing what's set up and what the user still needs to do,
in plain language. Used by the app's first-run panel (auto-refreshes until all green).
Which AI it checks comes from config.json "agent" (claude by default - see agent.py).

    python doctor.py            -> JSON
    python doctor.py --detect   -> also asks the agent for the user's Slack id and saves it to config.json
"""
import json, os, re, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

from . import agent
from .paths import ROOT
CONFIG = ROOT / "config.json"
LOGS = ROOT / "state" / "logs"
WIN = sys.platform == "win32"


def run(args, timeout=60, **kw):
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, shell=WIN, **kw)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa
        return 1, str(e)


# One `claude mcp list` line: "<name>: <url or command> - <mark> <state>", e.g.
#   plugin:slack:slack: https://mcp.slack.com/mcp (HTTP) - ✔ Connected
#   claude.ai Miro: https://mcp.miro.com - ! Needs authentication
# The name may itself hold colons, so it ends at the first ": "; the state follows the last " - ".
# The mark is optional and may be several symbols (a Windows console may print another glyph, an emoji
# font adds U+FE0F to "✔"); the words decide.
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")
_MCP_LINE = re.compile(r"^(.+?): .* - (?:[^\w\s]+\s*)?(\S.*)$")


def parse_mcp_list(txt):
    """`claude mcp list` output -> {server name: "connected" | "auth" | "failed"}. Colour codes, the
    "Checking MCP server health…" header and blank lines are skipped."""
    out = {}
    for line in _ANSI.sub("", txt or "").splitlines():
        m = _MCP_LINE.match(line.strip())
        if m:
            said = m.group(2).strip().lower()
            out[m.group(1).strip()] = "connected" if said.startswith("connected") else ("auth" if "auth" in said else "failed")
    return out


def route(svc, servers):
    """Which way Claude reaches one service -> (source, state, name): source is a key of agent.CLAUDE_SERVERS[svc]
    ("plugin", "connector", "server"), state as parse_mcp_list, name the server exactly as listed (what
    `claude mcp login` needs, even if a later Claude Code renames it). ("", "", "") when no such server is set up.
    A connected route beats one that needs signing in; otherwise the plugin wins, as the jobs expect."""
    names = agent.CLAUDE_SERVERS[svc]
    found = []
    for name, state in servers.items():
        src = agent._route_of(name, svc)  # exact name, or for a server renamed by a later Claude Code its shape
        if src in names:
            found.append((src, state, name))
    found.sort(key=lambda f: (f[1] != "connected", list(names).index(f[0])))
    return found[0] if found else ("", "", "")


def claude_steps(steps):
    """Installed / signed in / Slack / Gmail / Miro, read straight from the Claude Code CLI (`claude auth status`,
    `claude mcp list`) - no model call. Each red row names in "connect" the setup step the page's button starts
    (agent.login_cmd); rows without one need something no button can do."""
    have = shutil.which("claude") is not None
    steps.append({"id": "claude", "ok": have, "title": "Claude is installed",
                  "fix": "Run the installer again, or ask IT to install Claude Code." if not have else ""})

    logged, email = False, ""
    if have:
        rc, txt = run(["claude", "auth", "status"])
        logged = bool(re.search(r'"loggedIn"\s*:\s*true', txt))
        e = re.search(r'"email(?:Address)?"\s*:\s*"([^"]+)"', txt)  # 2.1.x says "email"; older builds "emailAddress"
        email = e.group(1) if e else ""
        if not email:  # fall back to the account stored by Claude Code
            try:
                cj = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8-sig"))
                email = cj.get("oauthAccount", {}).get("emailAddress", "")
            except Exception:
                pass
    login = {"id": "login", "ok": logged, "title": f"Signed in to Claude{(' as ' + email) if email else ''}",
             "fix": "Press Sign in: your browser opens the Claude sign-in page. Use your work Google account." if not logged else ""}
    if have and not logged:
        login["connect"] = "login"
    steps.append(login)

    # "plugin" (plugin:slack:slack) or "connector" (claude.ai Slack): jobs need the right tool prefix. Reported even
    # while it still needs signing in, so the Connect button signs in to the route that is actually there.
    servers, unlisted = {}, ""
    if logged:
        rc, txt = run(["claude", "mcp", "list"], timeout=90)
        servers = parse_mcp_list(txt)
        if rc != 0:  # the listing failed (timeout, CLI error), perhaps part-way: a service it did not print may
            # still be set up, so it is "unknown", not "missing". Services it did print keep what it said about them.
            unlisted = (txt.strip().splitlines() or ["exit code " + str(rc)])[-1][:200]
    (slack_source, s_st, s_nm), (_, g_st, g_nm), (miro_source, m_st, m_nm) = (route(k, servers) for k in ("slack", "gmail", "miro"))
    names = {k: n for k, n in (("slack", s_nm), ("gmail", g_nm), ("miro", m_nm)) if n}  # for agent.login_cmd
    slack, gmail, miro = s_st == "connected", g_st == "connected", m_st == "connected"
    first = "Sign in to Claude first (the row above)."

    def row(id_, ok, title, state, connect, fix_missing, fix_auth):
        r = {"id": id_, "ok": ok, "optional": True, "title": title, "fix": ""}
        if not ok:
            if not logged:
                r["fix"] = first
            elif unlisted and not state:  # no button: installing would not fix a listing that did not finish
                r["fix"] = f"Couldn't ask Claude which connections it has just now ({unlisted}). Press Check again."
            elif not state:
                r["fix"], r["connect"] = fix_missing
            else:
                r["fix"], r["connect"] = fix_auth, connect
                if state == "failed":
                    r["fix"] = "It is set up but did not answer just now. " + fix_auth
            if not r.get("connect"):
                r.pop("connect", None)
        return r

    steps.append(row("slack", slack, "Slack connected (optional)", s_st, "slack",
                     ("Press Install Slack plugin (it takes about half a minute), then Connect Slack.", "slack_install"),
                     "Press Connect Slack: your browser opens Slack's sign-in page; click Allow."))
    steps.append(row("gmail", gmail, "Gmail connected (optional)", g_st, "gmail",
                     ("Gmail is added on claude.ai, not here: claude.ai → Settings → Connectors → Gmail. Then press Check again.", None),
                     "Press Connect Gmail: your browser opens Google's sign-in page; click Allow."))
    steps.append(row("miro", miro, "Miro connected (optional, for the Roadmap card)", m_st, "miro",
                     ("Add Miro first: claude.ai → Settings → Connectors → Miro, or in a terminal: "
                      "claude plugin install miro@claude-plugins-official. Then press Check again and Connect Miro.", None),
                     "Press Connect Miro: your browser opens Miro's sign-in page; pick the team and click Allow."))
    return email, slack, gmail, slack_source, miro, miro_source, names


def grok_steps(steps):
    """Installed / signed in / Slack / Gmail via the Grok CLI, its Slack plugin, and gmail_auth.py."""
    cli = agent.cli()
    have = bool(shutil.which("grok")) or Path(cli).exists()
    steps.append({"id": "claude", "ok": have, "title": "Grok is installed",
                  "fix": "Install the Grok CLI (grok.com/cli), then press 'Check again'." if not have else ""})

    logged, email = False, ""
    auth = Path.home() / ".grok" / "auth.json"
    if auth.exists():
        try:
            for v in json.loads(auth.read_text(encoding="utf-8-sig")).values():
                if isinstance(v, dict) and v.get("refresh_token"):
                    logged, email = True, v.get("email", "")
        except Exception:
            pass
    steps.append({"id": "login", "ok": logged, "title": f"Signed in to Grok{(' as ' + email) if email else ''}",
                  "fix": "Click 'Open Grok' below and follow the sign-in link it shows." if not logged else ""})

    # Gmail: bundled gmail_mcp.py + gmail_auth.py. Slack: only if Settings → Use Slack.
    # Never probe Vercel. Named `mcp doctor gmail` so Slack is not started when opted out.
    from . import gmail_auth
    tok = gmail_auth.token() if logged else None
    slack = gmail_srv = False
    want_slack = agent.use_slack()
    if have and logged:
        args = [cli, "mcp", "doctor"] + ([] if want_slack else ["gmail"]) + ["--json"]
        try:
            p = subprocess.run(args, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120, cwd=str(ROOT),
                               env=agent.grok_job_env(), shell=WIN)
            data = json.loads(p.stdout or "{}")
            servers = data.get("servers") or ([data] if isinstance(data, dict) and data.get("name") else [])
            for srv in servers:
                if srv.get("name") == "gmail" and srv.get("healthy"):
                    gmail_srv = True
                if want_slack and srv.get("name") == "slack" and srv.get("healthy"):
                    slack = True
        except Exception:
            pass
    if want_slack:
        steps.append({"id": "slack", "ok": slack, "optional": True, "title": "Slack connected (optional)",
                      "fix": "Click 'Open Grok', type /mcps and press Enter, select Slack, press i to authenticate, and approve in the browser." if not slack else ""})
    gmail = bool(tok) and gmail_srv
    if not gmail:
        if not tok and gmail_auth._load_store():
            gmail_fix = ("Gmail sign-in expired — Google's Testing-mode tokens last 7 days. "
                         "In Terminal run:  python3 -m openloops.gmail_auth connect   (from the Open Loops folder), "
                         "approve the Google account, then press Check again.")
        elif not tok:
            gmail_fix = ("Gmail needs a one-off Google sign-in of its own: follow 'Gmail with Grok' in INSTALL.md "
                         "(create a Desktop-app OAuth client, save it as google_oauth_client.json in the app folder, "
                         "then run: python3 -m openloops.gmail_auth connect).")
        else:
            gmail_fix = "Open Grok once in this folder and trust it, so it picks up the app's .grok/config.toml."
    else:
        gmail_fix = ""
    steps.append({"id": "gmail", "ok": gmail, "optional": True, "title": "Gmail connected (optional)",
                  "fix": gmail_fix})
    return email, slack, gmail, "", False, "", {}


def _save(updates, names=None):
    """Write doctor's own keys into config.json as it is now, under its cross-process lock: a check can take minutes
    (claude mcp list, the Slack-id prompt), and Settings saved meanwhile must survive. names (service -> server
    name) are merged into the claude_servers the file holds now, not the copy read at the start."""
    from .store import update_json

    def mutate(cfg):
        new = dict(updates)
        if names:
            new["claude_servers"] = {**(cfg.get("claude_servers") or {}), **names}
        if all(cfg.get(k) == v for k, v in new.items()):
            return False  # already so: leave the file alone
        cfg.update(new)
    update_json(CONFIG, mutate)


# What the page says about the Mac's weekday morning refresh (launchd, scripts/run-refresh.sh).
# Plain words (issue #25): what happened in one sentence, one thing to do, no jargon, no paths.
SCHEDULE_MSG = {
    "blocked": {"title": "Your Mac's privacy settings stopped the automatic morning refresh, so your list only updates when you press Refresh.",
                "fix": "Download and run the latest Open Loops installer."},
    "failed": {"title": "The automatic morning refresh could not start, so your list only updates when you press Refresh.",
               "fix": "Download and run the latest Open Loops installer."},
    "started": {"title": "The automatic morning refresh last started by itself on {when}.", "fix": ""},
}
# where "the latest installer" is: the page shows it as a button under the red row (the installer moves old installs)
DOWNLOAD_URL = "https://github.com/OscarC178/Open-Loops/releases/latest"
RUN_REFRESH_RE = re.compile(r":\s(/[^:]*/scripts/run-refresh\.sh)")


def schedule_step(logs=None, root=None):
    """The weekday morning refresh, as far as its logs show. Returns a checklist step or None (no evidence yet).

    - Red ("blocked" / "failed"): launchd.err.log has a start failure for THIS install's run-refresh.sh and no
      runner-<date>.log is newer. launchd writes that log only when it cannot start the job at all; the case
      from #24 is `/bin/bash: .../scripts/run-refresh.sh: Operation not permitted` (a background job may not
      read ~/Documents). The LATEST such line decides which, not any older one.
    - Green ("started"): a runner-<date>.log exists and is newer than any failure. That proves the script
      started (it writes that log first), not that the refresh inside it succeeded - the title says "started".
    Lines naming any other install's script are ignored: a moved install carries its old log along."""
    logs, root = Path(logs or LOGS), Path(root or ROOT)
    mine = os.path.realpath(root / "scripts" / "run-refresh.sh")  # the plist may name it via a symlink (/var -> /private/var)
    err = logs / "launchd.err.log"
    try:
        text, err_at = err.read_text(encoding="utf-8", errors="replace"), err.stat().st_mtime
    except OSError:
        text, err_at = "", 0.0
    lines = [ln for ln in text.splitlines() if (m := RUN_REFRESH_RE.search(ln)) and os.path.realpath(m.group(1)) == mine]
    ran = max((f.stat().st_mtime for f in logs.glob("runner-*.log")), default=0.0)
    if lines and err_at > ran:
        last = lines[-1]
        kind = "blocked" if "operation not permitted" in last.lower() else "failed"
        return {"id": "schedule", "ok": False, "optional": True, "alert": True, "kind": kind,
                "title": SCHEDULE_MSG[kind]["title"], "fix": SCHEDULE_MSG[kind]["fix"], "link": DOWNLOAD_URL,
                "detail": last[-300:]}  # developer detail for the Console / diag, never shown in the sentence
    if ran:
        when = datetime.fromtimestamp(ran).strftime("%a %-d %b at %H:%M")
        return {"id": "schedule", "ok": True, "optional": True, "kind": "started",
                "title": SCHEDULE_MSG["started"]["title"].format(when=when), "fix": ""}
    return None


def main(detect=False):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig")) if CONFIG.exists() else {}
    out = {"steps": [], "agent": agent.name()}
    email, slack, gmail, slack_source, miro, miro_source, names = (grok_steps if agent.name() == "grok" else claude_steps)(out["steps"])
    out["miro"] = miro
    # Remember which Slack / Miro route Claude has, so the job scripts allow the right tool prefix.
    # ...and the exact server names, so a Connect button signs in to the server that is really there
    updates = {k: v for k, v in (("slack_source", slack_source), ("miro_source", miro_source)) if v}
    cfg.update(updates)
    if updates or names:
        _save(updates, names)
    out["slack_source"] = slack_source or cfg.get("slack_source") or ""
    out["miro_source"] = miro_source or cfg.get("miro_source") or ""

    # Sources are pluggable: any ONE of them is enough to be useful
    out["steps"].append({"id": "channel", "ok": slack or gmail, "title": "At least one source connected (Slack or Gmail)",
                         "fix": "Connect whichever you actually use, above - one is enough. You can add the other any time." if not (slack or gmail) else ""})

    # Who am I on Slack (needed to find your own messages - Slack only)
    sid = cfg.get("slack_self_id") or ""
    if not sid and slack and detect:
        p = agent.run("Reply with ONLY the current logged-in user's Slack user id (it starts with U). "
                      "The Slack search tool's description states it; if not, use slack_search_users with query 'me'.",
                      ["slack.search_users"])
        m = re.search(r"\bU[0-9A-Z]{8,}\b", p.stdout or "")
        if m:
            sid = m.group(0)
            cfg["slack_self_id"] = sid
            _save({"slack_self_id": sid})
    out["steps"].append({"id": "self", "ok": bool(sid), "optional": not slack,
                         "title": f"Knows who you are on Slack{(' (' + sid + ')') if sid else ''}",
                         "fix": ("This fills in by itself once Slack is connected - nothing to do."
                                 if slack else "Only needed if you connect Slack.") if not sid else ""})

    # Mac only: the weekday morning refresh runs from launchd, and a failure there is otherwise silent (#24).
    # Optional, so a broken schedule never sends a set-up user back to the connection steps.
    sched = schedule_step() if sys.platform == "darwin" else None
    if sched:
        out["steps"].append(sched)

    out["all_ok"] = all(s["ok"] for s in out["steps"] if not s.get("optional"))
    out["email"] = email
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main(detect="--detect" in sys.argv)
