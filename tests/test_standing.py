"""Standing-items.md round-trip: list, compulsory closure, vault cards on Home.

    python3 tests/test_standing.py    # fast; no Slack/Gmail. Temp vault + temp install.

Last, #61: two overlapping /api/state polls each apply only their own vault_seen changes (standing.merge_seen), so
neither loses the other's first_seen / changed_at.
"""
import json, shutil, time, urllib.error, urllib.request

from _helpers import fresh_install, isolated_env, start_app, stop

PORT = 0  # set by start_app(): the port the app says it bound
SAMPLE = """---
title: Standing Items
type: standing-items
updated: 2026-08-31
next-id: 10
---

# Standing Items

## Open

- [ ] A6 | Claude Code Vault | Run the skill audit | added 2026-08-28
- [ ] A7 | The Tenants Voice | Encode one Harbor eval | added 2026-08-28
- [ ] A8 | Claude Code Vault | Add a pre-send verifier | added 2026-08-28
- [ ] A9 | Claude Code Vault | Snoozed example | added 2026-08-31 | snoozed-until 2099-01-01

## Closed

- [x] A1 | Claude Code Vault | Old item | added 2026-06-05 | done 2026-08-22

Closure notes (2026-08-22): A1 → already done.
"""
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


tmp = app = fresh_install("openloops-standing-")
vault = tmp / "vault"
(vault / "02-Research").mkdir(parents=True)
(vault / "02-Research" / "standing-items.md").write_text(SAMPLE, encoding="utf-8")
cfg = json.loads((app / "config.json").read_text(encoding="utf-8"))
cfg.update(owner_name="Oscar", vault_path=str(vault))
(app / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
(app / "state.json").write_text(json.dumps({
    "cursor": "2026-01-01T00:00", "last_refresh": "2026-01-02T00:00", "loops": [],
}), encoding="utf-8")

env = isolated_env(tmp, OPENLOOPS_SKIP_AGENT="1")
srv, PORT = start_app(app, env)
try:
    loops = api("/api/state")[1]["state"]["loops"]
    ids = {l["id"] for l in loops}
    check(ids == {"vault-A6", "vault-A7", "vault-A8"}, "open items A6–A8 on Needs me; snoozed A9 hidden")
    a6 = next(l for l in loops if l["id"] == "vault-A6")
    check(a6["status"] == "needs_me" and a6["channel"] == "vault" and a6["owner"] == "Claude Code Vault",
          "vault card shape")
    check(a6.get("source", "") == "standing-items.md" and a6.get("vault_flag") == "new",
          "card says where it came from and flags first sighting as new")
    st = json.loads((app / "state.json").read_text(encoding="utf-8"))
    check("A6" in (st.get("vault_seen") or {}) and "loops" in st and not any(l.get("channel")=="vault" for l in st["loops"]),
          "first-seen is tracked in state.json; vault cards are not stored as loops")
    text = (vault / "02-Research" / "standing-items.md").read_text(encoding="utf-8")
    (vault / "02-Research" / "standing-items.md").write_text(
        text.replace("Encode one Harbor eval", "Encode TWO Harbor evals"), encoding="utf-8")
    a7 = next(l for l in api("/api/state")[1]["state"]["loops"] if l["id"] == "vault-A7")
    check(a7["vault_flag"] == "updated" and "TWO" in a7["ask"] and a7.get("vault_changed_at"),
          "rewritten standing-item is flagged updated on the next read")

    code, err = api("/api/action", {"id": "vault-A6", "action": "done", "closure": ""})
    check(code == 400 and "closing" in err.get("error", ""), "done without a closure note is 400")
    text = (vault / "02-Research" / "standing-items.md").read_text(encoding="utf-8")
    check("- [ ] A6 |" in text, "empty closure did not close A6")

    code, out = api("/api/action", {"id": "vault-A6", "action": "done",
                                    "closure": "Ran the deletion test; /synthesise split is a follow-up."})
    check(code == 200 and out.get("ok"), "done with a closure note")
    text = (vault / "02-Research" / "standing-items.md").read_text(encoding="utf-8")
    check("- [x] A6 |" in text and "- [ ] A6 |" not in text, "A6 moved to Closed as [x]")
    check("## Closure notes" in text and "Ran the deletion test" in text, "clarification written under Closure notes")
    ids = {l["id"] for l in api("/api/state")[1]["state"]["loops"]}
    check("vault-A6" not in ids and "vault-A7" in ids, "closed item leaves Needs me; others stay")

    html = (app / "openloops" / "index.html").read_text(encoding="utf-8")
    check("closeVault(" in html and "How are you closing" in html and "dlgOpen(" in html and "Mark done" in html,
          "Home tab has the compulsory close popup")
finally:
    stop(srv)
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- #61: two overlapping polls, key by key
# Two polls read state.json (no lock) and the to-do file, then save under the lock one after the other. Poll A saw
# the file with A6 only, poll B a moment later with A6 and a new A10 (and A7 reworded); both started from the same
# state.json. The old handler saved each poll's whole map, so the second save dropped the first one's keys.
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openloops import standing  # noqa: E402  (pure functions only: nothing here reads or writes a file)

item = lambda i, action, added="2026-09-20": {"id": i, "project": "P", "action": action, "added": added}
cp = lambda x: json.loads(json.dumps(x))
old = {"fp": standing._fp(item("A7", "Encode one Harbor eval")), "first_seen": "2026-09-01T09:00+01:00", "changed_at": None,
       "action": "Encode one Harbor eval"}
start = {"vault_seen": {"A7": dict(old)}}
poll_a, poll_b = cp(start), cp(start)
before_a, before_b = dict(poll_a["vault_seen"]), dict(poll_b["vault_seen"])
standing.touch_seen(poll_a, [item("A6", "Run the skill audit"), item("A7", "Encode one Harbor eval")])
poll_a["vault_seen"]["A6"]["first_seen"] = "2026-09-24T10:00+01:00"   # A came first: its time for A6 must stay
standing.touch_seen(poll_b, [item("A6", "Run the skill audit"), item("A7", "Encode TWO Harbor evals"), item("A10", "Chase the invoice")])
poll_b["vault_seen"]["A6"]["first_seen"] = "2026-09-24T10:01+01:00"
check(before_a == start["vault_seen"] and before_a["A7"] == old, "touch_seen leaves the map it was given as it was (it changes a copy)")
fresh = cp(start)   # state.json as the first save finds it
check(standing.merge_seen(fresh, before_a, poll_a["vault_seen"]), "poll A's merge changes state.json")
check(standing.merge_seen(fresh, before_b, poll_b["vault_seen"]), "poll B's merge, on the file A saved, changes it too")
seen = fresh["vault_seen"]
check(set(seen) == {"A6", "A7", "A10"}, f"both polls' keys are kept ({sorted(seen)})")
check(seen["A6"]["first_seen"] == "2026-09-24T10:00+01:00", "A6 keeps the first_seen of the poll that saw it first")
check(seen["A7"]["first_seen"] == old["first_seen"] and seen["A7"]["changed_at"] and seen["A7"]["action"] == "Encode TWO Harbor evals",
      "A7 keeps its first_seen and takes B's changed_at for the new wording")
check(seen["A10"]["first_seen"] and seen["A10"]["action"] == "Chase the invoice", "A10, seen by B only, is added")
# the other order: B saves first, then A (which read the file before A10 existed and before A7 changed)
fresh2 = cp(start)
standing.merge_seen(fresh2, before_b, poll_b["vault_seen"])
standing.merge_seen(fresh2, before_a, poll_a["vault_seen"])
check(set(fresh2["vault_seen"]) == {"A6", "A7", "A10"} and fresh2["vault_seen"]["A7"]["changed_at"]
      and fresh2["vault_seen"]["A6"]["first_seen"] == "2026-09-24T10:01+01:00",
      "the other order keeps both too: A cannot drop A10 or undo B's changed_at, and B's first_seen for A6 stays")


def two_polls(start, items_a, items_b, when_a, when_b):
    """Two polls from the same state.json, reading the file as items_a / items_b -> (before, after) for each; the
    times each records are when_a / when_b, so the test can tell whose record survived."""
    out = []
    for items, when in ((items_a, when_a), (items_b, when_b)):
        st = cp(start)
        b0 = dict(st["vault_seen"])
        standing.touch_seen(st, items)
        for k, rec in st["vault_seen"].items():   # stamp what this poll wrote with its own time
            if b0.get(k) != rec:
                rec.update({f: when for f in ("first_seen", "changed_at") if rec.get(f) and (k not in b0 or f == "changed_at")})
        out.append((b0, st["vault_seen"]))
    return out


def both_orders(start, a, b):
    """fresh after A then B, and after B then A."""
    ab, ba = cp(start), cp(start)
    standing.merge_seen(ab, *a); standing.merge_seen(ab, *b)
    standing.merge_seen(ba, *b); standing.merge_seen(ba, *a)
    return ab["vault_seen"], ba["vault_seen"]


# review of #66: a poll that saw A7 leave (closed) and one that saw it back, reworded, afterwards
a, b = two_polls(start, [], [item("A7", "Encode TWO Harbor evals")], "T1", "T2")
ab, ba = both_orders(start, a, b)
check("A7" not in ab, "closed then back: the close saved first wins that save; the poll that saw it back does not "
      "resurrect it from a record that was deleted meanwhile (the next poll records it again)")
check(ba.get("A7", {}).get("changed_at") == "T2" and ba["A7"]["first_seen"] == old["first_seen"],
      f"back then closed, saved in that order: the stale close does not delete the newer record ({ba.get('A7')})")
nxt = {"vault_seen": ab}
standing.touch_seen(nxt, [item("A7", "Encode TWO Harbor evals")])
check("A7" in nxt["vault_seen"], "...and the next poll after the close records A7 again")
# a key another poll re-added after this poll's snapshot lost it: never deleted by the stale poll
a, b = two_polls({"vault_seen": {"A7": dict(old)}}, [], [item("A7", "Encode one Harbor eval")], "T1", "T2")
fresh3 = {"vault_seen": {"A7": dict(old, first_seen="T9")}}   # re-added by a third poll with its own first_seen
standing.merge_seen(fresh3, *a)
check(fresh3["vault_seen"].get("A7", {}).get("first_seen") == "T9", "a record re-added since this poll's snapshot is not deleted by it")
# two successive rewords: A saw wording 2, B saw wording 3 (the newer), from the same state
a, b = two_polls(start, [item("A7", "wording 2")], [item("A7", "wording 3")], "T1", "T2")
ab, ba = both_orders(start, a, b)
check(ab["A7"]["action"] == "wording 2" and ab["A7"]["changed_at"] == "T1",
      "rewords saved A then B: B finds A7 changed since its snapshot and keeps A's record, not overwrite it blindly")
check(ba["A7"]["action"] == "wording 3" and ba["A7"]["changed_at"] == "T2",
      "rewords saved B then A: the older wording (A's) never overwrites the newer one")
nxt = {"vault_seen": cp(ab)}
_, dirty = standing.touch_seen(nxt, [item("A7", "wording 3")])
check(dirty and nxt["vault_seen"]["A7"]["action"] == "wording 3", "...and the next poll records the newest wording")
# "added here" never clears a concurrent changed_at: A saw A8 new; meanwhile B added it and C reworded it
fresh4 = {"vault_seen": {"A8": {"fp": "x", "first_seen": "T0", "changed_at": "T5", "action": "newer"}}}
b0 = {}
st = {"vault_seen": {}}
standing.touch_seen(st, [item("A8", "older")])
standing.merge_seen(fresh4, b0, st["vault_seen"])
check(fresh4["vault_seen"]["A8"] == {"fp": "x", "first_seen": "T0", "changed_at": "T5", "action": "newer"},
      "a key this poll saw as new but another poll recorded (and reworded) first is kept whole, changed_at included")
gone = {"vault_seen": {"A6": dict(old, fp="x"), "A7": dict(old)}}
b0 = dict(gone["vault_seen"])
standing.touch_seen(gone, [item("A7", "Encode one Harbor eval")])
fresh5 = {"vault_seen": {"A6": dict(old, fp="x"), "A7": dict(old), "A11": dict(old, fp="y")}}   # A11: another poll's
standing.merge_seen(fresh5, b0, gone["vault_seen"])
check(set(fresh5["vault_seen"]) >= {"A7", "A11"} and "A6" not in fresh5["vault_seen"],
      "a key this poll saw leave, unchanged since, is removed; one it never saw (another poll's) is left alone")
app_src = (Path(__file__).resolve().parent.parent / "openloops" / "app.py").read_text(encoding="utf-8")
check("standing.merge_seen(fresh, seen_before" in app_src and 'fresh["vault_seen"] = s.get(' not in app_src,
      "/api/state merges per key under the lock, never saves the pre-lock map whole")
say("all passed")
