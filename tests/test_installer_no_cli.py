"""The installers never install an AI CLI themselves (#39); the app's Install button is the only route.

    python3 tests/test_installer_no_cli.py    # macOS/Linux; downloads nothing. Throwaway $HOME, spare port.

install.sh runs on a PATH with no claude, codex or grok on it: the system folders plus a folder of stubs. The stub
`curl` (and `wget`) fail loudly, and leave a mark, if they are ever asked for a vendor's installer (claude.ai,
chatgpt.com, x.ai), so a vendor script run by the installer, piped or not, is caught. Checks:
  1. With no AI anywhere: exit 0, the one plain sentence, no vendor URL fetched, nothing put in ~/.local/bin.
  2. With an AI already there (on PATH, or only in ~/.local/bin or ~/.grok/bin): no sentence, still exit 0.
  3. The app started from that install, with the same PATH and a temp HOME: the checklist's first row offers
     Install (connect "install") for the configured agent, Claude by default and Grok once config.json says grok.
  4. setup.ps1 (cannot run here): no Invoke-Expression / iex / irm, no Claude download, no winget Claude block,
     Python via winget kept, and the same sentence.
  5. Nothing tracked in the repo pipes a download into a shell (curl ... | bash / sh, irm ... | iex).
"""
import json, os, re, shutil, stat, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SENTENCE = "No AI is installed yet. Open Loops will offer to install one on its first screen."
VENDORS = re.compile(r"claude\.ai|chatgpt\.com|x\.ai|anthropic\.com|openai\.com")
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


def script(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------- 4 and 5: static, run everywhere (Windows too)
say("4. setup.ps1 installs no AI (static: PowerShell cannot run here)")
ps = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
code = "\n".join(l for l in ps.splitlines() if not l.lstrip().startswith("#"))  # the script, not its comments
check(not re.search(r"Invoke-Expression|\biex\b|\birm\b|Invoke-RestMethod", code, re.I), "no Invoke-Expression / iex / irm")
check("claude.ai/install" not in ps and not re.search(r"winget\s+install[^\n]*Anthropic", ps, re.I),
      "no Claude download and no winget Claude block")
check(re.search(r"winget\s+install\s+--id\s+Python\.Python", code) is not None, "Python is still installed with winget")
check(SENTENCE in ps and "exit 1" not in ps[ps.index("# ---------- 2. AI"):ps.index("# ---------- 3. Copy files")],
      "the same sentence when no AI is found, and that step never exits")

say("5. no download piped into a shell anywhere in the repo")
PIPE = re.compile(r"(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b|\|\s*(?:iex|Invoke-Expression)\b", re.I)
SKIP_DIRS = {".git", "node_modules", "dist", "state", ".worktrees"}
hits = []
for root, dirs, files in os.walk(REPO):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for f in files:
        p = Path(root) / f
        if p.resolve() == Path(__file__).resolve() or p.suffix.lower() in (".png", ".ico", ".icns", ".jpg", ".gif"):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits += [f"{p.relative_to(REPO)}:{n}" for n, line in enumerate(txt.splitlines(), 1) if PIPE.search(line)]
check(not hits, f"no curl|bash, curl|sh or irm|iex ({hits})")

if sys.platform == "win32":
    print("SKIP: install.sh is the Mac installer - setup.ps1 was checked statically above")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import start_app, stop  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="openloops-nocli-"))
