"""Connection check. Prints JSON describing what's set up and what the user still needs to do,
in plain language. Used by the app's first-run panel (auto-refreshes until all green).
Which AI it checks comes from config.json "agent" (claude by default - see agent.py).

    python doctor.py            -> JSON
    python doctor.py --detect   -> also asks the agent for the user's Slack id and saves it to config.json
    python doctor.py --recheck  -> Codex: ask Codex about Gmail / Slack again instead of reusing the last answer
"""
import json, os, re, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

from . import agent
from .messages import FAILURES as _F, say
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


def runs(cli):
    """Whether a CLI that was found actually starts: `<cli> --version` exits 0. A half-installed or broken CLI is on
    PATH but is not installed as far as the checklist goes."""
    return run([cli, "--version"], timeout=30)[0] == 0


def install_row(label, have, found=None):
    """The "<AI> is installed" row: "have" = the CLI was found and answers --version, "found" = it was found at all.
    When it is missing (or found but will not start) the row carries the Install button (connect "install", run by
    app.py from agent.install_cmd) and the exact commands, which the page shows before anything is pressed. If the
    installer itself cannot run here, it says so and offers no button."""
    r = {"id": "claude", "ok": have, "title": f"{label} is installed", "fix": ""}
    if have:
        return r
    broken = found if found is not None else False
    ic = agent.install_cmd()
    missing = [t for t in (ic or {}).get("needs", []) if not agent.prereq().get(t)]
    if not ic:
        r["fix"] = say("ai_no_installer", ai=label)
    elif missing:
        r["fix"] = say("ai_installer_blocked", ai=label, tools=", ".join(missing))
    else:
        r.update(fix=say("ai_broken" if broken else "ai_missing", ai=label, vendor=ic["vendor"]), connect="install",
                 command=ic["command"], agent=ic["agent"], command_id=ic["id"])  # sent back with the press: app.py runs nothing else
    return r


def claude_steps(steps):
    """Installed / signed in / Slack / Gmail / Miro, read straight from the Claude Code CLI (`claude auth status`,
    `claude mcp list`) - no model call. Each red row names in "connect" the setup step the page's button starts
    (agent.install_cmd / agent.login_cmd); rows without one need something no button can do."""
    found = shutil.which("claude") is not None
    have = found and runs("claude")
    steps.append(install_row("Claude", have, found))

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
    # Not signed in, but Claude still remembers an account: the sign-in ran out or was signed out (#25), not "never".
    login = {"id": "login", "ok": logged, "title": f"Signed in to Claude{(' as ' + email) if email and logged else ''}",
             "fix": "" if logged else say("needs_install", ai="Claude") if not have else
                    say("signin_expired", email=email) if email else say("signin_needed")}
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
            unlisted = (txt.strip().splitlines() or ["exit code " + str(rc)])[-1][:200]  # Console / diag only
    (slack_source, s_st, s_nm), (_, g_st, g_nm), (miro_source, m_st, m_nm) = (route(k, servers) for k in ("slack", "gmail", "miro"))
    names = {k: n for k, n in (("slack", s_nm), ("gmail", g_nm), ("miro", m_nm)) if n}  # for agent.login_cmd
    slack, gmail, miro = s_st == "connected", g_st == "connected", m_st == "connected"
    # the step that blocks every source row: installing Claude, else signing in (#25: not "Sign in" before it exists)
    first = say("needs_install", ai="Claude") if not have else say("needs_signin", ai="Claude")

    def row(id_, ok, title, state, connect, fix_missing, fix_auth, service):
        r = {"id": id_, "ok": ok, "optional": True, "title": title, "fix": ""}
        if not ok:
            if not logged:
                r["fix"] = first
            elif unlisted and not state:  # no button: installing would not fix a listing that did not finish
                r["fix"], r["detail"] = say("listing_failed"), unlisted  # what the CLI said: Console / diag, not the sentence
            elif not state:
                r["fix"], r["connect"] = fix_missing
            else:
                r["fix"], r["connect"] = fix_auth, connect
                if state == "failed":
                    r["fix"] = say("source_not_answering", service=service)
            if not r.get("connect"):
                r.pop("connect", None)
        return r

    steps.append(row("slack", slack, "Slack connected (optional)", s_st, "slack",
                     (say("slack_missing"), "slack_install"), say("slack_signin"), "Slack"))
    steps.append(row("gmail", gmail, "Gmail connected (optional)", g_st, "gmail",
                     (say("gmail_missing"), None), say("gmail_signin"), "Gmail"))
    steps.append(row("miro", miro, "Miro connected (optional, for the Roadmap card)", m_st, "miro",
                     (say("miro_missing"), None), say("miro_signin"), "Miro"))
    return email, slack, gmail, slack_source, miro, miro_source, names


