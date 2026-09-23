"""refresh.apply(): the pure merge of the agent's JSON into state.

    python3 tests/test_refresh_apply.py    # fast; no Slack/Gmail/Claude.

Slack-only runs must advance only slack_cursor and must never admit an email loop; full runs
advance both cursors; links merge without duplicates; needs_me clears a snooze; a Slack ask of the
owner (inbound) lands on Needs me once per DM/thread, however the agent names it.
"""
import sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from openloops import refresh  # noqa: E402

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def base():
    return {"cursor": "2026-09-01T09:00+01:00", "slack_cursor": "2026-09-10T12:00+01:00", "loops": [
        {"id": "sam-deck", "owner": "Sam", "ask": "send the deck", "channel": "slack", "status": "waiting",
         "snooze_until": "2099-01-01", "links": [{"url": "https://docs.google.com/d/1", "label": "deck"}]},
        {"id": "ana-invoice", "owner": "Ana", "ask": "invoice", "channel": "email", "status": "waiting"},
    ]}


out = {"new_loops": [
            {"id": "bo-brief", "owner": "Bo", "ask": "brief", "channel": "slack", "thread": "DM Bo D1",
             "links": [{"url": "https://miro.com/app/board/x", "label": "board"}, {"url": "notaurl"}]},
            {"id": "cy-quote", "owner": "Cy", "ask": "quote", "channel": "email", "thread": "Quote"}],
       "updates": [
            {"id": "sam-deck", "status": "needs_me", "last_reply_at": "2026-09-15T10:00", "reply_snippet": "here",
             "links": [{"url": "https://docs.google.com/d/1", "label": "dup"}, {"url": "https://docs.google.com/d/2", "label": "v2"}]},
            {"id": "ghost", "status": "done"}],
       "gmail_available": True}

# --- slack-only
s = base()
n_new, n_upd = refresh.apply(s, out, slack_only=True, now="2026-09-15T14:00+01:00")
ids = [l["id"] for l in s["loops"]]
check("bo-brief" in ids and "cy-quote" not in ids, "slack-only admits the Slack loop and drops the email one")
check((n_new, n_upd) == (1, 1), f"slack-only counts new=1 upd=1 (got {n_new},{n_upd})")
check(s["cursor"] == "2026-09-01T09:00+01:00", "slack-only leaves the Gmail cursor alone")
check(s["slack_cursor"] == "2026-09-15T14:00+01:00" and s["last_slack_refresh"] == s["slack_cursor"], "slack-only advances slack_cursor + last_slack_refresh")
check("last_refresh" not in s and "gmail_available" not in s, "slack-only does not touch last_refresh / gmail_available")
sam = next(l for l in s["loops"] if l["id"] == "sam-deck")
check(sam["status"] == "needs_me" and sam["snooze_until"] is None, "needs_me update clears the snooze")
check([x["url"] for x in sam["links"]] == ["https://docs.google.com/d/1", "https://docs.google.com/d/2"], "links merge without duplicating a url")
check(sam["links"][0]["label"] == "deck", "an existing link keeps its label")
bo = next(l for l in s["loops"] if l["id"] == "bo-brief")
check(bo["links"] == [{"url": "https://miro.com/app/board/x", "label": "board"}], "new loop keeps http links only")
check(bo["status"] == "waiting" and bo["chases"] == 0 and bo["snooze_until"] is None, "new loop gets the default fields")
check(bo["priority"] == "normal" and bo["priority_by"] == "ai" and bo["theme"] == "", "new loop without a judgement: normal priority, marked AI, no theme")

# --- priority + theme: the agent judges, the person's own setting always stands
s = base()
s["loops"][1].update({"priority": "low", "priority_by": "you", "theme": "old theme"})   # Ana: set by hand
pt = {"new_loops": [{"id": "di-plan", "owner": "Di", "ask": "plan", "channel": "slack", "thread": "DM Di", "priority": "high", "theme": "Q4 budget planning and more words"},
                    {"id": "ed-x", "owner": "Ed", "ask": "x", "channel": "slack", "thread": "DM Ed", "priority": "urgent"}],
      "updates": [{"id": "sam-deck", "status": "waiting", "priority": "high", "theme": "the deck"},
                  {"id": "ana-invoice", "status": "waiting", "priority": "high", "theme": "new theme"}]}
refresh.apply(s, pt, slack_only=True, now="2026-09-15T16:00+01:00")
di = next(l for l in s["loops"] if l["id"] == "di-plan"); ed = next(l for l in s["loops"] if l["id"] == "ed-x")
check(di["priority"] == "high" and di["priority_by"] == "ai" and di["theme"] == "Q4 budget planning and more words"[:40], "new loop keeps the agent's priority + theme (theme capped at 40)")
check(ed["priority"] == "normal", "an unknown priority word falls back to normal")
s2 = base(); refresh.apply(s2, {"new_loops": [{"id": "Fi')<b>;x", "owner": "Fi", "ask": "y", "channel": "slack", "thread": "DM Fi"}]}, slack_only=True, now="2026-09-15T16:00+01:00")
check(s2["loops"][-1]["id"] == "fi-b-x", "an agent-written id is reduced to a slug before it reaches the page")
sam = next(l for l in s["loops"] if l["id"] == "sam-deck"); ana = next(l for l in s["loops"] if l["id"] == "ana-invoice")
check(sam["priority"] == "high" and sam["priority_by"] == "ai" and sam["theme"] == "the deck", "an update sets priority + theme on a loop that had neither")
check(ana["priority"] == "low" and ana["priority_by"] == "you" and ana["theme"] == "old theme", "an update never overrides a priority or theme the person set")

