# Working on Open Loops

Everything you need to run a checkout, change it, test it and open a pull request. The app is stdlib Python
plus one HTML page; `npm` is only a command runner here and installs nothing.

## Prerequisites

| Need | Why | Check |
|---|---|---|
| Python 3.11+ | runs the app and the tests | `python --version` (Mac: `python3 --version`) |
| Node 18+ | the `npm run …` wrapper | `node --version` |
| Claude Code, signed in (or Grok) | the jobs that read Slack / Gmail / Miro | `claude --version` |
| Slack and/or Gmail connected in Claude | anything past the setup screen | `/mcp` in Claude |

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

Anything after `--` is passed through: `npm run dev -- --no-browser`, `npm run refresh -- --slack-only`.
No Node? `python -m openloops.app --port 8766`, `python -m openloops.app --stop [--now] --port 8766`, `python tests/run_all.py`.

## Two copies, two ports

- **Installed copy**: `%LOCALAPPDATA%\OpenLoops` (Mac `~/Documents/OpenLoops`), port **8765**, started by the Desktop
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
| `openloops/index.html` | the whole page: CSS, markup, JS |
| `openloops/agent.py` | how a job calls Claude / Grok and which MCP tools it may use |
| `openloops/refresh.py`, `chase.py`, `daylog.py`, `roadmap.py`, `people.py`, `voice.py` | the jobs; each runs as `python -m openloops.<name>` |
| `openloops/store.py` | JSON helpers; `update_state()` so a long job never overwrites clicks made meanwhile |
| `openloops/doctor.py` | the connection checklist |
| `openloops/standing.py` | the optional to-do file |
| `tests/` | one file per area; `run_all.py` runs them all |
| `config.template.json` | every setting with its default; `config.json` is the personal copy and is gitignored |

## Conventions

- Stdlib only. No pip packages, no npm packages.
- Every external call goes through `agent.run(prompt, tools)` with an explicit tool allow-list. Jobs never send;
  chases are drafts unless the user has ticked *Send* in Settings.
- Jobs write state through `store.update_state()`, never a plain write of a stale copy.
- Anything that exits early prints `SKIPPED: <reason>` and exits 2, so the page can say why.
- UI changes follow the 20 UX laws summary in the spec's "UX pass" section: one primary action per section,
  36 px targets, instant feedback with Undo where cheap, one dialog for every "type something" moment.