def grok_steps(steps):
    """Installed / signed in / Slack / Gmail via the Grok CLI, its Slack plugin, and gmail_auth.py."""
    cli = agent.cli()
    found = bool(shutil.which("grok")) or Path(cli).exists()
    have = found and runs(cli)
    steps.append(install_row("Grok", have, found))

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


# ---- Codex: Gmail and Slack are ChatGPT connectors, so the only way to know they answer is to ask Codex once. That
# costs a run against the ChatGPT plan's allowance, so every attempt (a good answer, a timeout, a cold start) is kept in
# state/codex-probe.json and reused: never asked again within 30 seconds, even by Check again (--recheck); without
# Check again, a working answer is kept a day, "nothing connected" 15 minutes, a failed attempt 5 minutes. A new
# sign-in or account (auth.json changed) asks afresh. One run checks every source and reads the Slack user id too.
CODEX_PROBE = ROOT / "state" / "codex-probe.json"
PROBE_TIMEOUT_S = 90  # measured 2026-09-23: 38 s for the whole check with Gmail and Slack both answering
PROBE_KEEP_OK_S = 24 * 3600
PROBE_KEEP_BAD_S = 15 * 60
PROBE_KEEP_FAIL_S = 5 * 60
PROBE_MIN_GAP_S = 30
PROBE_WARMING_MAX = 3  # attempts that found no connector list before the rows give up waiting and offer Connect
PROBE_STEP = {"gmail": "gmail.get_profile with no arguments.",
              "slack": ("slack.slack_read_user_profile with no user id (it returns your own profile). If that fails, "
                        "slack.slack_list_user_channels once."),
              "miro": "one read-only call on the miro MCP server (for example, list the boards you can see)."}
PROBE_LINES = {"gmail": ["GMAIL: CONNECTED or NOT-CONNECTED"],
               "slack": ["SLACK: CONNECTED or NOT-CONNECTED", "SLACK_ID: <the Slack user id you read, which starts with U> or NONE"],
               "miro": ["MIRO: CONNECTED or NOT-CONNECTED"]}
PROBE_SRC_TOOLS = {"gmail": ["gmail.get_profile"], "slack": ["slack.read_user_profile", "slack.list_user_channels"],
                   "miro": ["miro.*"]}
PROBE_TOOLS = PROBE_SRC_TOOLS["gmail"] + PROBE_SRC_TOOLS["slack"]


def probe_prompt(srcs):
    """The probe's prompt for the sources it asks about (gmail / slack / miro, in that order)."""
    steps = "\n".join(f"{i}. {PROBE_STEP[x]}" for i, x in enumerate(srcs, 1))
    lines = "\n".join(ln for x in srcs for ln in PROBE_LINES[x])
    return ("Check which of this ChatGPT account's connections answer. Make these calls, one each, and no others:\n"
            f"{steps}\nThen reply with exactly these lines and nothing else:\n{lines}\n"
            "A connection is CONNECTED only if its call returned data. It is NOT-CONNECTED if the tool is not there, "
            "fails, or asks to connect or sign in.")
PROBE_PROOF = {"gmail": ("gmail.get_profile",), "slack": ("slack.slack_read_user_profile", "slack.slack_list_user_channels")}
# What the Gmail / Slack / Miro rows say when the probe itself could not answer (#25: what happened, what to do)
CODEX_SAID = {
    "timeout": "Codex took too long to answer, so Open Loops couldn't check your connections just now. Press Check again.",
    "limit": ("Your ChatGPT plan's Codex allowance is used up for now, so Open Loops couldn't check your connections. "
              "It comes back by itself, usually within a few hours. Press Check again then."),
    "expired": "Your ChatGPT sign-in has run out. Press Sign in to sign in again.",
    "failed": "Couldn't ask Codex about your connections just now. Press Check again.",
    "warming": "Codex is still getting ready (loading your ChatGPT connections). Press Check again in a minute.",
    "stale": ("Codex couldn't refresh its list of your ChatGPT connections (it is more than a day old). "
              "Press Check again in a minute."),
}
_codex_found = {}  # what the probe learnt that main() uses: the Slack user id, and which account it was


