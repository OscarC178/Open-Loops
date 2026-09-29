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
    check(set(d["board"]) == {"name", "url", "frame", "frame_id", "kind", "lanes", "columns", "fields", "read_at", "existing"}, "board shape")
    check(d["board"]["kind"] == "frame" and d["board"]["fields"] == {"title": "Title", "detail": "Description", "status": "Status"},
          "a board is a frame until Read says otherwise; a Kanban's column titles default to Miro's")
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
    check(roadmap.item_url("https://miro.com/app/board/uXjVK1abc=/?share_link_id=1", "3074457350605242225")
          == "https://miro.com/app/board/uXjVK1abc=/?moveToWidget=3074457350605242225", "item_url: the table tools' link, query string dropped")
    check(roadmap.item_url("Planning", "1") == "" and roadmap.item_url("https://miro.com/app/board/uXjVK1abc=/", "") == "", "item_url needs both a board link and an item id")

    # A Kanban: Read stores kind "table", its statuses as columns, the column titles; Add uses the table prompt
    roadmap.configured = lambda: {"board": "https://miro.com/app/board/uXjVK1abc=/", "frame": "WEEK 40", "ok": True}
    asked = []
    def fake_run(prompt, tools, **k):
        asked.append(prompt)
        if "find the item titled" in prompt:
            return _P('<<<ROADMAP>>>{"board_url": "https://miro.com/app/board/uXjVK1abc=/", "frame_title": "WEEK 40", "frame_id": "345", '
                      '"kind": "table", "fields": {"title": "Title", "detail": "Description", "status": "Status", "bogus": "x"}, "lanes": [], '
                      '"columns": ["Not Started", "In Progress", "Blocked", "Complete"], '
                      '"existing": [{"title": "Fix layout tools", "lane": "", "column": "Not Started"}]}<<<END>>>')
        return _P('<<<ROADMAP>>>{"created": [{"id": "k1", "item_id": "row-9", "url": "https://miro.com/app/board/uXjVK1abc=/?moveToWidget=345", "note": ""}]}<<<END>>>')
    roadmap.agent.run = fake_run
    roadmap.main("read")
    b = roadmap.load()["board"]
    check(b["kind"] == "table" and b["frame_id"] == "345" and b["columns"] == ["Not Started", "In Progress", "Blocked", "Complete"] and b["lanes"] == [],
          "read of a Kanban: kind table, statuses as columns, no lanes")
    check(b["fields"] == {"title": "Title", "detail": "Description", "status": "Status"} and b["existing"][0]["column"] == "Not Started",
          "read of a Kanban: column titles kept (unknown keys dropped), rows as existing items")
    d = roadmap.load(); d["rows"] = [roadmap.normalise_row({"id": "k1", "title": "Ship it", "detail": "the last step", "owners": "Rafe", "column": "In Progress", "state": "in_progress"})]
    d["preview"] = {"at": "x", "plan": [{"id": "k1", "action": "add", "why": ""}]}; roadmap.save(d)
    roadmap.main("build", confirm=True)
    bp = asked[-1]
    check("table_sync_rows" in bp and "https://miro.com/app/board/uXjVK1abc=/?moveToWidget=345" in bp and '"Status"' in bp and "Not Started, In Progress, Blocked, Complete" in bp,
          "build on a Kanban: the table prompt, with the Kanban's link, its Status column and statuses")
    check("canvas_update_from_svg" not in bp.split("Insert exactly")[1] and "NEVER pass a rowId" in bp, "build on a Kanban: inserts only, no card SVG")
    check(roadmap.load()["rows"][0]["posted_id"] == "row-9", "build on a Kanban: the rowId is the posted id")
    d = roadmap.load(); d["board"]["kind"] = "frame"; roadmap.save(d)
    d["rows"].append(roadmap.normalise_row({"id": "k2", "title": "Frame card", "lane": "Comp", "column": "W1"})); d["preview"] = {"at": "x", "plan": [{"id": "k2", "action": "add", "why": ""}]}; roadmap.save(d)
    try:
        roadmap.main("build", confirm=True)
    except SystemExit:
        pass
    check("canvas_update_from_svg" in asked[-1] and "table_sync_rows" not in asked[-1].split("Create exactly")[1], "build on a frame: the card prompt as before")
    say("all good")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
