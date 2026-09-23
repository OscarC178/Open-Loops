"""install.sh --help and bad options: nothing is written, paused or started (#55).

    python3 tests/test_install_help.py    # macOS/Linux; no network, no real install. Throwaway $HOME.

install.sh used to drop any option it did not know, so `--help` ran a full default install against the live copy.
Every run here uses a throwaway $HOME (holding a pretend older ~/Documents/OpenLoops, so a default install would have
something to copy and a morning refresh to pause) and a PATH whose launchctl, curl, rsync, open, osascript and lsof
are stubs that only log that they were called. Checks:
  1. --help and -h: exit 0, print the usage line and the flag list; nothing written anywhere, no stub called.
  2. an unknown option (--bogus, a typo --isolatd, a stray word, one after a good option): exit 1, "unknown option:
     <arg>" and the usage line on stderr; nothing written, no stub called, "Paused" never printed.
  3. a value-taking option with no value (--dest last, --dest --isolated, --port last, --at ""): exit 1 the same way,
     and no folder named "--isolated" appears where it was run.
  4. setup.ps1 (static, PowerShell cannot run here): takes -Help, and it exits before the banner and any check.
"""
import os, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


# ---------- 4 first: the static setup.ps1 check runs on every platform ----------
say("4. setup.ps1 -Help (static: PowerShell cannot run here)")
ps = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
param_at = ps.index("param(")
help_at = ps.find("if ($Help)")
check("[switch]$Help" in ps[param_at:ps.index(")\n", param_at) + 1], "setup.ps1's param() takes -Help")
check(0 < help_at < ps.index("Open Loops - setup\"") and help_at < ps.index("$At -notmatch"),
      "-Help is handled before the banner and the first check")
check("exit 0" in ps[help_at:help_at + 600], "-Help exits 0")

if sys.platform == "win32":
    print("SKIP: install.sh is the Mac installer - setup.ps1 was checked statically above")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix="openloops-help-"))
try:
    home = tmp / "home"
    old = home / "Documents" / "OpenLoops"   # an older install a default run would copy from (and pause the job of)
    (old / "openloops").mkdir(parents=True)
    (old / "openloops" / "app.py").write_text("# pretend\n")
    (old / "config.json").write_text('{"owner_name": "Real"}\n')
    cwd = tmp / "cwd"   # where install.sh is run from: a relative --dest would land here
    cwd.mkdir()
    fakebin = tmp / "bin"
    fakebin.mkdir()
    calls = tmp / "calls.log"   # outside HOME, so the HOME snapshot below is not changed by it
    for tool in ("launchctl", "curl", "rsync", "open", "osascript", "lsof"):
        f = fakebin / tool
        f.write_text(f'#!/bin/bash\necho "{tool} $*" >> "{calls}"\nexit 0\n')
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def tree(root):
        """Every path under root with its size and mtime: any write, new file or new folder changes this."""
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for n in dirnames + filenames:
                p = os.path.join(dirpath, n)
                st = os.lstat(p)
                out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
        return out

    def run(*args):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENLOOPS_PORT", "OPENLOOPS_DEST", "OPENLOOPS_ISOLATED")}
        env.update(HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}", BROWSER="/usr/bin/true")
        return subprocess.run(["bash", str(REPO / "install.sh"), *args], cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)

    before_home, before_cwd = tree(home), tree(cwd)

    def untouched(what):
        check(tree(home) == before_home and tree(cwd) == before_cwd and not calls.exists(),
              f"{what}: nothing written under HOME or where it was run, no launchctl/curl/rsync/open called")

    say("1. --help and -h")
    for flag in ("--help", "-h", "--no-launch --help"):
        r = run(*flag.split())
        check(r.returncode == 0 and r.stdout.startswith("usage: bash install.sh") and "--isolated" in r.stdout
              and "--dest DIR" in r.stdout and "set -e" not in r.stdout, f"{flag}: exit 0, usage and the flag list")
        check("Checking Python" not in r.stdout and "Installing Open Loops" not in r.stdout, f"{flag}: no install started")
        untouched(flag)

    say("2. unknown options")
    for args in (["--bogus"], ["--isolatd"], ["stray"], ["--no-launch", "--Dest", "x"]):
        r = run(*args)
        bad = args[0] if args[0] != "--no-launch" else args[1]
        check(r.returncode == 1 and f"unknown option: {bad}" in r.stderr and "usage: bash install.sh" in r.stderr
              and "Paused" not in r.stdout + r.stderr and "Checking Python" not in r.stdout,
              f"{' '.join(args)}: exit 1, 'unknown option: {bad}' and the usage line, before anything else")
        untouched(" ".join(args))

    say("3. a value-taking option with no value")
    for args in (["--dest"], ["--dest", "--isolated"], ["--port"], ["--name", "Sam", "--at", ""]):
        r = run(*args)
        flag = [a for a in args if a in ("--dest", "--port", "--at")][0]
        check(r.returncode == 1 and f"{flag} needs a value" in r.stderr and "usage: bash install.sh" in r.stderr
              and "Checking Python" not in r.stdout, f"{' '.join(repr(a) if not a else a for a in args)}: exit 1, says {flag} needs a value")
        untouched(" ".join(args))
    check(not (cwd / "--isolated").exists(), "--dest --isolated did not make a folder called --isolated")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

say("PASS - install.sh --help and bad options exit before anything is written, paused or started")
