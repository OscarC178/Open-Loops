"""Every failure Open Loops can detect and a person can fix, in plain words, in one table (#25).

Each entry has:
  "what"    what happened, in one sentence a non-technical colleague would understand
  "fix"     what to do about it, in one sentence (a button's name where the page has one)
  "button"  the checklist step whose button fixes it (doctor.py's "connect" value), or None
  "fix_win" optional: the fix as Windows says it, where the Mac wording names Mac places
  "fix_follow" optional: the fix when the row has no button to press, pointing at the row itself ({row}, its title)
  "fix_busy" optional: the fix while that row's own step is already under way (its button shows a spinner instead)
  "fix_test", "fix_test_win" optional: the fix on a test copy (config.json "test_copy" or "isolated", #50), which has
            no Desktop or Applications icon; part() / for_page() take test=True for it

The rules (#25): name who did it (Google, Slack, Claude, your Mac's privacy settings), never "the agent"; no exit
codes, file paths, tracebacks or tool names; UK English. The developer detail (the CLI's last line, the log
tail) goes to the page's Console and /api/diag, never into these sentences. tests/test_messages.py holds the rules.

doctor.py and app.py look their sentences up here with say(); the page gets the whole table (app.py puts it in
index.html as it serves it, for the platform it runs on), so it can still say "Open Loops isn't running" once the
server has gone. Placeholders ({ai}, {vendor}, ...) are filled by whoever shows the sentence.
"""
import json
import os
import re
import string
import sys
from datetime import datetime

from .paths import ROOT

