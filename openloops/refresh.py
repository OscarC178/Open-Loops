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

{mode_note}You maintain {name}'s "open loops": requests they made to a named person that have not yet
been actioned.{slack_note} Today is {today}.

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
Reply with ONLY a JSON object between the markers, nothing else:
<<<OPENLOOPS>>>
{{
  "new_loops": [{{"id": "<owner-slug>-<topic-slug>", "owner": "...", "owner_email": "... or null",
                  "ask": "one line", "channel": "slack|email", "thread": "DM <name> <channel id> | #channel | email subject",
                  "link": "slack://channel?team=&id=<id> or gmail search url", "asked_at": "ISO datetime",
                  "status": "waiting, or needs_me for inbound", "inbound": false, "notes": "",
                  "priority": "high|normal|low", "theme": "2-4 words",
                  "links": [{{"url": "https://...", "label": "short label"}}]}}],
  "updates": [{{"id": "<existing id>", "status": "waiting|needs_me|done", "last_reply_at": "ISO or null",
                "reply_snippet": "<=120 chars", "asked_at": "ISO (only if a new ask by {name})",
                "priority": "high|normal|low (only if it changed)", "theme": "2-4 words (only if missing)",
                "links": [{{"url": "https://...", "label": "short label"}}]}}],
  "gmail_available": true
}}
<<<END>>>
"""


PRIORITIES = ("high", "normal", "low")
SLACK_CID = re.compile(r"\b([CDG][A-Z0-9]{8,})\b")   # Slack conversation id: C channel, D DM, G group
SLACK_TS = re.compile(r"\b(\d{10}\.\d{6})\b")        # Slack message / thread timestamp


def from_mail(l):
    # typed reminders (channel "note") and vault items stay until the owner marks them done
    return not l.get("manual") and l.get("channel") not in ("note", "vault")


def slack_key(l):
    """What makes two Slack inbound loops the same ask, whatever id or wording the agent used:
    a DM is its id; a channel or group is its id plus the thread ts (the asker if the ts is missing);
    no id -> the thread text. None when there is no thread to go on."""
    t = str(l.get("thread") or "")
    cid = SLACK_CID.search(t)
    if not cid:
        return " ".join(t.lower().split()) or None
    if cid.group(1).startswith("D"):
        return cid.group(1)
    ts = SLACK_TS.search(t)
    return cid.group(1) + "/" + (ts.group(1) if ts else str(l.get("owner") or "").strip().lower())


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
            "you pass the cursor. Read each DM/thread and keep it only where the LATEST message is from someone "
            "else, asks {n} for a specific action or answer, and {n} has not replied since. Drop bots, apps, "
            "workflows, joins, reminders, reactions and {n}'s own messages. Slack loops: channel \"slack\", "
            'thread "DM <asker> <DM channel id>" or "#<channel> <channel id> <thread ts>", link = the message '
            "permalink.".format(n=name, u=SELF_ID, d=slack_date))
    inbound = ("1b. ASKS OF {n} (inbound). Search what others sent {n}:\n{parts}\n   These become new loops with "
               '"status": "needs_me" and "inbound": true - owner is the person asking; ask = one line on what they '
               "need from {n}. The same exclusions and duplicate rule apply.").format(n=name, parts="\n".join(inbound)) if inbound else ""
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
        loops=json.dumps([{k: l[k] for k in ("id", "owner", "ask", "channel", "thread", "asked_at", "status", "closed_at", "priority", "priority_by", "theme") if k in l} for l in open_loops], indent=1, ensure_ascii=False),
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


def apply(s, out, slack_only, now):
    """Merge the agent's JSON into a (fresh) state dict. Pure; returns (n_new, n_updated)."""
    by_id = {l["id"]: l for l in s["loops"]}
    # Slack asks of the owner already open (Needs me or answered-and-waiting): the same DM or thread is
    # not added again under a new id. Closed ones do not count - a fresh ask there is a fresh loop.
    inbound_open = {slack_key(l) for l in s["loops"]
                    if l.get("inbound") and l.get("channel") == "slack" and l.get("status") in ("waiting", "needs_me")}
    n_new = n_upd = 0
    for nl in out.get("new_loops", []) or []:
        nl["id"] = re.sub(r"[^a-z0-9._-]+", "-", str(nl.get("id") or "").lower()).strip("-")[:80]  # ids land in markup and CSS selectors: slugs only
        if not nl["id"] or nl["id"] in by_id:
            continue
        if slack_only and nl.get("channel") != "slack":
            continue  # belt and braces: the prompt says no email, the merge enforces it
        key = slack_key(nl) if nl.get("inbound") and nl.get("channel") == "slack" else None
        if key and key in inbound_open:
            continue
        links = nl.pop("links", None)
        nl.setdefault("status", "needs_me" if nl.get("inbound") else "waiting")
        nl["priority"] = nl.get("priority") if nl.get("priority") in PRIORITIES else "normal"
        nl["priority_by"] = "ai"
        nl["theme"] = str(nl.get("theme") or "")[:40]
        nl.update({"last_reply_at": None, "reply_snippet": None, "chases": 0, "snooze_until": None})
        merge_links(nl, links)
        s["loops"].append(nl)
        by_id[nl["id"]] = nl
        if key:
            inbound_open.add(key)
        n_new += 1
    for u in out.get("updates", []) or []:
        l = by_id.get(u.get("id"))
        if not l:
            continue
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
        n_upd += 1
    if slack_only:
        s["slack_cursor"] = now
        s["last_slack_refresh"] = now
    else:
        s["cursor"] = now
        s["slack_cursor"] = now
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
    p = agent.run(prompt, tools)
    (LOG / f"{kind}-{stamp}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    # some agents drop the markers and emit bare JSON - accept that too
    m = re.search(r"<<<OPENLOOPS>>>(.*?)<<<END>>>", p.stdout, re.S) or re.search(r'(\{\s*"new_loops"\s*:.*\})', p.stdout, re.S)
    if not m:
        print("!! no OPENLOOPS block in output (rc %s). See log." % p.returncode)
        print(p.stdout[-1500:])
        sys.exit(1)
    out = json.loads(m.group(1))
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    counts = {}
    # re-read state.json at write time: the page may have added notes or snoozes meanwhile
    s = update_state(lambda fresh: counts.update(zip(("new", "upd"), apply(fresh, out, SLACK_ONLY, now))))
    gm = "" if SLACK_ONLY else f", gmail={'yes' if s.get('gmail_available') else 'NO'}"
    print(f"done: {counts['new']} new, {counts['upd']} updated{gm}")


if __name__ == "__main__":
    main()
