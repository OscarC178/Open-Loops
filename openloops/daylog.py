"""Day log: what moved today, as an instant digest and (on demand) first-person prose.

    python3 -m openloops.daylog                 -> asks the agent, writes state/daylog/<date>.json + .html
    python3 -m openloops.daylog --digest-only   -> no agent; digest + page only

digest(state, day) is pure and is what the page shows straight away: loops opened, closed and
chased today, plus how many are still waiting / need you. main() adds prose written from the
owner's own sent Slack + Gmail messages since midnight (read-only, via agent.py) and the digest.
Never sends anything.
"""
import html, json, re, sys
from datetime import date, datetime
from pathlib import Path

from . import agent, messages
from .paths import ROOT
from .store import load_cfg, load_state, read_json, write_json

DIR = ROOT / "state" / "daylog"
LOG = ROOT / "state" / "logs"

GMAIL_TOOLS = ["gmail.search_threads", "gmail.get_thread"]
SLACK_TOOLS = ["slack.read_channel", "slack.read_thread", "slack.search_public_and_private", "slack.search_users"]

PROMPT = """UNATTENDED RUN - nobody can answer questions. Do not ask any. Output only what is requested.

You are writing {name}'s day log for {today} - a short, honest, first-person note of what actually
moved today, for their own records and tomorrow's stand-up.{slack_note}

## What the tracker already knows about today
Loops {name} opened today (asks they made): {opened}
Loops closed today: {closed}
Loops chased today: {chased}
Still waiting on other people: {waiting}. Still needing {name}'s reply: {needs_me}.

## Read today's own outbound messages
{sources}
Look only at messages {name} sent. Do not read other people's channels beyond the threads those
messages sit in.

## Write it
5-10 bullets, first person ("I ..."), British English, specific and plain. Group them under exactly
three headings: "Done", "Moved" (progressed but not finished), "Waiting on". Name the person or
project in each bullet. Only report things you can see evidence for in the messages or the tracker
lists above - never invent, never pad. If a group has nothing, write "- nothing" under it.
Then pick 1-3 highlights: the bullets that matter most tomorrow.

## Output
Reply with ONLY a JSON object between the markers, nothing else:
<<<DAYLOG>>>
{{"text": "Done\\n- ...\\n\\nMoved\\n- ...\\n\\nWaiting on\\n- ...", "highlights": ["...", "..."]}}
<<<END>>>
"""


def _item(l):
    return {"id": l.get("id"), "owner": l.get("owner") or "", "ask": l.get("ask") or "",
            "channel": l.get("channel") or "", "status": l.get("status") or ""}


def digest(state, day):
    """Pure. Loops opened / closed / chased on `day` (YYYY-MM-DD), plus live waiting / needs_me counts."""
    loops = [l for l in (state or {}).get("loops") or [] if l.get("channel") != "vault"]
    out = {"opened": [], "closed": [], "chased": [], "waiting": 0, "needs_me": 0}
    for l in loops:
        if str(l.get("asked_at") or "").startswith(day):
            out["opened"].append(_item(l))
        if str(l.get("closed_at") or "").startswith(day):
            out["closed"].append(_item(l))
        if str(l.get("last_chase_at") or "").startswith(day):
            out["chased"].append(_item(l))
        snoozed = bool(l.get("snooze_until")) and str(l["snooze_until"]) > day
        if l.get("status") in ("waiting", "needs_me") and not snoozed:
            out[l["status"]] += 1
    return out


def page_path(day):
    return DIR / f"{day}.html"


def entry_path(day):
    return DIR / f"{day}.json"


def status(day=None):
    """What the page shows: today's digest plus the saved prose, if any."""
    day = day or date.today().isoformat()
    e = read_json(entry_path(day), {}) or {}
    return {"date": day, "digest": digest(load_state(), day),
            "text": e.get("text"), "highlights": e.get("highlights") or [],
            "written_at": e.get("written_at"), "has_page": page_path(day).exists()}


def _prose_html(text):
    """Headings on their own line, bullets from '- ', blank lines split paragraphs."""
    out, ul = [], []
    def flush():
        nonlocal ul
        if ul:
            out.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in ul) + "</ul>")
            ul = []
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln:
            flush(); continue
        if ln.startswith(("- ", "* ", "• ")):
            ul.append(ln[2:].strip()); continue
        flush()
        if len(ln) <= 40 and not ln.endswith("."):
            out.append(f"<h2>{html.escape(ln)}</h2>")
        else:
            out.append(f"<p>{html.escape(ln)}</p>")
    flush()
    return "\n".join(out)


def _list_html(title, items):
    if not items:
        return f"<h3>{html.escape(title)}</h3><p class=mut>none</p>"
    lis = "".join(f"<li><b>{html.escape(i.get('owner') or 'me')}</b> — {html.escape(i.get('ask') or '')}"
                  f" <span class=mut>({html.escape(i.get('channel') or '')})</span></li>" for i in items)
    return f"<h3>{html.escape(title)}</h3><ul>{lis}</ul>"