FAILURES = {
    # ---- the page and the server
    "server_offline": {
        "what": "Open Loops isn't running on this computer.",
        "fix": "Open it from the Open Loops icon on your Desktop or in Applications.",
        "fix_win": "Open it from the Open Loops icon on your Desktop or in the Start menu.",
        # a test copy (install.sh --dest --no-app / --isolated) has no icon to open it from (#50)
        "fix_test": "Start it again by typing python3 -m openloops.app in Terminal, in this copy's folder.",
        "fix_test_win": "Start it again by typing python -m openloops.app in PowerShell, in this copy's folder.",
        "button": None},
    "server_error": {
        "what": "Open Loops couldn't answer this page just now.",
        "fix": "Reload the page, and if it happens again press Copy all in the Console at the bottom and send it to "
               "whoever set Open Loops up.",
        "button": None},
    "port_moved": {
        "what": "Another program is using the address Open Loops normally opens on, so it opened on port {port} instead.",
        "fix": "Nothing to do: your browser goes to the new address by itself.",
        "button": None},
    "no_free_port": {
        "what": "Other programs are using every address Open Loops can open on, so it couldn't start.",
        "fix": "Restart your computer, then open Open Loops again.",
        "button": None},
    "config_unreadable": {
        "what": "Open Loops couldn't read its settings file, so nothing was changed.",
        "fix": "Press Copy all in the Console at the bottom and send it to whoever set Open Loops up.",
        "button": None},

    # ---- the connection check (doctor.py)
    "check_failed": {
        "what": "Open Loops couldn't run its connection check just now; this isn't a problem with your Slack or email.",
        "fix": "Press Retry, or wait: the page tries again by itself.",
        "button": None},
    "ai_missing": {
        "what": "Open Loops couldn't find {ai} on this computer.",
        "fix": "Press Install {ai}: it downloads {ai} from {vendor} and takes a minute or two.",
        "button": "install"},
    "ai_broken": {
        "what": "{ai} is on this computer but won't start.",
        "fix": "Press Install {ai} to install it again: it downloads {ai} from {vendor} and takes a minute or two.",
        "button": "install"},
    "ai_no_installer": {
        "what": "Open Loops couldn't find {ai} on this computer.",
        "fix": "Ask IT to install it, then press Check again.",
        "button": None},
    "ai_installer_blocked": {
        "what": "Open Loops couldn't find {ai} on this computer, and can't install it here because a tool the installer "
                "needs is missing ({tools}).",
        "fix": "Ask IT to install {ai}, then press Check again.",
        "button": None},
    "needs_install": {
        "what": "{ai} isn't installed yet.",
        "fix": "Install {ai} first (the row above).",
        "button": None},
    "needs_signin": {
        "what": "Open Loops isn't signed in to {ai} yet.",
        "fix": "Sign in to {ai} first (the row above).",
        "button": None},
    "signin_needed": {
        "what": "Open Loops isn't signed in to Claude on this computer yet.",
        "fix": "Press Sign in: your browser opens the Claude sign-in page, where you use your work Google account.",
        "button": "login"},
    "signin_expired": {
        "what": "Claude has signed {email} out on this computer, so Open Loops can't read anything.",
        "fix": "Press Sign in: your browser opens the Claude sign-in page.",
        "button": "login"},
    "listing_failed": {
        "what": "Claude didn't answer when Open Loops asked which connections it has, so this one couldn't be checked.",
        "fix": "Press Check again.",
        "button": None},
    "slack_missing": {
        "what": "Slack isn't set up in Claude yet.",
        "fix": "Press Install Slack plugin (it takes about half a minute), then Connect Slack.",
        "button": "slack_install"},
    "slack_signin": {
        "what": "Slack isn't connected to Claude yet.",
        "fix": "Press Connect Slack: your browser opens Slack's sign-in page, where you click Allow.",
        "button": "slack"},
    "gmail_missing": {
        "what": "Gmail isn't added to your Claude account yet.",
        "fix": "Add it on claude.ai under Settings → Connectors → Gmail, then press Check again.",
        "button": None},
    "gmail_signin": {
        "what": "Gmail isn't connected to Claude yet.",
        "fix": "Press Connect Gmail: your browser opens Google's sign-in page, where you click Allow.",
        "button": "gmail"},
    "miro_missing": {
        "what": "Miro isn't added to your Claude account yet.",
        "fix": "Add it on claude.ai under Settings → Connectors → Miro, then press Check again and Connect Miro.",
        "button": None},
    "miro_signin": {
        "what": "Miro isn't connected to Claude yet.",
        "fix": "Press Connect Miro: your browser opens Miro's sign-in page, where you pick the team and click Allow.",
        "button": "miro"},
    "source_not_answering": {
        "what": "{service} is set up in Claude but didn't answer just now.",
        "fix": "Press Connect {service} to sign in to it again.",
        "button": None},  # the row's own Connect step
    "no_source": {
        "what": "Neither Slack nor Gmail is connected yet.",
        "fix": "Connect whichever you use above: one is enough, and you can add the other any time.",
        "button": None},
    "slack_id_unknown": {
        "what": "Slack is connected, but Open Loops couldn't work out which Slack account is yours.",
        "fix": "Press Check again: it asks Slack again.",
        "button": None},

    # ---- a refresh reading state.json (refresh.parse_when, #51): a stored cursor that is not a date. The refresh
    # refuses (reading back a guessed window would bring closed email loops back as new ones, #52 review); the job
    # prints this and records it (report_refusal), so the page's toast says it.
    "cursor_unreadable": {
        "what": "Open Loops can't read when it last checked.",
        "fix": "Press Start over in Settings, or fix state.json.",
        "button": None},

    # ---- the Mac's weekday morning refresh (doctor.schedule_step, #24 / #31)
    "schedule_blocked": {
        "what": "Your Mac's privacy settings stopped the automatic morning refresh, so your list only updates when you press Refresh.",
        "fix": "Download and run the latest Open Loops installer.",
        "button": None},  # the row carries the download page as its link
    "schedule_failed": {
        "what": "The automatic morning refresh could not start, so your list only updates when you press Refresh.",
        "fix": "Download and run the latest Open Loops installer.",
        "button": None},
    "schedule_test_copy": {
        "what": "This copy of Open Loops is a test copy in a folder of its own, so the installer won't fix its morning refresh.",
        "fix": "Press Refresh when you want a fresh list.",
        "button": None},

    # ---- the setup buttons (app.py /api/connect, the page's connectStep)
    "connect_busy": {
        "what": "That sign-in is already under way, perhaps in another tab.",
        "fix": "Finish it in your browser: this row updates by itself.",
        "button": None},
    "install_busy": {
        "what": "Open Loops is already installing {ai}.",
        "fix": "Wait for it to finish: this row updates by itself.",
        "button": None},
    "connect_start_failed": {
        "what": "Open Loops couldn't start that step.",
        "fix": "Press the button again.",
        "button": None},
    "connect_failed": {
        "what": "The {party} sign-in didn't finish.",
        "fix": "Press the button to try again, and click Allow when your browser asks.",
        "button": None},
    "slack_install_failed": {
        "what": "Claude couldn't install the Slack plugin.",
        "fix": "Press Install Slack plugin to try again.",
        "button": "slack_install"},
    "connect_no_change": {
        "what": "The sign-in finished, but {party} still doesn't show as connected.",
        "fix": "Press the button again, and make sure you click Allow in your browser.",
        "button": None},
    "connect_timeout": {
        "what": "Open Loops stopped waiting for your browser after 5 minutes.",
        "fix": "Press the button again when you're ready to sign in.",
        "button": None},
    "ai_changed": {
        "what": "The AI chosen in Settings changed since this page showed the Install button, so nothing was installed.",
        "fix": "Press Check again.",
        "button": None},
    "app_updated": {
        "what": "Open Loops was updated since this page loaded, so nothing was installed.",
        "fix": "Press Check again.",
        "button": None},

    # ---- how an install ended (app.run_install; was agent.INSTALL_SAID, #32)
    "install_network": {
        "what": "{ai}'s installer couldn't download because this computer couldn't reach {vendor}.",
        "fix": "Check your internet connection, then press Install {ai} again.",
        "button": "install"},
    "install_vendor": {
        "what": "{vendor}'s download site answered with an error, so {ai} wasn't installed; the problem is at {vendor}'s end.",
        "fix": "Wait a few minutes, then press Install {ai} again.",
        "button": "install"},
    "install_download": {
        "what": "{ai}'s installer couldn't download.",
        "fix": "Press Install {ai} again.",
        "button": "install"},
    "install_check": {
        "what": "{ai} was installed but won't start.",
        "fix": "Press Install {ai} to try again, or ask IT to install {ai}.",
        "button": "install"},
    "install_error": {
        "what": "{ai}'s installer stopped with an error.",
        "fix": "Press Install {ai} to try again, and if it fails again open Show the exact command below and paste it "
               "into Terminal (Windows: PowerShell).",
        "button": "install"},
    "install_timeout": {
        "what": "The install took longer than {limit}, so Open Loops stopped it.",
        "fix": "Press Install {ai} to try again.",
        "button": "install"},
    "install_start": {
        "what": "Open Loops couldn't start {ai}'s installer.",
        "fix": "Press Install {ai} to try again.",
        "button": "install"},

    # ---- a job that ended badly (app.run_job; the page's toast)
    "job_signed_out": {
        "what": "{job} stopped because {ai} has signed you out.",
        "fix": "Press Sign in on the connection checklist, which is back on the Home tab.",
        "button": "login"},
    "job_usage_limit": {
        "what": "{job} stopped because your {ai} plan's usage limit has been reached.",
        "fix": "Try again once the limit resets.",
        "button": None},
    "job_network": {
        "what": "{job} stopped because this computer couldn't reach {ai}.",
        "fix": "Check your internet connection, then try again.",
        "button": None},
    "job_failed": {
        "what": "{job} didn't finish.",
        "fix": "Try again, and if it fails again press Copy all in the Console at the bottom and send it to whoever "
               "set Open Loops up.",
        "button": None},
    "job_start_failed": {
        "what": "Open Loops couldn't start {job_lower}.",
        "fix": "Close this tab, open Open Loops again, then try again.",
        "button": None},

    # ---- Codex (ChatGPT): why Open Loops would not run it, or a run failed (agent.CODEX_REFUSE, #40). A job prints the
    # sentence itself; the checklist shows the same words.
    "codex_keyring": {
        "what": "Codex keeps your sign-in in {store}, which Open Loops can't share with its own settings yet.",
        "fix": "Run codex logout, then codex login again with file storage (see INSTALL.md, Codex (ChatGPT)).",
        "button": None},
    "codex_signin": {
        "what": "Codex isn't signed in with a ChatGPT account.",
        "fix": "Press Sign in on the connection checklist.",
        "button": "login"},
    "codex_link": {
        "what": "Open Loops couldn't link Codex's sign-in into its own settings folder, so it didn't run Codex.",
        "fix": "Check that the Open Loops folder isn't read-only, then press Check again.",
        "button": None},
    "codex_cold": {
        "what": "Codex is still getting ready (loading your ChatGPT connections), so Open Loops didn't run it.",
        "fix": "Press Check again on the connection checklist in a minute.",
        "button": None},
    "codex_limit": {
        "what": "Your ChatGPT plan's Codex allowance is used up for now, so Open Loops couldn't run Codex.",
        "fix": "Wait: it comes back by itself, usually within a few hours.",
        "button": None},
    "codex_expired": {
        "what": "Your ChatGPT sign-in has run out, so Open Loops couldn't run Codex.",
        "fix": "Press Sign in on the connection checklist.",
        "button": "login"},
    "codex_failed": {
        "what": "Codex stopped with an error before it could start this job, so nothing was saved.",
        "fix": "Try again in a minute.",
        "button": None},
    "codex_unlisted": {
        "what": "Codex used a tool this job did not allow, so nothing was saved.",
        "fix": "Try again, and if it happens again press Copy all in the Console and send it to whoever set Open Loops up.",
        "button": None},
    "codex_notools": {
        "what": "Codex couldn't reach its Gmail or Slack tools this time, so nothing was saved.",
        "fix": "Try again in a minute.",
        "button": None},
    "codex_stale": {
        "what": "Codex couldn't refresh its list of your ChatGPT connections (it is more than a day old), so Open Loops didn't run it.",
        "fix": "Press Check again on the connection checklist in a minute.",
        "button": None},
    "codex_nosources": {
        "what": "Neither Gmail nor Slack is connected in this ChatGPT account, so there was nothing to read.",
        "fix": "Connect one on chatgpt.com/apps, then press Check again.",
        "button": None},
    "codex_timeout": {
        "what": "Codex took longer than {limit}, so Open Loops stopped it and saved nothing.",
        "fix": "Try again in a minute.",
        "button": None},
    "codex_start": {
        "what": "Open Loops couldn't start Codex.",
        "fix": "Press Check again on the connection checklist, which offers Install Codex if it's missing.",
        "button": None},

    # ---- Codex on the checklist (doctor.codex_steps / CODEX_SAID, #40)
    "codex_check_timeout": {
        "what": "Codex took too long to answer, so Open Loops couldn't check your connections just now.",
        "fix": "Press Check again.",
        "button": None},
    "codex_check_limit": {
        "what": "Your ChatGPT plan's Codex allowance is used up for now, so Open Loops couldn't check your connections.",
        "fix": "It comes back by itself, usually within a few hours: press Check again then.",
        "button": None},
    "codex_check_expired": {
        "what": "Your ChatGPT sign-in has run out.",
        "fix": "Press Sign in to sign in again.",
        "button": "login"},
    "codex_check_failed": {
        "what": "Couldn't ask Codex about your connections just now.",
        "fix": "Press Check again.",
        "button": None},
    "codex_check_warming": {
        "what": "Codex is still getting ready (loading your ChatGPT connections).",
        "fix": "Press Check again in a minute.",
        "button": None},
    "codex_check_stale": {
        "what": "Codex couldn't refresh its list of your ChatGPT connections (it is more than a day old).",
        "fix": "Press Check again in a minute.",
        "button": None},
    "codex_apikey": {
        "what": "Codex is signed in with an API key, which can't use Gmail or Slack.",
        "fix": "Sign in with your ChatGPT account instead: press Sign in.",
        "button": "login"},
    "codex_signin_needed": {
        "what": "Open Loops isn't signed in to ChatGPT through Codex yet.",
        "fix": "Press Sign in to open the ChatGPT sign-in page, using the account whose Gmail and Slack Open Loops should "
               "read (if the browser never comes back, type codex login --device-auth in Terminal and follow what it "
               "says, then press Check again).",
        "button": "login"},
    "codex_source_missing": {
        "what": "{service} is connected in your ChatGPT account, not in Open Loops.",
        "fix": "Press Connect {service} to open ChatGPT's apps page, connect {service} there, then come back and press Check again.",
        "button": None},  # the row's own Connect step
    "codex_browser_failed": {
        "what": "Open Loops couldn't open your browser at ChatGPT's apps page.",
        "fix": "Go to chatgpt.com/apps yourself, connect {service} there, then press Check again.",
        "button": None},
    "codex_no_miro": {
        "what": "Miro isn't available with Codex, so the Roadmap card stays off.",
        "fix": "To use it, choose Claude under Settings, Your AI.",
        "button": None},
    "codex_miro_failed": {
        "what": "Codex has a Miro server but it didn't answer.",
        "fix": "Open Terminal, type codex mcp login miro, press Enter and follow what it says, then press Check again.",
        "button": None},

    # ---- setup finished once, then a connection needs attention again (a sign-out, #50): the page shows only the
    # checklist under this one line, not the whole setup stepper again. {ai} is the one to sign in to (Codex: ChatGPT).
    # The page picks the fix from the row itself: "fix" names the row's button ({button}, as the page labels it), and
    # "fix_follow" points at the row when it has none (Grok's Open Grok, a Codex keyring sign-in, no installer here).
    "setup_done_signin": {
        "what": "Setup is done; {ai} just needs signing in again.",
        "fix": "Press {button} below.",
        "fix_follow": "Follow the ‘{row}’ row below.",
        "fix_busy": "Please wait while that finishes.",
        "button": None},
    "setup_done_install": {
        "what": "Setup is done; {ai} just needs installing again.",
        "fix": "Press {button} below.",
        "fix_follow": "Follow the ‘{row}’ row below.",
        "fix_busy": "Please wait while that finishes.",
        "button": None},
    "setup_done_other": {
        "what": "Setup is done; one connection just needs attention.",
        "fix": "Press {button} below.",
        "fix_follow": "Follow the ‘{row}’ row below.",
        "fix_busy": "Please wait while that finishes.",
        "button": None},

    # ---- the first scan (index.html's setup, #38): not a failure, but said once, here, like the rest.
    # {days} is Settings > History (history_days), {sources} "Slack and Gmail", "Slack" or "Gmail": what is connected.
    "first_scan": {
        "what": "Looking back {days} days across {sources}.",
        "fix": "The first pass can take ten minutes.",
        "button": None},
    "first_scan_ask": {
        "what": "Open Loops is ready to read your own messages, read-only, to see who you talk to, how you write and what is still open.",
        "fix": "Press Start the first scan when you have a few minutes; nothing runs until you do.",
        "button": None},
    "first_scan_ask_slack": {   # the same, with Slack connected: the checklist has looked up who you are on Slack once
        "what": "Open Loops is ready to read your own messages, read-only, to see who you talk to, how you write and what is still open.",
        "fix": "Press Start the first scan when you have a few minutes; nothing runs until you do, apart from a quick check of who you are on Slack.",
        "button": None},
    "scheduled_isolated": {
        "what": "This copy is an isolated test copy, so the scheduled refresh did not read anything.",
        "fix": "Open its page and press Refresh when you want a scan.",
        "button": None},
    "scheduled_later": {
        "what": "The first scan hasn't been started yet, so the scheduled refresh did not read anything.",
        "fix": "Open Open Loops and press Start the first scan.",
        "button": None},
    "first_scan_not_saved": {
        "what": "Open Loops couldn't save your choice, so nothing has changed.",
        "fix": "Press Retry.",
        "button": None},
    "first_scan_later": {
        "what": "Not started; Open Loops remembers that, so nothing reads your messages until you choose.",
        "fix": "Press Start the first scan when you're ready.",
        "button": None},

    # ---- your own to-do file (standing.py, #37)
    "standing_no_path": {
        "what": "No to-do file is selected.",
        "fix": "Choose a file in Settings → Connections.",
        "button": None},
    "standing_exists": {
        "what": "There is already a file there, so Open Loops left it alone.",
        "fix": "Use that file, or choose another name.",
        "button": None},
    "standing_write": {
        "what": "Open Loops couldn't create a file in that folder.",
        "fix": "Check the folder exists and is yours, or choose another place.",
        "button": None},
}

