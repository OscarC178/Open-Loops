"""refresh.build_prompt(): which searches the agent is told to run, per mode.

    python3 tests/test_refresh_prompt.py    # fast; no Slack/Gmail/Claude.

Asks OF the owner (step 1b, inbound) come from Gmail on a full run and from Slack DMs + @-mentions
whenever Slack is on, including a slack-only pass; with Slack off there is no Slack search at all,
and a slack-only prompt carries no Gmail search.
"""
import sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from _helpers import isolate_this_process  # noqa: E402
isolate_this_process("openloops-refresh-")  # importing refresh creates state/logs next to the package: not in the checkout
from openloops import refresh  # noqa: E402

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


# a fixed owner + Slack id, whatever the checkout's own config.json says
refresh.SELF_ID = "U0TESTSELF"
refresh.CFG = {"owner_name": "Oscar", "exclude_people": ["Zed"], "exclude_topics": ["payroll"]}
state = {"cursor": "2026-09-01T09:00+01:00", "slack_cursor": "2026-09-10T12:00+01:00", "loops": [
    {"id": "sam-deck", "owner": "Sam", "ask": "send the deck", "channel": "slack", "thread": "DM Sam D0SAMDM01", "status": "waiting"},
    {"id": "ana-invoice", "owner": "Ana", "ask": "invoice", "channel": "email", "thread": "Invoice", "status": "waiting"},
]}
DM_Q, MENTION_Q = '"to:<@U0TESTSELF> after:2026-09-09"', '"<@U0TESTSELF> after:2026-09-09"'   # slack_cursor - 1 day
INBOX_Q = '"in:inbox after:2026/08/31'                                                         # cursor - 1 day


def inbound_of(prompt):
    """The 1b block only: from its heading to step 2."""
    if "1b. ASKS OF" not in prompt:
        return ""
    return prompt.split("1b. ASKS OF", 1)[1].split("\n2. REPLIES", 1)[0]


# --- the headline "since" is the oldest cut-off among the searched sources, never newer than a per-source after: date
held = dict(state, cursor="2026-09-12T09:00+01:00", gmail_cursor="2026-08-20T09:00+01:00")  # Gmail held back (e.g. Codex had no Gmail)
p, _ = refresh.build_prompt(held, slack_only=False, slack_on=True)
check("since 2026-08-20T09:00+01:00:" in p and '"in:sent after:2026/08/19"' in p,
      "a held gmail_cursor is the headline date, and Gmail's own after: date is unchanged (cursor - 1 day)")
p, _ = refresh.build_prompt(dict(state, cursor="2026-09-12T09:00+01:00"), slack_only=False, slack_on=True)
check("since 2026-09-10T12:00+01:00:" in p, "with Slack further back than Gmail, the headline is Slack's cursor")
p, _ = refresh.build_prompt(dict(state, cursor="2026-09-12T09:00+01:00"), slack_only=False, slack_on=False)
check("since 2026-09-12T09:00+01:00:" in p, "with Slack off, Slack's cursor does not pull the headline back")


# --- full run, Slack on: Gmail inbox + Slack DMs/mentions, one shared needs_me/inbound sentence
p, n = refresh.build_prompt(state, slack_only=False, slack_on=True)
ib = inbound_of(p)
check(n == 2, f"full run re-checks both open loops (got {n})")
check(INBOX_Q in ib, "full run: inbound searches the Gmail inbox from the Gmail cursor")
check(DM_Q in ib and MENTION_Q in ib, "full run: inbound searches Slack DMs and @-mentions from slack_cursor")
check("slack_search_public_and_private" in ib and "sort=timestamp" in ib and "paginate" in ib, "Slack inbound search is paginated by timestamp to the cursor")
check("first ignore bots" in ib and "Oscar's own messages" in ib and "has not answered since" in ib and "does not cancel it" in ib,
      "Slack inbound drops noise first, then keeps each unanswered ask from people (a later message from others does not cancel it)")
check("<ask ts>" in ib and "Several asks in one" in ib, "Slack inbound asks for the ts of the asking message: one loop per ask")
check("slack://" not in p and "#<channel> <channel id> <thread ts> <ask ts>" in p.split("## Output", 1)[1], "the output example matches: channel id + thread ts + ask ts, permalink")
check("requests people\nmade of them" in p, "the opening definition covers asks of the owner too")
check('"slack_available": true\n' in p and '"slack_available": false if' in p and '"reply_ts"' in p,
      "the output asks for a JSON boolean on whether Slack worked, and for the ts of a reported reply")
check('channel "slack"' in ib and "<DM channel id>" in ib, "Slack inbound loops are told their channel + thread id")
check(ib.count('"status": "needs_me" and "inbound": true') == 1 and "same exclusions" in ib, "one shared needs_me/inbound sentence (the JSON and apply() stay the same)")
check("Zed" in p and "payroll" in p, "exclude_people / exclude_topics still reach the prompt")
check("SLACK-ONLY" not in p, "a full run has no slack-only note")
p, _ = refresh.build_prompt({**state, "loops": state["loops"] + [
    {"id": "jo-budget", "owner": "Jo", "ask": "sign off", "channel": "slack", "thread": "DM Jo D0JODM0001", "status": "needs_me", "inbound": True}]},
    slack_only=False, slack_on=True)
check('"inbound": true' in p.split("## Task", 1)[0], "existing loops reach the model with their inbound mark (the reply-to-close rule needs it)")

# --- slack-only: Slack inbound present, Gmail absent, mode note no longer says 'nothing inbound'
p, n = refresh.build_prompt(state, slack_only=True, slack_on=True)
ib = inbound_of(p)
check(n == 1, f"slack-only re-checks only the Slack loop (got {n})")
check(DM_Q in ib and MENTION_Q in ib, "slack-only: inbound searches Slack DMs and @-mentions")
check("in:inbox" not in p and "in:sent" not in p and "search_threads" not in p, "slack-only: no Gmail search anywhere")
check('"status": "needs_me" and "inbound": true' in ib, "slack-only: Slack asks become needs_me / inbound loops")
check("SLACK-ONLY RUN" in p and "of Oscar) are in scope" in p, "slack-only mode note says asks of the owner are in scope")

# --- Slack off (full run): Gmail inbound only, no Slack search at all
p, n = refresh.build_prompt(state, slack_only=False, slack_on=False)
ib = inbound_of(p)
check(INBOX_Q in ib, "Slack off: Gmail inbound still there")
check("to:<@" not in p and "slack_search" not in p and "U0TESTSELF" not in p, "Slack off: no Slack search or Slack id anywhere")
say("ALL OK")
