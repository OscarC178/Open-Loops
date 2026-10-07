# Open Loops, for an agent working in this checkout

- **Drive it from the terminal, not a browser**: `python3 -m openloops <command>` (`npm run cli -- <command>` from a
  checkout). The skill in `.claude/skills/openloops-console/SKILL.md` lists the commands, their `--json` shapes and
  when to prefer each; `python3 -m openloops --help` is the full reference.
- **Jobs without a sign-in**: `OPENLOOPS_AGENT=mock` makes `run <job>` answer in seconds with canned results
  (`openloops/mock_agent.py`). Never make it a default.
- **Tests**: `npm test` (or `python3 tests/run_all.py`); each test is its own process on a spare port and never
  calls Slack, Gmail or an AI.
- **Branches**: work branches from `develop`, PRs land on `develop`; `main` is the live app. See CONTRIBUTING.md.
- Stdlib Python only, no packages. UK English in anything a person reads.
