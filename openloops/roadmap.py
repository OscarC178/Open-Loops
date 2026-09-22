"""Roadmap card: paste standup notes, stage rows, add cards to a Miro frame.

    python -m openloops.roadmap read              -> read the board's frame: lanes, columns, what's on it
    python -m openloops.roadmap parse             -> turn the pasted notes into rows (no Miro call)
    python -m openloops.roadmap preview           -> plan which rows would be added / skipped
    python -m openloops.roadmap build --confirm   -> add ONE sticky note per planned row. Adds only.

Store: state/roadmap.json (rows, pasted, board, preview). Staging rows live here, never in
state.json, because refresh.py rewrites state.json. Miro is reached through the agent (agent.py)
and the official Miro plugin - no API key. Nothing is ever deleted, moved or edited on the board.
"""
import json, re, sys, time
from datetime import datetime

from . import agent, store
from .paths import ROOT
FILE = ROOT / "state" / "roadmap.json"
CREATED = ROOT / "state" / "roadmap-created.txt"
LOG = ROOT / "state" / "logs"

MODES = ("read", "parse", "preview", "build")
STATES = ("not_started", "in_progress", "blocked", "done")
MIRO_TOOLS = ["miro.*"]

HEAD = """UNATTENDED RUN - nobody can answer questions. Do not ask any. Output only what is requested.
The Miro tools come from the Miro plugin; use whichever of them fit (board_list_items / canvas_search /
canvas_read_as_svg for reading, canvas_update_from_svg / canvas_create_from_svg for creating). If a tool
fails, try another approach once, then report what you could.

"""

READ_PROMPT = HEAD + """Find {name}'s roadmap board on Miro: "{board}" (a board name, or a board link - if it is a
link, open that board). On it, find the frame titled "{frame}".

The frame is a grid. Lane names run down the LEFT edge (one per row - e.g. teams or workstreams);
period / column names run ACROSS THE TOP (e.g. weeks or months). Sticky notes and cards inside the
frame are roadmap items, each sitting in one lane row and one column. Read the frame's contents:
list the items inside it and infer the lane names and column names from the text items along the
frame's left edge and top edge (positions tell you which is which). For every sticky note / card
inside the grid, give its text and the lane and column it sits in.

Reply with ONLY a JSON object between the markers, nothing else:
<<<ROADMAP>>>
{{"board_url": "https://miro.com/app/board/...", "frame_title": "{frame}", "frame_id": "<the frame item's id>",
  "lanes": ["lane name", "..."], "columns": ["column name", "..."],
  "existing": [{{"title": "card text", "lane": "lane name", "column": "column name"}}]}}
<<<END>>>
"""

PARSE_PROMPT = HEAD + """Turn {name}'s standup notes into roadmap rows. One row per distinct piece of work; skip
chatter. Notes:

{pasted}

Known lanes (must match exactly, or "" if unsure): {lanes}
Known columns (must match exactly, or "" if unsure): {columns}
People {name} works with (use for owners when a first name appears): {people}

For each row: title (short, imperative or noun phrase), detail (one line, may be ""), owners
(comma-separated names or ""), lane, column, state - "blocked" if it is waiting on someone /
something else or explicitly stuck (even if the work itself is finished, e.g. "done, waiting for
sign-off"), "done" if it is finished and nothing more is awaited from anyone, "in_progress" if it is
being worked on, otherwise "not_started".

Reply with ONLY a JSON object between the markers, nothing else:
<<<ROADMAP>>>
{{"rows": [{{"title": "...", "detail": "...", "owners": "...", "lane": "...", "column": "...", "state": "not_started"}}]}}
<<<END>>>
"""

PREVIEW_PROMPT = HEAD + """{name} wants to add these rows to the frame "{frame}" on the Miro board "{board}" ({url}).
Nothing is created in this run - only plan it.

Rows to consider (id, title, owners, lane, column):
{rows}

What is already inside the frame (from the last read; re-read the frame with the Miro tools if
you can, to be sure):
{existing}

For each row decide "add" or "skip". Skip when: an item with the same meaning is already in the
frame (say which), or the lane / column is empty or not one of the frame's lanes {lanes} /
columns {columns}. Otherwise add.

Reply with ONLY a JSON object between the markers, nothing else:
<<<ROADMAP>>>
{{"plan": [{{"id": "r1", "action": "add", "why": "one line"}}]}}
<<<END>>>
"""

