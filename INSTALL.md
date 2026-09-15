# Open Loops — install & hand-over guide

A small local tool that tracks the requests you make on Slack and email until the other person
responds, then reminds you until *you* respond. Nothing runs in the cloud; nothing is sent on your
behalf — chases are created as **drafts** in the original thread and you press send.

## 1. What you need

| Requirement | Why | Check |
|---|---|---|
| Windows 10/11, or macOS | Task Scheduler + Desktop shortcut (Windows) / `launchd` + Desktop launcher (Mac) | — |
| Python 3.11+ (stdlib only, no pip installs) | runs the page and the scripts | `python --version` (Windows) / `python3 --version` (Mac) |
| An AI CLI, logged in — Claude Code (default) or Grok | does the reading/classifying via headless runs (`agent.py`) | `claude --version` / `grok --version` |
| Slack connected in that CLI *(optional)* | reads your DMs/channels, creates Slack drafts | Claude: `/mcp` shows *slack* connected · Grok: off unless ⚙ Settings → *Use Slack*, then `/mcps`, select *slack*, press `i` |
| Gmail connected *(optional)* | reads sent mail/threads, creates Gmail drafts | Claude: `/mcp` → *claude.ai Gmail* → Authenticate · Grok: see **Gmail with Grok** below |

Slack and Gmail are both optional sources — connect **at least one**; the checklist and every job adapt to
whichever is available (Gmail-only and Slack-only installs both work).

**Accounts / permissions this touches**
- **Slack**: whatever your Slack user can already see. The tool never posts; it only uses `slack_send_message_draft`.
- **Gmail**: with Claude, the claude.ai Gmail connector (OAuth to your Google account), read-only plus `create_draft`.
  With Grok, the app's own bundled Gmail MCP server (`gmail_mcp.py`) talking to the Gmail REST API with a
  token minted locally by `gmail_auth.py` (see below).
- **The AI CLI**: a subscription/API access for the headless runs. Each refresh is one short session.
- **No other credentials.** With Claude nothing is stored by this tool; with Grok the Gmail refresh token lives in `state/google_oauth.json` on your machine.

### Choosing your AI

`config.json` has `"agent": "claude"` (default) or `"grok"` — change it in ⚙ Settings → Preferences → *Your AI*. `agent.py` maps
each job's tool list to the agent's own naming and flags; the prompts are identical. The connection checklist
(`doctor.py`) checks whichever agent is selected. With Grok, Slack is **opt-in** (`"use_slack"`): off, jobs are
Gmail-only and the Slack plugin is not started or probed. Vercel is never loaded. Headless Grok jobs pass
`--effort low` because the CLI defaults to `xhigh`.

### Gmail with Grok (one-off, ~5 minutes)

