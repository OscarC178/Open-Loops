"""`python3 -m openloops.app --help` and unknown options: nothing is written, opened or started (#64).

    python3 tests/test_app_help.py    # fast; no Slack/Gmail/Claude. Throwaway folder and $HOME, spare port.

The app used to ignore any option it did not know, so `--help` started it and opened the browser (the same class of
bug as install.sh's #55). Every run here is in a throwaway folder holding only a copy of openloops/ (no config.json,
no state.json), with $HOME an empty folder, BROWSER a stub that logs the call, and `open`, `xdg-open` and `claude`
stubs first on PATH that log the call too, so any reach for the browser or a check fails the test. A sitecustomize.py
on PYTHONPATH wraps socket bind/listen and webbrowser.open in the app's own process and logs every call, so an
attempted bind is seen even if the socket is closed again before the process exits (a first run proves the logging
works). Checks:
  1. --help and -h: exit 0 at once, the usage line (under 80 columns) and a row for each option; the list names
     exactly the options app.py reads (from its source, so the two cannot drift), every line under 80 columns and no
     issue numbers in it. Nothing is written (no config.json, state.json, state/, nothing under $HOME), no stub is
     called, no socket is bound or listened on and no browser is asked for (the sitecustomize log stays empty).
  2. an unknown option (--bogus, a typo --no-browsr, a stray word, one after good options), --port with no number:
     exit 1, "unknown option: <arg>" (or the --port line) and the usage line on stderr; the same nothing-happened
     checks.
  3. in-process: the options the launchers and npm scripts pass (--port N, --port=N, --no-browser, --stop --now)
     are accepted by check_args() without exiting.
"""
import os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from _helpers import free_port, isolate_this_process  # noqa: E402
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
    # in the app's own process: every bind / listen / webbrowser.open is logged before it runs
    inst, calls = tmp / "instrument", tmp / "py-calls.log"
    inst.mkdir()
    (inst / "sitecustomize.py").write_text(f"""import socket, webbrowser
_LOG = {str(calls)!r}
def _note(what):
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(what + "\\n")
_bind, _listen, _open = socket.socket.bind, socket.socket.listen, webbrowser.open
def bind(self, *a):
    _note("bind " + repr(a))
    return _bind(self, *a)
def listen(self, *a):
    _note("listen")
    return _listen(self, *a)
def wopen(url, *a, **k):
    _note("webbrowser.open " + url)
    return True
socket.socket.bind, socket.socket.listen, webbrowser.open = bind, listen, wopen
""", encoding="utf-8")
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), BROWSER=str(stubs / "browser"),
               PATH=str(stubs) + os.pathsep + os.environ.get("PATH", ""), PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(inst) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""))
    env.pop("OPENLOOPS_PORT", None)
    port = free_port()
    # the instrument works: a process that binds and closes at once is still caught
    subprocess.run([sys.executable, "-c", "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); s.close()"],
                   env=env, check=True, timeout=20)
    check(calls.exists() and "bind" in calls.read_text(encoding="utf-8"), "the bind log catches a bind closed at once")
    calls.unlink()
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
        check(not calls.exists(), f"{what}: no socket bound or listened on, no browser asked for "
                                  f"({calls.read_text(encoding='utf-8').strip() if calls.exists() else ''})")

    # ------------------------------------------------------------ 1. --help / -h
    src = (REPO / "openloops" / "app.py").read_text(encoding="utf-8")
    read = set(re.findall(r'"(--[a-z-]+)"\s+(?:not\s+)?in\s+sys\.argv', src)) | {"--port"}   # _port_arg reads --port
    for flag in ("--help", "-h"):
        rc, out, err, took = run(flag, "--port", str(port))
        check("Open Loops ->" not in out, f"{flag}: the app never says it is listening")
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
                       (["--port=abc"], "--port needs a number"),
                       (["--port=\u00b2"], "--port needs a number"), (["--port", "\u0663"], "--port needs a number"),
                       (["--port", "80"], "--port needs a number"), (["--port", "70000"], "--port needs a number"), (["--port", "--no-browser"], "--port needs a number")):
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
    # an OPENLOOPS_PORT the app could never listen on counts as unset, as a bad config.json "port" does (review of #70);
    # before, 99999 went straight to the port scan, which found nothing and exited
    had = os.environ.pop("OPENLOOPS_PORT", None)
    try:
        base = app._port_arg()
        for env_port, want in (("8791", 8791), ("99999", base), ("abc", base), ("", base)):
            os.environ["OPENLOOPS_PORT"] = env_port
            check(app._port_arg() == want, f"OPENLOOPS_PORT={env_port!r} gives port {want} (unusable values count as unset)")
    finally:
        os.environ.pop("OPENLOOPS_PORT", None)
        if had is not None:
            os.environ["OPENLOOPS_PORT"] = had
    say("all passed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