def parse_probe(text):
    """The probe's answer -> {"gmail", "slack", "miro": True | False | None (no such line), "slack_id": str}.
    Strict: only a whole line "GMAIL: CONNECTED" or "GMAIL: NOT-CONNECTED" counts (surrounding spaces allowed), so an
    answer that repeats the question ("GMAIL: CONNECTED or NOT-CONNECTED") says nothing."""
    got = {}
    lines = [ln.strip() for ln in (text or "").splitlines()]
    for key in ("gmail", "slack", "miro"):
        said = [ln.split(":", 1)[1].strip() for ln in lines if ln.upper().startswith(key.upper() + ":")]
        got[key] = True if said == ["CONNECTED"] else False if said == ["NOT-CONNECTED"] else None
    ids = [m.group(1) for ln in lines if (m := re.fullmatch(r"SLACK_ID:\s*(U[0-9A-Z]{8,})", ln))]
    got["slack_id"] = ids[0] if len(ids) == 1 else ""
    return got


def _probe_keep(old, recheck, now):
    """Whether the last attempt can answer this check -> bool."""
    age = now - float(old.get("at") or 0)
    if not 0 <= age:
        return False
    if age < PROBE_MIN_GAP_S:
        return True
    if recheck:
        return False
    keep = PROBE_KEEP_FAIL_S if old.get("why") else PROBE_KEEP_OK_S if (old.get("gmail") or old.get("slack")) else PROBE_KEEP_BAD_S
    return age < keep


def codex_probe(sig, want_miro, recheck=False, now=None):
    """Gmail / Slack (/ Miro) through Codex -> {"gmail", "slack", "miro", "slack_id", "why", "tries", "at", "sig"}.
    "why" is a key of CODEX_SAID or agent.CODEX_REFUSE when the probe could not answer; the sources are then None
    (unknown, not missing). A source is True only when the run exited 0, its line says exactly CONNECTED, and its
    own tool call is in the run's events as completed without an error. A failed run (timeout, 401, spent allowance)
    wins over whatever text came back."""
    from .store import read_json, write_json
    import time as _t
    now = _t.time() if now is None else now
    old = read_json(CODEX_PROBE) or {}
    if old.get("sig") == sig and old.get("miro_asked") == want_miro and _probe_keep(old, recheck, now):
        return old
    # Ask only about the sources whose tools are in the connector list Codex last fetched: one that is not there is
    # not connected in ChatGPT (that is where the list comes from), so it gets Connect without a question, and a
    # Gmail-only account is never held up waiting for Slack. With no list yet, ask about both: the run warms up first.
    listed = {t for names in agent.codex_connectors(agent.codex_home(sig.split(":", 1)[0])).values() for t in names}
    srcs = [x for x in ("gmail", "slack") if not listed or set(agent._qualify(PROBE_SRC_TOOLS[x])) & listed]
    srcs += ["miro"] if want_miro else []
    if not srcs:
        res = {"gmail": False, "slack": False, "miro": None, "slack_id": "", "why": "", "tries": 0,
               "account": sig.split(":", 1)[0], "at": now, "sig": sig, "miro_asked": want_miro}
        write_json(CODEX_PROBE, res)
        return res
    p = agent.codex_run(probe_prompt(srcs), [t for x in srcs for t in PROBE_SRC_TOOLS[x]],
                        timeout=PROBE_TIMEOUT_S, effort_="low")
    got = parse_probe(p.stdout)
    for x in ("gmail", "slack"):
        if x not in srcs or x in (getattr(p, "dropped", []) or []):
            got[x] = False  # not in Codex's list: not connected in ChatGPT
    ok = set(getattr(p, "tools_ok", []) or [])
    refused = getattr(p, "refused", "") or ""
    if refused == "nosources":  # the list has neither Gmail nor Slack: both not connected in ChatGPT
        why = ""
        got.update(gmail=False, slack=False)
    elif refused:  # not run, or failed: keyring / signin / link go on the sign-in row; a warm-up's failure is its own
        why = {"cold": "warming", "unlisted": "failed", "start": "failed", "notools": "failed"}.get(refused, refused)
    else:  # the same order as agent.codex_failure: a failed run beats whatever text came back
        why = agent.codex_failure(p.returncode, getattr(p, "errors", []), getattr(p, "codex_stderr", "")) or (
            "failed" if all(got[x] is None for x in srcs if x in ("gmail", "slack") and x not in (getattr(p, "dropped", []) or [])) else "")
        # #44: a run whose model saw a source's tools and called none is no longer refused by agent.py. For the probe
        # that is still no answer about that source (its line alone is not believed): "couldn't ask", not "not connected".
        called = {u.split("/", 1)[-1] for u in getattr(p, "tools_used", []) or []}
        if not why and any(x in PROBE_PROOF and x not in (getattr(p, "dropped", []) or []) and not called.intersection(PROBE_PROOF[x])
                           for x in srcs):
            why = "failed"
    if why:
        got.update(gmail=None, slack=None, miro=None, slack_id="")
    else:
        for src_, tools_ in PROBE_PROOF.items():
            got[src_] = bool(got[src_]) and bool(ok.intersection(tools_))
        got["miro"] = bool(want_miro and got["miro"] and any(u.startswith("miro/") for u in getattr(p, "tools_used", [])))
        if not got["slack"]:
            got["slack_id"] = ""
    # Warming attempts are counted per account and the count is kept through any other outcome (a timeout, a spent
    # allowance): only a probe that answered, or another account, starts it again. From the third on, the rows stop
    # saying "getting ready" and offer Connect, and stay that way.
    account = sig.split(":", 1)[0]
    tries = int(old.get("tries") or 0) if old.get("account") == account else 0
    if why == "warming":
        tries += 1
        if tries >= PROBE_WARMING_MAX:
            why, got = "", dict(got, gmail=False, slack=False, miro=False if want_miro else None)
    elif not why:
        tries = 0
    res = {**got, "why": why, "tries": tries, "account": account, "at": now, "sig": sig, "miro_asked": want_miro}
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / "codex-probe-last.log").write_text(
            f"rc={p.returncode} why={why or '-'}\n--- final message ---\n{p.stdout}\n--- stderr ---\n{(p.stderr or '')[-3000:]}",
            encoding="utf-8")
        write_json(CODEX_PROBE, res)  # every attempt, so the cooldown holds for failures too
    except OSError:
        pass
    return res


