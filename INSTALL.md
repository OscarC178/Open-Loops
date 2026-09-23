# Open Loops — install & hand-over guide

A small local tool that tracks the requests you make on Slack and email until the other person
responds, then reminds you until *you* respond. Nothing runs in the cloud; nothing is sent on your
behalf — chases are created as **drafts** in the original thread and you press send.

## 1. What you need

| Requirement | Why | Check |
|---|---|---|
| Windows 10/11, or macOS | Task Scheduler + Desktop shortcut (Windows) / `launchd` + Desktop launcher (Mac) | — |
| Python 3.11+ (stdlib only, no pip installs) | runs the page and the scripts | `python --version` (Windows) / `python3 --version` (Mac) |
| An AI CLI, logged in — Claude Code (default), Codex or Grok | does the reading/classifying via headless runs (`agent.py`) | Nothing to do beforehand: the checklist's **Install** button installs it (the command is shown first), then the **Sign in** button runs `claude auth login` (Claude) or `codex login` (Codex). Check by hand: `claude --version` / `codex --version` / `grok --version` |
| Slack connected in that CLI *(optional)* | reads your DMs/channels, creates Slack drafts | Claude: the checklist's **Install Slack plugin** / **Connect Slack** buttons (fallback: *Open Claude (advanced)* → `/mcp` → *slack* → Authenticate) · Codex: connected in your ChatGPT account; the checklist's **Connect Slack** opens chatgpt.com/apps (see **Codex (ChatGPT)** below) · Grok: off unless ⚙ Settings → *Use Slack*, then `/mcps`, select *slack*, press `i` |
| Gmail connected *(optional)* | reads sent mail/threads, creates Gmail drafts | Claude: the checklist's **Connect Gmail** button (Gmail must be added at claude.ai → Settings → Connectors first; fallback: `/mcp` → *claude.ai Gmail* → Authenticate) · Codex: connected in your ChatGPT account, like Slack · Grok: see **Gmail with Grok** below |

Slack and Gmail are both optional sources — connect **at least one**; the checklist and every job adapt to
whichever is available (Gmail-only and Slack-only installs both work).