BUILD_PROMPT = HEAD + """Add roadmap items to the frame "{frame}" on {name}'s Miro board "{board}" ({url}).

Create exactly ONE CARD per row below, INSIDE the frame, at the intersection of the row's lane (a row
of the grid, named down the left edge) and its column (named across the top). Cards, never sticky
notes: this frame is a card board and a sticky note on it is a mistake.

How to make a card with the Miro tools: first canvas_read_as_svg on the frame so you have its
data-miro-id and the positions of its lane / column labels and existing items. Then ONE
canvas_update_from_svg call whose SVG wraps the new elements in the frame's own
<g data-miro-id="<frame id>" transform="translate(fx,fy)"> so child x / y are relative to the frame.
Each new card is (no data-miro-id - the server assigns one):
  <rect data-type="custom-widget" data-widget-type="card" data-title="<title>"
        data-description="<detail>  Owners: <owners>" data-color="<hex>" x=".." y=".."
        width="320" height="88" fill="none" stroke="none" />
Leave data-description off when there is neither detail nor owners (then height="60").
data-color by state: not_started #9aa0a8 (gray), in_progress #f5c400 (yellow), blocked #da0063 (red),
done #00b86b (green). Match the size of the cards already on the frame if they differ from 320x88.

If the cell already has items, place the new card below them without overlapping; if the cell is
full, place it just outside the frame next to that lane and say so in "note" (the owner will move
it in by hand). If a card could not be created at all, report it with "item_id": "" and why in "note".

NEVER delete, move, resize or edit any existing item. Do not create anything not listed here.
The item ids you report must be the data-miro-id values from the result_svg, never invented.

Rows (id, title, owners, lane, column, state):
{rows}
Details (id: detail text):
{details}

Reply with ONLY a JSON object between the markers, nothing else:
<<<ROADMAP>>>
{{"created": [{{"id": "r1", "item_id": "<miro item id, or empty when not created>", "url": "<link to the item or board, or empty>", "note": "<empty, or e.g. placed outside the frame / why it was not created>"}}]}}
<<<END>>>
"""


def _default():
    return {"rows": [], "pasted": "", "updated_at": "",
            "board": {"name": "", "url": "", "frame": "", "frame_id": "", "lanes": [], "columns": [],
                      "read_at": "", "existing": []},
            "preview": {"at": "", "plan": []}}


def load():
    d = store.read_json(FILE, None) or {}
    out = _default()
    out.update({k: v for k, v in d.items() if k in out})
    for k in ("board", "preview"):
        base = _default()[k]
        base.update(d.get(k) or {})
        out[k] = base
    out["rows"] = [normalise_row(r, i) for i, r in enumerate(out.get("rows") or [])]
    return out


def save(d):
    d["updated_at"] = datetime.now().isoformat(timespec="minutes")
    store.write_json(FILE, d)
    return d


_seq = 0


def normalise_row(r, idx_hint=None):
    global _seq
    r = r if isinstance(r, dict) else {}
    s = lambda k: str(r.get(k) or "").strip()
    state = s("state").lower().replace("-", "_").replace(" ", "_")
    rid = s("id")
    if not rid:
        _seq += 1
        rid = f"r{int(time.time() * 1000)}{_seq}"
    return {"id": rid, "title": s("title"), "detail": s("detail"), "owners": s("owners"),
            "lane": s("lane"), "column": s("column"),
            "state": state if state in STATES else "not_started",
            "source": s("source") or "hand",
            "posted_id": r.get("posted_id") or None}


def stage(rows=None, pasted=None):
    """The page's autosave: replace the staged rows and/or the pasted notes. Board/preview untouched."""
    d = load()
    if rows is not None:
        d["rows"] = [normalise_row(r, i) for i, r in enumerate(rows)]
    if pasted is not None:
        d["pasted"] = str(pasted)
    return save(d)