def codex_steps(steps, recheck=False):
    """Installed / signed in with ChatGPT / Gmail / Slack / Miro for Codex. Sign-in is read from `codex login status`
    and auth.json (no model call); the sources from one cached probe run (codex_probe)."""
    cli = agent.cli()
    found = bool(shutil.which("codex")) or Path(cli).exists()
    have = found and runs(cli)
    steps.append(install_row("Codex", have, found))

    auth = agent.codex_auth()
    logged, mode = False, ""
    if have:
        rc, txt = run([cli, "login", "status"])
        low = txt.lower()
        logged = rc == 0 and "logged in" in low and "not logged in" not in low
        mode = auth["mode"] or ("keyring" if logged and "chatgpt" in low else "apikey" if "api key" in low else "")
    chatgpt = logged and mode == "chatgpt"
    want_miro = agent.codex_has_miro()
    probe = codex_probe(auth["account"] + ":" + auth["sig"], want_miro, recheck) if chatgpt else {}
    pwhy = probe.get("why") or ""
    ok = chatgpt and pwhy not in ("expired", "keyring", "signin", "link")
    login = {"id": "login", "ok": ok, "title": f"Signed in to ChatGPT{(' as ' + auth['email']) if ok and auth['email'] else ''}"}
    if not have:
        login["fix"] = "Install Codex first (the row above)."
    elif logged and mode == "keyring" or pwhy == "keyring":
        login["fix"] = agent.CODEX_REFUSE["keyring"].format(store=agent.codex_keyring_store())  # no button: a terminal step
    elif pwhy == "link":
        login["fix"] = agent.CODEX_REFUSE["link"]
    elif logged and mode == "apikey":
        login.update(fix="Codex is signed in with an API key, which can't use Gmail or Slack. Sign in with your ChatGPT "
                         "account instead: press Sign in.", connect="login")
    elif pwhy == "expired":
        login.update(fix=CODEX_SAID["expired"], connect="login")
    elif not ok:
        login.update(fix="Press Sign in: your browser opens the ChatGPT sign-in page. Use the ChatGPT account whose Gmail "
                         "and Slack you want Open Loops to read. If the browser never comes back to Open Loops, open "
                         "Terminal, type codex login --device-auth, press Enter and follow what it says, then press Check again.",
                     connect="login")
    else:
        login["fix"] = ""
    steps.append(login)

    first = "Sign in to ChatGPT first (the row above)."
    why = CODEX_SAID.get(pwhy, "")

    def row(id_, title, name):
        state = probe.get(id_)
        r = {"id": id_, "ok": bool(ok and state), "optional": True, "title": title, "fix": ""}
        if r["ok"]:
            return r
        if not ok:
            r["fix"] = first
        elif state is None and why:
            r["fix"] = why
        else:
            r["fix"] = (f"{name} is connected in your ChatGPT account, not in Open Loops. Press Connect {name}: ChatGPT's "
                        f"apps page opens in your browser. Connect {name} there, come back and press Check again.")
            r["connect"] = id_
        return r

    slack_row, gmail_row = row("slack", "Slack connected (optional)", "Slack"), row("gmail", "Gmail connected (optional)", "Gmail")
    steps += [slack_row, gmail_row]
    miro = {"id": "miro", "ok": bool(ok and want_miro and probe.get("miro")), "optional": True,
            "title": "Miro connected (optional, for the Roadmap card)", "fix": ""}
    if not miro["ok"]:
        miro["fix"] = ("Miro isn't available with Codex, so the Roadmap card stays off. To use it, choose Claude "
                       "under Settings, Your AI." if not want_miro else first if not ok else why or
                       "Codex has a Miro server but it didn't answer. Open a terminal, type codex mcp login miro, "
                       "press Enter and follow what it says, then press Check again.")
    steps.append(miro)
    _codex_found.clear()
    if ok and not pwhy:  # a real answer: its Slack id (or none) is the truth for this account
        _codex_found.update(account=auth["account"], slack_self_id=(probe.get("slack_id") or "") if slack_row["ok"] else "")
    return auth["email"] if ok else "", slack_row["ok"], gmail_row["ok"], "", miro["ok"], "", {}


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
# Plain words (issue #25): what happened in one sentence, one thing to do, no jargon, no paths. The failures are
# worded in messages.py with every other failure; "started" is not a failure, so it lives here.
SCHEDULE_MSG = {
    "blocked": {"title": _F["schedule_blocked"]["what"], "fix": _F["schedule_blocked"]["fix"]},
    "failed": {"title": _F["schedule_failed"]["what"], "fix": _F["schedule_failed"]["fix"]},
    "started": {"title": "The automatic morning refresh last started by itself on {when}.", "fix": ""},
}
# where "the latest installer" is: the page shows it as a button under the red row (the installer moves old installs)
DOWNLOAD_URL = "https://github.com/OscarC178/Open-Loops/releases/latest"
RUN_REFRESH_RE = re.compile(r":\s(/[^:]*/scripts/run-refresh\.sh)")


