"""Refresh open loops via a headless agent run (agent.py) using the Slack + Gmail tools.

    python3 -m openloops.refresh                # full: Slack + Gmail, both cursors advance
    python3 -m openloops.refresh --slack-only   # quick mid-day pass: Slack tools only

Reads state.json, asks the agent to (a) find new asks the owner made since the cursor, and asks
made OF the owner (Gmail inbox on a full run; Slack DMs and @-mentions whenever Slack is on),
(b) re-check every open loop for a reply from its owner, and merges the JSON it returns
back into state.json. Never sends anything.

Two cursors. A slack-only run advances only `slack_cursor`; the full run advances both. If the
quick pass moved the shared cursor, every email ask made between two Slack runs would be skipped
forever by the next full refresh.

The final write goes through store.update_state, so anything the page changed while the agent
was running (a note, a snooze, a done click) is kept.
"""
import json, re, sys
from datetime import datetime, timedelta

from . import agent
from .paths import ROOT
from .store import load_cfg, load_state, update_state
LOG = ROOT / "state" / "logs"
LOG.mkdir(parents=True, exist_ok=True)
CFG = load_cfg()
SELF_ID = CFG.get("slack_self_id") or ""
SLACK_ONLY = "--slack-only" in sys.argv

# Sources are pluggable: Slack needs the owner's id to find their messages, Gmail doesn't.
# A source that isn't connected simply has no tools in the session; the prompt says "if available".
GMAIL_TOOLS = ["gmail.search_threads", "gmail.get_thread"]
SLACK_TOOLS = ["slack.read_channel", "slack.read_thread", "slack.search_public_and_private", "slack.search_users"]

PROMPT = """UNATTENDED RUN - nobody can answer questions. Do not ask any. Output only what is requested.

{mode_note}You maintain {name}'s "open loops": requests they made to a named person, and requests people
made of them, that have not yet been actioned.{slack_note} Today is {today}.

## Existing open loops (JSON)
{loops}

## Task
1. NEW ASKS. Search {name}'s own outbound messages since {since}:
{sources}
   Keep only messages that ask a specific named person/vendor for an action or answer
   (imperatives, "can you ...", "please ...", "let me know ..."). Drop chatter, FYIs,
   broadcast reminders, anything to these people: {exclude_people}; these topics: {exclude_topics};
   and anything that duplicates an existing loop (same owner + same subject => attach to the
   existing loop instead of creating a new one).
{inbound}
2. REPLIES. For every existing loop with status "waiting" or "needs_me", re-read its thread/DM
   ({thread_howto}).
   Report the newest message from the owner AFTER {name}'s ask, and whether {name} has replied since.
   Loops with status "done" are listed too (closed in the last few days): for those ONLY report an update
   if the owner posted a NEW question or request after `closed_at` -> status "needs_me" (reopen). Otherwise omit.
   Rules: owner replied and {name} has not answered since -> "needs_me".
          owner replied with a clear completion ("done", "sorted", delivered the thing) -> "done".
          {name} replied after them with a new ask -> "waiting" (update asked_at).
          inbound loops (marked "inbound"): once {name} has replied -> "done", unless that reply
          asks them for something new -> "waiting".
          nothing new -> leave unchanged (omit from updates).
3. PRIORITY + THEME. For every new loop give "priority": "high" (blocks {name} or a deadline this week,
   senior asker, money or a customer), "normal", or "low" (nice-to-have, no date), and "theme": 2-4 words
   naming what it is about (e.g. "Q4 budget", "contract redlines", "hiring: designer"). Existing loops listed
   without a theme get one in their update; only change an existing priority if the thread makes it clearly
   more or less urgent (loops marked "priority_by": "you" were set by {name}: never change those).
4. LINKS. If a thread mentions a document URL (Google Drive/Docs/Sheets, Miro, Notion, Figma, a
   ticket), include it in that loop's "links" with a 2-4 word label. New loops and updates both take
   a "links" array; omit it when there is nothing.

## Output
Reply with ONLY a JSON object between the markers, nothing else ("slack_available": false if the Slack searches failed):
<<<OPENLOOPS>>>
{{
  "new_loops": [{{"id": "<owner-slug>-<topic-slug>", "owner": "...", "owner_email": "... or null",
                  "owner_id": "Slack user id of the owner, or null", "ask_ts": "Slack ts of the asking message, or null",
                  "ask": "one line", "channel": "slack|email", "thread": "DM <name> <channel id> <ask ts> | #<channel> <channel id> <thread ts> <ask ts> | email subject",
                  "link": "Slack message permalink (required for Slack) or gmail search url", "asked_at": "ISO datetime",
                  "status": "waiting, or needs_me for inbound", "inbound": false, "notes": "",
                  "priority": "high|normal|low", "theme": "2-4 words",
                  "links": [{{"url": "https://...", "label": "short label"}}]}}],
  "updates": [{{"id": "<existing id>", "status": "waiting|needs_me|done", "last_reply_at": "ISO or null",
                "reply_snippet": "<=120 chars", "reply_ts": "Slack ts of that reply, or null", "reply_link": "Slack permalink of that reply (required for Slack)", "asked_at": "ISO (only if a new ask by {name})",
                "priority": "high|normal|low (only if it changed)", "theme": "2-4 words (only if missing)",
                "links": [{{"url": "https://...", "label": "short label"}}]}}],
  "gmail_available": true, "slack_available": true
}}
<<<END>>>
"""


