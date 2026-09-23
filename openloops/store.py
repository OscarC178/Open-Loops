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
"""
import json, os, sys, tempfile
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


@contextmanager
def _locked(p):
    """Hold <file>.lock exclusively, across processes: flock on POSIX, msvcrt.locking on Windows."""
    with open(p.with_name(p.name + ".lock"), "a+b") as f:
        if sys.platform == "win32":
            import msvcrt
            while True:  # LK_LOCK itself retries for about 10 s, then raises: keep waiting
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    pass
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def update_json(path, mutate):
    """Read one JSON object fresh, let mutate(obj) change it in place, write it back - all under the file's lock,
    so a writer in another process cannot slip in between and lose either side's change. mutate returning False
    means nothing changed: nothing is written. -> the object, or False if the file is there but could not be read
    (then it is left exactly as it is: writing the few keys a caller changed over it would wipe everything else)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _locked(p):
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


def load_state():
    return read_json(STATE, {"cursor": None, "last_refresh": None, "loops": []})


def update_state(fn):
    """Read state.json fresh, let fn mutate it in place, write it back. Returns the state."""
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
