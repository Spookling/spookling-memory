// The memory server a hook talks to: the one serving this hook's .memory folder, started here when it is down.
// Each folder runs its own server on its own port, recorded in <folder>/server.json, so two projects never share one.
// SPOOKLING_MEMORY_URL overrides it all (a server you run yourself, WSL): that URL, never started from here.
const http = require('node:http');
const { spawn } = require('node:child_process');
const { closeSync, existsSync, mkdirSync, openSync, readFileSync, realpathSync, statSync, writeFileSync } = require('node:fs');
const { homedir } = require('node:os');
const { dirname, join, resolve } = require('node:path');

const START_LOCK = 'server.starting'; // another hook is starting it: wait for that one instead of a second
const LOCK_FRESH = 60000; // ms after which a start lock is stale (the start died)

const fresh = (dir) => { try { return Date.now() - statSync(join(dir, START_LOCK)).mtimeMs < LOCK_FRESH; } catch { return false; } };
const same = (a, b) => (process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b);
const arg = (k) => { const i = process.argv.indexOf(k); return i < 0 ? undefined : process.argv[i + 1]; };

// --memory <folder> as init wires it; else, for older wiring, the project's .memory, then ~/.memory
function folder(p = {}) {
  const given = arg('--memory');
  const dir = given || [process.env.CLAUDE_PROJECT_DIR || p.cwd, homedir()].filter(Boolean)
    .map((d) => join(d, '.memory')).find((d) => existsSync(d));
  if (!dir) return null;
  try { return realpathSync(dir); } catch { return resolve(dir); }
}

const json = (f) => { try { return JSON.parse(readFileSync(f, 'utf8')); } catch { return null; } };

function base(dir) {
  if (process.env.SPOOKLING_MEMORY_URL) return process.env.SPOOKLING_MEMORY_URL.replace(/\/+$/, '');
  const s = dir && json(join(dir, 'server.json'));
  return Number.isInteger(s?.port) ? `http://127.0.0.1:${s.port}` : null;
}

// `uv run ... serve --folder <dir>`, detached, logging to <dir>/server.log; the server reads the tagger and keys from
// the folder. false when nothing could be started (no folder, a start already under way, no uv)
function start(dir) {
  if (!dir || process.env.SPOOKLING_MEMORY_URL) return false;
  if (fresh(dir)) return false;
  try { writeFileSync(join(dir, START_LOCK), String(process.pid)); } catch { return false; }
  const c = json(join(dir, 'spookling.json')) || {};
  const server = c.server || join(__dirname, '..', 'server');
  if (!existsSync(join(server, 'pyproject.toml'))) return false;
  try {
    mkdirSync(dir, { recursive: true });
    const log = openSync(join(dir, 'server.log'), 'a');
    const child = spawn(c.uv || 'uv', ['run', '--project', server, 'spookling-memory', 'serve', '--folder', dir], {
      cwd: dirname(dir), detached: true, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, PYTHONUTF8: '1' }, // PYTHONUTF8, or Python on Windows reads files as cp1252
    });
    child.on('error', () => {});
    child.unref();
    closeSync(log);
    if (child.pid) writeFileSync(join(dir, 'server.pid'), `${child.pid}\n`);
    return !!child.pid;
  } catch { return false; }
}

// one request: { status, headers, body }, or { down, connected } when nothing (or another folder's server) answered
function once(url, dir, path, { method = 'GET', body, timeout = 15000 } = {}) {
  return new Promise((res) => {
    let connected = false;
    const fail = () => res({ down: true, connected });
    try {
      const req = http.request(url + path, { method, headers: { 'content-type': 'application/json' }, timeout: 1000 }, (r) => {
        let text = '';
        r.setEncoding('utf8');
        r.on('data', (d) => { text += d; });
        r.on('error', fail);
        r.on('end', () => {
          const at = r.headers['x-spookling-folder'];
          // a stale server.json can point at a port another folder's server took since
          if (at && dir && !process.env.SPOOKLING_MEMORY_URL && !same(at, dir)) return res({ down: true, connected: false });
          res({ status: r.statusCode, headers: r.headers, body: text });
        });
      });
      // 1 s to connect: WSL drops a dead port instead of refusing it
      req.on('socket', (s) => s.once('connect', () => { connected = true; req.setTimeout(timeout); }));
      req.on('timeout', () => req.destroy());
      req.on('error', fail);
      req.end(body);
    } catch { fail(); }
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * The folder's server answers `path`; if it is down, it is started and waited for up to `wait` ms. Resolves the
 * reply, or { down, connected, started }. Never throws.
 */
async function request(dir, path, { wait = 10000, ...opts } = {}) {
  const url = base(dir);
  let r = url ? await once(url, dir, path, opts) : { down: true, connected: false };
  if (!r.down || r.connected || !wait) return r;
  const started = start(dir) || (!!dir && fresh(dir));
  if (!started) return { ...r, started: false };
  for (const until = Date.now() + wait; Date.now() < until;) {
    await sleep(300);
    const u = base(dir);
    if (u && !(r = await once(u, dir, path, opts)).down) return r;
  }
  return { ...r, started: true };
}

module.exports = { base, folder, request, start };