Grok's MCP sign-in can't register itself with Google (Google's OAuth has no Dynamic Client Registration), and
Google's hosted Gmail MCP endpoint is gated behind the Workspace Developer Preview Program. So the app does the
Google sign-in itself (`gmail_auth.py`) and ships its own tiny Gmail MCP server (`gmail_mcp.py`, registered in
`.grok/config.toml`) that talks to the plain Gmail REST API — the same `search_threads` / `get_thread` /
`create_draft` tools the Claude connector has.

1. In [Google Cloud Console](https://console.cloud.google.com) create (or pick) a project, then **APIs & Services**:
   enable the **Gmail API**; on the **OAuth consent screen** add yourself as a **test user**; under **Credentials**
   create an **OAuth client ID** of type **Desktop app** and download its JSON.
2. Save that file as `google_oauth_client.json` in the app folder.
3. Run `python3 -m openloops.gmail_auth connect` (Windows: `python -m openloops.gmail_auth connect`) from the Open Loops folder and approve in the browser —
   pick the Gmail account Open Loops should read.
4. Open Grok once in the app folder and trust it, so it reads the app's `.grok/config.toml`.

Note: the bundled Gmail server deliberately has **no send tool** (drafts only), so with Grok, email chases are
always drafts even if *Send* is ticked; Slack sending still works.

## 2. Install (10 minutes)

**Easiest: the one-file installer.** [OpenLoops-Setup.exe](https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops-Setup.exe) (Windows) or [OpenLoops.dmg](https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops.dmg) (Mac),
from the [Releases page](https://github.com/OscarC178/Open-Loops/releases). Neither contains the app: when run,
it downloads the release's source from this repo and hands over to the same `setup.ps1` / `install.sh` described
below, so the two routes end up identical.

- **Windows**: `OpenLoops-Setup.exe` is an Inno Setup wizard. It asks for the first name and refresh time, downloads
  the repo zip, unpacks it to `%LOCALAPPDATA%\Programs\Open Loops` (the per-user Programs folder - no admin rights,
  and the app can write its `config.json` / `state.json` there, which `C:\Program Files` would not allow), runs
  `setup.ps1 -Dest … -NoLaunch`, and registers an uninstaller in *Apps & features*. Uninstalling removes the program
  files, the icons and the scheduled task, and asks before deleting `config.json` / `state.json`.
  SmartScreen shows "unknown publisher" because the exe is not code-signed: *More info → Run anyway*.
- **Mac**: `OpenLoops.dmg` holds **Install Open Loops.app**. Double-clicked, it opens Terminal, downloads the repo zip
  and runs `install.sh`. It is not signed with a Developer ID, so macOS 14 and earlier need *right-click → Open*, and
  macOS 15+ needs *System Settings → Privacy & Security → Open Anyway* once.

Both are built by `.github/workflows/release.yml` when a `v*` tag is pushed (see `packaging/README.md`).

**Alternative: the zip.**

1. Unzip anywhere (Downloads is fine).
   - **Windows**: double-click **`Open Loops.cmd`**. It runs `setup.ps1`, which installs Python / Claude Code via
     winget if missing, copies the app to `%LOCALAPPDATA%\OpenLoops` (no admin rights), asks for the user's first
     name, writes a fresh `config.json` from `config.template.json` and an empty `state.json`, creates the Desktop
     icon (→ `pythonw.exe app.py` in that folder), registers the weekday task (default 09:15) via Task Scheduler,
     and opens the app.
   - **Mac**: double-click **`Open Loops.command`** (right-click → **Open** the first time, to get past the
     unidentified-developer warning). It runs `install.sh`, which does the same but installs Python / Claude Code
     via Homebrew / the official installer if missing, copies the app to
     `~/Documents/OpenLoops`, creates **Open Loops.app** (logo icon) on the Desktop and in
     `~/Applications` and pins it to the Dock, and registers
     the weekday refresh as a `launchd` agent (`com.openloops.refresh`, default 09:15).

   The downloaded folder can be deleted afterwards either way.
2. On first open the app shows the **connection checklist** (`doctor.py`, re-checked every minute) until Claude is
   signed in and Slack + Gmail are connected. The Slack user id is detected automatically.
3. When green it shows **Who's who?** (`people.py`): the 12–15 people the user messages most, each with a sample
   line and a guessed *senior / peer / junior / external* to correct with radio buttons. Saving writes
   `config.people`, then runs *Learn my tone* (`voice.py`) and the first refresh automatically.
4. Everything else (name, refresh time, domains, sending, timer) is in ⚙ Settings — no file editing needed.

Manual equivalents, for support: `python -m openloops.doctor`, `python -m openloops.people`, `python -m openloops.voice`, `python -m openloops.refresh`,
`scripts\register-task.ps1 -At HH:MM` (`-Remove` to delete the task) on Windows, or
`scripts/register-task.sh --at HH:MM` (`--remove` to delete the agent) on Mac.

## 3. Daily use

- Double-click **Open Loops** → page opens at http://localhost:8765 (already refreshed by the morning job).
  If another program already uses port 8765, Open Loops picks the next free port and opens the browser
  there instead; set `OPENLOOPS_PORT` if you want a fixed one.
- **Pinned** (top of Home): the boards and docs you open every day, yours rather than a loop's. *+ pin* takes any
  link; a Miro board chip also gets a ▣ that opens the board right there, read-only (Miro's free live embed, no
  API call). Stored as `pinned_links` in config.json; unpin with × (Undo for a few seconds).
- **Your own to-do file (optional)**: Settings → Connections. Point it at a markdown file you already keep (or press
  *Create a starter file there*). Open lines appear under Needs me; pressing done asks how you closed the item and
  writes that back. The format and everything else Open Loops logs are listed under that setting. The old
  `vault_path` folder setting still works.
- **Closing the tab stops the app** a few seconds later (it waits for any running job first), so the next
  double-click starts fresh with whatever code is installed. *Quit Open Loops* under Settings → App does the same
  without closing the tab, and `python -m openloops.app --stop` does it from a terminal. If the tab just vanished
  (browser crash, laptop shut), the app notices within 15 minutes, and in any case quits after 3 h idle.
- **Home is a stack of collapsible sections**, all closed until you open them, and the browser remembers which you left open.
  *Add a note* stays on top. Then **Needs me** (they replied, you owe a response), **Waiting on them** (your ask is outstanding:
  green <2 workdays, amber 2–4, red >4), **Day log**, **Roadmap**, and **Snoozed / done** last. Each row shows its count and a
  one-line summary (e.g. "7 · 4 people · 3 fresh, 2 amber, 2 red"), so you can read the state of play without opening anything.
- Inside *Needs me* and *Waiting on them* the loops are grouped **by person**: one row each, with a colour
  block per loop (age) and a short line on what they are about; click the row for the cards. Cards carry a
  **priority** select (high / normal / low, guessed by the refresh, yours once you change it) and lists can
  be sorted oldest-first or by priority.
- A **Console** section sits at the bottom of both tabs: a timestamped record of checks, jobs and errors, with
  *Copy*, *Copy all* (adds build, port, last check and job output from `/api/diag`) and *Clear*.
- **draft chase** → warm, seniority-aware nudge appears as a draft in the same Slack DM / email thread. The card then shows *"✎ chase drafted <time>"* so you don't draft twice.
- **done / snooze / reopen** are local only, and each one shows a toast with **Undo** for a few seconds. Snooze offers
  tomorrow / 2 days / next Monday / a week or a date. Recently-closed loops are still watched for 5 days and reopen if the
  person comes back with a new question. Each card shows its one main action (draft chase, or done when it needs you);
  note, + link, + note and the auto-chase switch sit under **more ▾**.
- **⚙ Settings** (tab at the top) is six collapsed sections, so the one you need is a glance away: **Personal** (name, *Learn my
  tone*, who's who, exclusions), **Chasing** (external on/off, draft or send, timer, tone per seniority), **Preferences** (AI,
  model, refresh time), **History** (how far back it reads, what it writes to disk), **Connections** (to-do file, Miro board),
  **App** (quit, start over). The browser remembers which sections you left open. *Save settings* stays pinned at the bottom.
- **Update Slack** (next to Refresh, shown once Slack is connected) is a quick Slack-only pass: no email, about a
  third of the time. It keeps its own cursor, so the next full Refresh still picks up every email ask made in between.
- **+ link** on a card attaches a document URL (Drive, Miro, Notion, Figma); the refresh also captures any document
  link it sees in the thread. Links show as chips; bare URLs typed into a note become clickable too.
- **+ note** on a card pre-fills the *Needs me* form with that person and ask, for a reminder to yourself about it.
- **Day log**: what moved today, straight from the tracker. *Write it up* asks the
  AI to read today's sent messages and write a short first-person note (Done / Moved / Waiting on) with a copy button
  and a printable page. Nothing is sent.
- **Roadmap** (needs Miro connected and a board + frame set in Settings): paste standup notes,
  *Read these notes* turns them into rows with lane / column / owner, fix any mistakes, *Preview* shows what would be
  added, then *Add to the roadmap* (press twice within 6 s) adds one sticky note per row inside the frame. It never
  deletes, moves or edits anything on the board. *Read board* first so the lane and column choices match the frame.
  The section ends with a live, view-only embed of the board opened on that frame (put the board *link* in Settings,
  not just its name, to get it before the first read). Miro's plan sets a daily cap on tool calls (Free 100, Starter
  500, Business 2,000); a read + preview + build is roughly 20-40 calls.

## 4. Rules the tool follows (worth telling whoever installs it)

1. **Read-only, except drafts.** Allowed tools are pinned in each script (`ALLOWED = [...]`). No `send_message`, no `reply`, no `forward`, no label/trash tools.
2. **Only your own outbound messages** are scanned for new asks. Other people's messages are read only to check for replies on loops you already have.
3. **Headless runs cannot ask questions**; if a run is unsure it leaves the loop unchanged. Every run writes a log to `state/logs/`.
4. **External contacts** (email domain not in `internal_domains`) get the *external* tone and are skipped entirely if the Settings toggle is off.
5. **No counting** ("third time of asking") in chases, ever — escalation is done with dates and what's blocked, not guilt.

### Sending instead of drafting
Two tick boxes in ⚙ Settings: **Send to internal** and **Send to external** (both off by default).
- *Internal* = email domain in `internal_domains` (e.g. example.com, example.co.uk — set in ⚙ Settings), or a Slack-only contact.
- *External* = anyone else with an email — vendors, agencies, lawyers.
When a box is ticked, the button on those cards changes to **send chase**, asks for a confirm, and the message
goes straight out (Slack `send_message` / Gmail `reply` on the original thread) in your name — you don't see it
first. The card then shows *"➤ chase sent <time>"*. Everything else stays draft. The send tools are only added
to the headless run's allow-list when the relevant box is ticked, so with both unticked the tool cannot send.

### Timer (auto-chase)
⚙ Settings → *Enable timer*, with "after N workdays" and "max chases per loop". `autochase.py` runs right after
the morning refresh: any loop still *waiting*, not snoozed, quiet for N workdays since the ask or the last chase,
and under the max, gets chased — as a draft or a send according to the two boxes above. Every waiting card
then shows an **auto: on / off** switch so a particular message can be excluded from the timer. Never fires
twice on the same day for the same loop, never at weekends. Off by default.

## 5. Adding channels

The tool is channel-agnostic: a loop is `{owner, ask, thread, asked_at}`; `refresh.py` just needs a way to
(a) list your outbound messages and (b) re-read a thread. Add a channel by giving the headless Claude a tool
that can do those two things and mentioning it in `refresh.py`'s prompt and `ALLOWED` list.

| Channel | How | Status |
|---|---|---|
| Slack | Claude Code Slack plugin | ✅ built in |
| Gmail | claude.ai Gmail connector | ✅ built in |
| Outlook / Teams | claude.ai Microsoft 365 connector, when enabled for your org | doable — same pattern as Gmail |
| Notion comments | Notion connector | doable |
| **WhatsApp (desktop)** | No official API for personal accounts; WhatsApp Desktop exposes nothing to automate. Options: (1) WhatsApp Business Cloud API — only for a business number, and it can't read your personal chats; (2) drive **web.whatsapp.com** in Chrome via the Claude-in-Chrome extension — works for reading your own sent messages and thread replies, but it's screen-scraping: fragile, needs the tab open, and against WhatsApp's ToS for automation. | ⚠ not recommended; possible as a browser-driven read-only scan if you accept the fragility |
| SMS / iMessage | no desktop access on Windows | ✗ |

## 6. Files

```
openloops/        the app (python3 -m openloops.app)
  app.py          local web page (port 8765, or the next free port if that is taken)
  refresh.py      new asks + reply detection → state.json
  chase.py        draft a nudge for one loop
  voice.py        learn writing style → voice.json
  daylog.py       today's digest + optional prose → state/daylog/<date>.json/.html
  roadmap.py      Roadmap card: read board / parse notes / preview / build (Miro via the agent)
  store.py        JSON helpers; update_state re-reads state.json before a job writes it
  index.html      the page
tests/            qa.py and unit tests
scripts/          weekday refresh (Task Scheduler / launchd) + macos-app.sh
docs/             logo, screenshot, GitHub Pages, Mac Dock icon
config.json       your settings (gitignored, next to the folder root)
state.json        your list (gitignored)
state/roadmap.json  staged roadmap rows (kept out of state.json on purpose)
state/daylog/     one json + html per day
state/logs/       one log per run
```

## 7. Before you start: gotchas

1. **Slack route.** Claude can reach Slack through the Slack *plugin* (`plugin:slack:slack`) or the *claude.ai Slack
   connector*. Same tools, different tool prefix, and with the wrong one a refresh silently finds nothing. The
   connection check detects which you have and stores it as `slack_source` in `config.json`; Settings shows the
   detected route. If you switch, press *Check again* on the Home tab.
2. **Miro (Roadmap card only).** Two routes, like Slack: the *claude.ai Miro connector* (add it at claude.ai →
   Connectors, or *Open Claude* → `/mcp` → **Miro** → Authenticate) or the *Miro plugin*
   (`claude plugin install miro@claude-plugins-official`, then `/mcp` → **miro** → Authenticate). The connection check
   detects whichever is connected and stores it as `miro_source`; the plugin wins if both are. Each Miro login is tied
   to one Miro team.
3. **Second launch only opens the browser.** If Open Loops is already running, double-clicking the icon just opens the
   page. After editing anything in `openloops/`, close the tab (the app stops a few seconds later) or run
   `python -m openloops.app --stop`, then launch again. Developers: run a checkout side by side with the installed
   copy using `npm run dev` (port 8766) and `npm run stop`; see "Running from a checkout" in README.md. If you reopen the page within those few seconds the app simply
   carries on; a reload never stops it.
4. **UTF-8 BOM.** PowerShell tends to write a BOM at the start of JSON files. Every reader in the app uses `utf-8-sig`
   and the installer writes without a BOM; keep both if you add scripts.
5. **OneDrive / Dropbox folders** lock files while syncing. Install to the default `%LOCALAPPDATA%\OpenLoops`, not a
   synced folder.
6. **Which model the jobs use.** Every job runs `claude -p` with `--model` and `--effort` from `model` and
   `effort` in `config.json` (template: `sonnet` at `xhigh`; Settings → Preferences → Your AI). Sonnet at xhigh or Opus at medium
   both do the job. Leave either blank and the jobs inherit whatever `claude` defaults to on that computer, which
   is usually the most expensive model available. Grok ignores both.
7. **Jobs never clobber your clicks.** A refresh can run for minutes; anything you add or snooze meanwhile is kept
   because every job re-reads `state.json` just before writing (`store.update_state`).