PRIORITIES = ("high", "normal", "low")
SLACK_CID = re.compile(r"\b([CDG][A-Z0-9]{8,})\b")   # Slack conversation id: C channel, D DM, G group
SLACK_TS = re.compile(r"\b(\d{10}\.\d{6})\b")        # Slack message / thread timestamp


def from_mail(l):
    # typed reminders (channel "note") and vault items stay until the owner marks them done
    return not l.get("manual") and l.get("channel") not in ("note", "vault")


def words(v):
    return " ".join(str(v or "").lower().split())


def slack_conv(l):
    """The Slack conversation a loop lives in: the DM / channel / group id in `thread` (a message ts is
    unique within one), else the thread text. None when there is no thread to go on."""
    t = str(l.get("thread") or "")
    cid = SLACK_CID.search(t)
    return cid.group(1) if cid else (words(t) or None)


def ask_ts(l):
    """The ts of the message that asked: `ask_ts`, else the last ts in `thread`, else None."""
    v = str(l.get("ask_ts") or "").strip()
    if SLACK_TS.fullmatch(v):
        return v
    ts = SLACK_TS.findall(str(l.get("thread") or ""))
    return ts[-1] if ts else None


def same_asker(a, b):
    """Slack user ids when both loops have one; otherwise the full names, and only when both have one.
    Two missing identities are not the same person."""
    ia, ib = str(a.get("owner_id") or "").strip().upper(), str(b.get("owner_id") or "").strip().upper()
    if ia and ib:
        return ia == ib
    na, nb = words(a.get("owner")), words(b.get("owner"))
    return bool(na and nb) and na == nb


def sent_before_close(ts, iso):
    """Whether Slack ts `ts` is no later than ISO `closed_at` (a naive one is local time). A missing or
    malformed date is not evidence: False."""
    try:
        return datetime.fromtimestamp(float(ts)).astimezone() <= datetime.fromisoformat(str(iso)).astimezone()
    except (TypeError, ValueError):
        return False


def link(l, key="link"):
    return str(l.get(key) or "").strip()


# Principle: a missing ts or permalink is never proof that two messages are the same. With no evidence
# the candidate becomes its own loop - a duplicate row the owner can press done on beats an ask that
# silently never shows.
def known_ask(c, loops):
    """Whether Slack inbound candidate c is an ask already on the list. Evidence, strongest first:
    - both have a ts: the same ts is the same message, open or closed (the agent may re-report a closed
      one); a different ts is a different message, whatever the wording;
    - the same permalink (it embeds the message ts): the same message; a loop with no ts takes c's ts -
      unless it is closed and c was not provably sent before it closed;
    - neither side has a ts or a permalink: the same asker, the same wording, asked the same day, and
      the loop still open.
    Anything else is a new loop."""
    conv, ts, lk = slack_conv(c), ask_ts(c), link(c)
    for l in loops:
        if slack_conv(l) != conv:
            continue
        lts, llk = ask_ts(l), link(l)
        if ts and lts:
            if ts == lts:
                return True
            continue
        if lk and llk:
            if lk != llk:
                continue
            if l.get("status") == "done" and not (ts and sent_before_close(ts, l.get("closed_at"))):
                continue
            if ts:
                l["ask_ts"] = ts
            return True
        if ts or lts or lk or llk:
            continue   # one side has evidence the other lacks: not proof either way
        day = str(c.get("asked_at") or "")[:10]
        if (l.get("status") in ("waiting", "needs_me") and same_asker(c, l) and words(c.get("ask")) == words(l.get("ask"))
                and len(day) == 10 and day == str(l.get("asked_at") or "")[:10]):
            return True
    return False


