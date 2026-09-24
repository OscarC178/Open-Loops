"""A sign-in step's output as the app reads it (review of #70): app.SigninOutput and app._read_on, on every platform.

    python3 tests/test_connect_output.py    # fast; no Slack/Gmail/Claude, no CLI, no browser. In-process, temp files.

The Windows runner reads the hidden console's output from a file, the pty runner from a terminal; both hand each new
piece to the same SigninOutput, which keeps the log's text (every link without its query) and finds the sign-in
link. These checks feed it by hand, so they run on the Mac and Linux too, where the Windows runner itself cannot.
Before the review the Windows runner read the file's last 64 KB on each poll: a link followed by more than that
before the next poll was never found, and its query reached the log with the https:// cut off.
"""
import os, sys, tempfile, time
from pathlib import Path

from _helpers import isolate_this_process  # noqa: E402
isolate_this_process("openloops-connect-output-")  # importing app writes config/state beside it: a throwaway copy
from openloops import app  # noqa: E402

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


LINK = "https://example.invalid/authorize?state=SECRET&code_challenge=abc"
NOTE = "https://example.invalid/authorize?(rest of the link not saved)"

# ---------------------------------------------------------------- the boundary the review reproduced
say("1. a link followed by 63,975 bytes of noise between two polls of the output file")
with tempfile.TemporaryDirectory(prefix="openloops-out-") as td:
    path = Path(td) / "connect-miro.out"
    o, seen = app.SigninOutput(), []

    def saw(chunk):  # what _connect_one's handler does with each piece, minus the log file and the browser
        seen.append(len(chunk))
        o.feed(chunk)

    path.write_bytes(b"Starting authentication...\n")
    off = app._read_on(path, 0, saw)              # poll 1
    with open(path, "ab") as f:                    # written between poll 1 and poll 2
        f.write(b"https://example.invalid/authorize?state=SECRET\n" + b"x" * 63975)
    off = app._read_on(path, off, saw)             # poll 2
    check(off == path.stat().st_size and sum(seen) == off, "every byte of the file was handed over, once")
    check(o.url == "https://example.invalid/authorize?state=SECRET", f"the link is still found ({o.url!r})")
    check("SECRET" not in o.text() and "state=" not in o.text(), "its query is not in the log's text")
    check(NOTE in o.text(), "the log keeps the address with the note in place of the query")
    check(app._read_on(Path(td) / "not-there-yet.out", 7, saw) == 7, "a file not created yet is read later from the same place")

# ---------------------------------------------------------------- pieces split anywhere
say("2. a link, an escape code and a UTF-8 character split between two reads")
raw = ("Visit this URL to authorize:\n  \x1b]8;;" + LINK + "\x1b\\\x1b[94m" + LINK + "\x1b[39m\x1b]8;;\x1b\\\n"
       "Waiting for authorisation…\n").encode("utf-8")
for cut in range(1, len(raw)):  # every possible split point
    o = app.SigninOutput()
    o.feed(raw[:cut])
    mid = o.text()
    o.feed(raw[cut:])
    if "SECRET" in mid or "state=" in mid or "SECRET" in o.text() or "\x1b" in o.text() or o.url != LINK \
            or not o.text().endswith("Waiting for authorisation…\n"):
        raise SystemExit(f"FAIL: split at byte {cut}: url={o.url!r} mid={mid!r} text={o.text()!r}")
check(True, f"at every one of {len(raw) - 1} split points: link found whole, no query or escape code in the text, the … intact")
o = app.SigninOutput()
o.feed(b"open https://example.invalid/authorize?state=SEC")
check(o.url == "" and "SEC" not in o.text(), "a link still arriving is not taken yet, and its query so far is not shown")
o.feed(b"RET and press Enter")
check(o.url == "https://example.invalid/authorize?state=SECRET" and "SECRET" not in o.text(),
      "...a space after it completes it before any newline")

# ---------------------------------------------------------------- bounds
say("3. the kept text stays bounded, and a line that never ends is never written without its start")
o = app.SigninOutput(limit=1000)
for i in range(500):
    o.feed(f"line {i}\n".encode())
check(len(o.text()) <= 1000 and o.text().endswith("line 499\n") and o.text().startswith("line "),
      f"only the newest lines are kept, cut at a line break ({len(o.text())} characters)")
