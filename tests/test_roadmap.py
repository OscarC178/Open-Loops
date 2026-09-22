"""Roadmap staging store: default shape, autosave, note merge, build gate.

    python3 tests/test_roadmap.py    # fast; no Miro, no agent, no server. Temp store.
"""
import shutil, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from openloops import roadmap  # noqa: E402

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


tmp = Path(tempfile.mkdtemp(prefix="openloops-roadmap-"))
roadmap.FILE = tmp / "roadmap.json"
roadmap.CREATED = tmp / "roadmap-created.txt"
roadmap.LOG = tmp / "logs"
try:
    d = roadmap.load()
    check(set(d) == {"rows", "pasted", "updated_at", "board", "preview"}, "load() has every top-level key")
    check(set(d["board"]) == {"name", "url", "frame", "frame_id", "lanes", "columns", "read_at", "existing"}, "board shape")
    check(set(d["preview"]) == {"at", "plan"} and d["rows"] == [], "preview shape, no rows")

    r = roadmap.normalise_row({"title": "  Fix layout tool ", "state": "bogus", "owners": "Rafe"})
    check(r["title"] == "Fix layout tool" and r["state"] == "not_started" and r["source"] == "hand", "normalise_row strips, defaults state")
    check(r["id"].startswith("r") and r["posted_id"] is None, "normalise_row mints id, posted_id None")
    r2 = roadmap.normalise_row({"id": "keep", "state": "In Progress", "posted_id": "m1"})
    check(r2["id"] == "keep" and r2["state"] == "in_progress" and r2["posted_id"] == "m1", "normalise_row keeps id/posted_id, maps state")

    d = roadmap.stage(rows=[{"title": "A", "lane": "Comp"}, {"title": "B"}], pasted="raw notes")
    again = roadmap.load()
    check([x["title"] for x in again["rows"]] == ["A", "B"] and again["pasted"] == "raw notes", "stage() persists rows + pasted")
    check(again["updated_at"], "stage() sets updated_at")
    roadmap.stage(pasted="only notes")
    again = roadmap.load()
    check([x["title"] for x in again["rows"]] == ["A", "B"] and again["pasted"] == "only notes", "stage(pasted) keeps rows")

    merged = roadmap.merge_rows(again["rows"], [{"title": "  a "}, {"title": "C", "state": "done"}, {"title": ""}, {"title": "c"}])
    check([x["title"] for x in merged] == ["A", "B", "C"], "merge_rows de-dupes by title, ignores blanks")
    check(merged[2]["source"] == "notes" and merged[2]["state"] == "done" and merged[0]["source"] == "hand", "merge_rows tags parsed rows as notes")

    roadmap.configured = lambda: {"board": "b", "frame": "f", "ok": True}
    roadmap.agent.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("agent must not run"))
    try:
        roadmap.main("build", confirm=False)
        raise SystemExit("FAIL: build without confirm did not exit")
    except SystemExit as e:
        check(e.code == 2, "build without --confirm exits 2 before any agent call")

    # build: a created card is posted even when flagged (e.g. placed outside the frame - a card exists,
    # re-adding would duplicate it); a row the agent could not create at all stays pending
    class _P:  # what agent.run returns, minus everything build ignores
        returncode, stderr = 0, ""
        def __init__(self, out): self.stdout = out
    d = roadmap.stage(rows=[{"id": "r1", "title": "A"}, {"id": "r2", "title": "B"}])
    d["preview"] = {"at": "x", "plan": [{"id": "r1", "action": "add", "why": ""}, {"id": "r2", "action": "add", "why": ""}]}
    roadmap.save(d)
    roadmap.agent.run = lambda *a, **k: _P('<<<ROADMAP>>>{"created": [{"id": "r1", "item_id": "m1", "url": "", "note": "placed outside the frame"}, '
                                           '{"id": "r2", "item_id": "", "url": "", "note": "tool failed"}]}<<<END>>>')
    try:
        roadmap.main("build", confirm=True)
        raise SystemExit("FAIL: partial build did not exit 1")
    except SystemExit as e:
        check(e.code == 1, "build exits 1 when a row was not created")
    rows = {r["id"]: r for r in roadmap.load()["rows"]}
    check(rows["r1"]["posted_id"] == "m1" and rows["r2"]["posted_id"] is None, "flagged-but-created row is posted; uncreated row stays pending")
    check(roadmap.CREATED.read_text(encoding="utf-8").strip() == "m1  A", "created log has only the real card")
    check(roadmap.load()["preview"]["plan"] == [], "build clears the preview plan")
    roadmap.configured = lambda: {"board": "", "frame": "", "ok": False}
    try:
        roadmap.main("read")
        raise SystemExit("FAIL: unconfigured read did not exit")
    except SystemExit as e:
        check(e.code == 2, "unconfigured board exits 2")
    check(roadmap.board_id("https://miro.com/app/board/uXjVK1abc=/") == "uXjVK1abc=", "board_id from a board link")
    check(roadmap.board_id("https://miro.com/app/board/uXjVK1abc=/?share_link_id=1") == "uXjVK1abc=", "board_id ignores the query string")
    check(roadmap.board_id("Planning & Roadmap") == "", "board_id empty for a board name")
    check(roadmap.embed_url("Planning") == "", "embed_url empty until the board is known by link")
    e = roadmap.embed_url("https://miro.com/app/board/uXjVK1abc=/", "3074457350605242225")
    check(e == "https://miro.com/app/live-embed/uXjVK1abc=/?autoplay=true&embedMode=view_only_without_ui&moveToWidget=3074457350605242225", "embed_url focuses the frame")
    check("moveToWidget" not in roadmap.embed_url("https://miro.com/app/board/uXjVK1abc=/"), "embed_url without a frame id shows the whole board")
    say("all good")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
