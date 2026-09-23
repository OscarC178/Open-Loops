"""store.py: update_state keeps page edits made while a job ran; norm_date; BOM-tolerant reads.

    python3 tests/test_store.py    # fast; no Slack/Gmail. Temp files only.

Guards the race the colleague's copy documented: refresh.py used to load state.json, run the agent
for minutes, then write the stale copy back - dropping any note, snooze or done click made from
the page meanwhile.
"""
import json, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from openloops import store  # noqa: E402

t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


tmp = Path(tempfile.mkdtemp(prefix="openloops-store-"))
store.STATE = tmp / "state.json"
store.CONFIG = tmp / "config.json"
store.TEMPLATE = tmp / "config.template.json"

# --- read_json: missing, BOM, junk
check(store.read_json(tmp / "nope.json", {"d": 1}) == {"d": 1}, "read_json returns default when missing")
(tmp / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode())
check(store.read_json(tmp / "bom.json") == {"a": 1}, "read_json accepts a PowerShell UTF-8 BOM")
(tmp / "junk.json").write_text("{not json", encoding="utf-8")
check(store.read_json(tmp / "junk.json", "x") == "x", "read_json returns default on junk")

# --- write_json is atomic-ish: no .tmp left behind, content round-trips
store.write_json(tmp / "w.json", {"k": "é"})
check(not (tmp / "w.json.tmp").exists(), "write_json leaves no temp file")
check(json.loads((tmp / "w.json").read_text(encoding="utf-8")) == {"k": "é"}, "write_json round-trips unicode")

# --- the race: a job read state, the page wrote a note, the job writes through update_state
store.write_json(store.STATE, {"cursor": "2026-09-01T09:00", "loops": [
    {"id": "a", "status": "waiting", "notes": ""}]})
job_copy = store.load_state()                      # what a long-running job read at start
page = store.load_state()                          # the page adds a note and a manual loop meanwhile
page["loops"][0]["notes"] = "typed while the agent was running"
page["loops"].insert(0, {"id": "note-x", "status": "needs_me", "manual": True})
store.write_json(store.STATE, page)


def job_result(fresh):                              # the job's merge, applied to the FRESH copy
    for l in fresh["loops"]:
        if l["id"] == "a":
            l["status"] = "needs_me"
    fresh["cursor"] = "2026-09-15T10:00"


s = store.update_state(job_result)
ids = [l["id"] for l in s["loops"]]
check("note-x" in ids, "update_state keeps a loop the page added during the job")
a = next(l for l in s["loops"] if l["id"] == "a")
check(a["notes"] == "typed while the agent was running", "update_state keeps a note typed during the job")
check(a["status"] == "needs_me" and s["cursor"] == "2026-09-15T10:00", "update_state applies the job's own changes")
check(job_copy["loops"][0]["status"] == "waiting", "the job's stale copy was not what got written")

# --- norm_date
check(store.norm_date("2026-9-5") == "2026-09-05", "norm_date zero-pads")
check(store.norm_date("2026-09-15") == "2026-09-15", "norm_date leaves a good date alone")
for bad in ("2026-13-01", "soon", "", None, "2026-09"):
    try:
        store.norm_date(bad)
        raise SystemExit(f"FAIL: norm_date accepted {bad!r}")
    except ValueError:
        pass
say("ok   norm_date rejects non-dates")

# --- load_state default when missing
store.STATE = tmp / "missing.json"
check(store.load_state()["loops"] == [], "load_state returns an empty state when the file is missing")
# --- load_cfg: template defaults under a thin config.json (a checkout that never ran the installer)
store.write_json(store.TEMPLATE, {"owner_name": "", "history_days": 30,
                                  "tone": {"base": "warm", "peer": "casual"}, "auto_chase": {"enabled": False, "max_chases": 3}})
store.write_json(store.CONFIG, {"owner_name": "Oscar", "tone": {"base": "", "senior": "respectful"}, "auto_chase": {"enabled": True}, "extra": 1})
c = store.load_cfg()
check(c["owner_name"] == "Oscar" and c["history_days"] == 30, "load_cfg keeps set values and fills missing keys from the template")
check(c["tone"] == {"base": "warm", "peer": "casual", "senior": "respectful"}, "blank tone entries fall back to the template, set ones win")
check(c["auto_chase"] == {"enabled": True, "max_chases": 3}, "nested sections merge one level deep")
check(c["extra"] == 1, "keys the template does not know survive")
store.TEMPLATE = tmp / "no-template.json"
check(store.load_cfg()["owner_name"] == "Oscar", "no template file: config.json alone")
store.CONFIG = tmp / "no-config.json"
check(store.load_cfg() == {}, "neither file: empty dict, no crash")
# --- update_state and update_json share one lock (review of #59): a refresh ending while the page repairs a cursor
import threading  # noqa: E402
store.write_json(store.STATE, {"cursor": "junk", "loops": [{"id": "a"}]})
read_it = threading.Event()


def slow_refresh(s):   # a job's update_state: it has read the file, and takes a moment before its write
    read_it.set()
    time.sleep(0.5)
    s["loops"].append({"id": "b"})
    s["last_refresh"] = "2026-09-24T09:00+01:00"


job = threading.Thread(target=store.update_state, args=(slow_refresh,))
job.start()
read_it.wait(5)
t1 = time.time()
store.update_json(store.STATE, lambda s: s.update(cursor=None))   # the page's repair, in the middle of that
job.join()
got = store.load_state()
check(time.time() - t1 >= 0.3, "the repair waited for the job's write instead of slipping in between")
check(got["cursor"] is None and [l["id"] for l in got["loops"]] == ["a", "b"] and got["last_refresh"],
      f"...and neither write is lost: the repair and the job's new loop are both there ({got})")
say("ALL OK")
