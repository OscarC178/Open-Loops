"""Create a chase DRAFT for one loop via a headless agent run (agent.py). Never sends.

Writes back through store.update_state so a note or snooze added from the page while the
agent was running is kept.

    python3 -m openloops.chase <loop id>

Driven by config.json:
  chase_external_email  if false, external email loops are skipped
  tone / people         seniority-aware tone; voice.json (from voice.py) supplies the owner's real examples
"""
import json, sys
from datetime import datetime
from pathlib import Path

from . import agent
from .paths import ROOT
from .store import load_cfg, load_state, read_json, update_state
CFG = load_cfg()
VOICE = read_json(ROOT / "voice.json", {}) or {}
LOG = ROOT / "state" / "logs"
LOG.mkdir(parents=True, exist_ok=True)


PROMPT = """UNATTENDED RUN - do not ask questions. Delivery mode for this message: {mode}.

You are writing a follow-up on behalf of {name}{slack_note} to nudge this open loop:
{loop}

## Who you're writing to
Seniority: {level}. Tone for this level: {tone_level}
House style (always): {tone_base}
{person_style}
{examples}

## Escalation
This is nudge number {n}: {escalation}
Never say "third time", "still waiting", "as per my last message" or count previous asks - warmth first.

## Write it
2-4 sentences in {name}'s voice. British spelling. One clear ask. Make it easy to reply (a yes/no,
a quick call offer, or "shall I do X?"). Reference the original ask naturally. Sign off warmly.
{samples}

## Deliver it - mode: {mode}
If mode is draft: Slack -> slack_send_message_draft in the DM / channel id given in `thread` (in-thread if the
  ask was in one). Email -> search_threads by the subject in `thread`, then create_draft as a reply keeping
  existing recipients. Never send.
If mode is send: Slack -> slack_send_message to the DM / channel id in `thread` (in-thread if the ask was in one).
  Email -> search_threads by subject, then reply on that thread keeping existing recipients. Send exactly ONE
  message. If the thread can't be found or the recipient is ambiguous, do NOT send - report DRAFT_FAILED.

Last line of your reply must be exactly one of:
{marker}: <where>
DRAFT_FAILED: <reason>
"""


def level_for(owner):
    o = owner.lower()
    for name, meta in CFG["people"].items():
        if o.startswith(name.lower()) or any(a.lower() in o for a in meta.get("aliases", [])):
            return name, meta.get("level", "peer")
    return None, "peer"


def is_external(loop):
    e = (loop.get("owner_email") or "").lower()
    return bool(e) and not any(e.endswith("@" + d) for d in CFG["internal_domains"])


def main(loop_id):
    s = load_state()
    l = next((x for x in s["loops"] if x["id"] == loop_id), None)
    if not l:
        print("no such loop"); sys.exit(1)
    if l.get("manual") or l.get("channel") in ("note", "vault"):
        print("SKIPPED: this is a typed reminder, not a chase"); sys.exit(2)
    ext = is_external(l)
    if l["channel"] == "email" and ext and not CFG.get("chase_external_email", True):
        print("SKIPPED: external-email chasing is switched off in Settings"); sys.exit(2)

    person, level = level_for(l["owner"])
    if ext and level == "peer":
        level = "external"
    vp = VOICE.get("people", {}).get(person or "", {})
    n = l.get("chases", 0) + 1
    # send_internal / send_external tick boxes in Settings decide draft vs send for this contact
    send = CFG.get("send_external", False) if ext else CFG.get("send_internal", False)
    if "--force-draft" in sys.argv:
        send = False
    if send and l["channel"] == "email" and agent.name() == "grok":
        # Grok's Gmail is the bundled gmail_mcp.py, which has no send/reply tool - only drafts -
        # so email chases stay drafts regardless of the tick boxes. (Claude replies; Codex's Gmail
        # connector sends with send_email, see agent._CODEX_RENAME.)
        print("note: sending email isn't available via this agent - creating a draft instead")
        send = False
    mode = "send" if send else "draft"
    marker = "SENT" if send else "DRAFT_CREATED"
    # only the tools for this loop's channel - the other source needn't be connected at all
    if l["channel"] == "email":
        tools = ["gmail.search_threads", "gmail.get_thread", "gmail.reply" if send else "gmail.create_draft"]
    else:
        if not agent.slack_enabled():
            print("SKIPPED: Slack is off in Settings (Use Slack)"); sys.exit(2)
        tools = ["slack.read_channel", "slack.search_users", "slack.send_message" if send else "slack.send_message_draft"]
    sid = CFG.get("slack_self_id") or ""
    prompt = PROMPT.format(
        mode=mode, marker=marker,
        name=CFG["owner_name"], slack_note=f" (Slack <@{sid}>)" if sid else "", loop=json.dumps(l, indent=1, ensure_ascii=False),
        level=level, tone_level=CFG["tone"].get(level, CFG["tone"]["peer"]), tone_base=CFG["tone"]["base"],
        person_style=f"How {CFG['owner_name']} writes to this person: {vp['style']}" if vp.get("style") else "",
        examples=("Verbatim examples of past messages to them:\n" + "\n".join(f"- {e}" for e in vp.get("examples", []))) if vp.get("examples") else "",
        n=n, escalation=CFG["escalation"].get(str(min(n, 3))),
        samples=f"Sample in the right register (match the feel, don't copy):\n{VOICE['samples'][level]}" if VOICE.get("samples", {}).get(level) else "",
    )
    p = agent.run(prompt, tools)
    (LOG / f"chase-{loop_id}-{datetime.now():%Y-%m-%d_%H%M}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    print(f"[{mode} | {level}{' | external' if ext else ''}]\n" + p.stdout[-1200:])
    if f"{marker}:" in p.stdout:
        def mark(fresh):  # re-read at write time so page edits made meanwhile survive
            for x in fresh["loops"]:
                if x["id"] == loop_id:
                    x["chases"] = n
                    x["last_chase_at"] = datetime.now().isoformat(timespec="minutes")
                    x["last_chase_mode"] = mode
        update_state(mark)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1])
