# Working on Open Loops

Everything you need to run a checkout, change it, test it and open a pull request. The app is stdlib Python
plus one HTML page; `npm` is only a command runner here and installs nothing.

## Prerequisites

| Need | Why | Check |
|---|---|---|
| Python 3.11+ | runs the app and the tests | `python --version` (Mac: `python3 --version`) |
| Node 18+ | the `npm run …` wrapper | `node --version` |
| Claude Code, signed in (or Codex with a ChatGPT sign-in, or Grok) | the jobs that read Slack / Gmail / Miro | `claude --version` |
| Slack and/or Gmail connected in Claude | anything past the setup screen | the checklist's Connect buttons, or `claude mcp list` |

## The commands

Run from the checkout folder.

| Command | What it does |
|---|---|
| `npm run dev` | stop any earlier dev session (cutting short a job it is running), then start this checkout on http://localhost:8766 and open the browser |
| `npm run stop` | stop it (closing its tab does the same a few seconds later); `-- --now` cuts a running job short |
| `npm run prod` | start, or just open, the installed copy on 8765 (the live version); says which commit it was installed from |
| `npm test` | every `tests/test_*.py`, one process each, with a summary |
| `npm run doctor` | the connection checklist with Slack / Miro route detection |
| `npm run refresh` | one refresh job in the foreground |
| `npm run setup` | install or upgrade the installed copy from this checkout |
| `npm run cli -- <command>` | the developer console for this checkout (see below): `status`, `list`, `act done <id>`, `run refresh`, `logs refresh`, `api GET /api/state` |

Anything after `--` is passed through: `npm run dev -- --no-browser`, `npm run refresh -- --slack-only`.
No Node? `python -m openloops.app --port 8766`, `python -m openloops.app --stop [--now] --port 8766`, `python tests/run_all.py`.

## The developer console

`python3 -m openloops <command>` (from a checkout: `npm run cli -- <command>`, which aims it at the dev port) is the
page without the page: everything you would otherwise click or read in the browser, from the terminal, so a change
can be built, run and checked in one place. It works on the copy it is run from, so the same command in
`~/Library/Application Support/OpenLoops` is the live install. `python3 -m openloops --help` lists everything;
the ones you will use most:

| Command | What it does |
|---|---|
| `status` | this copy: root and commit, whether its server is up (and whether the port holds another copy), agent / model / effort, loop counts, the jobs' last results |
| `list [needs-me\|waiting\|snoozed\|done\|all]` | the loops as the page lists them, age in workdays and colour band included; `show <id>` for one loop in full |
| `act done\|reopen\|snooze\|priority\|note\|add_link … <id>`, `add "<ask>"` | the card's buttons; the same code path as `/api/action` (`openloops/actions.py`) |
| `run refresh [--slack-only]`, `run chase <id>`, `run daylog`, … | one job in the foreground with its output as it comes, then its exit code, failure record and log path; refused if the server is already running that job |
| `jobs`, `logs [job]`, `diag`, `doctor` | what the server's jobs are doing, the newest run logs (`logs refresh` tails the last refresh), the Console's *Copy all*, the connection checklist |
| `config [key]`, `config set <key> <value>` | the settings in force; change one (`config set effort medium`, `config set auto_chase.enabled true`) |
| `agent [--check]` | which AI runs the jobs, the exact `claude -p …` command line, and with `--check` the sign-in kind; warns if a provider API key sits in your shell, because a job started from that shell would inherit it |
| `app start\|stop\|open\|url` | this copy's server; `api <METHOD> </api/path> [json]` hits any route on it |
| `screenshot [--width N] [--out F]` | the running page captured headless (Chrome + Node) to a PNG, `state/logs/page-<stamp>.png` by default; prints the path. Refused in one line without Chrome |
| `test [name …]` | `npm test`, or only the tests whose file name contains a word (`test cli connect`) |

With the server running, `list`, `act`, `add` and `jobs` go through it, so the page sees the change on its next
poll; without it, loops are read from `state.json` and actions are applied under the same file lock the jobs use.
`--json` gives machine-readable output on the commands that list things. `--help` writes nothing, like the app's.

An agent working in a checkout finds all this by itself: `.claude/skills/openloops-console/SKILL.md` (the commands,
the `--json` shapes, when to prefer which) and the pointer in `CLAUDE.md`.

### The mock agent: a job end to end with no sign-in

