---
name: openloops-console
description: Drive this Open Loops checkout from the terminal with `python3 -m openloops <command>` instead of a browser or guessed /api routes. Use when you need to see loops or settings, click a loop (done, snooze, note), run a job, read a run log, check connections, start or stop this copy's server, or capture the page. Also covers the mock agent for running jobs with no AI sign-in.
---

# Open Loops developer console

`python3 -m openloops <command> [options]` is the page without the page: everything the browser shows or clicks,
from the terminal. It works on the copy it runs from (this checkout, or the installed folder), and when that
copy's server is up it goes through the same `/api/*` routes the page uses, so the page sees every change on its
next poll. `python3 -m openloops --help` is the full reference and writes nothing; CONTRIBUTING.md has the long
form.

Prefer `status` and `list` for a look; they cost nothing. `run <job>` calls the real AI and takes minutes unless
the mock agent is on (below). `screenshot` needs Chrome and Node.

## Two copies, two ports

- The checkout (this folder) answers on **8766** when started with `npm run dev` or `app start`. From a checkout
  use `npm run cli -- <command>`, which sets `OPENLOOPS_PORT=8766` for you; a bare `python3 -m openloops` looks on
  8765 first, finds the installed copy there, names it as "another copy" and leaves it alone.
- The installed copy (`~/Library/Application Support/OpenLoops`, Windows `%LOCALAPPDATA%\OpenLoops`) answers on
  **8765**. Run the console *from that folder* to drive it. Never start a checkout on 8765.
- `--port N` beats `OPENLOOPS_PORT` beats `config.json "port"` beats 8765.

## Commands

| Command | Use it for |
|---|---|
| `status [--json]` | root, commit, whether this copy's server is up, agent / model / effort, loop counts, the jobs' last results |
| `list [needs-me\|waiting\|snoozed\|done\|all] [--json]` | loops as the page lists them, age in workdays and colour band |
| `show <id>` | one loop, every field, as stored |
| `act <action> <id> [opt]` | `done`, `reopen`, `snooze --until YYYY-MM-DD`, `unsnooze`, `priority --priority high\|normal\|low`, `auto_on`, `auto_off`, `note --notes T`, `add_link --url U [--label L]`, `drop_link --url U` |
| `add "<ask>" [--owner N] [--notes T]` | a typed reminder (a Needs me card) |
| `run <job> [job options]` | one job in the foreground with its output, exit code, failure record and log path: `refresh [--slack-only]`, `chase <id>`, `daylog [--digest-only]`, `people`, `voice`, `roadmap read\|parse\|preview\|build [--confirm]`, `standing`. Refused while the server is running that job (`--force` overrides) |
| `jobs` | what the running server's jobs are doing (needs the server) |
| `logs [job\|file] [-n N] [--all] [--json]` | the newest run logs, or the tail of a job's newest log (`logs refresh -n 40`) |
| `doctor [--detect] [--recheck] [--json]` | the connection checklist as the page runs it; exit 1 when not ready |
| `diag [--json]` | what the Console's *Copy all* pastes |
| `config [key] [--json]`, `config set <key> <value>` | settings in force; change one (`config set effort medium`, `config set auto_chase.enabled true`; the value is JSON, else a string) |
| `agent [--check] [--json]` | which AI runs the jobs, its command line, and with `--check` the sign-in kind; warns about provider API keys in the shell |
| `app [start\|stop\|open\|url] [--no-browser] [--now]` | this copy's server; `app start` returns once it answers |
| `screenshot [--width N] [--out F]` | the running page captured headless to a PNG (default `state/logs/page-<stamp>.png`), path printed |
| `api <METHOD> </api/path> [json]` | any route on the running server (`api GET /api/state`, `api POST /api/action {"action":"done","id":"x"}`) |
| `test [name ...]` | every test, or the tests whose file name contains a word (`test cli mock`) |

Refusals are one line on stderr plus the usage line, exit 1; never a traceback.

## `--json` shapes

- `status --json`: `{root, build, server: null | {port, url, up_since, pages, ...}, other_servers: {root: port}, agent, mock, model, effort, loops: {needs_me, waiting, snoozed, done}, people, last_refresh, cursor, setup_done, jobs: {name: {running, rc, finished_at, failure, said}}}`.
- `list ... --json`: a list of rows `{id, owner, status, age_wd, band (green|amber|red), priority, priority_by, channel, ask, theme, extra}`, in the page's order. `show <id>` prints the loop as stored.
- `doctor --json`: `{steps: [{id, ok, optional?, title, fix, detail?, connect?}], agent, mock, all_ok, slack_source, miro_source, email}`.
- `agent --json`: `{agent, display, command, found, model, effort, slack_source, miro_source, job_argv?, mock?, note?, signed_in_as? (with --check)}`.
- `jobs --json`: the server's job table `{name: {running, rc, finished_at, log, said, ...}}`.

## The mock agent (no AI, no sign-in)

`OPENLOOPS_AGENT=mock python3 -m openloops run refresh` answers every job in seconds with canned text of the shape
its prompt asks for: a refresh writes two plausible loops to `state.json` (so `list` and the page show them), a
chase counts a draft, people / daylog / voice / roadmap / standing write their files. `config set agent mock`
does the same for a whole copy. `doctor`, `agent` and `status` say "mock" in so many words; nothing real is read
or written. Directives, in `OPENLOOPS_MOCK` or anywhere in the prompt: `MOCK:SLEEP=<s>` (a slow job),
`MOCK:FAIL` (fails, the plain "didn't finish"), `MOCK:FAIL=expired|limit|network` (fails as that AI failure).
Use it for a change to a job's prompt, parsing or state write-back; use the real agent for anything about what the
AI actually does with Slack or Gmail.

## Typical loops

- See a change to a job: `OPENLOOPS_AGENT=mock npm run cli -- run refresh`, then `npm run cli -- list`, then
  `npm run cli -- logs refresh`.
- See a change to the page: `npm run dev -- --no-browser` (or `npm run cli -- app start --no-browser`), then
  `npm run cli -- screenshot --width 1280`, and read the PNG.
- Check a click: `npm run cli -- act done <id>` then `npm run cli -- show <id>`; `act reopen <id>` undoes it.
- Everything green before a PR: `npm test` (or `npm run cli -- test cli mock screenshot` for just those).