STARTED_RE = re.compile(r"^openloops-refresh started (\S+) (.+)$")   # written by scripts/run-refresh.sh
TAIL_BYTES = 256 * 1024   # the latest lines are what matter; a years-old log is not read whole every minute


def usual_places():
    """Where a Mac install lives unless the installer was told otherwise: install.sh's DEFAULT_DEST, and ~/Documents
    where installs before #24 went (the installer is exactly the fix for those). A copy anywhere else is a --dest test
    copy (or a checkout): the installer would update the copy in the usual place, not it (#25, from the #31 test)."""
    home = Path.home()
    return [home / "Library" / "Application Support" / "OpenLoops", home / "Documents" / "OpenLoops"]


def schedule_step(logs=None, root=None, usual=None):
    """The weekday morning refresh, as far as launchd.err.log shows. Returns a checklist step or None (no evidence).

    That log gets two kinds of line about an install: launchd's own start failures, e.g. #24's
    `/bin/bash: .../scripts/run-refresh.sh: Operation not permitted` (a background job may not read ~/Documents),
    and "openloops-refresh started <time> <root>", which run-refresh.sh writes to stderr as soon as it runs.
    Only lines about THIS install count (script path or root, symlinks resolved), and the LAST of them decides,
    by its position in the file - never the file's modified time, which a line about another install can move.
    - last is a failure -> red ("blocked" for Operation not permitted, else "failed")
    - last is a start   -> green "started" with its own time. It proves the script ran, not that the refresh
      inside it succeeded, and says only that.
    A red row for a copy outside the usual place (a test copy) says the installer won't fix it, with no download link."""
    logs, root = Path(logs or LOGS), Path(root or ROOT)
    mine = os.path.realpath(root / "scripts" / "run-refresh.sh")  # the plist may name it via a symlink (/var -> /private/var)
    home = os.path.realpath(root)
    err = logs / "launchd.err.log"
    try:
        with open(err, "rb") as f:
            f.seek(max(0, err.stat().st_size - TAIL_BYTES))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    last = None
    for ln in text.splitlines():
        if (m := STARTED_RE.match(ln.strip())) and os.path.realpath(m.group(2)) == home:
            last = ("started", m.group(1), ln)
        elif (m := RUN_REFRESH_RE.search(ln)) and os.path.realpath(m.group(1)) == mine:
            last = ("failed", None, ln)
    if last is None:
        return None
    kind, when, ln = last
    if kind == "started":
        try:
            when = datetime.strptime(when, "%Y-%m-%dT%H:%M:%S%z").strftime("%a %-d %b at %H:%M")
        except ValueError:
            pass  # keep the raw text rather than hide the row
        return {"id": "schedule", "ok": True, "optional": True, "kind": "started",
                "title": SCHEDULE_MSG["started"]["title"].format(when=when), "fix": ""}
    kind = "blocked" if "operation not permitted" in ln.lower() else "failed"
    r = {"id": "schedule", "ok": False, "optional": True, "alert": True, "kind": kind,
         "title": SCHEDULE_MSG[kind]["title"], "fix": SCHEDULE_MSG[kind]["fix"], "link": DOWNLOAD_URL,
         "detail": ln[-300:]}  # developer detail for the Console / diag, never shown in the sentence
    if home not in [os.path.realpath(p) for p in (usual if usual is not None else usual_places())]:  # a test copy
        r.update(fix=say("schedule_test_copy"), test_copy=True)
        r.pop("link")
    return r