# What each job is called in a sentence (the page's toasts and app.py's job_failure()).
# "refresh_slack": the refresh started by Update Slack (--slack-only), named as the button the person pressed (#50).
JOBS = {"refresh": "The refresh", "refresh_slack": "The Slack update", "chase": "The chase", "people": "Looking at who you talk to",
        "voice": "Learning your tone", "daylog": "The day log", "roadmap": "The roadmap step",
        "standing": "Closing the to-do item"}


class _Keep(dict):
    """format_map helper: a placeholder nobody filled stays as it is ("{ai}") instead of raising KeyError."""
    def __missing__(self, key):
        return "{" + key + "}"


def _fill(text, fmt):
    return string.Formatter().vformat(text, (), _Keep(fmt))


def part(id_, which, win=None, test=False, **fmt):
    """One part ("what" or "fix") of a failure, placeholders filled from fmt. win=True picks "fix_win" if there is one;
    test=True (a test copy) picks "fix_test" / "fix_test_win" if there is one."""
    m = FAILURES[id_]
    win = sys.platform == "win32" if win is None else win
    text = m[which]
    if which == "fix":
        keys = (("fix_test_win", "fix_test") if win else ("fix_test",)) if test else ()
        keys += ("fix_win",) if win else ()
        text = next((m[k] for k in keys if m.get(k)), text)
    return _fill(text, fmt)


