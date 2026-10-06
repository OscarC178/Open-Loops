"""One click on a loop, applied to the state: done, reopen, snooze, priority, a note, a link, a typed reminder.

    answer, status, write = apply(state, body, people)

`body` is what the page sends to /api/action ({"action", "id", ...}); `people` is config.json "people" (read before
the state lock, which covers state.json only). The state is changed in place and saved by the caller only when
`write` is true, so a refused click (a bad date, an unknown priority) writes nothing. The page (app.py) and the
developer console (cli.py) both go through here, so a `done` from a terminal is the same `done` as the button's.
Vault (to-do file) items are not loops and are closed by standing.close_item, not here.
"""
import re
from datetime import datetime

from .store import norm_date

PRIORITIES = ("high", "normal", "low")
ACTIONS = ("add", "done", "reopen", "snooze", "unsnooze", "priority", "auto_on", "auto_off", "note", "add_link",
           "drop_link")


def _slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:32] or "x"


def apply(s, body, people=None):
    """The click in `body` applied to state `s` -> (answer, http status, write): s is saved only when write is true."""
    act = body.get("action")
    people = people or {}
    if act == "add":
        owner = (body.get("owner") or "").strip()[:80]
        ask = (body.get("ask") or "").strip()[:300]
        notes = (body.get("notes") or "").strip()[:2000]
        if not ask:
            return {"error": "need something to do"}, 400, False
        email = None
        for name, p in people.items():
            if name.lower() == owner.lower():
                owner = name
                email = (p or {}).get("email")
                break
        now = datetime.now().astimezone()
        lid = f"note-{_slug(owner or 'me')}-{_slug(ask)}-{now.strftime('%Y%m%d%H%M%S')}"
        s["loops"].insert(0, {
            "id": lid, "owner": owner, "owner_email": email, "ask": ask,
            "channel": "note", "thread": None, "link": None,
            "asked_at": now.isoformat(timespec="minutes"),
            "status": "needs_me", "inbound": True, "manual": True,
            "notes": notes, "links": [], "last_reply_at": None, "reply_snippet": None,
            "chases": 0, "snooze_until": None,
        })
        return {"ok": True, "id": lid}, 200, True
    for lp in s["loops"]:
        if lp["id"] == body.get("id"):
            if act == "done":
                lp["status"] = "done"
                lp["closed_at"] = datetime.now().isoformat(timespec="minutes")
            elif act == "reopen":
                # typed reminders belong in Needs me, not Waiting on them
                lp["status"] = "needs_me" if lp.get("channel") == "note" or lp.get("manual") else "waiting"
                lp["snooze_until"] = None
            elif act == "snooze":
                try:
                    lp["snooze_until"] = norm_date(body.get("until"))
                except ValueError as e:
                    return {"error": str(e)}, 400, False
            elif act == "unsnooze":
                lp["snooze_until"] = None
            elif act == "priority":
                pr = body.get("priority")
                if pr not in PRIORITIES:
                    return {"error": "priority is high, normal or low"}, 400, False
                lp["priority"], lp["priority_by"] = pr, "you"
            elif act == "auto_off":
                lp["auto_off"] = True
            elif act == "auto_on":
                lp["auto_off"] = False
            elif act == "note":
                lp["notes"] = (body.get("notes") or "").strip()[:2000]
            elif act == "add_link":
                url = (body.get("url") or "").strip()
                if not url.startswith("http"):
                    return {"error": "link must start with http"}, 400, False
                links = lp.setdefault("links", [])
                if not any(x.get("url") == url for x in links):
                    links.append({"url": url, "label": (body.get("label") or "").strip()[:60]})
            elif act == "drop_link":
                lp["links"] = [x for x in lp.get("links") or [] if x.get("url") != body.get("url")]
    return {"ok": True}, 200, True
