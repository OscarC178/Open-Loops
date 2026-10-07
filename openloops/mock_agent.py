"""The mock agent (#76): every job answers in seconds with canned text, with no Slack, no Gmail and no AI sign-in.

    OPENLOOPS_AGENT=mock python3 -m openloops run refresh      (or: config set agent mock, for a whole copy)

For checking a change to a job's prompt, parsing or state write-back without a live account: `run refresh` then
writes two plausible loops to state.json, so `list` and the page show them; `run chase <id>` counts a draft;
people, daylog, voice, roadmap and standing get a block of the shape their prompt asks for. agent.run() hands the
job the same CompletedProcess shape claude_result() does (agent "mock", is_error, refused, ...), so nothing in the
jobs knows the difference. Nothing here reads or writes anything outside the job's own state.

It can never pass for a real sign-in: doctor's rows, `agent`, `status` and every run's stderr say "mock" in so many
words, config.template.json says claude, and the page's Settings offer no such choice. Only the console's `config
set agent mock` or the environment variable turns it on, and the variable is honoured for "mock" alone (any other
value is ignored, so a stray variable can never pick a real AI).

Directives, as Slipway's fleet mock takes them: in OPENLOOPS_MOCK in the environment, or anywhere in the prompt
(a loop's notes reach the chase prompt, so a card can carry one):
  MOCK:SLEEP=<seconds>   wait that long first (a slow job for the page's busy states)
  MOCK:FAIL              fail: exit 1, no stdout, the plain "didn't finish" on the page
  MOCK:FAIL=expired      fail as a signed-out AI would (the page's sign-in sentence); also limit, network
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timedelta

from .messages import CLAUDE_REFUSED

SLEEP_RE = re.compile(r"MOCK:SLEEP=(\d+)")
FAIL_RE = re.compile(r"MOCK:FAIL(?:=(expired|limit|network|failed))?\b")
SLACK_ID = "U0MOCK0001"   # what doctor's Slack lookup gets: a Slack-shaped id that is plainly not real


def directives(prompt):
    """(seconds to sleep, failure kind or None) from OPENLOOPS_MOCK and the prompt. A FAIL in either wins."""
    text = (os.environ.get("OPENLOOPS_MOCK") or "") + "\n" + (prompt or "")
    s = SLEEP_RE.search(text)
    f = FAIL_RE.search(text)
    return int(s.group(1)) if s else 0, ((f.group(1) or "failed") if f else None)


def _ago(days, hour=10):
    return (datetime.now() - timedelta(days=days)).replace(hour=hour, minute=0).isoformat(timespec="minutes")


def refresh_block():
    """One ask the owner made (waiting, email) and one ask of the owner (needs me, Slack DM). The Slack one carries a
    fixed message ts, so a second run is told the same message and refresh.known_ask adds no duplicate; the email
    one keeps its id, which refresh skips once it exists."""
    out = {"new_loops": [
        {"id": "mock-sam-budget-figures", "owner": "Sam Mock", "owner_email": "sam@example.com", "owner_id": None,
         "ask_ts": None, "ask": "Send over the Q4 budget figures", "channel": "email", "thread": "Q4 budget figures",
         "link": "https://mail.google.com/mail/u/0/#search/Q4+budget+figures", "asked_at": _ago(3),
         "status": "waiting", "inbound": False, "notes": "", "priority": "normal", "theme": "Q4 budget",
         "links": [{"url": "https://docs.google.com/spreadsheets/d/mock-budget", "label": "budget sheet"}]},
        {"id": "mock-priya-deck-review", "owner": "Priya Mock", "owner_email": None, "owner_id": "U0MOCK0002",
         "ask_ts": "1700000000.000100", "ask": "Can you review the launch deck before Thursday?", "channel": "slack",
         "thread": "DM Priya D0MOCK0001 1700000000.000100",
         "link": "https://example.slack.com/archives/D0MOCK0001/p1700000000000100", "asked_at": _ago(1, 15),
         "status": "needs_me", "inbound": True, "notes": "", "priority": "high", "theme": "launch deck"}],
        "updates": [], "gmail_available": True, "slack_available": True}
    return "Read 2 threads (mock: nothing was read).\n<<<OPENLOOPS>>>" + json.dumps(out) + "<<<END>>>"


def people_block():
    return "<<<PEOPLE>>>" + json.dumps({"people": [
        {"name": "Sam Mock", "email": "sam@example.com", "channel": "email", "count": 14, "guess": "senior",
         "example": "Sam, when you get a sec could you send the figures over? No rush if it's this week."},
        {"name": "Priya Mock", "email": None, "channel": "slack", "count": 9, "guess": "peer",
         "example": "Priya - deck's in the shared drive, shout if anything looks off."},
        {"name": "Alex Mock", "email": "alex@vendor.example.org", "channel": "email", "count": 4, "guess": "external",
         "example": "Thanks Alex, that works - I'll confirm the dates on Monday."}]}) + "<<<END>>>"


def daylog_block():
    return "<<<DAYLOG>>>" + json.dumps({
        "text": "Done\n- Sent Sam the Q4 budget figures (mock)\n\nMoved\n- Launch deck: first pass of comments to Priya (mock)\n\n"
                "Waiting on\n- Sam: budget sign-off (mock)",
        "highlights": ["Budget sign-off from Sam unblocks the forecast", "Deck review due Thursday"]}) + "<<<END>>>"


def voice_block(prompt):
    people = re.findall(r'^\s*"([^"]+)":\s*"(senior|peer|junior|external)"', prompt or "", re.M)
    return "<<<VOICE>>>" + json.dumps({
        "general": "Writes short, warm messages in British English, first names, one clear ask, and signs off lightly. (mock)",
        "people": {n: {"level": lv, "style": f"Relaxed and direct with {n}; asks straight and offers help. (mock)",
                       "examples": [f"Hi {n.split()[0]}, quick one - any news on this? Happy to help if useful."]}
                   for n, lv in people},
        "samples": {"senior": "When you get a sec, could I get a steer on this? No rush if it's this week. (mock)",
                    "peer": "Quick one - any news on this? Shout if I can take anything off your plate. (mock)",
                    "junior": "Could you send this over by Thursday? Thanks - and ask away if anything is unclear. (mock)",
                    "external": "Following up on the thread below - would a short call this week help? Thanks for your time. (mock)"}}) + "<<<END>>>"


def roadmap_block(prompt):
    """The shape the roadmap prompt asks for: read (board_url), parse (rows), preview (plan) or build (created)."""
    ids = re.findall(r"^- (r\d+) \|", prompt or "", re.M)
    if '"created": [' in prompt:
        body = {"created": [{"id": i, "item_id": f"mock-item-{i}", "url": "https://miro.com/app/board/mock/", "note": ""} for i in ids]}
    elif '"plan": [' in prompt:
        body = {"plan": [{"id": i, "action": "add", "why": "nothing like it on the board (mock)"} for i in ids]}
    elif '"rows": [' in prompt:
        body = {"rows": [{"title": "Launch deck review", "detail": "first pass of comments (mock)", "owners": "Priya Mock",
                          "lane": "", "column": "", "state": "in_progress"}]}
    else:
        body = {"board_url": "https://miro.com/app/board/mock/", "frame_title": "", "frame_id": "mock-frame", "kind": "frame",
                "lanes": ["Product", "Platform"], "columns": ["Now", "Next", "Later"],
                "existing": [{"title": "Launch deck (mock)", "lane": "Product", "column": "Now"}]}
    return "<<<ROADMAP>>>" + json.dumps(body) + "<<<END>>>"


def answer(prompt, tools):
    """The canned text for one prompt, picked by the block (or marker line) the prompt asks for."""
    p = prompt or ""
    if "<<<OPENLOOPS>>>" in p:
        return refresh_block()
    if "<<<PEOPLE>>>" in p:
        return people_block()
    if "<<<DAYLOG>>>" in p:
        return daylog_block()
    if "<<<VOICE>>>" in p:
        return voice_block(p)
    if "<<<STANDING>>>" in p:
        return "<<<STANDING>>>" + json.dumps({"updates": []}) + "<<<END>>>"
    if "<<<ROADMAP>>>" in p:
        return roadmap_block(p)
    m = re.search(r"^(SENT|DRAFT_CREATED): <where>", p, re.M)   # chase.py's last-line marker
    if m:
        where = "nowhere: the mock agent wrote no draft and sent nothing"
        return f"Hi there - quick nudge on the ask below, no rush if it's this week. (mock)\n{m.group(1)}: {where}"
    if "SLACK_ID:" in p:
        return f"SLACK_ID: {SLACK_ID}\nSLACK_NAME: Mock User"
    return "OK (mock)"


def run(prompt, tools, effort_=None):
    """One mock run -> the CompletedProcess the jobs expect (see agent.claude_result): stdout is the canned answer,
    stderr one line saying no AI was called. MOCK:FAIL: exit 1, empty stdout, is_error, and refused set so
    messages.report() files the sign-out / limit / network reason when one was named (the plain fail files none)."""
    secs, fail = directives(prompt)
    if secs:
        print(f"mock: sleeping {secs}s (MOCK:SLEEP)", file=sys.stderr, flush=True)
        time.sleep(secs)
    p = subprocess.CompletedProcess(["mock"], 0, stdout="", stderr="")
    p.agent, p.is_error, p.error_text, p.refused, p.usage, p.cost_usd, p.session_id = "mock", False, "", "", None, None, ""
    if fail:
        p.returncode, p.is_error = 1, True
        p.error_text = f"mock: simulated failure (MOCK:FAIL={fail})"
        p.refused = CLAUDE_REFUSED.get(fail, "")   # a failure id messages.ai_failure takes as it is; "failed" -> none
        p.stderr = f"mock: no AI was called; FAILED on purpose ({fail})\nmock error: {p.error_text}\n"
        return p
    p.stdout = answer(prompt, tools)
    p.stderr = f"mock: no AI was called; answered with canned text for {', '.join(tools) or 'no tools'}\n"
    return p
