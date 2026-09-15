# Open Loops

<p align="center"><img src="docs/logo-512.png" width="128" alt="Open Loops"></p>

<p align="center">The list of things you asked people for — and the things they are waiting on you for — on your computer, in your words.</p>

<p align="center"><img src="docs/home.png" alt="Open Loops home: Needs me, Waiting on them, add a note"></p>

Runs locally. Uses the Claude or Grok subscription you already have. No API key costs.

## Why you want this

You ask people for things all day, on Slack and by email. Most come back. The ones that do not just go
quiet, and a quiet thread gives you nothing to notice: no unread badge, no reminder, no reason to scroll
back. It happens in reverse too. Someone replied three days ago, you meant to answer that afternoon, and
the thread has since sunk below the fold.

Search does not fix it, because search only helps once you already remember the thing you have forgotten.
So the loop closes when the other person chases you, or it never closes at all.

Open Loops gives you one list to read in the morning: what you are owed, what you owe, and how long each
has been sitting there. Chasing stops being something you have to remember and becomes something you tick
off.

**Who it's for:** people who run work over Slack and email, and who are the hold-up in more threads than
they can keep in their head. Connect either source, or both. Each works on its own.

## What it does

- **Two lists, so you always know where you stand.** *Needs me* is the people who have replied and are now
  waiting on you. *Waiting on them* is your asks that are still outstanding. Each list is one row per
  person: their name, a small block per loop coloured by age (green under two workdays, amber at two to
  four, red beyond that), and a line on what the loops are about. Click the person to open the cards.
- **Priority, judged for you and correctable by you.** The refresh marks each loop high, normal or low
  and names its theme in a few words. Change the priority on a card and your setting sticks; the AI
  never overrides a priority you set. Sort a list by oldest first or by priority.
- **Writes the chase so you don't have to.** *draft chase* puts a "just checking in…" nudge into the
  original Slack DM or email thread as a **draft** you read and send. It learns your voice from how you
  already write to those people and pitches it by seniority, so the nudge to your boss does not read like
  the one to your mate. It never counts the times of asking: escalation is dates and what is blocked, not
  guilt.
- **Keeps your own to-dos in the same place.** Add a note with or without a contact, so the jobs only you
  can do sit next to the real threads instead of in a separate app you stop opening.
- **Reads your own to-do file, if you keep one** (any markdown file; Settings → Connections), so open items you wrote down in your notes
  show up on the same morning list. Marking one done asks how you closed it and writes that back to the
  file.
- **Closes loops properly.** *done* and *snooze* are local. A loop you have closed is still watched for
  five days and reopens if the person comes back with a new question, so nothing slips out the back.
- **Ready before you sit down.** It refreshes itself every weekday morning, by default at 09:15.
- **Optional timer**, off unless you switch it on: chases anything quiet for N workdays automatically, with
  a cap per loop and an *auto: on/off* switch on every card.
- **Links and notes live on the card.** *+ link* attaches the doc a loop is about (Drive, Miro, Notion, Figma)
  and the refresh captures any document link it sees in the thread. *+ note* is a reminder to yourself about
  that person's ask.
- **Pinned**, at the top of Home: the boards and docs you open every day, yours rather than a loop's. A
  Miro board pin opens the board right there, read-only.
- **Day log**: what moved today, straight from the tracker, plus *Write it up*, which reads today's sent
  messages and drafts a short first-person note (Done / Moved / Waiting on) you can copy or print.
- **Roadmap** (needs Miro): paste standup notes, check the rows it reads out of them, preview, then add
  one sticky note per row to a frame on your Miro roadmap. It never deletes, moves or edits anything there.
- **Closing the tab stops the app.** No stray server to hunt for in Task Manager. *Quit* in Settings does
  the same, and the next double-click starts fresh.
- **A Console at the bottom of the page** keeps a short record of what the page did (checks, jobs, errors).
  *Copy all* adds what the app knows about itself, so a bug report is one paste, not a screenshot.

Setting up takes one pass through Settings. On first run the page shows only the setup steps: it suggests
the dozen or so people you message most, guesses *senior / peer / junior / external* for each, and asks you
to correct it. *Learn my tone* then reads how you actually write to them, which is what makes the drafts
sound like you rather than like a reminder bot. Every action gives instant feedback, and *done* and
*snooze* come with an Undo.

## Why it's safe to trust

- **It runs on your machine.** A small local web page, no servers. Your list stays in `state.json` there,
  and tokens and settings are gitignored.
- **It reads your own sent messages and the threads they sit in. Nothing else.** Other people's messages
  are read only to check for a reply on a loop you already have.
