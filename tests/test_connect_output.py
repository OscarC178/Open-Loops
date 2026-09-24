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

say("all ok")