def main(detect=False, recheck=False):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig")) if CONFIG.exists() else {}
    out = {"steps": [], "agent": agent.name()}
    email, slack, gmail, slack_source, miro, miro_source, names = (grok_steps if agent.name() == "grok" else
        (lambda s: codex_steps(s, recheck)) if agent.name() == "codex" else claude_steps)(out["steps"])
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
                         "fix": say("no_source") if not (slack or gmail) else ""})

    # Who am I on Slack (needed to find your own messages - Slack only)
    sid = cfg.get("slack_self_id") or ""
    if detect and agent.name() == "codex" and _codex_found.get("account"):
        # Codex: the probe itself read the Slack id. A different id replaces the stored one; so does a different ChatGPT
        # account (its Slack may be another person or workspace), with nothing if that account has no Slack.
        found, acct = _codex_found.get("slack_self_id") or "", _codex_found["account"]
        new = found or (sid if cfg.get("codex_account") == acct else "")
        if new != sid or cfg.get("codex_account") != acct:
            sid = cfg["slack_self_id"] = new
            _save({"slack_self_id": new, "codex_account": acct})
    if not sid and slack and detect and agent.name() != "codex":  # Codex asked already, in the probe: no second run
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
                         # Slack ticked but no id: the lookup ran (the page always asks with --detect) and failed
                         "fix": (say("slack_id_unknown") if slack else "Only needed if you connect Slack.") if not sid else ""})

    # Mac only: the weekday morning refresh runs from launchd, and a failure there is otherwise silent (#24).
    # Optional, so a broken schedule never sends a set-up user back to the connection steps.
    sched = schedule_step() if sys.platform == "darwin" else None
    if sched:
        out["steps"].append(sched)

    out["all_ok"] = all(s["ok"] for s in out["steps"] if not s.get("optional"))
    out["email"] = email
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main(detect="--detect" in sys.argv, recheck="--recheck" in sys.argv)
