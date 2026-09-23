#!/usr/bin/env node
// `npm run <cmd>` from a git checkout. Finds a Python 3 and runs the app from THIS folder, on port 8766,
// so it never collides with (or gets mistaken for) the installed copy on 8765.
//
//   npm run dev       stop any earlier dev session (never the installed copy), then start this checkout on
//                     http://localhost:8766 and open the browser - one command to see your latest edits
//   npm run stop      ask the dev instance to quit (same as closing its tab); `-- --now` cuts a running job short
//   npm run prod      start (or just open) the installed copy on 8765 - the live version you use day to day
//   npm test          every tests/test_*.py, one after the other
//   npm run doctor    the connection checklist, with route detection
//   npm run refresh   one refresh job, in the foreground, from this checkout's state.json
//   npm run setup     install/refresh %LOCALAPPDATA%\OpenLoops (Windows) or ~/Library/Application Support/OpenLoops (Mac)
//                     from this checkout (install.sh moves an older ~/Documents/OpenLoops there first)
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { connect } from "node:net";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DEV_PORT = process.env.OPENLOOPS_PORT || "8766";
const win = process.platform === "win32";
// Mac: not ~/Documents - a launchd job may not read files there (#24)
const INSTALLED = win ? join(process.env.LOCALAPPDATA || "", "OpenLoops") : join(homedir(), "Library", "Application Support", "OpenLoops");

function python() {
  // no shell: the exe resolves on PATH directly, and nothing gets re-quoted by cmd.exe
  for (const c of win ? ["python", "py", "python3"] : ["python3", "python"]) {
    const r = spawnSync(c, ["--version"], { encoding: "utf-8" });
    const m = /Python (\d+)\.(\d+)/.exec((r.stdout || "") + (r.stderr || ""));
    if (r.status === 0 && m && (+m[1] > 3 || (+m[1] === 3 && +m[2] >= 11))) return c;
  }
  console.error("Python 3.11+ was not found on PATH.");
  process.exit(1);
}

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { cwd: ROOT, stdio: "inherit", ...opts });
  if (r.error) { console.error(r.error.message); return 1; }
  return r.status ?? 1;
}

function portFree(port) {
  return new Promise((resolve) => {
    const s = connect({ host: "127.0.0.1", port: +port });
    s.once("connect", () => { s.destroy(); resolve(false); });
    s.once("error", () => resolve(true));
  });
}

async function waitFree(port, ms) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await portFree(port)) return true;
    await new Promise((r) => setTimeout(r, 250));
  }
  return portFree(port);
}

function git(...args) {
  const r = spawnSync("git", args, { cwd: ROOT, encoding: "utf-8" });
  return r.status === 0 ? r.stdout.trim() : "";
}

const cmd = process.argv[2] || "dev";
const extra = process.argv.slice(3);   // `npm run dev -- --no-browser`, `npm run refresh -- --slack-only`
const py = python();
const scripts = {
  dev: async () => {
    if (Number(DEV_PORT) === 8765) {
      console.error("npm run dev cannot use port 8765: that is the installed copy's port (npm run prod). Unset OPENLOOPS_PORT.");
      return 1;
    }
    // A dev session is throwaway: stop the earlier one (and cut short any job it is running) so the page
    // you open is definitely this code. The installed copy on 8765 is a different port and is never touched.
    const r = spawnSync(py, ["-m", "openloops.app", "--stop", "--now", "--port", DEV_PORT], { cwd: ROOT, encoding: "utf-8" });
    if (r.status === 0) {
      console.log("earlier dev session: " + (r.stdout || "").trim());
      if (!(await waitFree(DEV_PORT, 15000))) {
        console.error(`port ${DEV_PORT} is still busy 15 s after asking that session to quit; stop it by hand and retry.`);
        return 1;
      }
    } else if (!/not running/.test(r.stdout || "")) {
      process.stdout.write(r.stdout || "");
      process.stderr.write(r.stderr || "");
    }
    return run(py, ["-m", "openloops.app", "--port", DEV_PORT, ...extra]);
  },
  stop: () => run(py, ["-m", "openloops.app", "--stop", "--port", DEV_PORT, ...extra]),
  prod: () => {
    if (!existsSync(join(INSTALLED, "openloops", "app.py"))) {
      console.error(`No installed copy at ${INSTALLED}. Run: npm run setup`);
      return 1;
    }
    const stamp = join(INSTALLED, "INSTALLED.txt");
    const from = existsSync(stamp) ? ` (${readFileSync(stamp, "utf-8").trim()})` : "";
    console.log(`installed copy: ${INSTALLED}${from}`);
    const env = { ...process.env };
    delete env.OPENLOOPS_PORT;  // the installed copy answers on its own default, 8765 - never the dev port
    return run(py, ["-m", "openloops.app", ...extra], { cwd: INSTALLED, env });
  },
  test: () => run(py, ["tests/run_all.py", ...extra]),
  doctor: () => run(py, ["-m", "openloops.doctor", "--detect", ...extra]),
  refresh: () => run(py, ["-m", "openloops.refresh", ...extra]),
  setup: () => {
    const rc = win
      ? run("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", join(ROOT, "setup.ps1")])
      : run("bash", [join(ROOT, "install.sh")]);
    if (rc === 0 && existsSync(INSTALLED)) {  // so `npm run prod` can say which build the installed copy is
      const when = new Date().toISOString().slice(0, 16).replace("T", " ");
      const what = `${git("rev-parse", "--abbrev-ref", "HEAD") || "?"} @ ${git("rev-parse", "--short", "HEAD") || "?"}, installed ${when} from ${ROOT}`;
      try { writeFileSync(join(INSTALLED, "INSTALLED.txt"), what + "\n"); } catch { /* the stamp is a nicety */ }
    }
    return rc;
  },
};

if (!scripts[cmd]) {
  console.error(`unknown command "${cmd}". One of: ${Object.keys(scripts).join(", ")}`);
  process.exit(2);
}
if (cmd === "dev" && !existsSync(join(ROOT, "config.json"))) {
  console.log("No config.json in this checkout yet - the page will walk you through setup. (Settings are per-checkout and gitignored.)");
}
process.exit(await scripts[cmd]());
