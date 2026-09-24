"""Small shared helpers for the JSON files under the install root.

read_json / write_json  BOM-tolerant readers, atomic writers (a temp file of its own + fsync + replace).
update_json             read-modify-write of one file under a lock that holds across processes: doctor.py
                        (its own process) and the app's Settings / Start over both rewrite config.json.
load_cfg                config.json laid over config.template.json, so a copy that never went
                        through the installer (a git checkout, a hand-made config) still has every
                        default; empty strings inside tone / auto_chase / escalation fall back too.
load_state / update_state
    Every job script (refresh, chase, autochase, daylog, roadmap) runs for minutes between
    reading state.json and writing it back. update_state re-reads the file at write time and
    applies the change to that fresh copy, so anything the page wrote meanwhile - a note,
    a snooze, a vault save, a done click - survives.
norm_date               zero-pads YYYY-M-D; snooze checks are string comparisons everywhere.
isolated                whether this copy is an isolated test copy (#36): no to-do file, no automatic scans.
"""
import json, os, sys, tempfile, time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .paths import ROOT
STATE = ROOT / "state.json"
CONFIG = ROOT / "config.json"
TEMPLATE = ROOT / "config.template.json"


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return default


def write_json(path, obj):
    """Write via a temp file with a name of its own (two writers never share one) and swap it in whole."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, prefix=p.name + ".",
                                     suffix=".tmp", delete=False) as f:
        try:
            f.write(json.dumps(obj, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            os.unlink(f.name)
            raise
    try:
        Path(f.name).replace(p)
    except BaseException:
        Path(f.name).unlink(missing_ok=True)
        raise


class LockTimeout(TimeoutError):
    """The file lock was not free within its deadline: another writer is holding it (review of #59)."""


LOCK_WAIT_S = 10.0       # a click on the page: past this, the app answers "busy, try again" instead of hanging
JOB_LOCK_WAIT_S = 120.0  # a job's write after its AI run: worth waiting longer than a click, but never for ever


@contextmanager
def _locked(p, timeout=LOCK_WAIT_S):
    """Hold <file>.lock exclusively, across processes: flock on POSIX, msvcrt.locking on Windows. Asked without
    blocking every 50 ms until `timeout` seconds have passed, then LockTimeout. On Windows an error that goes on
    past the deadline is raised too, never retried for ever."""
    end = time.monotonic() + timeout
    with open(p.with_name(p.name + ".lock"), "a+b") as f:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as e:
                    if time.monotonic() >= end:
                        raise LockTimeout(f"{p.name}: not free within {timeout:g} s ({e})") from e
                    time.sleep(0.05)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:   # held by another writer; any other OSError is raised at once
                    if time.monotonic() >= end:
                        raise LockTimeout(f"{p.name}: not free within {timeout:g} s") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def update_json(path, mutate, timeout=LOCK_WAIT_S):
    """Read one JSON object fresh, let mutate(obj) change it in place, write it back - all under the file's lock,
    so a writer in another process cannot slip in between and lose either side's change. mutate returning False
    means nothing changed: nothing is written. -> the object, or False if the file is there but could not be read
    (then it is left exactly as it is: writing the few keys a caller changed over it would wipe everything else)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _locked(p, timeout):
        obj = {}
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError) as e:
                obj = e
            if not isinstance(obj, dict):
                print(f"{p.name} could not be read ({obj if isinstance(obj, Exception) else 'not a JSON object'}); "
                      "left as it is, nothing written", file=sys.stderr)
                return False
        if mutate(obj) is not False:
            write_json(p, obj)
    return obj


def load_cfg():
    """config.json with every missing key (and every blank entry in a nested section such as tone)
    filled from config.template.json. Keys the template does not know are kept as they are."""
    base = read_json(TEMPLATE, {}) or {}
    cfg = read_json(CONFIG, {}) or {}
    out = dict(base)
    for k, v in cfg.items():
        d = base.get(k)
        if isinstance(v, dict) and isinstance(d, dict):
            out[k] = {**d, **{kk: vv for kk, vv in v.items() if vv not in ("", None)}}
        else:
            out[k] = v
    return out


def isolated(cfg=None):
    """An isolated test copy (#36): `install.sh --isolated` / `setup.ps1 -Isolated` writes "isolated": true into its
    config.json, and OPENLOOPS_ISOLATED=1 in the environment does the same at run time (the jobs inherit it). Such a
    copy reads no to-do file, and the page starts no scan by itself: only a press of Start the first scan does."""
    if str(os.environ.get("OPENLOOPS_ISOLATED") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        return (cfg if cfg is not None else read_json(CONFIG, {}) or {}).get("isolated") is True
    except AttributeError:  # a config.json that is not a JSON object is not a record of anything
        return False


def scheduled_skip(cfg=None):
    """Why a job NOT started by the app (the weekday task, a terminal) must not read anyone's accounts, or "" (#38/#36
    review). The app starts every job with OPENLOOPS_RUN_ID set; without it the run is treated as scheduled.
    -> "isolated": an isolated test copy starts no scan by itself;
       "later":    config.json "first_scan" is "later": the first scan was not started on the page yet (a new install
                   is written with "later"; Start the first scan writes "go"). No key at all = "go": installs from
                   before this setting keep their morning refresh."""
    if os.environ.get("OPENLOOPS_RUN_ID"):
        return ""
    cfg = cfg if cfg is not None else read_json(CONFIG, {}) or {}
    if isolated(cfg if isinstance(cfg, dict) else {}):
        return "isolated"
    return "later" if isinstance(cfg, dict) and cfg.get("first_scan") == "later" else ""


def load_state():
    return read_json(STATE, {"cursor": None, "last_refresh": None, "loops": []})


def update_state(fn, timeout=JOB_LOCK_WAIT_S):
    """Read state.json fresh, let fn mutate it in place, write it back. Returns the state.
    Under the same file lock as update_json (review of #59): a refresh or chase finishing at the moment the page's
    Forget where I was (app.py, update_json) writes can no longer overwrite it with the copy it read a moment before.
    fn runs inside the lock, so it must be quick: the jobs call this after their AI run, never around it."""
    with _locked(STATE, timeout):
        s = load_state()
        s.setdefault("loops", [])
        fn(s)
        write_json(STATE, s)
    return s


def norm_date(v):
    """'2026-9-5' -> '2026-09-05'. Raises ValueError for anything that is not a date."""
    parts = str(v or "").strip().split("-")
    if len(parts) != 3 or not all(x.isdigit() for x in parts):
        raise ValueError("date must be YYYY-MM-DD")
    y, m, d = (int(x) for x in parts)
    return date(y, m, d).isoformat()