def _key(title):
    return re.sub(r"\s+", " ", str(title or "")).strip().lower()


def merge_rows(existing, parsed):
    out = list(existing)
    seen = {_key(r.get("title")) for r in out}
    for r in parsed:
        n = normalise_row(r)
        n["source"] = "notes"
        k = _key(n["title"])
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def board_id(url):
    """'https://miro.com/app/board/uXjVK1abc=/' -> 'uXjVK1abc='. Empty when it is not a board link."""
    m = re.search(r"miro\.com/app/(?:board|live-embed)/([^/?#]+)", str(url or ""))
    return m.group(1) if m else ""


def embed_url(board_url, frame_id=""):
    """Live Embed URL for the page's view-only iframe: the board, opened on the roadmap frame.
    Free, no token; the viewer needs normal access to the board (they are signed in to Miro anyway).
    Empty when the board is not known by link yet."""
    bid = board_id(board_url)
    if not bid:
        return ""
    url = f"https://miro.com/app/live-embed/{bid}/?autoplay=true&embedMode=view_only_without_ui"
    if frame_id:
        url += f"&moveToWidget={frame_id}"
    return url


def configured():
    cfg = store.load_cfg()
    board = str(cfg.get("roadmap_board") or "").strip()
    frame = str(cfg.get("roadmap_frame") or "").strip()
    return {"board": board, "frame": frame, "ok": bool(board and frame)}