o = app.SigninOutput(limit=1000)
o.feed(b"https://example.invalid/authorize?state=" + b"S" * 3000)   # a link longer than the limit, no space
o.feed(b"TAIL-OF-QUERY\nnext line\n")
check("S" * 10 not in o.text() and "TAIL-OF-QUERY" not in o.text() and o.text().endswith("next line\n"),
      "an over-long link with no space is dropped up to its newline, none of its query written")
o = app.SigninOutput(limit=1000)
o.feed(b"word " * 400 + b"https://example.invalid/authorize?state=SECRET more\n")
check(o.url == "https://example.invalid/authorize?state=SECRET" and "SECRET" not in o.text(),
      "an over-long line with spaces is cut between words, so the link on it is still found whole and redacted")
o = app.SigninOutput(limit=1000)   # CodeRabbit on #70: the same line still arriving, so the cut-at-space branch runs
for i in range(0, 2000, 300):     # 2 KB of words in pieces, no newline anywhere
    o.feed((b"word " * 400)[i:i + 300])
check(0 < len(o.carry) <= 1000 and o.kept.startswith("word ") and o.kept.endswith(" ") and len(o.text()) <= 2000,
      f"an over-long line still arriving is cut at a space: part kept, the rest carried ({len(o.kept)} kept, {len(o.carry)} carried)")
o.feed(b"https://example.invalid/authorize?state=SECRET more")
check(o.url == "https://example.invalid/authorize?state=SECRET" and "SECRET" not in o.text() and len(o.carry) <= 1000,
      "...a link arriving after the cut is found whole once a space follows it, and never written with its query")
for _ in range(20):
    o.feed(b"word " * 100)
check(len(o.kept) <= 1000 and len(o.carry) <= 1000 and "SECRET" not in o.text(),
      f"...and however long the line runs, what is kept stays bounded ({len(o.kept)} + {len(o.carry)})")

