# Design: colleague merge + Day log + Roadmap (Miro)

Date: 2026-09-15. Branch: `combining-feature-updates`. Source of the port list: `MERGE-MAP.md`.

## Goal

Bring the colleague's genuine additions into the package layout, fix the two bugs the comparison
surfaced, and add two new optional cards: **Day log** (what moved today) and **Roadmap** (paste
standup notes, stage rows, add cards to a Miro frame). Everything stays stdlib-only Python plus one
HTML page, and every external call goes through `agent.run()` with a connector, never an API key.

## Decisions taken

| Question | Decision |
|---|---|
| Miro access | Official Miro plugin for Claude Code (`claude plugin install miro@claude-plugins-official`), OAuth via `/mcp`. Server id `plugin:miro:miro`, tool prefix `mcp__plugin_miro_miro__`. Jobs allow the whole server. |
| Day log content | Instant digest from `state.json` (no agent call), plus a "Write it up" button that runs the agent for first-person prose from today's sent Slack/Gmail and the digest. |
| Board layout | Read from the board on demand. Settings hold board (name or link) and frame title. A "Read board" job returns lanes and columns, which feed the row dropdowns. |
| Install location | Unchanged (`%LOCALAPPDATA%\OpenLoops`). External folders are not needed; everything lives in `openloops/` and `state/`. |

## Architecture

```
openloops/
  store.py     NEW  read_json / write_json (atomic) / load_state / update_state / norm_date
  agent.py     CHG  slack_source (plugin|connector), miro server mapping
  refresh.py   CHG  --slack-only, two cursors, links capture, update_state on write
  chase.py     CHG  update_state on write
  autochase.py CHG  store readers
  doctor.py    CHG  detect slack_source, Miro step (optional)
  daylog.py    NEW  digest(state, day) pure; main() writes state/daylog/<date>.json + .html
  roadmap.py   NEW  state/roadmap.json store; modes read | parse | preview | build
  app.py       CHG  store helpers, norm_date, new jobs + routes, add_link action
  index.html   CHG  Update Slack, links, + note, generic log, Day log card, Roadmap card, settings
```

### store.py

- `read_json(path, default=None)`: `utf-8-sig`, returns `default` on missing or bad JSON.
- `write_json(path, obj)`: writes `path.tmp` then `replace()`; `indent=2`, `ensure_ascii=False`.
- `load_state()`: `read_json(ROOT/"state.json")`.
- `update_state(fn)`: read fresh, `fn(s)` mutates in place, write, return `s`. All job scripts use
  this for their final write so a refresh that ran for minutes cannot overwrite notes, snoozes,
  vault saves or clicks made meanwhile. Loops are matched by id inside `fn`.
- `norm_date(v)`: `YYYY-M-D` to `YYYY-MM-DD`; raises `ValueError` otherwise.

### agent.py

- `slack_source()`: config `slack_source` in `plugin` (default) or `connector`. Claude's Slack row
  in `_FMT` becomes `mcp__plugin_slack_slack__slack_{}` or `mcp__claude_ai_Slack__slack_{}`.
- `miro` row: claude `mcp__plugin_miro_miro`, grok `miro`. A logical tool `miro.*` qualifies to
  the server id alone, which Claude Code treats as "all tools from that server".

### refresh.py