def render_html(entry):
    """Standalone page for one day. No scripts; everything escaped."""
    day = entry.get("date") or ""
    d = entry.get("digest") or {}
    hl = entry.get("highlights") or []
    body = []
    if hl:
        body.append("<h2>Highlights</h2><ul class=hl>" + "".join(f"<li>{html.escape(h)}</li>" for h in hl) + "</ul>")
    if entry.get("text"):
        body.append(_prose_html(entry["text"]))
    else:
        body.append("<p class=mut>No write-up yet - press <b>Write it up</b> in Open Loops for the prose version.</p>")
    body.append("<h2>From the tracker</h2>")
    body.append(_list_html("Opened today", d.get("opened") or []))
    body.append(_list_html("Closed today", d.get("closed") or []))
    body.append(_list_html("Chased today", d.get("chased") or []))
    body.append(f"<p class=mut>Still waiting on others: {int(d.get('waiting') or 0)} · still needing me: {int(d.get('needs_me') or 0)}</p>")
    written = entry.get("written_at")
    foot = f"<p class=mut>written {html.escape(str(written))}</p>" if written else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Day log {html.escape(day)}</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1c1f24;--mut:#6b7280;--line:#e5e7eb;--b:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111318;--card:#1a1d24;--ink:#e6e7ea;--mut:#9aa0a8;--line:#2a2f38}}}}
body{{margin:0;font:15px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}}
main{{max-width:720px;margin:0 auto;padding:28px 24px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:22px 0 6px}}
h3{{font-size:13px;margin:14px 0 4px}}ul{{margin:4px 0 0 18px;padding:0}}li{{margin:3px 0}}
.mut{{color:var(--mut);font-size:13px}}.hl li{{font-weight:600}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 20px}}
</style></head><body><main>
<h1>Day log</h1><p class=mut>{html.escape(day)}</p>
<div class=box>
{chr(10).join(body)}
{foot}
</div></main></body></html>
"""


def _fmt_items(items):
    return "; ".join(f"{i['owner'] or 'me'}: {i['ask']}" for i in items) or "none"


def main():
    cfg = load_cfg()
    today = date.today().isoformat()
    name = cfg.get("owner_name") or "the owner"
    s = load_state()
    d = digest(s, today)
    DIR.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    entry = {"date": today, "text": None, "highlights": [], "digest": d,
             "written_at": datetime.now().isoformat(timespec="minutes")}

    if "--digest-only" not in sys.argv:
        sid = cfg.get("slack_self_id") or ""
        slack_on = bool(sid) and agent.slack_enabled()
        sources = []
        if slack_on:
            sources.append(f'- Slack (if the Slack tools are available): slack_search_public_and_private query "from:<@{sid}> after:{today}" sort=timestamp; read the threads the hits sit in for context.')
        sources.append(f'- Gmail (if the Gmail tools are available): search_threads query "in:sent after:{today.replace("-", "/")}", then get_thread on each.')
        prompt = PROMPT.format(
            name=name, today=datetime.now().strftime("%A %d %B %Y"),
            slack_note=f" {name}'s Slack user id is <@{sid}>." if slack_on else "",
            opened=_fmt_items(d["opened"]), closed=_fmt_items(d["closed"]), chased=_fmt_items(d["chased"]),
            waiting=d["waiting"], needs_me=d["needs_me"], sources="\n".join(sources))
        print(f"[{stamp}] daylog: {len(d['opened'])} opened, {len(d['closed'])} closed, {len(d['chased'])} chased - asking {agent.display_name()}...")
        p = agent.run(prompt, (SLACK_TOOLS if slack_on else []) + GMAIL_TOOLS)
        (LOG / f"daylog-{stamp}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
        # some agents drop the markers and emit bare JSON - accept that too
        m = re.search(r"<<<DAYLOG>>>(.*?)<<<END>>>", p.stdout, re.S) or re.search(r'(\{\s*"text"\s*:.*\})', p.stdout, re.S)
        if not m:
            print("!! no DAYLOG block in output (rc %s). See log." % p.returncode)
            print(p.stdout[-1500:])
            messages.report(p, "daylog")   # this run's failure file (state/jobs/): why the AI failed, if it says (#47 review)
            sys.exit(1)
        try:
            out = json.loads(m.group(1))
        except ValueError:
            print("!! DAYLOG block was not valid JSON. See log."); print(p.stdout[-1500:]); sys.exit(1)
        entry["text"] = (out.get("text") or "").strip() or None
        entry["highlights"] = [str(h) for h in (out.get("highlights") or [])][:5]
        entry["written_at"] = datetime.now().isoformat(timespec="minutes")

    write_json(entry_path(today), entry)
    page_path(today).write_text(render_html(entry), encoding="utf-8")
    print(f"done: day log for {today} -> {entry_path(today).name}" + (" (digest only)" if entry["text"] is None else f", {len(entry['highlights'])} highlights"))


if __name__ == "__main__":
    main()