def _ask(mode, prompt, tools):
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    LOG.mkdir(parents=True, exist_ok=True)
    p = agent.run(prompt, tools)
    (LOG / f"roadmap-{mode}-{stamp}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    m = re.search(r"<<<ROADMAP>>>(.*?)<<<END>>>", p.stdout, re.S) or re.search(r"(\{.*\})", p.stdout, re.S)
    if not m:
        print(f"!! no ROADMAP block in output (rc {p.returncode}). See log.")
        print(p.stdout[-1500:])
        sys.exit(1)
    try:
        return json.loads(m.group(1))
    except ValueError:
        print("!! ROADMAP block is not valid JSON. See log.")
        print(m.group(1)[-1500:])
        sys.exit(1)


def _rows_text(rows):
    return "\n".join(f'- {r["id"]} | {r["title"]} | {r["owners"] or "-"} | {r["lane"] or "?"} | {r["column"] or "?"} | {r.get("state") or "not_started"}'
                     for r in rows) or "- (none)"


def main(mode, confirm=False):
    if mode not in MODES:
        print(f"usage: python -m openloops.roadmap {'|'.join(MODES)} [--confirm]"); sys.exit(1)
    c = configured()
    if not c["ok"]:
        print("SKIPPED: set the Miro board and frame title in Settings > Roadmap first"); sys.exit(2)
    if mode == "build" and not confirm:
        print("SKIPPED: build needs --confirm (the page's two-click Add)"); sys.exit(2)
    cfg = store.load_cfg()
    name = cfg.get("owner_name") or "the owner"
    d = load()
    b = d["board"]
    b["name"], b["frame"] = c["board"], c["frame"]

    if mode == "read":
        out = _ask(mode, READ_PROMPT.format(name=name, board=c["board"], frame=c["frame"]), MIRO_TOOLS)
        b.update({"url": str(out.get("board_url") or b.get("url") or ""),
                  "frame_id": str(out.get("frame_id") or b.get("frame_id") or ""),
                  "lanes": [str(x) for x in out.get("lanes") or []],
                  "columns": [str(x) for x in out.get("columns") or []],
                  "existing": [{"title": str(e.get("title") or ""), "lane": str(e.get("lane") or ""),
                                "column": str(e.get("column") or "")} for e in out.get("existing") or []
                               if isinstance(e, dict)],
                  "read_at": datetime.now().isoformat(timespec="minutes")})
        save(d)
        print(f"done: {len(b['lanes'])} lanes, {len(b['columns'])} columns, {len(b['existing'])} items on the frame")
        return

    if mode == "parse":
        if not d["pasted"].strip():
            print("SKIPPED: nothing pasted"); sys.exit(2)
        out = _ask(mode, PARSE_PROMPT.format(
            name=name, pasted=d["pasted"].strip(),
            lanes=", ".join(b["lanes"]) or "(unknown - read the board first)",
            columns=", ".join(b["columns"]) or "(unknown - read the board first)",
            people=", ".join((cfg.get("people") or {}).keys()) or "none listed"), [])
        before = len(d["rows"])
        d["rows"] = merge_rows(d["rows"], [r for r in out.get("rows") or [] if isinstance(r, dict)])
        save(d)
        print(f"done: {len(d['rows']) - before} new rows ({len(d['rows'])} staged)")
        return

    pending = [r for r in d["rows"] if not r.get("posted_id")]
    if mode == "preview":
        if not pending:
            print("SKIPPED: no unposted rows"); sys.exit(2)
        out = _ask(mode, PREVIEW_PROMPT.format(
            name=name, frame=c["frame"], board=c["board"], url=b.get("url") or "url unknown",
            rows=_rows_text(pending),
            existing="\n".join(f'- {e["title"]} ({e["lane"]} / {e["column"]})' for e in b["existing"]) or "- (nothing, or not read yet)",
            lanes=b["lanes"], columns=b["columns"]), MIRO_TOOLS)
        ids = {r["id"] for r in pending}
        plan = [{"id": str(p.get("id")), "action": "add" if str(p.get("action")).lower() == "add" else "skip",
                 "why": str(p.get("why") or "")} for p in out.get("plan") or [] if isinstance(p, dict) and str(p.get("id")) in ids]
        d["preview"] = {"at": datetime.now().isoformat(timespec="minutes"), "plan": plan}
        save(d)
        print(f"done: {sum(p['action'] == 'add' for p in plan)} to add, {sum(p['action'] == 'skip' for p in plan)} to skip")
        return

    # build
    plan = {p["id"]: p["action"] for p in d["preview"].get("plan") or []}
    todo = [r for r in pending if plan.get(r["id"], "add" if not plan else "skip") == "add"]
    if not todo:
        print("SKIPPED: nothing planned to add - run Preview first"); sys.exit(2)
    out = _ask(mode, BUILD_PROMPT.format(name=name, frame=c["frame"], board=c["board"],
                                         url=b.get("url") or "url unknown", rows=_rows_text(todo),
                                         details="\n".join(f'- {r["id"]}: {r["detail"]}' for r in todo if r.get("detail")) or "- (none)"), MIRO_TOOLS)
    by_id = {r["id"]: r for r in todo}
    n = 0
    lines, missed, flagged, seen = [], [], [], set()
    for cr in out.get("created") or []:
        r = by_id.get(str(cr.get("id"))) if isinstance(cr, dict) else None
        if not r:
            continue
        seen.add(r["id"])
        note = str(cr.get("note") or "").strip()
        if not cr.get("item_id"):  # not created: row stays pending for a later build
            missed.append(f'{r["title"]}: {note or "no reason given"}')
            continue
        r["posted_id"] = str(cr["item_id"])  # a card exists (maybe outside the frame - see note), so never re-add it
        lines.append(f'{cr["item_id"]}  {r["title"]}')
        if note:
            flagged.append(f'{r["title"]}: {note}')
        n += 1
    missed += [f'{r["title"]}: not in the agent\'s reply' for r in todo if r["id"] not in seen]
    if lines:
        CREATED.parent.mkdir(parents=True, exist_ok=True)
        with CREATED.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    d["preview"] = {"at": "", "plan": []}
    save(d)
    for m in flagged:  # before the summary line, so the page's console tail stays the summary
        print("check on the board: " + m)
    for m in missed:
        print("not added: " + m)
    print(f"done: {n} of {len(todo)} added to the board" + (f" ({len(missed)} not added - see the job log)" if missed else ""))
    if n < len(todo):
        sys.exit(1)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "", confirm="--confirm" in sys.argv)
