"""Capture this copy's page headless, for the console's `screenshot` (#76): the one thing the console could not show.

    python3 -m openloops screenshot [--width N] [--out path]      -> prints the PNG's path

Chrome (or Chromium) is found where tests/test_messages.py always looked: CHROME_BIN, PATH, the usual application
folders, and Chrome for Testing as agent-browser installs it. Node 22+ drives it over the DevTools protocol
(headless_chrome.js); nothing is installed. Neither being there is a one-line refusal, not a traceback.
"""
import json, os, shutil, subprocess, sys
from pathlib import Path

from .paths import ROOT

JS = Path(__file__).resolve().parent / "headless_chrome.js"
# wait for the page's first state load (index.html sets S from /api/state), then a moment for the paint; 15 s cap
WAIT_LOADED = ("new Promise(r=>{const t0=Date.now();const f=()=>((typeof S!=='undefined'&&S)||Date.now()-t0>15000)"
               "?setTimeout(()=>r(!!(typeof S!=='undefined'&&S)),400):setTimeout(f,100);f()})")


def find_chrome(home=None):
    """A runnable Chrome / Chromium, or "". `home`: where to look for Chrome for Testing (default this user's home;
    a test whose HOME is a temp folder passes the real one)."""
    home = Path(home) if home else Path.home()
    names = [os.environ.get("CHROME_BIN") or ""]
    names += [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")]
    names += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if pf:  # Windows: Chrome's usual folders (per-machine and per-user)
            names.append(str(Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    browsers = home / ".agent-browser" / "browsers"  # Chrome for Testing, as agent-browser installs it
    try:
        names += [str(x) for x in sorted(browsers.glob("**/Google Chrome for Testing")) + sorted(browsers.glob("**/chrome.exe"))]
    except OSError:
        pass
    return next((n for n in names if n and Path(n).is_file() and os.access(n, os.X_OK)), "")


def missing():
    """Why a capture cannot run here, in one line, or "" when Chrome and Node are both there."""
    if not find_chrome():
        return "no Chrome or Chromium found (set CHROME_BIN to the browser's path)"
    if not shutil.which("node"):
        return "Node is needed to drive Chrome (headless_chrome.js); node was not found on PATH"
    return ""


def run_js(chrome, url, width, expr, png=None, timeout=120, env=None):
    """headless_chrome.js on `url` -> the JSON it printed (a dict, with "error" when it failed)."""
    args = [shutil.which("node"), str(JS), chrome, url, str(width), expr] + ([str(png)] if png else [])
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"error": f"Chrome gave no answer within {timeout} s"}
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": ((r.stdout + r.stderr).strip() or f"exit {r.returncode}")[-400:]}
    return out if isinstance(out, dict) else {"value": out}


def capture(url, out, width=1280):
    """Capture `url` to PNG file `out` at `width` px -> (ok, said): said is the result line or the reason."""
    why = missing()
    if why:
        return False, why
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res = run_js(find_chrome(), url, width, WAIT_LOADED, out)
    if res.get("error") or not out.is_file():
        return False, f"could not capture {url}: {res.get('error') or 'no file was written'}"
    note = "" if res.get("waited") else " (the page had not loaded its state within 15 s; captured as it was)"
    return True, f"{out}  ({res.get('width')}x{res.get('height')} px){note}"


def default_out():
    from datetime import datetime
    return ROOT / "state" / "logs" / f"page-{datetime.now():%Y-%m-%d_%H%M%S}.png"


if __name__ == "__main__":
    ok, said = capture(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else default_out(), int(sys.argv[3]) if len(sys.argv) > 3 else 1280)
    print(said, file=sys.stdout if ok else sys.stderr)
    sys.exit(0 if ok else 1)