`OPENLOOPS_AGENT=mock npm run cli -- run refresh` (or `config set agent mock` for a whole copy) runs the jobs
against `openloops/mock_agent.py` instead of an AI: each gets, in seconds, canned text of the shape its prompt asks
for, so a change to a job's prompt, parsing or state write-back can be checked without Slack, Gmail or a sign-in.
A refresh writes two plausible loops to `state.json` (the page and `list` show them), a chase counts a draft,
people / daylog / voice / roadmap / standing write their files. `doctor`, `agent` and `status` say "mock" in so
many words, the template default stays `claude`, and Settings offer no such choice, so it never ships as a default.
Directives, in `OPENLOOPS_MOCK` or anywhere in the prompt (as Slipway's fleet mock takes them): `MOCK:SLEEP=<s>`,
`MOCK:FAIL` (the plain "didn't finish"), `MOCK:FAIL=expired|limit|network` (that AI failure, with its sentence).
What the AI actually does with Slack or Gmail still needs the real agent.

## Two copies, two ports

- **Installed copy**: `%LOCALAPPDATA%\OpenLoops` (Mac `~/Library/Application Support/OpenLoops`, not `~/Documents`: the
  morning job may not read files there, see INSTALL.md gotcha 8), port **8765**, started by the Desktop
  icon and the morning task. This is what you use day to day.
- **Your checkout**: port **8766**, started by `npm run dev`. It has its own gitignored `config.json`, `state.json`,
  `voice.json` and `state/`, so it never touches the installed copy's data. On first run the page walks you through
  setup like a new user (connections, who's who, tone, first scan). `npm run dev` always restarts: it stops the
  dev session already there (a refresh it was running is cut short, this is throwaway data) and starts a fresh
  one, so what you see is the code on disk. `npm run prod` starts or opens the installed copy instead.

Never start a checkout on 8765: the launcher would find the installed copy already there and open *its* page, and
you would be reading old code while thinking you were on new.

## Two branches

- **`develop`** is where work happens. Every branch starts from it and every PR lands on it. It is GitHub's
  default branch.
- **`main`** is the live app. It only moves when the owner merges `develop` into it, which is what the installed
  copy and the GitHub Pages site (`docs/` on `main`) pick up. Releases are automatic: every merge to `develop`
  publishes a pre-release test build (`v0.1.1-dev.N`) and every merge to `main` publishes a release (`v0.1.1`,
  or a bigger bump with the `release:minor` / `release:major` label on the PR). See `packaging/README.md`.
  Each release's installers fetch the commit its tag points to; a locally built installer fetches `main` unless
  told otherwise.
- Fallen behind? Rebase onto `develop`, never `main` (`git fetch origin develop && git rebase origin/develop`),
  then `npm test`. Park uncommitted work on a temporary branch rather than `git stash`: the stash is shared
  across worktrees.

## Making a change

1. Branch from `develop`.
2. Edit. The page (`openloops/index.html`) is served fresh on every load, so reload the browser to see it. Python
   changes need `npm run dev` again (it restarts the dev session for you); job scripts (`refresh`, `chase`, `daylog`, `roadmap`, …) are
   separate processes and pick up edits on their next run without a restart.
3. `npm test`. Tests build a throwaway install on a spare port and never call Slack, Gmail or Claude.
4. Keep the docs honest: `INSTALL.md` for users, `README.md` for the folder map, the spec under `docs/superpowers/specs/`
   for design decisions.
5. Open a PR against `develop`. Describe what a user sees differently, not just what changed.

## Where things live

| Path | What |
|---|---|
| `openloops/app.py` | the local server and every `/api/*` route |
| `openloops/cli.py`, `openloops/__main__.py` | the developer console, `python3 -m openloops <command>` |
| `openloops/actions.py` | one click on a loop (done, snooze, priority, note, link, add), shared by `/api/action` and the console |
| `openloops/index.html` | the whole page: CSS, markup, JS |
| `openloops/agent.py` | how a job calls Claude / Grok / Codex and which tools it may use |
| `openloops/mock_agent.py` | the mock agent: canned answers for every job, no AI (`OPENLOOPS_AGENT=mock`) |
| `openloops/screenshot.py`, `headless_chrome.js` | the console's `screenshot`: finds Chrome, drives it headless over the DevTools protocol (the tests' layout checks use the same script) |
| `.claude/skills/openloops-console/`, `CLAUDE.md` | what an AI agent working in the checkout reads: the console, the mock, the tests |
| `openloops/refresh.py`, `chase.py`, `daylog.py`, `roadmap.py`, `people.py`, `voice.py` | the jobs; each runs as `python -m openloops.<name>` |
| `openloops/store.py` | JSON helpers; `update_state()` so a long job never overwrites clicks made meanwhile |
| `openloops/doctor.py` | the connection checklist |
| `openloops/messages.py` | every failure a person can fix, in plain words, with the fix: what the checklist, toasts and banners say |
| `openloops/standing.py` | the optional to-do file |
| `tests/` | one file per area; `run_all.py` runs them all |
| `config.template.json` | every setting with its default; `config.json` is the personal copy and is gitignored |

## Conventions

- Stdlib only. No pip packages, no npm packages.
- Every external call goes through `agent.run(prompt, tools)` with an explicit tool allow-list. Jobs never send;
  chases are drafts unless the user has ticked *Send* in Settings.
- Claude runs as `claude -p --output-format json`. `agent.run()` hands the job the JSON's `result` as `p.stdout`, so
  jobs parse text as before; a failure is read from the result's `is_error` flag (`p.refused`), never from the answer,
  and a failed run's `p.stdout` is empty. Output with no JSON result is raw-output tolerance only: passed on at exit 0,
  a plain failure otherwise.
- Jobs write state through `store.update_state()`, never a plain write of a stale copy.
- Anything that exits early prints `SKIPPED: <reason>` and exits 2, so the page can say why.
- A failure the person can fix is worded once, in `messages.py` (what happened, then what to do, naming who: Google, Slack,
  Claude, your Mac's privacy settings), and looked up with `say(id)`. No exit codes, paths or tool names in the sentence;
  that detail goes to the Console and `/api/diag`. `tests/test_messages.py` checks the rules.
- UI changes follow the 20 UX laws summary in the spec's "UX pass" section: one primary action per section,
  36 px targets, instant feedback with Undo where cheap, one dialog for every "type something" moment.
