"""Run every tests/test_*.py in turn and summarise. Exit 1 if any fails.

    python tests/run_all.py          (or: npm test)

Each test is its own process on its own spare port, so one failure never takes the others down.
"""
import re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIPPED = re.compile(r"\bSKIP\b|\] skip ")  # a test's own "SKIP: ..." / "skip the ...", not an app log's SKIPPED
tests = sorted(p for p in HERE.glob("test_*.py"))
results = []
t0 = time.time()
for t in tests:
    start = time.time()
    r = subprocess.run([sys.executable, str(t)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    results.append((t.name, ok, time.time() - start))
    print(f"{'PASS' if ok else 'FAIL'}  {t.name}  ({time.time() - start:.0f}s)", flush=True)
    for ln in r.stdout.splitlines():  # a pass with parts skipped says which, so a green summary is not overclaimed
        if ok and SKIPPED.search(ln):
            print(f"      {ln.strip()}", flush=True)
    if not ok:
        print((r.stdout + r.stderr)[-1500:], flush=True)
failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed in {time.time() - t0:.0f}s" + (f" - FAILED: {', '.join(failed)}" if failed else ""))
sys.exit(1 if failed else 0)
