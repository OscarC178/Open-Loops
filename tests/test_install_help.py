"""install.sh --help and bad options: nothing is written, paused or started (#55).

    python3 tests/test_install_help.py    # macOS/Linux; no network, no real install. Throwaway $HOME.

install.sh used to drop any option it did not know, so `--help` ran a full default install against the live copy.
Every run here uses a throwaway $HOME holding a pretend older ~/Documents/OpenLoops and its weekday job's plist, so a
default install would have something to copy and a morning refresh to pause. The harness stays safe even if the parser
regresses: PATH is only a folder of stubs plus /usr/bin and /bin (never the inherited PATH, so no Homebrew), where
launchctl, curl, rsync, open, osascript and lsof log the call and succeed, and brew, python3, pip3 and git log the
call and exit 99 - any reach into a real install fails loudly. Any stub call fails the test. Checks:
  1. --help and -h: exit 0, print the usage line and the flag list; nothing written anywhere, no stub called. The
     list is hand-written (#60): its table must name exactly the flags install.sh's case statement accepts (read from
     the script, so the two cannot drift), and every line fits an 80-column terminal with no issue numbers in it.
  2. an unknown option (--bogus, a typo --isolatd, a stray word, one after a good option): exit 1, "unknown option:
     <arg>" and the usage line on stderr; nothing written, no stub called, "Paused" never printed.
  3. a value-taking option with no value (--dest last, --dest --isolated, --port last, --at ""): exit 1 the same way,
     and no folder named "--isolated" appears where it was run.
  4. setup.ps1 (static, PowerShell cannot run here): takes -Help, and it exits before the banner and any check; its
     hand-written list has a row for every parameter in param() (#60), under 80 columns, no issue numbers.
  5. accepted: --name "" (it means "ask for the name"), values with spaces; refused: --dest "" (an empty
     --dest must never mean the copy you use), --, --at 25:00.
"""
import hashlib, os, plistlib, re, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def table_flags(text):
    """The options named in a help text's table (#60): rows are indented lines starting with "-", the first column
    ends at the first run of 2+ spaces, and may hold several spellings ("-h, --help"); "--at HH:MM" names --at."""
    out = []
    for ln in text.splitlines():
        if re.match(r"^ +-", ln):
            first = re.split(r" {2,}", ln.strip(), maxsplit=1)[0]
            out += [part.split()[0] for part in first.split(", ")]
    return out


def plain_lines(text, what):
    """Every line of a help text fits an 80-column terminal and carries no issue number such as (#36) (#60)."""
    long = [ln for ln in text.splitlines() if len(ln) >= 80]
    check(not long, f"{what}: every line under 80 columns ({long!r})")
    check(not re.search(r"#\d", text), f"{what}: no issue numbers in it")


# ---------- 4 first: the static setup.ps1 check runs on every platform ----------
say("4. setup.ps1 -Help (static: PowerShell cannot run here)")
ps = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
param_at = ps.index("\nparam(")   # the param() line itself, not the comment above it that names it
help_at = ps.find("if ($Help)")
check("[switch]$Help" in ps[param_at:ps.index(")\n", param_at) + 1], "setup.ps1's param() takes -Help")
check(0 < help_at < ps.index("Open Loops - setup\"") and help_at < ps.index("$At -notmatch"),
      "-Help is handled before the banner and the first check")
block_end = ps.index("\n}\n", help_at)   # the end of the if ($Help) { ... } block
check(0 < ps.find("exit 0", help_at) < block_end, "-Help exits 0")
ps_help = ps[ps.index("@'", help_at) + 2:ps.index("'@", help_at)].strip("\n")   # the here-string it prints
params = re.findall(r"\$(\w+)", ps[param_at:ps.index(")\n", param_at)])   # every parameter param() takes
check(ps_help.startswith("usage: setup.ps1") and sorted(table_flags(ps_help)) == sorted(f"-{n}" for n in params),
      f"setup.ps1 -Help lists exactly the parameters param() takes ({params})")
