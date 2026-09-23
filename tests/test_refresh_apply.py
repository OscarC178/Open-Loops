"""refresh.apply(): the pure merge of the agent's JSON into state.

    python3 tests/test_refresh_apply.py    # fast; no Slack/Gmail/Claude.

Slack-only runs must advance only slack_cursor and must never admit an email loop; full runs
advance both cursors; links merge without duplicates; needs_me clears a snooze; a Slack ask of the
owner (inbound) lands on Needs me once per ask (conversation + ask ts), however the agent names it.
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
s = base()
refresh.apply(s, out, slack_only=False, now="2026-09-15T15:00+01:00", slack_on=False)
check(s["cursor"] == "2026-09-15T15:00+01:00" and s["slack_cursor"] == "2026-09-10T12:00+01:00", "full run with Slack off advances only the Gmail cursor")
s = base(); del s["slack_cursor"]
refresh.apply(s, out, slack_only=False, now="2026-09-15T15:00+01:00", slack_on=False)
check(s["slack_cursor"] == "2026-09-01T09:00+01:00", "...and with no Slack cursor yet, pins it at the old shared cursor")
s = base()
refresh.apply(s, {**out, "slack_available": False}, slack_only=False, now="2026-09-15T15:00+01:00")
check(s["cursor"] == "2026-09-15T15:00+01:00" and s["slack_cursor"] == "2026-09-10T12:00+01:00", "full run whose Slack search failed (slack_available false) keeps the Slack cursor")
s = base()
refresh.apply(s, {"new_loops": [], "slack_available": False}, slack_only=True, now="2026-09-15T15:00+01:00")
check(s["slack_cursor"] == "2026-09-10T12:00+01:00" and "last_slack_refresh" not in s, "slack-only run whose Slack search failed moves nothing")
for junk in ("false", "no", 0):
    s = base()
    refresh.apply(s, {"new_loops": [], "slack_available": junk}, slack_only=True, now="2026-09-15T15:00+01:00")
    check(s["slack_cursor"] == "2026-09-10T12:00+01:00", f"slack_available {junk!r} is not coverage: Slack cursor kept")
for yes in (True, "true"):
    s = base()
    refresh.apply(s, {"new_loops": [], "slack_available": yes}, slack_only=True, now="2026-09-15T15:00+01:00")
    check(s["slack_cursor"] == "2026-09-15T15:00+01:00", f"slack_available {yes!r} advances the Slack cursor")

# --- Slack inbound: asks OF the owner from DMs and @-mentions, one loop per ask (conversation + ask ts)
JO_DM, NOW = "D0JODM0001", "2026-09-15T17:00+01:00"
s = base()
s["loops"].append({"id": "jo-budget", "owner": "Jo", "ask": "sign off the budget", "channel": "slack",
                   "thread": f"DM Jo {JO_DM} 1757800000.000100", "status": "needs_me", "inbound": True})
s["loops"].append({"id": "kit-old", "owner": "Kit", "ask": "old ask", "channel": "slack", "thread": "#ops C0OPSCH001 1757000000.000100",
                   "status": "done", "inbound": True, "closed_at": "2026-09-12T09:00"})
ib = {"new_loops": [
    # new DM ask, no status given: an inbound loop defaults to needs_me
    {"id": "lu-copy", "owner": "Lu", "ask": "approve the copy", "channel": "slack", "thread": "DM Lu D0LUDM0001 1757900000.000100",
     "link": "https://x.slack.com/archives/D0LUDM0001/p1757900000000100", "asked_at": "2026-09-15T09:00", "inbound": True},
    # the open Jo ask seen again under a different id, name and wording (same DM, same ask ts): dropped
    {"id": "jo-budget-2", "owner": "Jo Bloggs", "ask": "budget?", "channel": "slack", "thread": f"DM Jo Bloggs {JO_DM} 1757800000.000100",
     "status": "needs_me", "inbound": True},
    # the Lu ask again in the same batch: dropped
    {"id": "lu-copy-again", "owner": "Lu", "ask": "copy", "channel": "slack", "thread": "DM Lu (D0LUDM0001) 1757900000.000100", "status": "needs_me", "inbound": True},
    # REGRESSION: an unrelated second ask from Jo in the same DM (a later message): kept
    {"id": "jo-rota", "owner": "Jo", "ask": "check the rota", "channel": "slack", "thread": f"DM Jo {JO_DM} 1757900900.000100",
     "status": "needs_me", "inbound": True},
    # a mention in the thread of the closed Kit loop: closed loops never block a fresh ask
    {"id": "kit-new", "owner": "Kit", "ask": "review the rota", "channel": "slack", "thread": "#ops C0OPSCH001 1757000000.000100 1757900000.000400",
     "status": "needs_me", "inbound": True},
    # two mentions in one channel, different threads: both kept
    {"id": "mo-a", "owner": "Mo", "ask": "a", "channel": "slack", "thread": "#design C0DESIGN01 1757900000.000200", "status": "needs_me", "inbound": True},
    {"id": "mo-b", "owner": "Mo", "ask": "b", "channel": "slack", "thread": "#design C0DESIGN01 1757900500.000300", "status": "needs_me", "inbound": True},
    # an outbound loop in Jo's DM is a different loop (my ask of Jo), never collapsed into her ask of me
    {"id": "jo-deck", "owner": "Jo", "ask": "send the deck", "channel": "slack", "thread": f"DM Jo {JO_DM}", "status": "waiting"}]}
n_new, _ = refresh.apply(s, ib, slack_only=True, now=NOW)
ids = [l["id"] for l in s["loops"]]
lu = next(l for l in s["loops"] if l["id"] == "lu-copy")
check(lu["status"] == "needs_me" and lu["inbound"] is True and lu["channel"] == "slack", "a Slack inbound loop merges as needs_me / inbound on a slack-only run")
check(lu["thread"] == "DM Lu D0LUDM0001 1757900000.000100" and lu["link"].endswith("p1757900000000100"), "it keeps the agent's thread + permalink so the Needs me row opens the message")
check(lu["chases"] == 0 and lu["snooze_until"] is None and lu["last_reply_at"] is None, "it gets the usual new-loop fields")
check("jo-budget-2" not in ids and "lu-copy-again" not in ids, "the same ask seen again (same conversation + ask ts) is not added twice, whatever the wording")
check("jo-rota" in ids, "REGRESSION: an unrelated second ask in the same DM is its own loop")
check("kit-new" in ids, "a fresh ask in a thread whose inbound loop was closed is a new loop")
check("mo-a" in ids and "mo-b" in ids, "two mentions in one channel but different threads are two loops")
check("jo-deck" in ids, "an outbound loop in the same DM is never de-duplicated against an inbound one")
check(n_new == 6, f"counts only the merged loops (got {n_new})")

# REGRESSION: the old ask closes and a new one arrives in the same run - updates count first
s = base()
s["loops"].append({"id": "jo-nums", "owner": "Jo", "ask": "send the numbers", "channel": "slack", "thread": f"DM Jo {JO_DM}",
                   "status": "needs_me", "inbound": True})
n = refresh.apply(s, {"new_loops": [
        {"id": "jo-rota", "owner": "Jo", "ask": "check the rota", "channel": "slack", "thread": f"DM Jo {JO_DM} 1757900900.000100", "inbound": True},
        {"id": "jo-nums-again", "owner": "Jo", "ask": "Send the  numbers", "channel": "slack", "thread": f"DM Jo {JO_DM}", "inbound": True}],
    "updates": [{"id": "jo-nums", "status": "done"}]}, slack_only=True, now=NOW)
ids = [l["id"] for l in s["loops"]]
check(n == (2, 1) and "jo-rota" in ids, f"REGRESSION: a new ask survives the old one closing in the same run (got {n})")
check("jo-nums-again" in ids, "the same ask made again after it closed (no ts, same words) is a fresh loop")
s = base()
s["loops"].append({"id": "jo-nums", "owner": "Jo", "ask": "send the numbers", "channel": "slack", "thread": f"DM Jo {JO_DM}",
                   "status": "needs_me", "inbound": True, "asked_at": "2026-09-15T09:00"})
refresh.apply(s, {"new_loops": [{"id": "jo-nums-2", "owner": "Jo", "ask": "send the numbers", "channel": "slack", "thread": f"DM Jo {JO_DM}", "inbound": True, "asked_at": "2026-09-15T11:30"}]},
              slack_only=True, now=NOW)
check("jo-nums-2" in [l["id"] for l in s["loops"]], "no ts or permalink either side: same asker + words (09:00 / 11:30) are still two loops")

# REGRESSION: one reply seen by both searches - an update on my loop AND a new inbound ask - is one Needs me row
s = base()
s["loops"].append({"id": "sam-deck-dm", "owner": "Sam", "ask": "send the deck", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757800000.000500",
                   "status": "waiting"})
s["loops"].append({"id": "sam-thread", "owner": "Sam", "ask": "pick a date", "channel": "slack", "thread": "#plan C0PLANCH01 1757800000.000600",
                   "status": "waiting"})
n = refresh.apply(s, {"new_loops": [
        {"id": "sam-format", "owner": "Sam Jones", "ask": "which format?", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757900000.000700", "inbound": True},
        {"id": "sam-date", "owner": "Sam", "ask": "Tue or Wed?", "channel": "slack", "thread": "#plan C0PLANCH01 1757800000.000600 1757900000.000800", "inbound": True},
        {"id": "viv-date", "owner": "Viv", "ask": "can I join?", "channel": "slack", "thread": "#plan C0PLANCH01 1757800000.000600 1757900000.000900", "inbound": True},
        # a later, separate ask from Sam in the same DM, same run (CodeRabbit round 2)
        {"id": "sam-lunch", "owner": "Sam", "ask": "lunch friday?", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757900300.000750", "inbound": True},
        # no ask ts, but the same words as the reply snippet: the same message
        {"id": "sam-format-nots", "owner": "Sam", "ask": "Which format?", "channel": "slack", "thread": "DM Sam D0SAMDM001", "inbound": True}],
    "updates": [{"id": "sam-deck-dm", "status": "needs_me", "reply_snippet": "which format?", "reply_ts": "1757900000.000700"},
                {"id": "sam-thread", "status": "needs_me", "reply_snippet": "Tue or Wed?", "reply_ts": "1757900000.000800"}]}, slack_only=True, now=NOW)
ids = [l["id"] for l in s["loops"]]
check("sam-format" not in ids and "sam-date" not in ids, "REGRESSION: a reply already reported on my loop is not a second, inbound Needs me row")
check("sam-format-nots" in ids, "...but the same words with no ts or permalink are not evidence: kept (a duplicate beats a loss)")
check("viv-date" in ids, "someone else's ask in that same thread still is")
check("sam-lunch" in ids, "REGRESSION: a later, separate ask in that DM in the same run is kept")
s = base()
s["loops"].append({"id": "sam-deck-dm", "owner": "Sam", "ask": "send the deck", "channel": "slack", "thread": "DM Sam D0SAMDM001", "status": "waiting"})
refresh.apply(s, {"new_loops": [{"id": "sam-lunch", "owner": "Sam", "ask": "lunch?", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757900000.000700", "inbound": True}]},
              slack_only=True, now=NOW)
check("sam-lunch" in [l["id"] for l in s["loops"]], "a separate ask of me in a DM where my own ask is still waiting is kept")

check(refresh.slack_conv({"thread": f"dm jo bloggs  {JO_DM} 1757800000.000100"}) == JO_DM and refresh.ask_ts({"thread": f"DM Jo {JO_DM} 1757800000.000100"}) == "1757800000.000100",
      "slack_conv / ask_ts: a DM ask is its DM id + the ask ts")
check(refresh.slack_conv({"thread": "#ops C0OPSCH001 1757000000.000100 1757000500.000200"}) == "C0OPSCH001" and refresh.ask_ts({"thread": "#ops C0OPSCH001 1757000000.000100 1757000500.000200"}) == "1757000500.000200",
      "slack_conv / ask_ts: a channel ask is the channel id + the last ts (the asking message)")
check(refresh.ask_ts({"thread": "#ops C0OPSCH001 1757000000.000100", "ask_ts": "1757000999.000300"}) == "1757000999.000300", "ask_ts: an explicit ask_ts wins")
check(refresh.slack_conv({"thread": ""}) is None and refresh.slack_conv({"thread": "#General  chat"}) == "#general chat" and refresh.ask_ts({"thread": "#ops C0OPSCH001"}) is None,
      "slack_conv / ask_ts: no thread -> None; no id -> the thread text; no ts -> None")

# --- Slack ask identity across runs: message ts first, wording only as a fallback (Codex second pass)
def inb(id, owner, ask, thread, **kw):
    return {"id": id, "owner": owner, "ask": ask, "channel": "slack", "thread": thread, "inbound": True, **kw}


def run(loops, new, updates=()):
    s = base(); s["loops"] += loops
    n = refresh.apply(s, {"new_loops": new, "updates": list(updates)}, slack_only=True, now=NOW)
    return s, [l["id"] for l in s["loops"]], n


s, ids, _ = run([inb("jo-a", "Jo", "sign off the budget", f"DM Jo {JO_DM} 1757800000.000100", status="needs_me")],
                [inb("jo-b", "Jo", "approve the Q4 budget", f"DM Jo {JO_DM} 1757800000.000100")])
check("jo-b" not in ids, "the same message reworded between runs is the same ask (ts, never wording)")
PL = "https://x.slack.com/archives/D0JODM0001/p1757800000000100"
s, ids, _ = run([inb("jo-a", "Jo", "send the numbers", f"DM Jo {JO_DM}", status="needs_me", link=PL)],
                [inb("jo-b", "Jo", "Send the numbers", f"DM Jo {JO_DM} 1757800000.000100", link=PL)])
check("jo-b" not in ids and next(l for l in s["loops"] if l["id"] == "jo-a")["ask_ts"] == "1757800000.000100",
      "a ts appearing between runs, same permalink: the existing loop takes the ts, no new row")
s, ids, _ = run([inb("jo-a", "Jo", "send the numbers", f"DM Jo {JO_DM}", status="needs_me")],
                [inb("jo-b", "Jo", "send the numbers", f"DM Jo {JO_DM} 1757800000.000100")])
check("jo-b" in ids and "ask_ts" not in next(l for l in s["loops"] if l["id"] == "jo-a"),
      "a ts appearing with no permalink to prove it: a new loop, the old one untouched (no upgrade on wording)")
s, ids, _ = run([inb("jo-a", "Jo", "send the numbers", f"DM Jo {JO_DM} 1757800000.000100", status="needs_me", link=PL)],
                [inb("jo-b", "Jo", "send the numbers", f"DM Jo {JO_DM}", link=PL)])
check("jo-b" not in ids, "a ts disappearing between runs, same permalink: the same ask")
s, ids, _ = run([inb("jo-a", "Jo", "send the numbers", f"DM Jo {JO_DM} 1757800000.000100", status="needs_me")],
                [inb("jo-b", "Jo", "send the numbers", f"DM Jo {JO_DM}")])
check("jo-b" in ids, "a ts disappearing with no permalink: a new loop (a duplicate beats a loss)")
s, ids, _ = run([inb("sam-j", "Sam Jones", "can you review?", "#ops C0OPSCH001", status="needs_me", owner_id="U0SAMJONES")],
                [inb("sam-l", "Sam Lee", "can you review?", "#ops C0OPSCH001", owner_id="U0SAMLEE01")])
check("sam-l" in ids, "two askers with the same first name and wording (no ts) are two asks (Slack user ids)")
s, ids, _ = run([inb("sam-j", "Sam Jones", "can you review?", "#ops C0OPSCH001", status="needs_me", asked_at="2026-09-15T09:00")],
                [inb("sam-l", "Sam Lee", "can you review?", "#ops C0OPSCH001", asked_at="2026-09-15T10:00"),
                 inb("sam-j2", "sam  jones", "Can you review?", "#ops C0OPSCH001", asked_at="2026-09-15T10:00")])
check("sam-l" in ids and "sam-j2" in ids, "...and with no ts or permalink, even the same person's same words are kept (a duplicate beats a loss)")
s, ids, _ = run([inb("kit-a", "Kit", "review the rota", "#ops C0OPSCH001 1757800000.000100", status="done", closed_at="2026-09-14T09:00")],
                [inb("kit-b", "Kit", "review the rota pls", "#ops C0OPSCH001 1757800000.000100"),
                 inb("kit-c", "Kit", "review the rota", "#ops C0OPSCH001 1757900000.000200")])
check("kit-b" not in ids, "a closed message rediscovered with the same ts is not a second (needs_me) row")
check("kit-c" in ids, "a newer message after it closed is a fresh ask")
KPL = "https://x.slack.com/archives/C0OPSCH001/p1757800000000100"
s, ids, _ = run([inb("kit-a", "Kit", "review the rota", "#ops C0OPSCH001", status="done", closed_at="2026-09-14T09:00", link=KPL)],
                [inb("kit-old", "Kit", "review the rota", "#ops C0OPSCH001 1757800000.000100", link=KPL),     # 2025-09-13: before it closed
                 inb("kit-new", "Kit", "review the rota", "#ops C0OPSCH001 1789500000.000100")])              # 2026-09-15, own message
check("kit-old" not in ids, "a closed loop with no ts, same permalink, sent before it closed: the same message")
check("kit-new" in ids, "...and the same words in a later message of its own are a fresh ask")
s, ids, _ = run([inb("kit-a", "Kit", "review the rota", "#ops C0OPSCH001", status="done", link=KPL)],
                [inb("kit-old", "Kit", "review the rota", "#ops C0OPSCH001 1757800000.000100", link=KPL)])
check("kit-old" in ids, "a closed loop with no closed_at to compare against: no merge, a new loop")
s, ids, _ = run([{"id": "sam-deck", "owner": "Sam", "ask": "send the deck", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757800000.000500", "status": "waiting"}],
                [inb("sam-q", "Sam", "which format?", "DM Sam D0SAMDM001 1757950000.000900")],
                [{"id": "sam-deck", "status": "needs_me", "reply_snippet": "which format?", "reply_ts": "1757900000.000700"}])
check("sam-q" in ids, "a new message repeating (quoting) the reply's words but with its own ts is kept")
s, ids, _ = run([{"id": "sam-deck", "owner": "Sam", "ask": "send the deck", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757800000.000500", "status": "waiting"}],
                [inb("sam-q", "Sam", "Which format?", "DM Sam D0SAMDM001 1757950000.000900")],
                [{"id": "sam-deck", "status": "needs_me", "reply_snippet": "which format?", "reply_ts": None}])
check("sam-q" in ids, "a reply reported with no reply_ts or permalink is no evidence: the ask is kept (a duplicate beats a loss)")
s, ids, n = run([], [{"id": "ro-plan", "owner": "Ro", "ask": "send the plan", "channel": "slack", "thread": "DM Ro D0RODM0001 1757800000.000100", "status": "waiting"},
                     inb("ro-q", "Ro", "which plan?", "DM Ro D0RODM0001 1757900000.000200")],
                [{"id": "ro-plan", "status": "needs_me", "reply_snippet": "which plan?", "reply_ts": "1757900000.000200"}])
check("ro-q" not in ids and next(l for l in s["loops"] if l["id"] == "ro-plan")["status"] == "needs_me",
      "a loop created and replied to in one run is one Needs me row (its reply is not a second, inbound one)")
s, ids, n = run([], [inb("ty-a", "Ty", "call me", "DM Ty D0TYDM0001 1757900000.000100")], [{"id": "ty-a", "status": "done"}])
check(n == (1, 1) and next(l for l in s["loops"] if l["id"] == "ty-a")["status"] == "done", "an update to a Slack ask created in the same run still applies")

# --- evidence, not wording: a missing ts or permalink never proves two messages are the same (Codex narrow check)
s, ids, _ = run([inb("jo-25", "Jo", "send the numbers", f"DM Jo {JO_DM}", status="needs_me", asked_at="2025-09-13T09:00")],
                [inb("jo-26", "Jo", "send the numbers", f"DM Jo {JO_DM} 1789500000.000100", asked_at="2026-09-15T20:20",
                     link="https://x.slack.com/archives/D0JODM0001/p1789500000000100")])
check("jo-26" in ids and "ask_ts" not in next(l for l in s["loops"] if l["id"] == "jo-25"),
      "an open untimestamped 2025 ask and a separate 2026 message with the same words: two loops")
s, ids, _ = run([inb("jo-25", "Jo", "send the numbers", f"DM Jo {JO_DM}", status="needs_me", asked_at="2025-09-13T09:00")],
                [inb("jo-26", "Jo", "send the numbers", f"DM Jo {JO_DM}", asked_at="2026-09-15T20:20")])
check("jo-26" in ids, "same words, no ts or permalink either side, but a different day: two loops")
s, ids, _ = run([{"id": "sam-deck", "owner": "Sam", "ask": "send the deck", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757800000.000500", "status": "waiting"}],
                [inb("sam-q", "Sam", "which format?", "DM Sam D0SAMDM001 1757950000.000900", link="https://x.slack.com/archives/D0SAMDM001/p1757950000000900")],
                [{"id": "sam-deck", "status": "needs_me", "reply_snippet": "which format?", "reply_ts": "1757900000.000700",
                  "reply_link": "https://x.slack.com/archives/D0SAMDM001/p1757900000000700"}])
check("sam-q" in ids, "a new ask quoting an earlier reply, with its own ts and permalink: kept")
PQ = "https://x.slack.com/archives/D0SAMDM001/p1757900000000700"
s, ids, _ = run([{"id": "sam-deck", "owner": "Sam", "ask": "send the deck", "channel": "slack", "thread": "DM Sam D0SAMDM001 1757800000.000500", "status": "waiting"}],
                [inb("sam-q", "Sam", "which format?", "DM Sam D0SAMDM001", link=PQ)],
                [{"id": "sam-deck", "status": "needs_me", "reply_snippet": "which format?", "reply_link": PQ}])
check("sam-q" not in ids, "the reply's own permalink on an ask with no ts: the same message, one row")
s, ids, _ = run([inb("anon-a", "", "please review", "#ops C0OPSCH001", status="needs_me", asked_at="2026-09-15T09:00")],
                [inb("anon-b", "", "please review", "#ops C0OPSCH001", asked_at="2026-09-15T10:00"),
                 inb("anon-c", None, "please review", "#ops C0OPSCH001", asked_at="2026-09-15T11:00", owner_id="")])
check("anon-b" in ids and "anon-c" in ids, "two empty identities (no id, no name) never match: no merge")
s, ids, _ = run([inb("ida", "Sam", "please review", "#ops C0OPSCH001", status="needs_me", asked_at="2026-09-15T09:00", owner_id="U0SAMJONES")],
                [inb("idb", "Sam", "please review", "#ops C0OPSCH001", asked_at="2026-09-15T10:00", owner_id="U0SAMLEE01")])
check("idb" in ids, "same name, same words, different Slack ids: two people, two loops")
s, ids, _ = run([inb("jo-open", "Jo", "send the numbers", f"DM Jo {JO_DM}", status="needs_me", asked_at="2026-09-15T09:00")],
                [inb("jo-b", "Jo", "send the numbers", f"DM Jo {JO_DM}", asked_at="2026-09-15T09:30", link="https://x.slack.com/archives/D0JODM0001/p1789400000000100")])
check("jo-b" in ids, "one side has a permalink, the other none: not proof either way, a new loop")

# --- closing: a done update stamps closed_at (re-check window + day log); reopening clears it
s = base()
s["loops"].append({"id": "old-done", "owner": "Uma", "ask": "x", "channel": "slack", "status": "done", "closed_at": "2026-09-14T09:00"})
refresh.apply(s, {"updates": [{"id": "sam-deck", "status": "done"}, {"id": "old-done", "status": "done"}]}, slack_only=True, now=NOW)
sam = next(l for l in s["loops"] if l["id"] == "sam-deck"); od = next(l for l in s["loops"] if l["id"] == "old-done")
check(sam["closed_at"] == NOW and refresh.in_scope(sam, True, "2026-09-12T00:00"), "a loop the refresh closes gets closed_at and stays in the re-check window")
check(od["closed_at"] == "2026-09-14T09:00", "a loop already done keeps its original closed_at")
refresh.apply(s, {"updates": [{"id": "sam-deck", "status": "needs_me"}]}, slack_only=True, now=NOW)
check("closed_at" not in sam and sam["status"] == "needs_me", "reopening (needs_me) clears closed_at")

# --- in_scope: slack-only ignores email + typed loops; recent done loops stay in scope
recent = "2026-09-12T00:00"
check(refresh.in_scope({"channel": "slack", "status": "waiting"}, True, recent), "slack waiting loop in scope for slack-only")
check(not refresh.in_scope({"channel": "email", "status": "waiting"}, True, recent), "email loop out of scope for slack-only")
check(not refresh.in_scope({"channel": "note", "status": "needs_me", "manual": True}, False, recent), "typed note never goes to the agent")
check(refresh.in_scope({"channel": "email", "status": "done", "closed_at": "2026-09-14T09:00"}, False, recent), "recently closed loop is re-checked")
check(not refresh.in_scope({"channel": "email", "status": "done", "closed_at": "2026-08-01T09:00"}, False, recent), "old closed loop is not")
say("ALL OK")