**Connecting with Claude: buttons, not a terminal.** Each unticked row on the checklist has a button. The app runs
the matching Claude Code command in the background (`claude auth login`, `claude plugin install
slack@claude-plugins-official`, `claude mcp login <server>`), your browser opens the sign-in page, and you click
*Allow*; the row ticks a few seconds later. The checklist reads its ticks from `claude auth status` and
`claude mcp list`, not from a trial prompt. What each command printed is in `state/connect-<step>.log`. If a button
doesn't do it, *Open Claude (advanced)* opens a terminal running `claude`, where `/mcp` lists every connection.
Open Loops stores no tokens for this: the sign-ins stay wherever the Claude CLI keeps them.
A second Claude account needs its own Claude settings folder; for a second Slack workspace or Gmail inbox, see [More than one account](#8-more-than-one-account-work--personal).

**Installing the AI CLI: one button.** Apart from Python, nothing needs installing first, and the installers
(`OpenLoops-Setup.exe`, `OpenLoops.dmg`, the zip's `setup.ps1` / `install.sh`) never install an AI themselves: a
missing AI never stops them, they just say "No AI is installed yet. Open Loops will offer to install one on its
first screen." When the selected
AI's CLI is missing (or is there but won't start), the checklist's first row shows **Install Claude** (or Grok) with
every command it will run underneath; nothing downloads until you press it. The app saves the vendor's own installer
to `state/install/`, checks the download is complete, runs it, then checks the CLI answers `--version`; only then
does the row tick. None of it needs Node or Homebrew. What the commands printed is in `state/connect-install.log`;
the page itself only says, in plain words, which part failed. If the button fails, paste the commands the row
shows into Terminal (Windows: PowerShell). The installers, and where they are documented:

| AI | Mac installer (run with) | Windows installer | Source |
|---|---|---|---|
| Claude Code | `https://claude.ai/install.sh` (bash) | `https://claude.ai/install.ps1` | [code.claude.com/docs/en/setup](https://code.claude.com/docs/en/setup) (also `brew install --cask claude-code`, `winget install Anthropic.ClaudeCode`) |
| Codex | `https://chatgpt.com/codex/install.sh` (sh) | `https://chatgpt.com/codex/install.ps1` | [github.com/openai/codex](https://github.com/openai/codex) (also `npm i -g @openai/codex`, `brew install --cask codex`) |
| Grok | `https://x.ai/cli/install.sh` (bash) | `https://x.ai/cli/install.ps1` | [docs.x.ai/build/overview](https://docs.x.ai/build/overview) (lands in `~/.grok/bin`) |

**Accounts / permissions this touches**
- **Slack**: whatever your Slack user can already see. The tool never posts; it only uses `slack_send_message_draft`.
- **Gmail**: with Claude, the claude.ai Gmail connector (OAuth to your Google account), read-only plus `create_draft`.
  With Grok, the app's own bundled Gmail MCP server (`gmail_mcp.py`) talking to the Gmail REST API with a
  token minted locally by `gmail_auth.py` (see below). With Codex, the Gmail connector of your ChatGPT account.
- **The AI CLI**: a subscription/API access for the headless runs. Each refresh is one short session.
- **No other credentials.** With Claude nothing is stored by this tool (the setup buttons only start the Claude CLI's own sign-in; its tokens stay with the CLI); with Codex likewise (`state/codex-home/auth.json` is only a link to Codex's own `~/.codex/auth.json`); with Grok the Gmail refresh token lives in `state/google_oauth.json` on your machine.

### Choosing your AI

**The Set-up page.** On first run the page opens on Set-up: three cards, *Your AI* (Claude, ChatGPT (Codex) or Grok, each with what it needs and what it can't do), *Your sources* (Slack or Gmail is needed, and one of them is enough; Miro is optional, for the Roadmap card) and *Your schedule* (the morning refresh time, whether the Mac's morning refresh started, and last the *Start the first scan* box). Each card says Not started, In progress, Done or Needs you, and has one button taken from the connection checklist. A button that needs the browser opens a pop-up, "Click Allow in the tab that just opened, then come back", with the sign-in link in case no tab opened; it closes itself once that row ticks. The full checklist is under *Every check*. Later, ⚙ Settings → *Open Set-up* brings the same page back to fix a connection.

`config.json` has `"agent": "claude"` (default), `"codex"` or `"grok"` — change it in ⚙ Settings → Preferences → *Your AI*. `agent.py` maps
each job's tool list to the agent's own naming and flags; the prompts are identical. The connection checklist
(`doctor.py`) checks whichever agent is selected. With Grok, Slack is **opt-in** (`"use_slack"`): off, jobs are
Gmail-only and the Slack plugin is not started or probed. Vercel is never loaded. Headless Grok jobs pass
`--effort low` because the CLI defaults to `xhigh`.
What each of the three allows for a second account is in [More than one account](#8-more-than-one-account-work--personal).

### Codex (ChatGPT)

For people whose AI subscription is ChatGPT (Plus, Pro, Business, Enterprise). Choose **Codex (ChatGPT)** under
⚙ Settings → Preferences → *Your AI*. The checklist then shows:

1. **Codex is installed**: **Install Codex** runs OpenAI's installer (table above).
2. **Signed in to ChatGPT**: **Sign in** runs `codex login`, which opens the ChatGPT sign-in page in your browser. If
   the browser can't hand the sign-in back (it answers on `localhost:1455`), run `codex login --device-auth` in Terminal
   instead and press **Check again**. A Codex signed in with an **API key** shows red: OpenAI's connectors need a ChatGPT
   sign-in ("Some plugins aren't available with API key authentication", [learn.chatgpt.com/docs/plugins](https://learn.chatgpt.com/docs/plugins)).
   **Keychain sign-ins are not supported yet.** Open Loops runs Codex with its own settings folder and links your
   `~/.codex/auth.json` into it; if Codex keeps the sign-in in the Mac keychain (Windows Credential Manager) instead
   (`cli_auth_credentials_store = "keyring"` or `"auto"` in `~/.codex/config.toml`), there is no file to link, and Open
   Loops refuses to run rather than fall back to your own Codex settings. Fix: set `cli_auth_credentials_store = "file"`
   in `~/.codex/config.toml`, run `codex logout`, then `codex login` ([learn.chatgpt.com/docs/auth](https://learn.chatgpt.com/docs/auth)).
3. **Gmail / Slack connected**: these are ChatGPT connectors, set up once in your ChatGPT account, not in Open Loops or
   the CLI. **Connect Gmail** / **Connect Slack** open [chatgpt.com/apps](https://chatgpt.com/apps); connect there, come
   back and press **Check again**. The connectors read whichever Google and Slack accounts that ChatGPT account connected,
   so work and personal mail on one ChatGPT account is not possible; that needs two ChatGPT accounts (or, later,
   Open Loops profiles, see ROADMAP.md).
4. **Miro**: ChatGPT has no Miro connector, so the Roadmap card is off with Codex. If you have added a Miro MCP server
   to Codex yourself (`[mcp_servers.miro]` in `~/.codex/config.toml`, signed in with `codex mcp login miro`), Open
   Loops passes it through and asks about it. Slack through `mcp.slack.com` is not a route: Slack's MCP server does not
   support dynamic client registration and does not list Codex as a client ([docs.slack.dev](https://docs.slack.dev/ai/slack-mcp-server/)).

**How a Codex job runs.** One `codex exec` per job: the prompt on stdin, `--sandbox read-only`, `--ephemeral`, the
shell tool off, `codex_model` / `codex_effort` from `config.json` (the template's `gpt-5.6-sol` at `low` is Open Loops'
choice, not a Codex default; blank uses Codex's built-in default, never your own Codex settings; `gpt-5.5` leaves Codex
on 2026-10-14 per OpenAI's changelog). A job may run for `codex_timeout_s` (900 s) before it is stopped. Each run gets a
fresh settings folder of its own under `state/codex-home/<account>/` (one per ChatGPT account, so two accounts never
share anything): a link to your `~/.codex/auth.json` and a generated `config.toml`, so your own Codex plugins, skills,
memories and AGENTS.md are not loaded (measured on 2026-09-23: about 190k input tokens under a full `~/.codex`, 13k to
40k here). The first run for an account is a short warm-up with every connector off, so Codex can load its list of
ChatGPT connector tools. A run's folder is removed when it ends; one left by a run that was killed part-way is removed
by the next Codex run (before and after it) or app start, once the job that made it has ended, or after an hour if it
names no job. A folder whose job is still running is never removed, however old; on Windows, a job whose state can't
be read counts as running.

**Only the tools a job lists are switched on; anything else is switched off from the list Codex last fetched, and a
tool added by OpenAI since then is refused after the call.** Each run's `config.toml` switches every ChatGPT connector off
(`[apps._default] enabled = false`), switches on only the connectors the job needs (`[apps.<connector id>] enabled =
true`) and switches off each of their tools the job did not list (`[apps.<id>.tools.<tool>] enabled = false`). Those
tools are then not there to call: tested on 2026-09-23 with real runs, a run allowed only `gmail.get_profile` and told
to create a draft could not find a draft tool. So with both *Send* boxes off, `gmail.send_email` and
`slack.slack_send_message` are switched off, and chases are drafts (`gmail.create_draft`, `slack.slack_send_message_draft`);
with a *Send* box ticked, the chase gets the send tool and replies in the thread. The switches are made from one copy of
Codex's connector list taken into the run's own folder, and the run uses that same copy. If the copy lacks a tool the job
lists, the job does not run: one warm-up refreshes the list, and if it is still missing the job stops with "Codex is
still getting ready". **A tool OpenAI adds after the list was fetched cannot be switched off in advance: it is only
refused after it has run** (the run then fails and saves nothing: "Codex used a tool this job did not allow, so nothing
was saved"; a draft or message it made is not undone). To bound that window, a list older than 24 hours (or dated in the
future) is refreshed by a warm-up before the next job, and if the warm-up does not refresh it the job does not run
("more than a day old"). So the window is at most a day. A warm-up that fails (sign-in run out, allowance used up, too
slow) stops the job with that reason, within the job's own time limit.

**A source that isn't connected is skipped, not fatal.** If the list has no Gmail tools at all (Gmail not connected in
that ChatGPT account), a job runs without them and is told "Gmail is not connected in this ChatGPT account; skip email";
the same for Slack. A refresh then keeps that source's cursor (`gmail_cursor` / `slack_cursor` in `state.json`) where it
was, so nothing is skipped once it is connected. With neither connected, the job does not run and says so.
Now and then a Codex session starts without one connector's tools (seen in real runs). `codex exec --json` does not
report which tools a session got, so each run is asked to end its reply with a `TOOLS_SEEN:` line naming the listed
tools it found; Open Loops removes that line before the job reads the reply. **The line is the model's own report, not
something Codex checks.** "Codex couldn't reach its Gmail or Slack tools this time" appears only when a run called no
tool of a connector its job lists **and** its reply does not end with a `TOOLS_SEEN:` line naming one of them (a missing,
quoted, repeated or mid-reply line counts as none): the run then fails and saves nothing. A run whose line names the
tools but that called none of them (nothing to search, nothing it was allowed to do) is a normal result, and a refresh
applies it: each source's cursor moves when the reply says that source was searched (`gmail_available` /
`slack_available` true, or left out) and holds when it says false. So if a session really lacked a connector's tools
and the model still claimed them, an empty refresh counts as normal and can move the Gmail or Slack cursor past
messages it never read; those messages are then not searched again. The refusal is retried once first, but only when the job can only
read (refresh, people, tone, day log, the checklist), and never after the first attempt called anything but a read tool,
reported a draft or send, or failed on sign-in, allowance or time. Anything the first attempt called still counts: a
tool off the list in attempt 1 fails the run even if attempt 2 was clean. Two things
did **not** work and are not used: `default_tools_approval_mode` / `approval_mode = "approve"` (ignored by `codex exec`:
drafts were still created) and the app name `gmail` in place of the connector id.

**The checklist asks Codex once.** Whether Gmail and Slack answer can only be found out by asking Codex, which is a run
against your allowance. Every attempt is kept in `state/codex-probe.json`: a day while at least one source works,
15 minutes while none does, 5 minutes after a failed attempt. **Check again** asks afresh, but never more often than
every 30 seconds. While Codex is still loading the connection list the rows say "still getting ready"; from the third
such attempt for an account they offer **Connect** instead and keep doing so until a check gets an answer. A source ticks only when Codex's own call to it succeeded, not just because the answer says so. The
same run reads your Slack user id, which replaces a stored one that differs.

**Windows is not verified yet.** The Codex route was built and tested on a Mac. On Windows, the sign-in link falls
back to a hard link when symlinks are not allowed, and the fake-CLI tests are skipped; expect rough edges.

**The ChatGPT plan's limits.** Codex on a ChatGPT plan is metered per 5-hour window and per week, shared with
ChatGPT's other Codex tools ([help.openai.com](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)).
Every scan, refresh and chase is one run. If the allowance runs out, Open Loops can't read anything until it comes
back, and the checklist says so. Keep this in mind before scheduling the daily refresh on a plan you also use heavily
for coding.

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

A second Gmail inbox with Grok is a second install with its own Gmail sign-in (Mac); see [More than one account](#8-more-than-one-account-work--personal).

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

Neither installs an AI. When the app first opens, press **Install Claude** on the checklist's first row. For Grok:
open ⚙ Settings → Preferences → *Your AI*, choose Grok, press **Save settings**, return to **Home**, then press
**Install Grok**.

Both are built by `.github/workflows/release.yml` when a `v*` tag is pushed (see `packaging/README.md`).

**Alternative: the zip.**

1. Unzip anywhere (Downloads is fine).
   - **Windows**: double-click **`Open Loops.cmd`**. It runs `setup.ps1`, which installs Python via winget if
     missing, copies the app to `%LOCALAPPDATA%\OpenLoops` (no admin rights), asks for the user's first
     name, writes a fresh `config.json` from `config.template.json` and an empty `state.json`, creates the Desktop
     icon (→ `pythonw.exe app.py` in that folder), registers the weekday task (default 09:15) via Task Scheduler,
     and opens the app.
   - **Mac**: double-click **`Open Loops.command`** (right-click → **Open** the first time, to get past the
     unidentified-developer warning). It runs `install.sh`, which does the same but installs Python via Homebrew if
     missing (with no Homebrew it points to python.org and stops), copies the app to
     `~/Library/Application Support/OpenLoops`, creates **Open Loops.app** (logo icon) on the Desktop and in
     `~/Applications` and pins it to the Dock, and registers
     the weekday refresh as a `launchd` agent (`com.openloops.refresh`, default 09:15).
     Older versions installed to `~/Documents/OpenLoops`. Running the installer again copies that copy's list and
     settings (`state.json`, `config.json`, `voice.json`, `people_suggested.json`, `state/`, `.grok/`,
     `google_oauth_client.json`, `profiles/`, `private/`) into the new place and registers the morning refresh
     there. The old folder is never moved, changed or deleted; Open Loops simply stops using it. Before copying,
     the installer pauses the old morning refresh and makes sure the old copy is not running; if it can't be sure,
     it stops and says what to do. Every copied file is checked byte for byte, and shortcuts (symbolic links) are
     left out and listed. If the new place already has a list, nothing is copied again. See gotcha 8 for why.
     With Grok, open Grok once in the new folder and trust it.

   Neither script installs an AI (Claude, Codex or Grok); if none is found they say so in one line and carry on.
   The downloaded folder can be deleted afterwards either way.
2. On first open the app shows the **connection checklist** (`doctor.py`, re-checked every minute) until the AI is
   installed and signed in and at least one of Slack or Gmail is connected. With no AI on the computer the first row is
   **Install Claude** (or the AI chosen in ⚙ Settings), with the commands it will run shown underneath. The Slack
   user id is detected automatically.
3. When green it says what the first scan will read and roughly how long it takes ("Looking back 30 days across Slack
   and Gmail. The first pass can take ten minutes.", from ⚙ Settings → History and what is connected) and waits for
   **Start the first scan** (or **Not now**). Nothing reads your accounts before that press; the page remembers the
   answer until this tab is closed (another tab asks again). Then it shows **Who's who?** (`people.py`): the 12–15 people the user messages
   most, each with a sample line and a guessed *senior / peer / junior / external* to correct with radio buttons.
   Saving writes `config.people`, then runs *Learn my tone* (`voice.py`) and the first scan automatically. With Gmail
   not connected, the first scan is the Slack-only pass (**Update Slack**), and setup finishes when it has run.
   Setup stays finished (`setup_done` in state.json): connecting Gmail later only offers **Run a full scan**.
   The choice is also saved as `first_scan` (`"go"` / `"later"`) in config.json; a new install starts with `"later"`,
   and the weekday refresh (and auto-chase) skips with one `SKIPPED:` line in the runner log until it is `"go"`. A run
   the app starts (a button) always runs. An install from before this setting has no key, which counts as `"go"`.
   `npm run refresh` / `python -m openloops.refresh` from a terminal counts as scheduled.
4. Everything else (name, refresh time, domains, sending, timer) is in ⚙ Settings — no file editing needed.

Manual equivalents, for support: `python -m openloops.doctor`, `python -m openloops.people`, `python -m openloops.voice`, `python -m openloops.refresh`,
`scripts\register-task.ps1 -At HH:MM` (`-Remove` to delete the task) on Windows, or
`scripts/register-task.sh --at HH:MM` (`--remove` to delete the agent) on Mac.

### Testing a fresh install

To try the installer as a new user would, next to the copy you use every day and without touching it:

```bash
bash install.sh --dest ~/OpenLoops-test --isolated --port 8790 --name "Test"
```

**A test copy is not a sandbox.** It uses the AI CLI you are signed in to on this computer, and so your real Slack,
Gmail and Miro accounts: the checklist asks that CLI what is connected (and, with Slack connected, asks Claude once
for your Slack id), and every scan it runs reads your real messages. Scans only read: nothing is sent or drafted
unless you press *draft chase* (or turn on sending in Settings). Without `--isolated`, one press of **Start the first
scan** is remembered until that tab is closed and the page then carries on with the setup scans by itself, reloads
included (they fill the copy's own `people_suggested.json` and use your AI plan's allowance), and a to-do file named in
its Settings is read, and written back when you press *done*.
`--isolated` is recommended for every test copy:

- it implies `--no-app` and `--no-task`, and writes `"isolated": true` (and `"test_copy": true`) into the copy's
  `config.json`;
- no to-do file is read or written back, whatever `standing_file` / `vault_path` say;
- the page never starts *Who's who*, *Learn my tone* or the first scan by itself, not even after a reload: each
  page load waits for **Start the first scan** (the button still works, and so do **Refresh** / **Update Slack**);
- the header shows a grey **Test copy: no automatic scans** pill;
- the weekday refresh and auto-chase skip on it even if a scheduled job points at it, and `--isolated` removes that
  copy's own weekday job if it has one (a job for another copy is left alone);
- the old-install port probe (8765–8784) is skipped, so nothing is sent to the copy you use every day. Instead,
  when it copies from an old `~/Documents/OpenLoops`, it refuses while any process has a file or its working folder
  there, or names that folder on its command line. Not detectable: a copy started from another folder with the old
  package on `PYTHONPATH`; quit it first.

`OPENLOOPS_ISOLATED=1` in the environment does the same for any copy at run time
(`OPENLOOPS_ISOLATED=1 python3 -m openloops.app --port 8790`). Running the installer on that folder again without
`--isolated` takes the mark off. The whole fresh-install check needs no stubs this way:

```bash
bash install.sh --dest ~/OpenLoops-test --isolated --no-launch --port 8790 --name "Test"
cd ~/OpenLoops-test && python3 -m openloops.app --no-browser &   # answers on 8790
```

Opening its page then runs only the connection checklist; nothing is scanned until you press **Start the first
scan**, **Refresh** or **Update Slack**. Stop it with `python3 -m openloops.app --stop --port 8790`.

The other flags:

- `--dest DIR` installs there instead of `~/Library/Application Support/OpenLoops` (or set `OPENLOOPS_DEST`). It never
  reads an older `~/Documents/OpenLoops`; only a default install copies from it.
- `--no-app` leaves `Open Loops.app` in `~/Applications`, on the Desktop and in the Dock alone.
- `--no-task` leaves the weekday refresh alone. There is one `com.openloops.refresh` job per Mac; without this flag
  the test copy would take it over.
- `--port N` saves the port in the test copy's `config.json`, so it never competes with the installed copy on 8765.
  `app.py` takes `--port`, then `OPENLOOPS_PORT`, then `config.json` `port`, then 8765.
- `--dest` together with `--no-app` and `--no-task` marks the copy as a test copy: `"test_copy": true` in its
  `config.json` (Windows: `setup.ps1 -Dest … -NoApp -NoTask`). A test copy's morning-refresh row is grey and does not
  tell you to download the installer, because the installer would update your everyday copy, not this one. Running
  the installer on that folder again without those flags removes the mark.
- `--no-launch` also skips starting it at the end.
- `--isolated`, above.

Start it again later with `cd ~/OpenLoops-test && python3 -m openloops.app`; delete the folder when you are done.
Windows: `powershell -ExecutionPolicy Bypass -File setup.ps1 -Dest $HOME\OpenLoops-test -Isolated -Port 8790 -Name Test`
(`-Isolated` implies `-NoApp -NoTask`; `-NoLaunch` as before; `$env:OPENLOOPS_ISOLATED = "1"` for the run-time
switch).

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
  `vault_path` folder setting still works. With both `standing_file` and `vault_path` blank in config.json, no
  to-do file is read at all (a `vault_path` you set is still honoured): set the path in your own config.json, never
  in `config.template.json`.
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
- **Update Slack** (shown whenever Slack is connected, during setup too) is a quick Slack-only pass: no email, about a
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
   Connectors) or the *Miro plugin* (`claude plugin install miro@claude-plugins-official`); then press **Connect Miro**
   on the checklist (fallback: *Open Claude (advanced)* → `/mcp` → **Miro** → Authenticate). The connection check
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
   `effort` in `config.json` (template: `sonnet` at `high`; Settings → Preferences → Your AI). Sonnet at high or Opus at medium
   both do the job (`xhigh` cost about 8p even for a one-line answer in testing, #50). The two small lookups, the
   connection check's Slack lookup and *Who's who*, always run at `low`, whatever `effort` says. Leave either blank and
   the jobs inherit whatever `claude` defaults to on that computer, which
   is usually the most expensive model available. Grok ignores both. Codex has its own pair, `codex_model` and
   `codex_effort` (template `gpt-5.6-sol` at `low`), so switching AI never hands Codex a Claude model name.
7. **Jobs never clobber your clicks.** A refresh can run for minutes; anything you add or snooze meanwhile is kept
   because every job re-reads `state.json` just before writing (`store.update_state`).
8. **Mac: keep Open Loops out of Documents, Desktop and Downloads.** macOS privacy protection stops a background
   job started by `launchd` from reading those folders, so a weekday refresh installed there fails every morning
   with `Operation not permitted` in `state/logs/launchd.err.log`, and nothing notices unless you press Refresh
   yourself. That is why the install lives in `~/Library/Application Support/OpenLoops` (Finder:
   *Go → Go to Folder…* and paste the path). If the morning refresh cannot start, the page says so in a red box with
   the fix, and *Copy all* in the Console includes the tail of that log. The same limit applies to anything the
   morning refresh reads: keep your own to-do file outside those three folders too, or the morning refresh cannot
   read it.

## 8. More than one account (work + personal)

One install of Open Loops reads one set of accounts: one AI sign-in, one Slack workspace, one Gmail inbox. This
section says what each AI allows if you want a second account for the same source, what that costs you, and what
works now. Checked on 23 September 2026 against Claude Code 2.1.280, Codex CLI 0.156.1 and Grok 1.0.30; the
numbers in brackets point at the sources at the end of this section. *Unverified* means the documentation
suggests it but nobody has tried it yet.

| AI | Signing in to the AI | Second Slack workspace | Second Gmail inbox | Cost | Status |
|---|---|---|---|---|---|
| **Claude Code** | One account per settings folder (`CLAUDE_CONFIG_DIR`) [1][2] | Probably a second settings folder (*unverified*, note a) | Probably a second claude.ai account (*unverified*, note b) | Slack: probably none. Gmail: probably a second Claude plan (*unverified*) | Needs Profiles |
| **Codex** | One account per `CODEX_HOME` with file-based storage [5][6] | Probably a second ChatGPT account (*unverified*, note c) | Probably a second ChatGPT account (*unverified*, note c) | A second ChatGPT account [7] | One ChatGPT account per install works ([Codex (ChatGPT)](#codex-chatgpt)); a second needs Profiles |
| **Grok** | One sign-in per `GROK_HOME` [9] | Probably a second Grok folder (*unverified*, note d) | A second install with its own Gmail sign-in (note e) | No extra subscription; usage comes from the same accounts | Gmail: works today on a Mac. Slack: needs Profiles |

Calendar is not in the table because Open Loops does not read calendars.

**a. Claude and Slack.** With the Slack plugin, each settings folder has its own plugins [1], so a second workspace
probably means a second folder with its own `claude mcp login`. That the Slack sign-in is kept per folder, and that
the same Claude plan can be signed in to both folders, are *unverified*. With the claude.ai Slack connector, the
connection belongs to the claude.ai account [3]; whether one connector can hold two workspaces is *unverified*.

**b. Claude and Gmail.** The claude.ai Gmail connector reads "the Google account you've connected" [4]. A second
inbox therefore probably needs a second claude.ai account, signed in from a second settings folder, and Claude Code
needs a paid plan on that account [2]; both are *unverified*.

**c. Codex.** In testing ([#18](https://github.com/OscarC178/Open-Loops/issues/18)) ChatGPT's Gmail and Slack
connectors followed the ChatGPT account, not the `CODEX_HOME` folder, so a second inbox or workspace probably needs a
second ChatGPT account in a second folder. OpenAI does not document this. Folders are kept apart only with
file-based sign-in storage; with `keyring` storage, separation is *unverified* [6]. Slack's own MCP server is not
documented as supported for Codex [8]. Codex is included with Free and paid ChatGPT plans [7]; whether a Free account
gets Gmail and Slack in Codex is *unverified*.

**d. Grok and Slack.** Grok keeps its Slack sign-in in `mcp_credentials.json` in its folder [9], so a second workspace
probably means a second folder reusing the same Grok sign-in (*unverified*).

**e. Grok and Gmail.** Separate installs keep their Gmail sign-ins separately (`state/google_oauth.json` in each).
Follow [Gmail with Grok](#gmail-with-grok-one-off-5-minutes) in each install and select a different inbox; the
same Google Cloud project can serve both if the second address is added as a test user [10].

### Examples

**Work Claude + personal Grok.** On a Mac this works today as two installs, one for each AI. Keep your normal
install on Claude, then add a second copy with `bash install.sh --dest ~/OpenLoops-personal --no-app --no-task
--port 8790 --name "<your name>"`, choose Grok in its ⚙ Settings, and follow
[Gmail with Grok](#gmail-with-grok-one-off-5-minutes). The installer registers one scheduled refresh for the
current user, so keep `--no-task` on the second install and press Refresh there yourself (see
[Testing a fresh install](#testing-a-fresh-install)); start it with
`cd ~/OpenLoops-personal && python3 -m openloops.app`. Windows is *unverified*: the Grok Gmail server is set up with
`python3`, which Windows may not have ([ROADMAP](ROADMAP.md)).

**Two Gmail accounts on Codex.** Open Loops with Codex reads the Gmail of the one ChatGPT account Codex is signed in
with ([Codex (ChatGPT)](#codex-chatgpt)); a second inbox probably needs a second ChatGPT account (note c) and Profiles.
For two inboxes today, use two Grok installs as above, each signed in with `python3 -m openloops.gmail_auth connect`
to a different inbox.

**Two Slack workspaces on Claude.** Open Loops cannot keep two Claude Slack workspaces apart yet. Connect the
workspace you need most, and use separate installs once Profiles is available. The app passes its own environment to
its jobs and, on Windows, to *Open Claude (advanced)*, so a copy started with `CLAUDE_CONFIG_DIR` set should use that
folder; on a Mac, *Open Claude (advanced)* opens Terminal, which does not get that setting. Neither is tested
(*unverified*).

### What Profiles will do

Profiles are planned to keep work and personal settings separate; use separate installs until Profiles is
available. The [ROADMAP design sketch](ROADMAP.md#profiles-work--personal--all) gives each profile its own
settings under `profiles/<name>/`. The proposal in [#26](https://github.com/OscarC178/Open-Loops/issues/26) adds
each profile's own AI folder (`CLAUDE_CONFIG_DIR`, `CODEX_HOME` or `GROK_HOME`) and Gmail token file, so that its
jobs, checklist and **Connect** buttons use that profile's accounts and switching profile does not sign the other
out. These are proposals, not built behaviour.

### Sources

1. Claude Code environment variables, `CLAUDE_CONFIG_DIR`: "Useful for running multiple accounts side by side" and
   "All settings, session history, and plugins are stored under this path" — https://code.claude.com/docs/en/env-vars
2. Claude Code authentication, credential management: with `CLAUDE_CONFIG_DIR` set, the macOS Keychain entry is
   keyed to that folder, "so a session with a different `CLAUDE_CONFIG_DIR` reads a different entry"; account types
   Pro/Max, Team/Enterprise, Console — https://code.claude.com/docs/en/authentication. `claude auth login --help`
   offers only `--claudeai`, `--console`, `--email`, `--sso` (no folder option).
3. Claude Code MCP, "Use MCP servers from claude.ai": connectors are fetched only for a claude.ai subscription
   login — https://code.claude.com/docs/en/mcp. `claude mcp login --help`: "Authenticate with an MCP server (HTTP,
   SSE, or claude.ai connector)".
4. Claude Help Centre, Google Workspace connectors: "Claude can only access the Gmail, Calendar, and Drive data for
   the Google account you've connected" — https://support.claude.com/en/articles/10166901-use-google-workspace-connectors
5. Codex environment variables, `CODEX_HOME`: "Sets the root for Codex state, including config, auth, logs,
   sessions, skills" — https://learn.chatgpt.com/codex/config-file/environment-variables
6. Codex authentication: "file stores credentials in auth.json under CODEX_HOME"; `keyring` uses the system store
   instead — https://learn.chatgpt.com/docs/auth. `codex login --help` has no folder option.
7. Codex pricing: "ChatGPT Work and Codex are included in your ChatGPT Free, Go, Plus, Pro, Business, Edu, or
   Enterprise plan" — https://learn.chatgpt.com/docs/pricing
8. Slack MCP server: clients listed are Claude.ai, Claude Code, Perplexity, Cursor; "We do not support SSE-based
   connections or Dynamic Client Registration at this time" — https://docs.slack.dev/ai/slack-mcp-server/
9. Grok 1.0.30 built-in docs (`strings ~/.grok/bin/grok`): "`GROK_HOME` | Override config directory (default:
   `~/.grok`)"; tokens in `~/.grok/auth.json` and "MCP OAuth tokens in `~/.grok/mcp_credentials.json`". `grok --help`
   itself does not mention `GROK_HOME`; `grok login --help` has no folder option. Open Loops sets it in
   `agent.grok_job_env()`.
10. Google Cloud, Manage App Audience: an app in Testing can list up to 100 test users —
    https://support.google.com/cloud/answer/15549945
