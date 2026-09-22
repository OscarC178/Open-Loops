"""Connection check. Prints JSON describing what's set up and what the user still needs to do,
in plain language. Used by the app's first-run panel (auto-refreshes until all green).
Which AI it checks comes from config.json "agent" (claude by default - see agent.py).

    python doctor.py            -> JSON
    python doctor.py --detect   -> also asks the agent for the user's Slack id and saves it to config.json
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

from . import agent
from .paths import ROOT
CONFIG = ROOT / "config.json"
WIN = sys.platform == "win32"


def run(args, timeout=60, **kw):
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, shell=WIN, **kw)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa
        return 1, str(e)


def claude_steps(steps):
    """Installed / signed in / Slack / Gmail via the Claude Code CLI and its connectors."""
    have = shutil.which("claude") is not None
    steps.append({"id": "claude", "ok": have, "title": "Claude is installed",
                  "fix": "Run the installer again, or ask IT to install Claude Code." if not have else ""})

    logged, email = False, ""
    if have:
        rc, txt = run(["claude", "auth", "status"])
        logged = bool(re.search(r'"loggedIn"\s*:\s*true', txt))
        e = re.search(r'"emailAddress"\s*:\s*"([^"]+)"', txt)
        email = e.group(1) if e else ""
        if not email:  # fall back to the account stored by Claude Code
            try:
                cj = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8-sig"))
                email = cj.get("oauthAccount", {}).get("emailAddress", "")
            except Exception:
                pass
    steps.append({"id": "login", "ok": logged, "title": f"Signed in to Claude{(' as ' + email) if email else ''}",
                  "fix": "Click 'Open Claude' below, then follow the sign-in link it shows. Use your work Google account." if not logged else ""})

    slack = gmail = miro = False
    slack_source = ""  # "plugin" (plugin:slack:slack) or "connector" (claude.ai Slack) - jobs need the right prefix
    miro_source = ""   # same two routes for Miro; the plugin wins if both are connected
    if logged:
        rc, txt = run(["claude", "mcp", "list"], timeout=90)
        for line in txt.splitlines():
            low = line.lower()
            up = "connected" in low and "failed" not in low
            if "slack" in low and up:
                slack = True
                slack_source = "plugin" if "plugin" in low else "connector"
            if "gmail" in low and up:
                gmail = True
            if "miro" in low and up:
                miro = True
                # plugin:miro:miro / claude.ai Miro / a user-added server literally named "miro"
                src = "plugin" if "plugin" in low else ("connector" if "claude.ai" in low else "server")
                if miro_source != "plugin":
                    miro_source = src
    steps.append({"id": "slack", "ok": slack, "optional": True, "title": "Slack connected (optional)",
                  "fix": "Click 'Open Claude', type /mcp and press Enter, choose Slack, then Authenticate and approve in the browser." if not slack else ""})
    steps.append({"id": "gmail", "ok": gmail, "optional": True, "title": "Gmail connected (optional)",
                  "fix": "Click 'Open Claude', type /mcp and press Enter, choose 'claude.ai Gmail', then Authenticate and approve in the browser." if not gmail else ""})
    steps.append({"id": "miro", "ok": miro, "optional": True, "title": "Miro connected (optional, for the Roadmap card)",
                  "fix": "Click 'Open Claude', type /mcp and press Enter, choose Miro (the claude.ai connector, or the miro plugin if installed), Authenticate and approve in the browser. "
                         "No Miro entry? Add the server first: claude mcp add --scope user --transport http miro https://mcp.miro.com/ - then /mcp to authenticate." if not miro else ""})
    return email, slack, gmail, slack_source, miro, miro_source


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
    return email, slack, gmail, "", False, ""


def main(detect=False):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig")) if CONFIG.exists() else {}
    out = {"steps": [], "agent": agent.name()}
    email, slack, gmail, slack_source, miro, miro_source = (grok_steps if agent.name() == "grok" else claude_steps)(out["steps"])
    out["miro"] = miro
    # Remember which Slack / Miro route Claude has, so the job scripts allow the right tool prefix.
    changed = False
    for key, val in (("slack_source", slack_source), ("miro_source", miro_source)):
        if val and cfg.get(key) != val:
            cfg[key] = val
            changed = True
    if changed:
        CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
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
            CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    out["steps"].append({"id": "self", "ok": bool(sid), "optional": not slack,
                         "title": f"Knows who you are on Slack{(' (' + sid + ')') if sid else ''}",
                         "fix": ("This fills in by itself once Slack is connected - nothing to do."
                                 if slack else "Only needed if you connect Slack.") if not sid else ""})

    out["all_ok"] = all(s["ok"] for s in out["steps"] if not s.get("optional"))
    out["email"] = email
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main(detect="--detect" in sys.argv)
