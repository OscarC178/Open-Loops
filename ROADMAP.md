# Roadmap

## Profiles: work / personal / all

The single biggest planned change. Today one install = one config + one state, so a person with a work
setup (Claude + work Slack + work Gmail) and a personal one (Grok + personal Gmail) has to choose.

Design sketch:

- A **profile** is a named pair of `config.json` + `state.json` (plus its `voice.json` /
  `people_suggested.json`), stored under `profiles/<name>/`. Each profile has its own agent, sources,
  people, tone, send switches and schedule — a work profile can run Claude against work Slack while a
  personal profile runs Grok against personal Gmail, on the same machine.
- The page gets a **profile switcher** (tabs: *All · Work · Personal*, names free-form). *All* is a
  merged, read-only view of every profile's loops, labelled by profile; actions (chase, done, snooze)
  apply within the loop's own profile.
- The daily refresh runs per profile, sequentially. Doctor checks per profile.
- Migration: an existing install becomes the single default profile; `profiles/` backups made before
  the feature exists (e.g. `profiles/work-2026-09-05/`) can be adopted as a profile by renaming.

## Smaller items

- Per-profile Gmail accounts via multiple `gmail_auth.py` token stores (the store already lives in
  `state/`, so this mostly falls out of profiles).
- More agents behind `agent.py` (Gemini CLI is the obvious next: same logical-tool mapping pattern).
- `reply`/send tool in `gmail_mcp.py` behind the send switches, for full email-send parity with Claude.
- Windows support for the bundled Gmail MCP server (`python3` vs `python` in `.grok/config.toml`).