def say(id_, win=None, **fmt):
    """The whole message, what happened then what to do: "<what> <fix>"."""
    return part(id_, "what", win, **fmt) + " " + part(id_, "fix", win, **fmt)


# A job that failed with one of these points the person at the connection checklist (Sign in, Check again), which the
# page hides once set up: so the page re-runs the connection check, and the checklist (with its button) comes back.
RECHECK_AFTER_JOB = ("job_signed_out", "codex_signin", "codex_expired", "codex_keyring", "codex_link", "codex_cold",
                     "codex_stale", "codex_start")


def for_page(win=None, test=False):
    """The table as the page gets it: {id: {"what", "fix", "button", "recheck"[, "fix_follow", "fix_busy"]}}, fix already chosen for this platform
    (and, test=True, for a test copy: app.py passes it per install), placeholders left for the page to fill.
    "recheck": a failed job with this id re-runs the connection check."""
    return {k: {"what": part(k, "what", win), "fix": part(k, "fix", win, test), "button": v.get("button"),
                "recheck": k in RECHECK_AFTER_JOB, **{x: v[x] for x in ("fix_follow", "fix_busy") if v.get(x)}}
            for k, v in FAILURES.items()}


def page_json(win=None, table=None, test=False):
    """for_page() as JSON that is safe inside an inline <script>: "<", ">" and "&" become \\u escapes (a sentence
    holding "</script>" or "<!--" cannot end or change the script element), and so do U+2028 / U+2029 (line breaks
    to older JavaScript). JSON.parse and a JavaScript literal read them back as the same characters."""
    text = json.dumps(for_page(win, test) if table is None else table, ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(ch, esc)
    return text


# Why the AI process itself failed, read from its own diagnostic lines only (#25 review): the process must have exited
# non-zero, and a line of its STDERR must START with one of these (lower-cased, stripped). Stdout is never read: it is
# where the model's answer goes, and an answer can quote an email saying "Not logged in to the expenses portal".
AI_SIGNS = (
    ("job_signed_out", ("invalid api key", "not logged in", "please run /login", "oauth token has expired",
                        "api error: 401", "error: not logged in")),
    ("job_usage_limit", ("claude ai usage limit reached", "usage limit reached", "5-hour limit reached",
                         "weekly limit reached", "api error: 429")),
    ("job_network", ("api error: connection error", "api error: request timed out", "api error: unable to connect",
                     "error: getaddrinfo", "error: connect econnrefused", "error: connect etimedout")),
)
# Claude's structured reason (agent.claude_result's p.refused, read from a result the CLI marked is_error, #46) -> the
# job sentence. Same vocabulary as Codex's, but Claude's own sentences: "failed" has none, so it falls through to stderr.
CLAUDE_REFUSED = {"expired": "job_signed_out", "limit": "job_usage_limit", "network": "job_network"}
# the Codex refusals a job can end with (agent.CODEX_REFUSE keys, prefixed): its run printed the filled-in sentence
CODEX_JOB_IDS = ("codex_keyring", "codex_signin", "codex_link", "codex_cold", "codex_limit", "codex_expired",
                 "codex_failed", "codex_unlisted", "codex_notools", "codex_stale", "codex_nosources", "codex_timeout",
                 "codex_start")
FAILURE_DIR = ROOT / "state" / "jobs"   # why a job's AI run failed, written by the job itself, one file per run
RUN_ID_RE = re.compile(r"[0-9a-f]{32}")   # uuid4().hex, as app.run_job makes it


def failure_file(job, run_id=None):
    """state/jobs/<job>.<run_id>.failure.json for the run app.py started (run_id: argument, else OPENLOOPS_RUN_ID from
    the environment app.py gave the job). A run nobody gave an id (the scheduled morning refresh, a terminal) writes
    <job>.scheduled.failure.json, which the app never reads: no run can take another run's reason (#25 review)."""
    rid = run_id if run_id is not None else os.environ.get("OPENLOOPS_RUN_ID", "")
    return FAILURE_DIR / (f"{job}.{rid}.failure.json" if RUN_ID_RE.fullmatch(rid or "") else f"{job}.scheduled.failure.json")


def ai_failure(rc, stderr="", refused=""):
    """Why one AI run failed -> a FAILURES id, or "" (it did not, or nothing says why). From the AI's own structured
    reason, which wins: Codex's p.refused (a CODEX_REFUSE key), or Claude's as report() passes it (a CLAUDE_REFUSED
    id, from the JSON result's is_error flag); else the run's stderr; never its stdout."""
    if refused in CLAUDE_REFUSED.values():
        return refused
    if refused and "codex_" + refused in FAILURES:
        return "codex_" + refused
    if rc == 0:
        return ""
    lines = [ln.strip().lower() for ln in (stderr or "").splitlines() if ln.strip()][-40:]
    for id_, signs in AI_SIGNS:
        if any(ln.startswith(signs) for ln in lines):
            return id_
    return ""


def report(p, job, ai=None):
    """For a job about to exit 1 after an AI run p (a CompletedProcess): when the run's stderr or the AI's structured
    reason (Codex's refusal, Claude's is_error result) says why it failed, write this run's failure_file(job) = {"run_id", "failure", "ai", "at"} (plus "said": the
    filled-in sentence Codex's run printed). A file, not a line on stdout: nothing the model writes can forge it, and one
    per run, so no other run's reason is taken for this one (#25 review). "ai" is OPENLOOPS_AI, the AI app.py captured
    when it started the job; only a run with no such variable (scheduled, terminal) asks for it now."""
    refused = getattr(p, "refused", "") or ""
    if getattr(p, "agent", "") == "claude":   # Claude's reasons share Codex's names, not its sentences
        refused = CLAUDE_REFUSED.get(refused, "")
    fid = ai_failure(p.returncode, p.stderr, refused)
    if not fid:
        return
    if not ai:
        ai = os.environ.get("OPENLOOPS_AI") or ""
    if not ai:
        from . import agent   # here, not at the top: agent imports this module
        ai = agent.display_name()
    rid = os.environ.get("OPENLOOPS_RUN_ID", "")
    rec = {"run_id": rid if RUN_ID_RE.fullmatch(rid) else "scheduled", "failure": fid, "ai": ai,
           "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if fid in CODEX_JOB_IDS:  # a refused Codex run's stdout is Open Loops' own sentence (agent.CODEX_REFUSE), not the model's
        lead = FAILURES[fid]["what"].split("{")[0].strip()
        first = ((p.stdout or "").strip().splitlines() or [""])[0].strip()
        rec["said"] = first if lead and first.startswith(lead) else say(fid, limit="the time allowed", store="your system keychain")
    _write_failure(job, rec)


def _write_failure(job, rec):
    """This run's failure_file(job) = rec, written whole, then renamed into place: a reader never sees half a record."""
    try:
        FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        f = failure_file(job)
        tmp = f.with_name(f.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass  # the page falls back to "didn't finish"


# Failures a job finds itself, before any AI run, whose sentence job_failure() shows as it is (no {job}, no {ai})
JOB_OWN_IDS = ("cursor_unreadable",)


def report_refusal(job, fid):
    """For a job about to exit 1 without running the AI, for a reason of its own (a JOB_OWN_IDS id): record it as
    report() does, so the page says this sentence rather than the plain "didn't finish"."""
    rid = os.environ.get("OPENLOOPS_RUN_ID", "")
    _write_failure(job, {"run_id": rid if RUN_ID_RE.fullmatch(rid) else "scheduled", "failure": fid, "ai": "",
                         "at": datetime.now().astimezone().isoformat(timespec="seconds")})


def job_failure(name, rc, log, ai="Claude", failure=None):
    """The failure id and sentence for a job that ended with exit code rc (not 0, not 2 = SKIPPED) -> (id, said).
    failure: this run's own failure file (report()), the only thing that picks a specific sentence; ai: the AI the app
    captured when it started the run. Without it: the plain "didn't finish". The log is never read for why."""
    job = JOBS.get(name, "The " + name)
    if rc == -1 and log.startswith("could not start"):
        return "job_start_failed", say("job_start_failed", job=job, job_lower=job[0].lower() + job[1:])
    rec = failure if isinstance(failure, dict) else {}
    fid = rec.get("failure") or ""   # ai stays the name the app captured at start: the record never overrides it
    if fid in CODEX_JOB_IDS:
        return fid, rec.get("said") or say(fid, limit="the time allowed", store="your system keychain")
    if fid in ("job_signed_out", "job_usage_limit", "job_network"):
        return fid, say(fid, job=job, ai=ai)
    if fid in JOB_OWN_IDS:
        return fid, say(fid)
    return "job_failed", say("job_failed", job=job)
