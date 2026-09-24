"""`python3 -m openloops.app --help` and unknown options: nothing is written, opened or started (#64).

    python3 tests/test_app_help.py    # fast; no Slack/Gmail/Claude. Throwaway folder and $HOME, spare port.

The app used to ignore any option it did not know, so `--help` started it and opened the browser (the same class of
bug as install.sh's #55). Every run here is in a throwaway folder holding only a copy of openloops/ (no config.json,
no state.json), with $HOME an empty folder, BROWSER a stub that logs the call, and `open`, `xdg-open` and `claude`
stubs first on PATH that log the call too, so any reach for the browser or a check fails the test. Checks:
  1. --help and -h: exit 0 at once, the usage line (under 80 columns) and a row for each option; the list names
     exactly the options app.py reads (from its source, so the two cannot drift), every line under 80 columns and no
     issue numbers in it. Nothing is written (no config.json, state.json, state/, nothing under $HOME), no stub is
     called, nothing listens on the port given.
  2. an unknown option (--bogus, a typo --no-browsr, a stray word, one after good options), --port with no number:
     exit 1, "unknown option: <arg>" (or the --port line) and the usage line on stderr; the same nothing-happened
     checks.
  3. in-process: the options the launchers and npm scripts pass (--port N, --port=N, --no-browser, --stop --now)
     are accepted by check_args() without exiting.
"""
import os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from _helpers import free_port, isolate_this_process, listening  # noqa: E402
isolate_this_process("openloops-apphelp-parent-")   # importing app (part 3) writes its files into a throwaway copy
t0 = time.time()
WIN = sys.platform == "win32"


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def tree(root):
    """Every file and folder under root, bytecode caches left out (Python writes those for any import)."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if "__pycache__" not in p.parts)


tmp = Path(tempfile.mkdtemp(prefix="openloops-apphelp-"))
try:
    shutil.copytree(REPO / "openloops", tmp / "copy" / "openloops", ignore=shutil.ignore_patterns("__pycache__"))
    home, stubs, log = tmp / "home", tmp / "bin", tmp / "stub-calls.log"
    home.mkdir()
    stubs.mkdir()
    for name in ("open", "xdg-open", "claude", "codex", "browser"):   # each logs that it was called, and succeeds
        f = stubs / name
        f.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n', encoding="utf-8")
        f.chmod(0o755)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), BROWSER=str(stubs / "browser"),
               PATH=str(stubs) + os.pathsep + os.environ.get("PATH", ""), PYTHONDONTWRITEBYTECODE="1")
    env.pop("OPENLOOPS_PORT", None)
    port = free_port()
    before = tree(tmp)

    def run(*args):
        """The app with these options, in the throwaway copy -> (exit code, stdout, stderr, seconds)."""
        t = time.time()
        r = subprocess.run([sys.executable, "-m", "openloops.app", *args], cwd=tmp / "copy", env=env,
                           capture_output=True, text=True, timeout=20)
        return r.returncode, r.stdout, r.stderr, time.time() - t

    def nothing_happened(what):
        check(tree(tmp) == before, f"{what}: nothing written (no config.json, state.json, state/, nothing in $HOME)")
        check(not log.exists(), f"{what}: no browser opened and nothing checked (no stub called)")
        check(not listening(port), f"{what}: nothing listens on the port given")

    # ------------------------------------------------------------ 1. --help / -h
    src = (REPO / "openloops" / "app.py").read_text(encoding="utf-8")
    read = set(re.findall(r'"(--[a-z-]+)"\s+(?:not\s+)?in\s+sys\.argv', src)) | {"--port"}   # _port_arg reads --port
    for flag in ("--help", "-h"):
        rc, out, err, took = run(flag, "--port", str(port))
        check(rc == 0 and took < 10, f"{flag}: exits 0 at once ({rc}, {took:.1f} s, {err.strip()[-200:]})")
        lines = out.splitlines()
        check(lines and lines[0].startswith("usage: ") and "-m openloops.app" in lines[0] and len(lines[0]) < 80,
              f"{flag}: the first line is the usage line, under 80 columns ({lines[:1]})")
        rows = [ln for ln in lines if re.match(r"^ +-", ln)]
        named = {part.split()[0] for ln in rows for part in re.split(r" {2,}", ln.strip(), maxsplit=1)[0].split(", ")}
        check(named == read | {"-h", "--help"}, f"{flag}: one row per option app.py reads, no more, no fewer ({sorted(named)} vs {sorted(read)})")
        check(all(len(ln) < 80 and "#" not in ln for ln in lines), f"{flag}: every line under 80 columns, no issue numbers")
        check(not err.strip(), f"{flag}: nothing on stderr")
        nothing_happened(flag)

    # ------------------------------------------------------------ 2. unknown options, --port without a number
    for args, said in ((["--bogus"], "unknown option: --bogus"), (["--no-browsr"], "unknown option: --no-browsr"),
                       (["stray"], "unknown option: stray"),
                       (["--port", str(port), "--no-browser", "--bogus"], "unknown option: --bogus"),
                       (["--port"], "--port needs a number"), (["--port", "abc"], "--port needs a number"),
                       (["--port=abc"], "--port needs a number"), (["--port", "--no-browser"], "--port needs a number")):
        what = " ".join(args)
        rc, out, err, took = run(*args)
        check(rc == 1 and took < 10, f"{what}: exits 1 at once ({rc}, {took:.1f} s)")
        check(said in err and "usage: " in err and "-m openloops.app" in err and not out.strip(),
              f"{what}: says '{said}' and the usage line, on stderr ({err.strip()!r})")
        nothing_happened(what)

    # ------------------------------------------------------------ 3. what the launchers pass is still accepted
    from openloops import app   # noqa: E402  (the throwaway copy isolate_this_process() made)
    for args in (["--port", "8766"], ["--port=8790"], ["--no-browser"], ["--stop", "--now", "--port", "8766"], []):
        try:
            app.check_args(args)
            ok = True
        except SystemExit:
            ok = False
        check(ok, f"accepted without exiting: {' '.join(args) or '(no options)'}")
    say("all passed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
