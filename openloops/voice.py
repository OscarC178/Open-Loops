"""Learn the owner's writing voice per person from their own Slack DMs and sent email.

    python voice.py            -> writes voice.json

Read-only. Uses a headless agent run (agent.py) with the Slack + Gmail read tools. The result is a
per-person style note plus 2-3 verbatim examples of how the owner actually writes to them,
and a sample chase in that voice for each seniority tier - shown in the app's Settings.
"""
import json, re, sys
from datetime import datetime
from pathlib import Path

from . import agent, messages
from .paths import ROOT
from .store import load_cfg
CFG = load_cfg()
OUT = ROOT / "voice.json"
LOG = ROOT / "state" / "logs"
LOG.mkdir(parents=True, exist_ok=True)

GMAIL_TOOLS = ["gmail.search_threads", "gmail.get_thread"]
SLACK_TOOLS = ["slack.read_channel", "slack.search_public_and_private", "slack.search_users"]

PROMPT = """UNATTENDED RUN - do not ask questions. Output only the JSON requested.

You are building a writing-voice profile for {name}{slack_note} so that follow-up
messages sent on their behalf sound like them - not like a bot.

People to profile, with the seniority level {name} assigned them:
{people}

For each person:
1. Find messages from {name} to them:
{sources}
2. Look ONLY at messages written by {name}. Note: greeting habits (e.g. "hola", "holaaa", "hey"), sign-offs,
   capitalisation, emoji use, humour, how direct the ask is, how much context is given, typical length,
   any Spanish/French words, how they soften or escalate.
3. Pick 2-3 short verbatim messages from {name} to that person that best show the voice (asks or nudges preferred).

Then write a general profile and, for each seniority tier (senior, peer, junior, external), one sample
chase message in {name}'s voice that is warm, friendly, British English, and matches this house style:
{tone_base}

Output ONLY:
<<<VOICE>>>
{{
  "general": "3-5 sentences describing how {name} writes",
  "people": {{
    "<person name>": {{"level": "...", "style": "2-3 sentences on how {name} writes to this person specifically",
                       "examples": ["verbatim 1", "verbatim 2"]}}
  }},
  "samples": {{"senior": "...", "peer": "...", "junior": "...", "external": "..."}}
}}
<<<END>>>
"""


def main():
    people = {p: CFG["people"].get(p, {}).get("level", "peer") for p in CFG["voice_sample_people"]}
    sid = CFG.get("slack_self_id") or ""
    slack_on = bool(sid) and agent.slack_enabled()
    sources = []
    if slack_on:
        sources.append("   - Slack (if the Slack tools are available): find the DM with them (slack_search_users to get the id, then slack_read_channel on that id, limit 60).")
    sources.append('   - Gmail (if the Gmail tools are available): search_threads "to:<their name or email> in:sent" (skip if no email / nothing found).')
    prompt = PROMPT.format(name=CFG["owner_name"], slack_note=f" (Slack <@{sid}>)" if slack_on else "",
                           sources="\n".join(sources),
                           people=json.dumps(people, indent=1), tone_base=CFG["tone"]["base"])
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    print(f"[{stamp}] learning voice from {len(people)} people...")
    p = agent.run(prompt, (SLACK_TOOLS if slack_on else []) + GMAIL_TOOLS)
    (LOG / f"voice-{stamp}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    # some agents drop the markers and emit bare JSON - accept that too
    m = re.search(r"<<<VOICE>>>(.*?)<<<END>>>", p.stdout, re.S) or re.search(r'(\{\s*"general"\s*:.*\})', p.stdout, re.S)
    if not m:
        print("!! no VOICE block. See log."); print(p.stdout[-1000:]); messages.report(p, "voice", agent.display_name()); sys.exit(1)
    v = json.loads(m.group(1))
    v["learned_at"] = datetime.now().isoformat(timespec="minutes")
    OUT.write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"done: profiled {len(v.get('people', {}))} people -> voice.json")


if __name__ == "__main__":
    main()
