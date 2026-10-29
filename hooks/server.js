// The memory server a hook talks to: the one serving this hook's .memory folder, started here when it is down.
// Each folder runs its own server on a free port, recorded in <folder>/server.json, so two projects never share one.
// SPOOKLING_MEMORY_URL overrides it all (a server you run yourself, WSL): that URL, never started from here.
// No start lock: the store's folder lock already refuses a second server, and a start that dies is seen at once.
const http = require('node:http');
const { spawn } = require('node:child_process');
const { closeSync, existsSync, openSync, readFileSync, realpathSync } = require('node:fs');
const { homedir } = require('node:os');
const { dirname, join, resolve } = require('node:path');

const LEGACY = 'http://127.0.0.1:7447'; // a 0.1.x server: one fixed port, no server.json, no folder header
const INSTALL = join(__dirname, '..'); // ~/.spookling-memory/<version>: the server and hooks init copied there

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
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; } };

// the folder's server.json port; with none, the 0.1.x fixed port (checked by its reply, see once)
function base(dir) {
  if (process.env.SPOOKLING_MEMORY_URL) return process.env.SPOOKLING_MEMORY_URL.replace(/\/+$/, '');
  const s = dir && json(join(dir, 'server.json'));
  return Number.isInteger(s?.port) ? `http://127.0.0.1:${s.port}` : dir ? LEGACY : null;
}

// whether a reply came from this folder's server: its x-spookling-folder (percent-encoded) names the folder. A 0.1.x
// server sends no header; it counts only on the fixed port, with the folder's own server.pid (init 0.1.x) alive.
function ours(url, dir, at) {
  if (!dir || process.env.SPOOKLING_MEMORY_URL) return true;
  if (at === undefined) {
    const pid = Number((() => { try { return readFileSync(join(dir, 'server.pid'), 'utf8').trim(); } catch { return ''; } })());
    return url === LEGACY && pid > 0 && alive(pid);
  }
  try { return same(decodeURIComponent(at), dir); } catch { return false; }
}

// uv as init found it (in the install folder, never the project's), else the PATH; SPOOKLING_UV for tests
const uv = () => process.env.SPOOKLING_UV || json(join(INSTALL, 'install.json'))?.uv || 'uv';

// `uv run ... serve --folder <dir>`, detached, logging to <dir>/server.log; the server reads the tagger and keys from
// the folder. Only for a folder init named with --memory: an unwired folder or a cloned repo starts nothing. The server
// is the one beside these hooks, never a path read from the project. Resolves the child, or null.
function start(dir) {
  if (!dir || process.env.SPOOKLING_MEMORY_URL || !arg('--memory')) return null;
  const server = join(INSTALL, 'server');
  if (!existsSync(join(server, 'pyproject.toml')) || !existsSync(dir)) return null;
  try {
    const log = openSync(join(dir, 'server.log'), 'a');
    const child = spawn(uv(), ['run', '--project', server, 'spookling-memory', 'serve', '--folder', dir], {
      cwd: dirname(dir), detached: true, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, PYTHONUTF8: '1' }, // PYTHONUTF8, or Python on Windows reads files as cp1252
    });
    child.exited = false;
    child.on('error', () => { child.exited = true; });
    child.on('exit', () => { child.exited = true; });
    child.unref();
    closeSync(log);
    return child.pid ? child : null;
  } catch { return null; }
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
          // a stale server.json can point at a port another folder's server, or another app, took since
          if (!ours(url, dir, r.headers['x-spookling-folder'])) return res({ down: true, connected: false });
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
const GRACE = 2000; // ms after our start died for another hook's server (the one holding the folder) to write server.json

/**
 * The folder's server answers `path`; if it is down, it is started and waited for up to `wait` ms, or until the start
 * dies (uv missing, the folder held by another process): then at once. Resolves the reply, or { down, connected,
 * started }. Never throws.
 */
async function request(dir, path, { wait = 10000, ...opts } = {}) {
  const url = base(dir);
  let r = url ? await once(url, dir, path, opts) : { down: true, connected: false };
  if (!r.down || r.connected || !wait) return r;
  const child = start(dir);
  if (!child) return { ...r, started: false };
  let until = Date.now() + wait;
  while (Date.now() < until) {
    await sleep(300);
    const u = base(dir);
    if (u && !(r = await once(u, dir, path, opts)).down) return r;
    if (child.exited) until = Math.min(until, (child.diedAt ??= Date.now()) + GRACE);
  }
  return { ...r, started: !child.exited };
}

module.exports = { base, folder, ours, request, start };
