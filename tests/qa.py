"""New-user walkthrough test.

    python3 tests/qa.py            # ~5 minutes; needs Claude Code signed in with Slack connected (Gmail optional)

Builds a throwaway install in a temp folder (exactly what setup.ps1 produces: config from template, empty
state, nothing else), starts the app on a spare port, and drives the onboarding through the API the same way
the page does - asserting at every stage the fact the page uses to decide it is done. Nothing touches your
real install. Exit code 0 = a brand-new user would get all the way to a populated list.
"""
import json, os, shutil, tempfile, time, urllib.request
from pathlib import Path

from _helpers import start_app

REPO = Path(__file__).resolve().parent.parent
PORT = 0  # set by start_app(): the port the app says it bound
t0 = time.time()


def say(msg):
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def api(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def wait_job(name, limit=600):
    t = time.time()
    while time.time() - t < limit:
        j = api("/api/state")["jobs"][name]
        if not j["running"]:
            return j
        time.sleep(4)
    raise SystemExit(f"FAIL: job {name} still running after {limit}s")


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    say(f"ok   {what}")


tmp = Path(tempfile.mkdtemp(prefix="openloops-qa-"))
say(f"fresh install in {tmp}")
shutil.copytree(REPO / "openloops", tmp / "openloops")
shutil.copy(REPO / "config.template.json", tmp / "config.template.json")
shutil.copytree(REPO / "scripts", tmp / "scripts")
# what setup.ps1 writes (BOM-free)
tpl = json.loads((tmp / "config.template.json").read_text(encoding="utf-8-sig"))
tpl["owner_name"] = "Testuser"
tpl["refresh_time"] = "09:15"
(tmp / "config.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
(tmp / "state.json").write_text(json.dumps({"cursor": "2026-01-01T00:00", "last_refresh": None, "loops": []}), encoding="utf-8")

# the real HOME on purpose: this walkthrough drives the signed-in Claude Code and its Slack connection
env = dict(os.environ)
srv, PORT = start_app(tmp, env)
try:
    cfg = api("/api/config")
    check(cfg["config"].get("owner_name") == "Testuser", "config readable, name set by installer")
    check(not cfg["config"].get("people"), "stage: no people yet (new user)")
    check(cfg["voice"] is None and cfg["people_suggested"] is None, "stage: no voice, no suggestions yet")

    say("stage 1 - connect: running the checklist (detects Slack id)")
    doc = api("/api/doctor", {"force": True, "detect": True})
    for s in doc["steps"]:
        say(f"     {'OK ' if s['ok'] else 'NO '} {s['title']}")
    check(doc["all_ok"], "checklist green (Gmail optional)")
    check(api("/api/config")["config"].get("slack_self_id"), "slack_self_id saved to config by the checklist")

    say("stage 2 - who's who: finding people")
    check(api("/api/people", {})["started"], "people job started")
    j = wait_job("people")
    sugg = api("/api/config")["people_suggested"]
    check(sugg and len(sugg["people"]) >= 3, f"people suggested ({len(sugg['people']) if sugg else 0} found)")
    # what the page's Save does: accept the guesses
    people, sample = {}, []
    for p in sugg["people"]:
        people[p["name"]] = {"level": p.get("guess") or "peer", "aliases": [p["name"].split()[0]], "email": p.get("email")}
        if len(sample) < 6 and people[p["name"]]["level"] != "external":
            sample.append(p["name"])
    api("/api/config", {"people": people, "voice_sample_people": sample})
    check(len(api("/api/config")["config"]["people"]) == len(people), "people saved to config")

    say("stage 3 - learn tone")
    check(api("/api/voice", {})["started"], "voice job started")
    wait_job("voice")
    v = api("/api/config")["voice"]
    check(v and v.get("people"), f"voice.json has per-person entries ({len(v['people']) if v else 0})")

    say("stage 4 - first scan")
    check(api("/api/refresh", {})["started"], "refresh job started")
    j = wait_job("refresh")
    st = api("/api/state")["state"]
    check(st.get("last_refresh"), "last_refresh set")
    say(f"     {len(st['loops'])} loops found, gmail={st.get('gmail_available')}")

    say("settings round-trip")
    api("/api/config", {"owner_name": "Renamed", "send_internal": True})
    c = api("/api/config")["config"]
    check(c["owner_name"] == "Renamed" and c["send_internal"] is True and len(c["people"]) == len(people), "edits persist, people untouched")

    say("start over")
    api("/api/reset", {})
    c = api("/api/config")
    check(not c["config"]["people"] and c["voice"] is None and not api("/api/state")["state"]["last_refresh"], "reset returns to stage 1 facts")
    say("PASS - a new user gets from install to a populated list")
finally:
    srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