def reply_seen(c, replies):
    """Whether Slack inbound candidate c is the very reply this run already reported on one of the owner's
    loops (loop, update): the reply's ts is c's ask ts, or the reply's permalink is c's permalink. Wording
    is not evidence; a reply reported with neither says nothing about c."""
    conv, ts, lk = slack_conv(c), ask_ts(c), link(c)
    for l, u in replies:
        if slack_conv(l) != conv:
            continue
        rts = str(u.get("reply_ts") or "").strip()
        if (ts and SLACK_TS.fullmatch(rts) and ts == rts) or (lk and lk == link(u, "reply_link")):
            return True
    return False


def in_scope(l, slack_only, recent):
    if not from_mail(l):
        return False
    if slack_only and l.get("channel") != "slack":
        return False
    return l["status"] in ("waiting", "needs_me") or (
        l["status"] == "done" and (l.get("closed_at") or "") >= recent)


def build_prompt(s, slack_only, slack_on):
    recent = (datetime.now() - timedelta(days=5)).isoformat()
    open_loops = [l for l in s["loops"] if in_scope(l, slack_only, recent)]
    slack_since = s.get("slack_cursor") or s["cursor"]
    slack_date = (datetime.fromisoformat(slack_since) - timedelta(days=1)).date().isoformat()
    gmail_date = (datetime.fromisoformat(s["cursor"]) - timedelta(days=1)).date().isoformat()
    name = CFG.get("owner_name") or "the owner"
    sources = []
    if slack_on:
        sources.append(f'   - Slack (if the Slack tools are available): slack_search_public_and_private query "from:<@{SELF_ID}> after:{slack_date}" sort=timestamp, paginate until you pass the cursor.')
    if not slack_only:
        sources.append(f'   - Gmail (if the Gmail tools are available): search_threads query "in:sent after:{gmail_date.replace("-", "/")}".')
    # 1b. asks OF the owner: Gmail inbox on a full run, Slack DMs + @-mentions whenever Slack is on (so a
    # slack-only pass has them too, from slack_cursor). One shared closing sentence keeps the JSON the same.
    inbound = []
    if not slack_only:
        inbound.append(
            '   - Gmail (if available): search_threads query '
            '"in:inbox after:{g} -category:promotions -category:social". Keep only mail from real people '
            "(not newsletters, marketing, notifications, receipts, no-reply) where the thread's LATEST message "
            "asks {n} for a specific action or answer and {n} has not replied since.".format(n=name, g=gmail_date.replace("-", "/")))
    if slack_on:
        inbound.append(
            '   - Slack (if the Slack tools are available): slack_search_public_and_private queries '
            '"to:<@{u}> after:{d}" (DMs) and "<@{u}> after:{d}" (@-mentions), sort=timestamp, paginate until '
            "you pass the cursor. Read each DM/thread; first ignore bots, apps, workflows, joins, reminders, "
            "reactions and {n}'s own messages, then keep each message from someone else that asks {n} for a "
            "specific action or answer and that {n} has not answered since (a later message from someone else "
            "does not cancel it). Slack loops: channel \"slack\", "
            'thread "DM <asker> <DM channel id> <ask ts>" or "#<channel> <channel id> <thread ts> <ask ts>" '
            "(<ask ts> = the ts of the message that asks), link = that message's permalink (required). Several asks in one "
            "DM/thread are separate loops; a message you report as a reply in updates is not also a new loop."
            .format(n=name, u=SELF_ID, d=slack_date))
    inbound = ("1b. ASKS OF {n} (inbound). Search what others sent {n}:\n{parts}\n   These become new loops with "
               '"status": "needs_me" and "inbound": true - owner is the person asking; ask = one line on what they '
               "need from {n}. The same exclusions (excluded people as askers too) and duplicate rule apply.").format(n=name, parts="\n".join(inbound)) if inbound else ""
    prompt = PROMPT.format(
        name=name,
        mode_note=("SLACK-ONLY RUN: you have no Gmail tools. Ignore email entirely - do not report email loops. "
                   f"Slack asks both ways (by {name} and of {name}) are in scope.\n\n" if slack_only else ""),
        inbound=inbound,
        slack_note=f" {name}'s Slack user id is <@{SELF_ID}>." if slack_on else "",
        sources="\n".join(sources),
        thread_howto=("Slack: read the DM/channel with the id in `thread`" if slack_only else
                      "Slack: read the DM/channel with the id in `thread`; Gmail: search the subject in `thread`"),
        today=datetime.now().strftime("%Y-%m-%d %H:%M"),
        loops=json.dumps([{k: l[k] for k in ("id", "owner", "ask", "channel", "thread", "asked_at", "status", "closed_at", "inbound", "priority", "priority_by", "theme") if k in l} for l in open_loops], indent=1, ensure_ascii=False),
        # headline cursor: the older of the two in a full run, or email asks made since the last
        # slack-only pass would look "too old" to the model
        since=slack_since if slack_only else s["cursor"],
        exclude_people=", ".join(CFG.get("exclude_people", [])) or "none",
        exclude_topics="; ".join(CFG.get("exclude_topics", [])) or "none",
    )
    return prompt, len(open_loops)