stubs = tmp / "bin"
stubs.mkdir()
vendor_log = tmp / "vendor-called.txt"
script(stubs / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')  # this Python, never a folder with an AI CLI in it
for tool in ("curl", "wget"):
    # a vendor URL anywhere in the arguments: mark it, say so loudly, fail; anything else: "connection refused"
    script(stubs / tool, f"""#!/bin/bash
for a in "$@"; do
    if printf '%s' "$a" | grep -Eq '{VENDORS.pattern}'; then
        echo "{tool} $*" >> "{vendor_log}"
        echo "TEST STUB: the installer tried to fetch a vendor's AI installer: $a" >&2
        exit 99
    fi
done
exit 7
""")
PATH = os.pathsep.join([str(stubs), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
for cli in ("claude", "codex", "grok"):
    found = shutil.which(cli, path=PATH)
    if found:  # an AI in the system folders would make "no AI on PATH" untrue
        raise SystemExit(f"FAIL: {cli} is on the test PATH ({found}); cannot test an install with no AI")


def install(home, dest, path=PATH):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENLOOPS_")}
    env.update(HOME=str(home), USERPROFILE=str(home), PATH=path, BROWSER="true")
    return subprocess.run(["bash", str(REPO / "install.sh"), "--dest", str(dest), "--no-app", "--no-task", "--no-launch",
                           "--name", "Test"], env=env, capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)


def api(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


srv = None
try:
    say("1. install.sh with no AI anywhere")
    home = tmp / "home"
    home.mkdir()
    dest = tmp / "OpenLoops-nocli"
    r = install(home, dest)
    out = r.stdout + r.stderr
    check(r.returncode == 0, f"install.sh exits 0 ({out[-400:].strip() if r.returncode else 'ok'})")
    check(r.stdout.count(SENTENCE) == 1, "prints the one plain sentence, once")
    check(not vendor_log.exists(), f"no vendor installer fetched ({vendor_log.read_text() if vendor_log.exists() else ''})")
    check("Installing Claude" not in out and "Couldn't install" not in out, "no attempt to install Claude, no failure message")
    check(not (home / ".local" / "bin").exists() and not (home / ".grok").exists(), "nothing put in ~/.local/bin or ~/.grok")
    check((dest / "openloops" / "app.py").exists() and json.loads((dest / "config.json").read_text())["agent"] == "claude",
          "the app is installed, set to the default agent (claude)")

    say("2. an AI already there: no sentence")
    have = tmp / "have-bin"
    have.mkdir()
    script(have / "codex", "#!/bin/sh\nexit 0\n")
    r = install(tmp / "home", tmp / "dest-path", path=os.pathsep.join([str(have), PATH]))
    check(r.returncode == 0 and SENTENCE not in r.stdout, "codex on PATH: no sentence")
    for sub in (".local/bin/claude", ".grok/bin/grok"):  # where the vendors' installers put them, often not on PATH
        h = tmp / ("home-" + sub.split("/")[0].strip("."))
        (h / sub).parent.mkdir(parents=True)
        script(h / sub, "#!/bin/sh\nexit 0\n")
        r = install(h, tmp / ("dest-" + sub.split("/")[-1]))
        check(r.returncode == 0 and SENTENCE not in r.stdout, f"only in ~/{sub}: found, no sentence")
    check(not vendor_log.exists(), "still no vendor installer fetched")

    say("3. first open of that install: the checklist offers Install for the configured agent")
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENLOOPS_")}
    env.update(HOME=str(home), USERPROFILE=str(home), PATH=PATH, BROWSER="true")
    srv, port = start_app(dest, env)
    for ag, label, url in (("claude", "Claude", "https://claude.ai/install.sh"), ("grok", "Grok", "https://x.ai/cli/install.sh")):
        cfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
        cfg["agent"] = ag
        (dest / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        code, doc = api(port, "/api/doctor", {"force": True})
        row = (doc.get("steps") or [{}])[0]
        check(code == 200 and doc.get("agent") == ag and row.get("title") == f"{label} is installed" and row.get("ok") is False,
              f"{ag}: the first row is '{label} is installed', not ticked ({row.get('fix') or doc.get('error')})")
        check(row.get("connect") == "install" and row.get("agent") == ag and url in (row.get("command") or "")
              and row.get("fix", "").startswith(f"Open Loops couldn't find {label} on this computer. Press Install {label}"),
              f"{ag}: it offers Install {label}, showing the download of {url} to a file")
    check(not vendor_log.exists(), "opening the app fetched nothing either (nothing downloads until Install is pressed)")
    say("PASS - the installer installs no AI and never fails for want of one; the app's first screen offers Install")
finally:
    stop(srv)
    shutil.rmtree(tmp, ignore_errors=True)