- `SLACK_ONLY = "--slack-only" in sys.argv`. Requires Slack on, else prints `SKIPPED` and exits 2.
- Loops in scope: `from_mail()` and, in slack-only, `channel == "slack"`.
- Dates: Slack `after:` (outbound and inbound) from `slack_cursor or cursor`; Gmail and Gmail inbound from `cursor`.
- Tools: Slack only in slack-only; Gmail block and Gmail inbound omitted, Slack inbound (DMs + @-mentions, #20) kept; one-line mode note.
- Prompt gains a `links` array per new loop and per update: any document URL the thread mentions
  (Drive, Docs, Miro, Notion, Figma). Merged into the loop's `links` list, de-duplicated by URL.
- Write via `update_state`: slack-only sets `slack_cursor`, `last_slack_refresh`; full sets
  `cursor`, `slack_cursor` (only when Slack is on), `last_refresh`, `gmail_available`.
- `apply(s, out, slack_only, now)` is a pure function so the merge is unit-testable.

### daylog.py

- `digest(state, day)` returns `{opened, closed, chased, waiting, needs_me}` where the first three
  are lists of `{id, owner, ask, channel}` filtered by `asked_at`, `closed_at`, `last_chase_at`
  starting with `day`; the last two are counts of live loops.
- `main()`: builds the digest, asks the agent (Slack + Gmail read tools) for today's sent messages
  and writes 5 to 10 first-person bullets grouped Done / Moved / Waiting on, British English, as
  `<<<DAYLOG>>>{"text": "...", "highlights": [...]}<<<END>>>`. Saves `state/daylog/<date>.json`
  `{date, text, highlights, digest, written_at}` and renders `state/daylog/<date>.html`.
- `render_html(entry)`: minimal standalone page, no scripts.

### roadmap.py

Store `state/roadmap.json`:

```json
{"rows": [{"id": "r1", "title": "", "detail": "", "owners": "", "lane": "", "column": "",
           "state": "not_started|in_progress|done", "source": "notes|hand", "posted_id": null}],
 "pasted": "", "updated_at": "",
 "board": {"name": "", "url": "", "frame": "", "lanes": [], "columns": [], "read_at": "",
           "existing": [{"title": "", "lane": "", "column": ""}]},
 "preview": {"at": "", "plan": [{"id": "r1", "action": "add|skip", "why": ""}]}}
```

Modes (`python -m openloops.roadmap <mode>`), each an unattended agent run:

- `read`: Miro tools. Find the board (config `roadmap_board`, name or link) and the frame
  (`roadmap_frame`). Return board url, frame title, lane names (rows), column names, and existing
  card titles with lane and column. Saved to `board`.
- `parse`: no tools. Given `pasted`, known lanes, columns and the people list, return rows.
  Appended to `rows` (never replaces hand-edited rows).
- `preview`: Miro tools. For each unposted row, `add` or `skip` with a reason (already on the
  board, lane unknown). Saved to `preview`.
- `build`: Miro tools. Adds a sticky note or card per `add` row inside the frame at lane and
  column; never deletes or moves anything. Returns created ids; rows get `posted_id`; ids are
  appended to `state/roadmap-created.txt`. Requires `--confirm`.

Row staging (`save` mode) is handled in app.py without an agent.

**Live Embed.** The Roadmap card ends with a collapsed "Board (live, view only)" section holding a
Miro Live Embed iframe (`https://miro.com/app/live-embed/<board id>/?autoplay=true&embedMode=view_only_without_ui&moveToWidget=<frame id>`).
Free, no token, loads only when opened. The board id comes from the board link (Settings or the
`read` result); the frame id comes from `read`. `roadmap.embed_url()` builds it; `/api/roadmap` returns it as `embed`.

**Why MCP and not the REST API (decision 2026-09-15).** MCP needs no app registration or token
store and uses the user's own board permissions; its costs are model-driven placement and a daily
tool-call cap (Free 100, Starter 500, Business 2,000, Enterprise 10,000+ and admin-enabled). The REST
API is deterministic (list frame children with positions, create a sticky note with x/y and parent
frame) and effectively unlimited, but needs a Miro developer app plus a per-user OAuth token store
like `gmail_auth.py`. The Web SDK runs inside Miro and cannot be driven from the local page.
**Fallback rule:** keep `read` / `preview` / `build` as the interface; if after real use cards land
in the wrong cell more than about one run in five, or the daily cap blocks a build, re-implement
`read` and `build` on the REST API behind the same modes. Parsing stays with the agent either way.

### app.py

- Helpers from store; `snooze` uses `norm_date` and returns 400 on a bad date.
- `jobs` and `JOB_MOD` gain `daylog` and `roadmap`. `run_job(name, extra)` unchanged.
- Routes: `POST /api/refresh {slack_only}`; `GET /api/daylog` (digest + saved entry);
  `GET /api/daylog/page` (today's HTML, or `?date=`); `POST /api/daylog` (start prose job);
  `GET /api/roadmap` (store + config); `POST /api/roadmap {mode, rows, pasted, confirm}`;
  `POST /api/action {action: "add_link", id, url, label}` and `"drop_link"`.
- `EDITABLE` gains `slack_source`, `roadmap_board`, `roadmap_frame`.

### index.html

- Header: **Update Slack** button next to Refresh; meta shows `· slack <time>`.
- Cards: notes rendered through `linkify()` (escape then wrap URLs); `links` chips; **+ note**
  button opens the Needs-me add form pre-filled with owner and ask.
- Log pane built from `Object.keys(J)`; `button:disabled{cursor:wait}`.
- **Day log** card (collapsible, below the lists): digest lines, "Write it up", prose, copy,
  "open the page".
- **Roadmap** card (collapsible): board status line with "Read board"; paste box; "Read these
  notes"; "+ row by hand"; rows grid (title, detail, owners, lane, column, state, delete);
  Preview; "Add to the roadmap" armed for 6 seconds after a preview. Hidden entirely when Miro
  is not connected, with a one-line hint.
- Settings: Roadmap box (board, frame); Slack source shown read-only with the detected value.

### doctor.py

- Claude: detect `plugin:slack` vs `claude_ai_Slack` in `claude mcp list`, write `slack_source`.
  Add `{"id": "miro", "optional": true, "title": "Miro connected (optional)"}` with the `/mcp`
  fix text. Grok: no Miro step.

### Housekeeping

- `register-task.ps1` and `run-refresh.ps1` default and document 09:15; task time limit 60 min.
- `Open Loops.cmd` checks `pythonw` is on PATH before `start`.
- `setup.ps1` also excludes `docs` and `tests` from the install copy.
- INSTALL.md gains a "Before you start: gotchas" section and a Miro paragraph.

## Error handling

- Every job writes `state/logs/<job>-<stamp>.log` and prints a one-line summary; the page shows
  the tail under Last job output. Missing marker block: exit 1 with the last 1500 chars printed.
- Slack-only with Slack off, Roadmap with Miro unauthenticated: exit 2 with `SKIPPED:` reason.
- `add_link` rejects anything not starting with `http`.

## Testing

- `tests/test_store.py`: `update_state` preserves a note added between read and write; `norm_date`.
- `tests/test_refresh_apply.py`: slack-only leaves `cursor` alone and drops email new loops;
  full run advances both cursors; links merge without duplicates.
- `tests/test_daylog.py`: digest counts and filters; HTML renders without the agent.
- `tests/test_roadmap.py`: temp install on a spare port; save rows, parse-merge helper, preview
  gating, build refused without confirm; no agent involved.
- Existing tests keep passing: `test_manual_notes`, `test_port_clash`, `test_standing`.

## UX pass (2026-09-15, against the 20 UX laws)

Single role: the owner. Primary goal: see what needs me and what is stuck, act on it in one click.

- **Fitts**: every control is at least 36 px tall; chip × and row × are padded buttons; global `:focus-visible` ring.
- **Von Restorff / Hick**: one primary action per card (draft chase when waiting on them, done when it needs you);
  snooze quiet; note, + link, + note and auto-chase behind **more ▾** (remembered per card across re-renders).
- **Doherty / Peak-End / Postel (recover)**: every card action dims the card at once, then a toast says what happened;
  done, snooze, reopen, unsnooze and auto on/off carry **Undo**; failures toast with the reason and leave the card as it
  was. Job endings toast a summary (refresh: new / need you / waiting counts; chase: sent or drafted; skipped or failed
  jobs say so).
- **Postel (prevent) / Similarity**: the three native `prompt()` boxes and the vault-close popup are one `<dialog>` with
  labelled fields, Esc / Enter, and the input kept on a validation error. Snooze offers tomorrow / 2 days / next
  Monday / a week or a date.
- **Serial position / Miller / Zeigarnik (Settings)**: four groups (You, Chasing, Connections, App); Quit and Start
  over last, Start over marked destructive and its confirm says "cannot be undone"; a sticky Save bar that reads
  "unsaved changes" once anything is edited, and a leave-page warning while unsaved.
- **States**: "Checking your connections…" while the doctor runs instead of a blank Home.
- **First run (Hick / Peak-End / Postel recover)**: during setup the page is only the setup card and the steps
  strip; Day log, Roadmap and Refresh are hidden (not disabled) until "ready"; a failed step shows the last
  lines of its log inline next to Try again; reaching "ready" toasts "All set" with the morning refresh time.
- **Your own to-do file**: the "ClaudeCloud vault" folder setting becomes a generic, optional file path
  (`standing_file`; the old `vault_path` folder still resolves). The Settings box shows whether the file is
  there and how many open items it has as you type, offers "Create a starter file there", and folds the line
  format plus a list of everything else Open Loops writes to disk under a details toggle. Cards say
  "from <file name>" instead of naming a personal repo.
- **Pinned (Similarity / Fitts distance)**: a strip of chips at the top of Home for links that belong to the
  owner rather than to a loop (`config.pinned_links`, http only, one per url, label ≤ 60). Added through the same
  link dialog as card links; unpin has Undo. A Miro board chip gets a ▣ that opens the board in the same
  view-only live embed the Roadmap uses, one at a time, with "open in Miro" and "close" beside it. One pattern
  for Miro, Drive, Notion and the rest instead of a Miro-only button.

## Out of scope

- Sending email via the bundled Gmail MCP server, profiles, Gemini agent (ROADMAP.md).
- Deleting or moving Miro items. The build mode only adds.