- **Drafts by default.** It sends only if you tick *Send to internal* / *Send to external*. The send tools
  are handed to the AI run only when those boxes are ticked, so with both off it cannot send.
- **No API key costs and no new accounts.** It drives the Claude or Grok CLI you are already signed in to.
  Jobs run on Sonnet by default; the model and effort are a picker in Settings, so a scan never quietly
  burns your best model.
- **Every run leaves a log** in `state/logs/`, so you can see what it looked at and what it decided.
- **Open source, MIT licence.** Read it before you point it at your inbox.

## Install (1–3 minutes)

1. Download and run the installer: **[Windows – OpenLoops-Setup.exe](https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops-Setup.exe)** · **[Mac – OpenLoops.dmg](https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops.dmg)**.
   It fetches the latest Open Loops from this repo, installs Python and Claude Code if you don't have them,
   and puts an **Open Loops** icon on your Desktop. Windows may show a SmartScreen warning and macOS an
   unidentified-developer warning, because the installers are not code-signed: choose *More info → Run anyway*
   (Windows) or *right-click → Open* / *Privacy & Security → Open Anyway* (Mac).
   No installer? Download the zip of this repo instead and double-click `Open Loops.cmd` (Windows) or
   `Open Loops.command` (Mac).
2. Tick the checklist: sign in, connect Slack and/or Gmail (one is enough). Miro is optional and only
   needed for the Roadmap section.
3. Open **Open Loops** from the Desktop each morning (Mac: orange-loop app — drag it to the Dock).

Guides: [GETTING-STARTED.md](GETTING-STARTED.md) · [INSTALL.md](INSTALL.md) (Grok Gmail step is here).
What is planned next: [ROADMAP.md](ROADMAP.md).

MIT licence. WhatsApp is not possible (no API for personal accounts).

## Running from a checkout (developers)

The installed copy lives in `%LOCALAPPDATA%\OpenLoops` (Mac: `~/Documents/OpenLoops`) and answers on port 8765.
A git checkout is a second, separate copy with its own gitignored `config.json` and `state.json`. From the
checkout folder, in any terminal (needs Node for the `npm` wrapper, nothing is installed):

| Command | What it does |
|---|---|
| `npm run dev` | stop any earlier dev session, then start this checkout on http://localhost:8766 and open the browser (never touches the installed copy) |
| `npm run stop` | stop it, same as closing its tab (`-- --now` does not wait for a running job) |
| `npm run prod` | start, or just open, the installed copy on 8765: the live version you use day to day |
| `npm test` | every `tests/test_*.py`, with a summary |
| `npm run doctor` | the connection checklist with Slack / Miro route detection |
| `npm run refresh` | one refresh job in the foreground (`-- --slack-only` for the quick pass) |
| `npm run setup` | install or upgrade the installed copy from this checkout (keeps its config and state) |

Anything after `--` is passed through, e.g. `npm run dev -- --no-browser`. Without Node:
`python -m openloops.app --port 8766`, `python -m openloops.app --stop [--now] --port 8766`, `python tests/run_all.py`.
The full developer guide (two copies / two ports, making a change, where things live, conventions) is
[CONTRIBUTING.md](CONTRIBUTING.md).

## What's in the folder

| Path | What it does |
|---|---|
| `openloops/app.py` | the page at http://localhost:8765 (`python -m openloops.app`; `--port N` to choose, `--stop` quits a running one, as does closing the tab) |
| `package.json`, `scripts/loops.mjs` | `npm run dev` / `stop` / `prod` / `test` / `doctor` / `refresh` / `setup` for a checkout (no npm packages) |
| `openloops/refresh.py` | finds new asks, checks open threads for replies (`--slack-only` for a quick Slack pass) |
| `openloops/chase.py` · `autochase.py` | drafts (or, if you tick the boxes, sends) a nudge; the optional timer |
| `openloops/voice.py` · `people.py` | learns how you write to each person; finds who you talk to most |
| `openloops/daylog.py` | what moved today, digest + optional first-person write-up |
| `openloops/roadmap.py` | paste standup notes, add cards to a Miro roadmap frame (needs the Miro plugin) |
| `openloops/standing.py` | the optional to-do file: reads open lines, writes back how you closed them |
| `openloops/index.html` | the whole page: CSS, markup, JS |
| `openloops/doctor.py` | the "are you connected?" check (`python -m openloops.doctor`) |
| `config.json` · `state.json` | your settings and your list (private, gitignored) · `state/logs/` one log per run |
| `scripts/` | weekday scheduled refresh (Task Scheduler / launchd) |

Read `INSTALL.md` §7 before installing: the Slack plugin-vs-connector gotcha is the one that bites.
Want to change it? [CONTRIBUTING.md](CONTRIBUTING.md).