# --- full run
s = base()
refresh.apply(s, out, slack_only=False, now="2026-09-15T15:00+01:00")
ids = [l["id"] for l in s["loops"]]
check("cy-quote" in ids and "bo-brief" in ids, "full run admits both channels")
check(s["cursor"] == s["slack_cursor"] == s["last_refresh"] == "2026-09-15T15:00+01:00", "full run advances both cursors and last_refresh")
check(s["gmail_available"] is True, "full run records gmail_available")

# --- Slack inbound: asks OF the owner from DMs and @-mentions, merged once per DM/thread
s = base()
s["loops"].append({"id": "jo-budget", "owner": "Jo", "ask": "sign off the budget", "channel": "slack", "thread": "DM Jo D0JODM0001",
                   "status": "needs_me", "inbound": True})
s["loops"].append({"id": "kit-old", "owner": "Kit", "ask": "old ask", "channel": "slack", "thread": "#ops C0OPSCH001 1757000000.000100",
                   "status": "done", "inbound": True, "closed_at": "2026-09-12T09:00"})
ib = {"new_loops": [
    # new DM ask, no status given: an inbound loop defaults to needs_me
    {"id": "lu-copy", "owner": "Lu", "ask": "approve the copy", "channel": "slack", "thread": "DM Lu D0LUDM0001",
     "link": "https://x.slack.com/archives/D0LUDM0001/p1757900000000100", "asked_at": "2026-09-15T09:00", "inbound": True},
    # same DM as the open Jo loop under a different id and wording: dropped
    {"id": "jo-budget-2", "owner": "Jo Bloggs", "ask": "budget?", "channel": "slack", "thread": "DM Jo Bloggs D0JODM0001",
     "status": "needs_me", "inbound": True},
    # the Lu DM again in the same batch: dropped
    {"id": "lu-copy-again", "owner": "Lu", "ask": "copy", "channel": "slack", "thread": "DM Lu (D0LUDM0001)", "status": "needs_me", "inbound": True},
    # a mention in the same channel as the closed Kit loop, same thread: closed loops do not block a fresh ask
    {"id": "kit-new", "owner": "Kit", "ask": "review the rota", "channel": "slack", "thread": "#ops C0OPSCH001 1757000000.000100",
     "status": "needs_me", "inbound": True},
    # two mentions in one channel, different threads: both kept
    {"id": "mo-a", "owner": "Mo", "ask": "a", "channel": "slack", "thread": "#design C0DESIGN01 1757900000.000200", "status": "needs_me", "inbound": True},
    {"id": "mo-b", "owner": "Mo", "ask": "b", "channel": "slack", "thread": "#design C0DESIGN01 1757900500.000300", "status": "needs_me", "inbound": True},
    # an outbound loop in Jo's DM is a different loop (my ask of Jo), never collapsed into her ask of me
    {"id": "jo-deck", "owner": "Jo", "ask": "send the deck", "channel": "slack", "thread": "DM Jo D0JODM0001", "status": "waiting"}]}
n_new, _ = refresh.apply(s, ib, slack_only=True, now="2026-09-15T17:00+01:00")
ids = [l["id"] for l in s["loops"]]
lu = next(l for l in s["loops"] if l["id"] == "lu-copy")
check(lu["status"] == "needs_me" and lu["inbound"] is True and lu["channel"] == "slack", "a Slack inbound loop merges as needs_me / inbound on a slack-only run")
check(lu["thread"] == "DM Lu D0LUDM0001" and lu["link"].endswith("p1757900000000100"), "it keeps the agent's thread + permalink so the Needs me row opens the DM")
check(lu["chases"] == 0 and lu["snooze_until"] is None and lu["last_reply_at"] is None, "it gets the usual new-loop fields")
check("jo-budget-2" not in ids and "lu-copy-again" not in ids, "a second ask in an already-open inbound DM is not added again (DM id, not wording)")
check("kit-new" in ids, "a fresh ask in a thread whose inbound loop was closed is a new loop")
check("mo-a" in ids and "mo-b" in ids, "two mentions in one channel but different threads are two loops")
check("jo-deck" in ids, "an outbound loop in the same DM is never de-duplicated against an inbound one")
check(n_new == 5, f"counts only the merged loops (got {n_new})")
check(refresh.slack_key({"thread": "DM Jo D0JODM0001"}) == refresh.slack_key({"thread": "dm jo bloggs  D0JODM0001"}) == "D0JODM0001", "slack_key: a DM is its id")
check(refresh.slack_key({"thread": "#ops C0OPSCH001", "owner": "Kit "}) == "C0OPSCH001/kit", "slack_key: a channel ask without a ts falls back to the asker")
check(refresh.slack_key({"thread": ""}) is None and refresh.slack_key({"thread": "#General  chat"}) == "#general chat", "slack_key: no thread -> None; no id -> the thread text")

# --- in_scope: slack-only ignores email + typed loops; recent done loops stay in scope
recent = "2026-09-12T00:00"
check(refresh.in_scope({"channel": "slack", "status": "waiting"}, True, recent), "slack waiting loop in scope for slack-only")
check(not refresh.in_scope({"channel": "email", "status": "waiting"}, True, recent), "email loop out of scope for slack-only")
check(not refresh.in_scope({"channel": "note", "status": "needs_me", "manual": True}, False, recent), "typed note never goes to the agent")
check(refresh.in_scope({"channel": "email", "status": "done", "closed_at": "2026-09-14T09:00"}, False, recent), "recently closed loop is re-checked")
check(not refresh.in_scope({"channel": "email", "status": "done", "closed_at": "2026-08-01T09:00"}, False, recent), "old closed loop is not")
say("ALL OK")