# ---------------------------------------------------------------- Stop before the browser opens
say("4. a Stop that lands before the link is read never opens the browser (both runners share this handler)")
opened = []
app.webbrowser.open = lambda url, *a, **k: opened.append(url) or True   # no browser in this test, ever
ARGV = ["claude", "mcp", "login", "plugin:miro:miro", "--no-browser"]
with tempfile.TemporaryDirectory(prefix="openloops-stop-") as td:
    log = Path(td) / "connect-miro.log"
    printed = b"Visit this URL to authorize:\n  " + LINK.encode() + b"\n"

    me = {"running": True, "url": "", "stopped": True}   # Stop pressed before the first output poll
    app._output_handler(me, ARGV, log, "$ claude mcp login\n")(printed)
    check(not opened and not me["url"], "Stop before the first read: the link printed meanwhile opens nothing, and no fallback link")
    check("SECRET" not in log.read_text(encoding="utf-8") and NOTE in log.read_text(encoding="utf-8"),
          "...the log still shows what the CLI printed, the link redacted")

    me = {"running": True, "url": ""}
    saw = app._output_handler(me, ARGV, log, "")
    saw(printed[:-1])                  # the link printed, not yet known to be whole (no whitespace after it)
    me["stopped"] = True               # Stop lands before the next read
    saw(b"\nWaiting for authorization...\n")
    check(not opened and not me["url"], "Stop after the link was printed but before the next read: no browser opens")

    me = {"running": True, "url": ""}   # second review: Stop lands after the link was taken, before the browser call
    check(app._before_open is None, "the hook is None in the app, so it does nothing there")
    app._before_open = lambda run: run.update(stopped=True)
    try:
        app._output_handler(me, ARGV, log, "")(printed)
    finally:
        app._before_open = None
    check(not opened and not me["url"] and not me.get("opening"),
          "Stop between taking the link and opening it: the last check under the lock sees it, no browser opens")

    me = {"running": True, "url": ""}
    saw = app._output_handler(me, ARGV, log, "")
    saw(printed)
    check(me.get("opening") is False and me.get("link_opened") is True,
          "a dispatch that went ahead is cleared once the browser call returned, and recorded as link_opened")
    saw(b"again " + LINK.encode() + b"\n")
    check(opened == [LINK] and me["url"] == LINK, "a run nobody stopped: the link is opened, once")

    me = {"running": True, "url": ""}   # third review: a browser call that raises still clears "opening"
    real_open, app.webbrowser.open = app.webbrowser.open, lambda url, *a, **k: (_ for _ in ()).throw(OSError("no browser"))
    try:
        app._output_handler(me, ARGV, log, "")(printed)
        raise SystemExit("FAIL: the browser's error was swallowed")
    except OSError:
        pass
    finally:
        app.webbrowser.open = real_open
    check(me.get("opening") is False and not me.get("link_opened"), "a browser call that raised: opening cleared, nothing recorded as opened")

    # a Stop that lands while the browser call is under way waits for it (bounded) before it answers
    import threading  # noqa: E402
    app.connects["miro"] = me = {"running": True, "url": "", "run_id": "R1"}
    seen = {}

    def slow_open(url, *a, **k):
        t = threading.Thread(target=lambda: seen.update(stop=app.stop_connect("miro", "R1"), at_reply=dict(me)))
        t.start()
        for _ in range(100):   # until the Stop has marked the run (it then waits for "opening" to clear)
            if me.get("stopped"):
                break
            time.sleep(0.01)
        seen["t"] = t
        opened.append(url)
        return True
    real_open, app.webbrowser.open = app.webbrowser.open, slow_open
    try:
        app._output_handler(me, ARGV, log, "")(printed)
    finally:
        app.webbrowser.open = real_open
    seen["t"].join(5)
    check(seen["stop"][0] == "stopped" and seen["at_reply"].get("opening") is False and seen["at_reply"].get("link_opened") is True,
          "Stop during the browser call: it waits for the call to return, then sees the tab recorded as opened")
    check(seen["stop"][2] is True, "...and its reply says a tab had opened (tab_opened), so the toast can say so")

    app.connects["miro"] = me = {"running": True, "url": "", "run_id": "R2"}   # a Stop before the last check
    n = len(opened)
    app._before_open = lambda run: seen.update(early=app.stop_connect("miro", "R2"))
    try:
        app._output_handler(me, ARGV, log, "")(printed)
    finally:
        app._before_open = None
    check(seen["early"][0] == "stopped" and seen["early"][2] is False and len(opened) == n,
          "Stop before the last check: tab_opened false, and no tab opened")
    app.connects.pop("miro", None)
    opened.clear()
    opened.clear()
    me = {"running": True, "url": ""}
    app._output_handler(me, ["claude", "auth", "login"], log, "")(printed)
    check(not opened and me["url"] == LINK, "a CLI not told --no-browser opens its own: the link is only kept for the page")
    opened.clear()
    app.quit_requested = True
    me = {"running": True, "url": ""}
    app._output_handler(me, ARGV, log, "")(printed)
    app.quit_requested = False
    check(not opened and not me["url"], "Quit pressed: no browser opens for a link read afterwards")

# ---------------------------------------------------------------- the raw output file (Windows keeps one per run)
say("5. the raw output file: made private by the app, removed on every way out, a failed removal said in plain words")
from openloops import messages  # noqa: E402
with tempfile.TemporaryDirectory(prefix="openloops-raw-") as td:
    out = Path(td) / "connect-miro.out"
    out.write_text("left by a forced end: https://example.invalid/authorize?state=OLD\n", encoding="utf-8")
    fd = app._private_file(out)
    os.write(fd, b"new run\n")
    os.close(fd)
    check(out.read_bytes() == b"new run\n", "a file left by an earlier run is removed before the next run of the step writes")
    if os.name == "posix":
        check((out.stat().st_mode & 0o777) == 0o600, f"only this user may read it ({oct(out.stat().st_mode & 0o777)})")
    app.connect_outs["miro"] = out
    check(app._drop_out("miro", out) and not out.exists() and "miro" not in app.connect_outs,
          "removed at the end, and no longer listed for Quit")
    check(app._drop_out("miro", out), "removing a file already gone is fine (the worker and Quit may both try)")

    out.write_text("x", encoding="utf-8")
    app.connect_outs["gmail"] = out
    app.stop_connects()   # Quit: the worker is a daemon thread and dies with the app, so Quit removes the file itself
    check(not out.exists() and not app.connect_outs, "Quit (stop_connects) removes a running step's raw output file too")

    stuck = Path(td) / "connect-slack.out"    # a directory with something in it: unlink() fails however often it tries
    stuck.mkdir()
    (stuck / "held").write_text("x", encoding="utf-8")
    log = app.connect_log("slack")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("$ claude mcp login\n", encoding="utf-8")
    check(not app._drop_out("slack", stuck), "a file that cannot be removed is reported, not ignored")
    check(log.read_text(encoding="utf-8").strip().splitlines()[-1] == messages.say("signin_file_left"),
          "...in one plain-words line from messages.py, at the end of the step's log (the Console shows it)")

