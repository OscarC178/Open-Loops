# Open Loops — getting started (5 minutes)

**What it is.** You ask people for things all day — on Slack, by email. Some of them never come back to you.
Open Loops keeps a list of everything you're waiting on, tells you the moment someone replies, and can write
the "just checking in…" message for you in your own words.

**What it needs.** Permission to look at the messages *you* send, and at replies to them. It runs on your
computer only. It never sends anything unless you turn that on.

---

## 1. Install

1. Download the installer for your computer: **[Windows](https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops-Setup.exe)** or **[Mac](https://github.com/OscarC178/Open-Loops/releases/latest/download/OpenLoops.dmg)**.
   (Or, if you were given an **OpenLoops** folder instead, open it and double-click **Open Loops.cmd** on
   Windows or **Open Loops.command** on Mac.)
2. Run it. **Windows** may warn that the publisher is unknown — click **More info**, then **Run anyway**.
   **Mac** may say it can't check the app — right-click it and choose **Open** (macOS 15 and later: close the
   warning, then **System Settings → Privacy & Security → Open Anyway**), just this once.
3. A window appears. It may ask for your first name. Let it finish (1–3 minutes). It installs two
   helper programs if you don't have them (Python and Claude), puts **Open Loops.app** (the orange loop)
   on your Desktop and in the Dock, and opens the app.

## 2. Connect your accounts (one-off)

The app opens with a checklist. It watches itself and ticks things off as you go — you don't need to press anything to update it.

| Tick | What to do |
|---|---|
| **Signed in to Claude** | Press **Open Claude**. A black window opens. If it shows a sign-in link, open it and sign in with your **work Google account**. |
| **Slack connected** *(optional)* | In that same black window, type `/mcp` and press Enter. Pick **Slack** → **Authenticate** → click **Allow** in the browser. |
| **Gmail connected** *(optional)* | Same again: `/mcp` → **claude.ai Gmail** → **Authenticate** → **Allow**. |
| **At least one source connected** | Ticks by itself once Slack or Gmail is connected — you only need the one(s) you actually use. |
| **Knows who you are on Slack** | Fills in by itself (only matters if you use Slack). |

When the required rows are ticked the checklist disappears and your list starts building (first fill takes about 2 minutes).

## 3. Every day

- Double-click **Open Loops** on the Desktop, or the same icon in the Dock. It refreshes itself every weekday morning at 09:15, so it's ready when you sit down.
- The page is a stack of sections, closed until you open them (it remembers which). **Add a note** stays on top.
- **Needs me** — people who've replied and are waiting on *you*. The row shows the count even when closed.
- **Waiting on them** — things you've asked for.
- Both lists show **one row per person**: their name, a small block per loop (green = recent, amber = a
  few days, red = getting old), and a line on what it's about. Click the person to open the cards. Each
  card has a **priority** (high / normal / low): the refresh guesses it, you can correct it, and your
  choice sticks. *sort* at the top of a list switches between oldest first and priority.
- **draft chase** — writes a friendly nudge in your voice and puts it in the conversation as an unsent draft. You read it, you press send.
- **done** when it's sorted. **snooze** to hide it for a bit. Both give you a few seconds to **Undo**. Notes,
  links and the auto-chase switch are under **more ▾** on each card.
- **Pinned** (top of the page) — pin the boards and docs you open every day. A Miro board opens right there.
- **Day log** — what moved today. **Write it up** drafts a short end-of-day note you can copy.
- **Closing the tab stops the app.** Next time, just double-click the icon again.

## 4. Make it sound like you

Open ⚙ **Settings** (tab at the top) → **Personal** → **Learn my tone**. It reads how you write to a few people and
copies your style — relaxed with mates, a lighter touch with the boss. Add people and whether they're
*senior / peer / junior* in the box above it.

## 5. Optional — let it send for you

Also in Settings, under **Chasing**. Two boxes: **Send to internal people** and **Send to external contacts**. Off, it drafts.
On, it sends without showing you first. There's also a **Timer** that chases anything quiet for a few days
automatically; every item has an *auto: on/off* switch so you can leave one alone.

---

### Things to know
- Nothing runs in the cloud. Your list is a file on your computer.
- It only ever looks at your own sent messages, and at the threads they're in.
- It uses your normal Claude subscription — no extra accounts, no API keys.
- WhatsApp isn't supported (WhatsApp doesn't allow it).
- If something looks wrong, ⚙ Settings → History → "Last job output" shows what happened, and the `state/logs` folder keeps a record.
- Reporting a problem? Open **Console** at the bottom of the page and press **Copy all**: that is everything
  someone needs to help you (what the page did, the last check, the last job output), with nothing from
  your messages in it.
- If the page says *I couldn't run the connection check*, nothing is wrong with your accounts: the checker
  itself didn't answer. It retries by itself; **Retry** runs it now.
