"""Find the people the owner actually talks to, so they can be sorted into senior / peer / junior /
external with a click instead of editing JSON.

    python people.py   -> writes people_suggested.json

Read-only. Looks at the owner's own Slack DMs and sent mail from the last ~30 days and returns
the most frequent contacts with one short example of how the owner writes to each.
"""
import json, re, sys
from datetime import datetime, timedelta
from pathlib import Path

from . import agent, messages
from .paths import ROOT
from .store import load_cfg
CFG = load_cfg()
OUT = ROOT / "people_suggested.json"
LOG = ROOT / "state" / "logs"
LOG.mkdir(parents=True, exist_ok=True)

GMAIL_TOOLS = ["gmail.search_threads", "gmail.get_thread"]
SLACK_TOOLS = ["slack.read_channel", "slack.search_public_and_private", "slack.search_users"]

PROMPT = """UNATTENDED RUN - do not ask questions. Output only the JSON requested.

Find the people {name}{slack_note} communicates with most - in either direction: people {name}
writes to AND real people who write to {name} - so they can be sorted by seniority.

{sources}
Only count real humans writing personally. Skip anything automated or broadcast: newsletters,
marketing, notifications, receipts, no-reply senders, job alerts, mailing lists.
Take the top 12-15 people, combining both directions. For each, pick ONE short verbatim message
between them that shows the relationship - prefer one {name} wrote; if {name} never wrote to them,
one they wrote to {name} (a request or a reply, not a link dump). Guess their seniority from context
(title, how {name} addresses them, whether they give or take instructions) - the owner will confirm.
External = works at another company (email domain not in: {domains}).

<<<PEOPLE>>>
{{"people": [{{"name": "Full Name", "email": "x@y.com or null", "channel": "slack|email|both",
              "count": 12, "guess": "senior|peer|junior|external", "example": "verbatim message from {name}"}}]}}
<<<END>>>
"""


def main():
    days = min(int(CFG.get("history_days") or 30), 365)  # Settings > History
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
    sid = CFG.get("slack_self_id") or ""
    slack_on = bool(sid) and agent.slack_enabled()
    sources = []
    if slack_on:
        sources.append(f'- Slack (if the Slack tools are available): slack_search_public_and_private query "from:<@{sid}> after:{since}" sort=timestamp, several pages.\n'
                       '  Count which DMs / people the messages are addressed to (DM partner, or the @-mentioned person in a channel).')
    g = since.replace("-", "/")
    sources.append(f'- Gmail sent (if the Gmail tools are available): search_threads "in:sent after:{g}" - count recipients.')
    sources.append(f'- Gmail received (if available): search_threads "in:inbox after:{g} -category:promotions -category:social" - count the real people who wrote to {CFG.get("owner_name") or "the owner"} personally.')
    prompt = PROMPT.format(name=CFG.get("owner_name") or "the owner",
                           slack_note=f" (Slack <@{sid}>)" if slack_on else "",
                           sources="\n".join(sources), domains=", ".join(CFG.get("internal_domains", [])))
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    print(f"[{stamp}] finding people...")
    p = agent.run(prompt, (SLACK_TOOLS if slack_on else []) + GMAIL_TOOLS)
    (LOG / f"people-{stamp}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    # some agents drop the markers and emit bare JSON - accept that too
    m = re.search(r"<<<PEOPLE>>>(.*?)<<<END>>>", p.stdout, re.S) or re.search(r'(\{\s*"people"\s*:.*\})', p.stdout, re.S)
    if not m:
        print("!! no PEOPLE block. See log."); print(p.stdout[-800:]); messages.report(p, "people"); sys.exit(1)
    d = json.loads(m.group(1))
    d["found_at"] = datetime.now().isoformat(timespec="minutes")
    OUT.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"done: {len(d.get('people', []))} people -> people_suggested.json")


if __name__ == "__main__":
    main()