# ---------------------------------------------------------------- making it private must work before anything is written
say("5b. Windows' icacls must succeed, or the sign-in does not start (a fake icacls, so this runs everywhere)")
with tempfile.TemporaryDirectory(prefix="openloops-acl-") as td:
    fake = Path(td) / "fake_icacls.py"
    fake.write_text("import sys, time\nmode = open(sys.argv[1]).read().strip()\n"
                    "if mode == 'hang': time.sleep(30)\nsys.exit(0 if mode == 'ok' else 5)\n", encoding="utf-8")
    mode = Path(td) / "mode.txt"
    saved = (app.ICACLS, app.ICACLS_TIMEOUT_S, os.environ.get("USERNAME"))
    app.ICACLS, app.ICACLS_TIMEOUT_S = [sys.executable, str(fake), str(mode)], 2
    os.environ["USERNAME"] = "tester"
    out = Path(td) / "connect-miro.out"
    try:
        mode.write_text("ok")
        os.close(app._private_file(out, restrict=True))
        check(out.exists(), "icacls succeeds: the file is made and handed over")
        for m, what in (("fail", "icacls refuses (non-zero exit)"), ("hang", "icacls does not answer in time")):
            mode.write_text(m)
            try:
                app._private_file(out, restrict=True)
                raise SystemExit(f"FAIL: {what}: a file was handed over anyway")
            except app.PrivateFileError as e:
                check(str(e) == messages.say("signin_private_failed") and not out.exists(),
                      f"{what}: no file to write to, and the reason in plain words from messages.py")
        mode.write_text("ok")
        del os.environ["USERNAME"]
        try:
            app._private_file(out, restrict=True)
            raise SystemExit("FAIL: no user name: a file was handed over anyway")
        except app.PrivateFileError:
            check(not out.exists(), "no user name to grant it to: refused the same way")
    finally:
        app.ICACLS, app.ICACLS_TIMEOUT_S = saved[0], saved[1]
        if saved[2] is not None:
            os.environ["USERNAME"] = saved[2]
        else:
            os.environ.pop("USERNAME", None)

# ---------------------------------------------------------------- the refusal reaches the row
say("5c. a sign-in refused for want of a private file says so on its row (reason private_file)")
from openloops import agent  # noqa: E402
saved = (app._connect_one, agent.login_cmd, agent.connect_url, agent.name)
agent.name, agent.connect_url = (lambda: "claude"), (lambda step, who=None: None)
agent.login_cmd = lambda step, who=None: [["claude", "mcp", "login", "plugin:miro:miro", "--no-browser"]]
app._connect_one = lambda step, argv, log, deadline: (_ for _ in ()).throw(app.PrivateFileError(messages.say("signin_private_failed")))
try:
    ok, why = app.run_connect("miro")
    check(ok, f"the run starts ({why})")
    for _ in range(100):
        if not app.connects["miro"]["running"]:
            break
        time.sleep(0.05)
    st = app.connect_status("miro")
    check(st["running"] is False and st["rc"] != 0 and st.get("reason") == "private_file",
          f"its status carries reason private_file for the page ({st.get('reason')!r}, rc {st['rc']})")
    check(st["last"] == messages.say("signin_private_failed"), "...and the log's last line is the plain sentence")
finally:
    app._connect_one, agent.login_cmd, agent.connect_url, agent.name = saved

# ---------------------------------------------------------------- output that is not UTF-8
say("6. sign-in output that is not UTF-8 (a cp1252 console) still reads, and its link survives")
o = app.SigninOutput()
o.feed(b"Couldn\x92t authenticate: Jos\xe9\n")            # cp1252 bytes, invalid as UTF-8
o.feed(b"Visit \xff\xfe " + LINK.encode() + b" \x81\n")   # stray bytes either side of the link
text = o.text()
check(o.url == LINK, f"the link is found whole between the stray bytes ({o.url!r})")
check("Couldn\ufffdt authenticate: Jos\ufffd\n" in text and NOTE in text and "SECRET" not in text,
      "the rest reads with a replacement character where a byte is not UTF-8, the link still redacted")

say("all ok")
