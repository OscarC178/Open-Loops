"""setup.ps1 on a test copy (#27 part 2): the install says how to start the copy, and a copy that takes over the icons
or the weekday refresh says which copy they belonged to before.

    python tests\\test_setup_ps1.py    # Windows only (SKIP elsewhere); a real -Isolated install into a temp folder.

The isolated install is the real script (no stubs): -Isolated makes no icon and touches no scheduled task that runs
another folder, and -NoLaunch starts nothing, so the run leaves nothing outside the temp folder. The person's own
Desktop icon and the weekday task, if any, are read before and after to prove that. The take-over sentences cannot be
run for real here (that would make a Desktop icon and register the task); the function that decides whether a folder
is another copy is lifted from the script and run on its own, and the sentences are checked in the source.
Before #27 a test copy's install printed only a start command with no address, a second install silently took over
the everyday copy's icons and refresh, and INSTALL.md had no Windows stop command.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


if sys.platform != "win32" or not shutil.which("powershell"):
    say("SKIP: setup.ps1 only runs on Windows (tests/test_install_help.py reads it statically)")
    raise SystemExit(0)

SETUP = REPO / "setup.ps1"
src = SETUP.read_text(encoding="utf-8-sig")


def ps(*args, timeout=180, env=None):
    """Windows PowerShell 5.1 (the one every Windows has) on a script or a command; utf-8 both ways."""
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


def lnk_workdir(path):
    """The working folder a .lnk points at, or "" (none / not there)."""
    if not path.exists():
        return ""
    r = ps("-Command", f"(New-Object -ComObject WScript.Shell).CreateShortcut('{path}').WorkingDirectory")
    return r.stdout.strip()


def task_workdir():
    """The weekday task's working folder, or "" (no task)."""
    r = ps("-Command", "$t = Get-ScheduledTask -TaskName 'Claude Open Loops Refresh' -ErrorAction SilentlyContinue; "
                       "if ($t) { ($t.Actions | Select-Object -First 1).WorkingDirectory }")
    return r.stdout.strip()


# ---------------------------------------------------------------- static: the sentences
check('Warn "The Desktop and Start menu icons now open this copy; before this they opened $openedBefore."' in src,
      "taking over the icons says which copy they opened before (yellow, never a stop)")
check('Warn "The weekday refresh now runs this copy at $At; before this it ran $ranBefore."' in src,
      "taking over the weekday refresh says which copy it ran before")
check('Write-Host "  Start this copy with:"' in src and "the next free port if that one is taken" in src,
      "a copy with no icon is told how to start it and where it opens, as install.sh says")
check("Start this copy with: $manual" not in src, "the start command is printed once, at the end, not inside the icons line")

# ---------------------------------------------------------------- the deciding function, lifted from the script
m = re.search(r"^function OtherCopy\(\$path\) \{.*?^\}\r?$", src, re.M | re.S)
check(m, "setup.ps1 defines OtherCopy (is this working folder another copy that still exists?)")
with tempfile.TemporaryDirectory(prefix="openloops-setup-") as td:
    tmp = Path(td)
    dest = tmp / "copy"
    other = tmp / "other"
    other.mkdir()
    probe = tmp / "probe.ps1"
    probe.write_text(m.group(0) + f"""
$Dest = '{dest}'
Write-Output ("self=" + (OtherCopy '{dest}\\'))
Write-Output ("gone=" + (OtherCopy '{tmp / "gone"}'))
Write-Output ("empty=" + (OtherCopy ''))
Write-Output ("other=" + (OtherCopy '{other}\\'))
Write-Output ("OTHER=" + (OtherCopy '{str(other).upper()}'))
""", encoding="utf-8")
    r = ps("-File", str(probe))
    got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    check(r.returncode == 0 and got.get("self") == "" and got.get("gone") == "" and got.get("empty") == "",
          f"OtherCopy: this folder, a folder that is gone and no folder are not another copy ({got}, {r.stderr.strip()[:200]})")
    check(got.get("other") == str(other) and got.get("OTHER") == str(other).upper(),
          "OtherCopy: another folder that exists is named as it was found (case kept, trailing backslash dropped)")

    # ------------------------------------------------------------ the real thing: an isolated install
    desktop_lnk = Path(os.environ["USERPROFILE"]) / "Desktop" / "Open Loops.lnk"
    r0 = ps("-Command", "[Environment]::GetFolderPath('Desktop')")
    if r0.stdout.strip():
        desktop_lnk = Path(r0.stdout.strip()) / "Open Loops.lnk"
    lnk_before = (desktop_lnk.stat().st_mtime if desktop_lnk.exists() else None, lnk_workdir(desktop_lnk))
    task_before = task_workdir()
    say(f"before: Desktop icon -> {lnk_before[1] or '(none)'}; weekday task -> {task_before or '(none)'}")

    env = dict(os.environ)
    env.pop("OPENLOOPS_PORT", None)   # the -Port given must be what is printed, whatever the terminal had set
    r = ps("-File", str(SETUP), "-Dest", str(dest), "-Isolated", "-NoLaunch", "-Port", "8790", "-Name", "Test", env=env)
    out = r.stdout
    check(r.returncode == 0, f"setup.ps1 -Isolated -NoLaunch -Port 8790 exits 0 (got {r.returncode}: {r.stderr.strip()[:300]})")
    check("Skipped the Desktop and Start menu icons (test copy). How to start this copy is at the end." in out,
          "the icons line says the start command is at the end")
    check("Start this copy with:" in out and f'cd "{dest}"; python -m openloops.app --port 8790' in out,
          "the start command names this copy's folder and passes --port 8790, as the launch would")
    check("It opens at http://localhost:8790 (the -Port you gave; the next free port if that one is taken)" in out,
          "the address is the port given, with where it came from")
    check(out.index("Start this copy with:") < out.index("Done. You can close this window."),
          "the start block comes just before Done, where the eye lands")
    check("now open this copy" not in out and "now runs this copy" not in out,
          "an isolated copy takes nothing over, so no take-over line is printed")
    cfg = json.loads((dest / "config.json").read_text(encoding="utf-8-sig"))
    check(cfg.get("port") == 8790 and cfg.get("isolated") is True and cfg.get("test_copy") is True,
          "config.json holds port 8790, isolated and test_copy")
    check((dest / "openloops" / "app.py").exists() and not (dest / "tests").exists(),
          "the copy has the app and none of the tests")

    lnk_after = (desktop_lnk.stat().st_mtime if desktop_lnk.exists() else None, lnk_workdir(desktop_lnk))
    check(lnk_after == lnk_before, f"the person's Desktop icon is as it was ({lnk_after[1] or '(none)'})")
    check(task_workdir() == task_before, f"the weekday task is as it was ({task_before or '(none)'})")

    # ------------------------------------------------------------ INSTALL.md: the Windows stop command
    doc = (REPO / "INSTALL.md").read_text(encoding="utf-8")
    check("`python -m openloops.app --stop --port 8790`" in doc, "INSTALL.md gives the Windows stop command for a test copy")

say("PASS")