def merge_links(loop, links):
    """Add {url,label} entries to loop["links"], de-duplicated by url. Ignores non-http junk."""
    if not links:
        return
    have = loop.setdefault("links", [])
    seen = {x.get("url") for x in have if isinstance(x, dict)}
    for x in links:
        if not isinstance(x, dict):
            continue
        url = str(x.get("url") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        have.append({"url": url, "label": str(x.get("label") or "").strip()[:60]})
        seen.add(url)


def apply(s, out, slack_only, now, slack_on=True):
    """Merge the agent's JSON into a (fresh) state dict. Pure; returns (n_new, n_updated).
    slack_on: whether this run searched Slack at all (a full run with Slack off did not); an agent
    that reports "slack_available": false (the searches failed) leaves the Slack cursor where it was."""
    by_id = {l["id"]: l for l in s["loops"]}
    n_new = n_upd = 0

    def add(nl):
        nonlocal n_new
        links = nl.pop("links", None)
        nl.setdefault("status", "needs_me" if nl.get("inbound") else "waiting")
        nl["priority"] = nl.get("priority") if nl.get("priority") in PRIORITIES else "normal"
        nl["priority_by"] = "ai"
        nl["theme"] = str(nl.get("theme") or "")[:40]
        nl.update({"last_reply_at": None, "reply_snippet": None, "chases": 0, "snooze_until": None})
        merge_links(nl, links)
        s["loops"].append(nl)
        by_id[nl["id"]] = nl
        n_new += 1

    def update(l, u):
        nonlocal n_upd
        was = l.get("status")
        for k in ("status", "last_reply_at", "reply_snippet", "asked_at"):
            if u.get(k):
                l[k] = u[k]
        if u.get("theme") and not l.get("theme"):
            l["theme"] = str(u["theme"])[:40]
        if u.get("priority") in PRIORITIES and l.get("priority_by") != "you":  # the person's call always stands
            l["priority"], l["priority_by"] = u["priority"], "ai"
        merge_links(l, u.get("links"))
        if l["status"] == "needs_me":
            l["snooze_until"] = None
            l.pop("closed_at", None)
        elif l["status"] == "done" and was != "done":
            l["closed_at"] = now
        n_upd += 1

    # 1. new loops, except Slack asks of the owner (those wait until the list is settled)
    slack_in = []
    for nl in out.get("new_loops", []) or []:
        nl["id"] = re.sub(r"[^a-z0-9._-]+", "-", str(nl.get("id") or "").lower()).strip("-")[:80]  # ids land in markup and CSS selectors: slugs only
        if not nl["id"] or nl["id"] in by_id or nl["id"] in {x["id"] for x in slack_in}:
            continue
        if slack_only and nl.get("channel") != "slack":
            continue  # belt and braces: the prompt says no email, the merge enforces it
        if nl.get("inbound") and nl.get("channel") == "slack" and slack_conv(nl):
            slack_in.append(nl)
        else:
            add(nl)
    # 2. updates (to loops that exist now; any to a Slack ask added below are applied after it)
    later, replies = [], []
    for u in out.get("updates", []) or []:
        l = by_id.get(u.get("id"))
        if not l:
            later.append(u)
            continue
        update(l, u)
        if l["status"] == "needs_me" and l.get("channel") == "slack" and u.get("status") == "needs_me":
            replies.append((l, u))   # a reply reported on one of the owner's loops, created this run or before
    # 3. Slack asks of the owner, against the settled list: a known ask (by message ts first; see known_ask)
    # or a reply already reported in step 2 is not a second Needs me row
    for nl in slack_in:
        inbound = [l for l in s["loops"] if l.get("inbound") and l.get("channel") == "slack"]
        if known_ask(nl, inbound) or reply_seen(nl, replies):
            continue
        if ask_ts(nl):
            nl["ask_ts"] = ask_ts(nl)
        add(nl)
    for u in later:
        if u.get("id") in by_id:
            update(by_id[u["id"]], u)
    searched = slack_on and str(out.get("slack_available", True)).lower() == "true"   # missing -> trust the run, as before; "false"/junk -> not searched
    if slack_only:
        if searched:
            s["slack_cursor"] = now
            s["last_slack_refresh"] = now
    else:
        # Slack's cursor moves only when Slack was searched; with Slack off (or failing) it stays where Slack
        # coverage stopped (first time: the old shared cursor), so the next working run catches up from there
        s["slack_cursor"] = now if searched else (s.get("slack_cursor") or s.get("cursor"))
        s["cursor"] = now
        s["last_refresh"] = now
        s["gmail_available"] = bool(out.get("gmail_available"))
    return n_new, n_upd


def main():
    slack_on = bool(SELF_ID) and agent.slack_enabled()
    if SLACK_ONLY and not slack_on:
        print("SKIPPED: Slack is off or your Slack id is not known yet - run a full Refresh"); sys.exit(2)
    s = load_state()
    prompt, n_open = build_prompt(s, SLACK_ONLY, slack_on)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    kind = "refresh-slack" if SLACK_ONLY else "refresh"
    print(f"[{stamp}] {kind}: {n_open} open loops, cursor {s.get('slack_cursor') or s['cursor'] if SLACK_ONLY else s['cursor']}")
    tools = SLACK_TOOLS if SLACK_ONLY else (SLACK_TOOLS if slack_on else []) + GMAIL_TOOLS
    # the new cursor is taken BEFORE the agent searches: anything that lands while it runs is after it
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    p = agent.run(prompt, tools)
    (LOG / f"{kind}-{stamp}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    # some agents drop the markers and emit bare JSON - accept that too
    m = re.search(r"<<<OPENLOOPS>>>(.*?)<<<END>>>", p.stdout, re.S) or re.search(r'(\{\s*"new_loops"\s*:.*\})', p.stdout, re.S)
    if not m:
        print("!! no OPENLOOPS block in output (rc %s). See log." % p.returncode)
        print(p.stdout[-1500:])
        sys.exit(1)
    out = json.loads(m.group(1))
    counts = {}
    # re-read state.json at write time: the page may have added notes or snoozes meanwhile
    s = update_state(lambda fresh: counts.update(zip(("new", "upd"), apply(fresh, out, SLACK_ONLY, now, slack_on))))
    gm = "" if SLACK_ONLY else f", gmail={'yes' if s.get('gmail_available') else 'NO'}"
    print(f"done: {counts['new']} new, {counts['upd']} updated{gm}")


if __name__ == "__main__":
    main()