plain_lines(ps_help, "setup.ps1 -Help")

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
    (old / "scripts").mkdir()
    (old / "scripts" / "run-refresh.sh").write_text("#!/bin/bash\n")
    # the weekday job as an older install left it: migrate_install.py's unload_job() would pause this one
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.openloops.refresh.plist").write_bytes(plistlib.dumps(
        {"Label": "com.openloops.refresh", "ProgramArguments": ["/bin/bash", f"{old}/scripts/run-refresh.sh"]}))
    cwd = tmp / "cwd"   # where install.sh is run from: a relative --dest would land here
    cwd.mkdir()
    fakebin = tmp / "bin"
    fakebin.mkdir()
    calls = tmp / "calls.log"   # outside HOME, so the HOME snapshot below is not changed by it
    # quiet stubs answer as the real tool would; fail-on-call stubs stop anything that would install for real
    for tool, rc in [(t, 0) for t in ("launchctl", "curl", "rsync", "open", "osascript", "lsof")] + \
                    [(t, 99) for t in ("brew", "python3", "pip3", "git")]:
        f = fakebin / tool
        f.write_text(f'#!/bin/bash\necho "{tool} $*" >> "{calls}"\nexit {rc}\n')
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def tree(root):
        """root itself and every path under it: mode, size, mtime, and a hash of a file's bytes (a link's target).
        Any write, new or removed entry, permission change, or rewrite that keeps the size changes this."""
        def entry(p):
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                body = os.readlink(p)
            elif stat.S_ISREG(st.st_mode):
                body = hashlib.sha256(Path(p).read_bytes()).hexdigest()
            else:
                body = None
            return (st.st_mode, st.st_size, st.st_mtime_ns, body)
        out = {".": entry(root)}
        for dirpath, dirnames, filenames in os.walk(root):
            for n in dirnames + filenames:
                p = os.path.join(dirpath, n)
                out[os.path.relpath(p, root)] = entry(p)
        return out

    def run(*args):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENLOOPS_PORT", "OPENLOOPS_DEST", "OPENLOOPS_ISOLATED")}
        # the stubs, then only the system folders bash and its coreutils (sed, mkdir, ...) need: no inherited PATH
        env.update(HOME=str(home), PATH=f"{fakebin}:/usr/bin:/bin", BROWSER="/usr/bin/true")
        return subprocess.run(["/bin/bash", str(REPO / "install.sh"), *args], cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)

    # the whole temp folder: HOME, where it is run, and any --dest given as an absolute path in it ("a b" below)
    before = tree(tmp)

    def untouched(what):
        check(tree(tmp) == before and not calls.exists(),
              f"{what}: nothing written anywhere in the temp folder (HOME, where it was run, --dest), no stub called"
              + ("" if not calls.exists() else f" (called: {calls.read_text().strip()!r})"))

    say("1. --help and -h")
    # the flags the parser accepts, read from install.sh's own case statement: the patterns before ")" in the
    # `while [[ $# -gt 0 ]]` loop, "-h|--help" split into its spellings, the catch-all "*" left out
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    loop = sh[sh.index("while [[ $# -gt 0 ]]"):]
    loop = loop[:re.search(r"\n\s*esac\b", loop).start()]
    accepted = [f for pat in re.findall(r"^\s+([-\w|]+)\)", loop, re.M) for f in pat.split("|")]
    check(len(accepted) >= 10 and "-h" in accepted and "--isolated" in accepted,
          f"read the accepted flags from install.sh's case statement ({accepted})")
    for flag in ("--help", "-h", "--no-launch --help"):
        r = run(*flag.split())
        check(r.returncode == 0 and r.stdout.startswith("usage: bash install.sh") and "--isolated" in r.stdout
              and "--dest DIR" in r.stdout and "set -e" not in r.stdout, f"{flag}: exit 0, usage and the flag list")
        check("Checking Python" not in r.stdout and "Installing Open Loops" not in r.stdout, f"{flag}: no install started")
        check(sorted(table_flags(r.stdout)) == sorted(accepted),
              f"{flag}: the table lists every flag the parser accepts, and no other ({table_flags(r.stdout)})")
        plain_lines(r.stdout, flag)
        check("OPENLOOPS_DEST" in r.stdout and "OPENLOOPS_ISOLATED" in r.stdout, f"{flag}: the two environment settings")
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

    say("5. values the parser must accept, and ones it must still refuse")
    # --help after a value proves the value got through the parser (a refused value exits 1 before --help is read)
    for args in (["--name", "", "--help"], ["--name", "Mary Ann", "--dest", str(tmp / "a b"), "--help"]):
        r = run(*args)
        check(r.returncode == 0 and r.stdout.startswith("usage: bash install.sh"),
              f"{args!r}: accepted by the parser")
        untouched(repr(args))
    for args, said in ((["--dest", ""], "--dest needs a value"), (["--"], "unknown option: --"),
                       (["--at", "25:00"], "--at must be HH:MM")):
        r = run(*args)
        check(r.returncode == 1 and said in r.stderr and "Checking Python" not in r.stdout,
              f"{args!r}: exit 1, '{said}'")
        untouched(repr(args))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

say("PASS - install.sh --help and bad options exit before anything is written, paused or started")
