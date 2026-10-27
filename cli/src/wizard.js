// init, status and uninstall; demo is init with dry: the same prompts, answered by a script, nothing written or run.
import process from 'node:process';
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, closeSync, cpSync, existsSync, mkdirSync, openSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { basename, delimiter, dirname, isAbsolute, join, relative } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { C, DOWN, MODE, UP, askHidden, bail, choose, confirm, fits, paint, press, setAssume, setFeed, setRaw, sleep, write as out } from './term.js';
import { play } from './cutscene.js';
import { flyOnce } from './ghost.js';
import { stage } from './stage.js';
import {
  HOOK_FILES, MCP_NAME, addHooks, addMcp, hookHandlers, mcpEntry, mcpPath, readJson, removeHooks, removeMcp, settingsPath,
  wiring, wslShare, CODEX_EVENTS, codexHandlers, codexHooksPath, STATUS_FILE, addStatusLine, removeStatusLine, isOurStatus,
} from './hooks.js';

const WIN = process.platform === 'win32';
const ROOT = fileURLToPath(new URL('../..', import.meta.url)).replace(/[\\/]$/, '');
export const VERSION = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')).version;
// a folder per version, out of the npx cache (npm may clear it); a \\wsl.localhost clone is wired in place
const SHARE = WIN && wslShare(join(ROOT, 'server'));
export const INSTALL_DIR = SHARE ? ROOT
  : WIN ? join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'), 'spookling-memory', VERSION)
    : join(homedir(), '.spookling-memory', VERSION);
export const SERVER_DIR = join(INSTALL_DIR, 'server'), HOOKS_DIR = join(INSTALL_DIR, 'hooks');
const BAND = 'spookling', BAND_ID = 'spookling@spookling'; // the Claude Code plugin in claude-mod/, and its marketplace
// the plugin's one folder for every version: Claude Code refuses a marketplace name from a second folder, and reads a
// folder marketplace from the folder itself, so the next version's copy over it is the upgrade
export const BAND_DIR = join(dirname(INSTALL_DIR), 'claude-mod');
// the URL only for a server run by hand in WSL; every other folder's server picks its own port, in <folder>/server.json
const PORT = 7447, SERVER_URL = `http://127.0.0.1:${PORT}`;
const STEPS = ['Checks', 'Harness', 'Tagger', 'Server', 'Wire harness', 'Smoke test', 'Done'];
export const DEMO_LABEL = 'demo, nothing is written';
const IGNORE = ['keys.env', 'server.pid', 'server.log', 'server.json', 'server.starting', 'spookling.json', 'cache.sqlite*', 'vectors.sqlite*', 'pulse.json*'];

// at most 52 characters each, so the bubble fits beside the ghost at 80 columns (tested)
export const SAY = {
  1: 'Hello. First I check what this machine has.',
  2: 'I sit beside the tools you already use.',
  3: 'I use what you already have. A key is optional.',
  4: 'Moving in. Everything I remember goes in .memory/',
  5: 'I will remind Claude of what you told it before.',
  codex: 'I will show Codex when memory is ready.',
  6: 'A quick knock, to check I am home.',
  7: 'That is all. Carry on as you always do.',
  fail: 'Sorry, that failed. The line marked ! says why.',
};

export const TAGGERS = [
  { label: 'Jev through an OpenRouter key (cheapest, about $0.002 a chat)', model: 'typesafe/jev-router', decider: 'typesafe/jev-1.14', key: 'OPENROUTER_API_KEY', ask: 'OpenRouter API key for Jev:' },
  { label: 'Anthropic API key (Haiku 4.5)', model: 'anthropic/claude-haiku-4.5', key: 'ANTHROPIC_API_KEY', ask: 'Anthropic API key:' },
  { label: 'Your Codex subscription (the codex CLI, logged in)', model: 'codex', key: null },
  { label: 'No tagger: free, works now (messages kept and searched, rules by pattern)', model: 'none', key: null },
];
const NONE = TAGGERS.length - 1;
const viaOf = (model) => (['none', 'codex'].includes(model) ? model : model.startsWith('anthropic/') ? 'anthropic' : 'openrouter');
export const taggerText = (t) => (t.via === 'none' ? 'none (every message kept and searched word for word; standing rules found by pattern)'
  : t.via === 'codex' ? 'your Codex subscription (codex exec)'
  : `${t.model} through ${t.via === 'anthropic' ? 'the Anthropic API' : 'OpenRouter'}`) + (t.decider ? `, decisions by ${t.decider}` : '');

// what this machine already runs: the harnesses on it, and the tagger it can pay for with no new key or sign-up
export function detect(env = process.env, mem = null, have = version, login = codexLogin) {
  const codexHome = env.CODEX_HOME || join(homedir(), '.codex');
  const claude = !!have('claude') || existsSync(join(env.CLAUDE_CONFIG_DIR || join(homedir(), '.claude')));
  const codex = !!have('codex') || existsSync(codexHome);
  const keys = mem && existsSync(join(mem, 'keys.env')) ? readFileSync(join(mem, 'keys.env'), 'utf8') : '';
  const has = (k) => !!(env[k] || envValue(keys, k));
  const c = codex ? login(env) : { ok: false, line: '' };
  const tagger = c.ok && !/api key/i.test(c.line) ? 2 : has('ANTHROPIC_API_KEY') ? 1 : has('OPENROUTER_API_KEY') ? 0 : NONE;
  return { claude, codex, tagger, login: c };
}

// through a shell: npm's codex on Windows is codex.cmd; it checks a login is saved, not that it still works
export function codexLogin(env = process.env) {
  const r = spawnSync('codex login status', { shell: true, env, encoding: 'utf8', timeout: 15_000, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const line = (r.stdout?.trim().split('\n')[0] || r.stderr?.trim().split('\n').at(-1) || r.error?.message || '').trim();
  return { ok: r.status === 0, line: line.replace(/sk-[\w-]+/g, 'sk-…') };
}

const ok = (s) => out(`   ${paint(C.MINT, '✓')} ${s}\n`);
const warn = (s) => out(`   ${paint(C.LILAC, '!')} ${s}\n`);
const info = (s) => out(`   ${paint(C.GREY, s)}\n`);
const fail = (ctx, problem) => { ctx.problems.push(problem); return ctx.say(SAY.fail); };

async function step(ctx, n, line = SAY[n]) {
  const w = 28, fill = Math.round((w * n) / STEPS.length);
  out(`\n${paint(C.LILAC, '━'.repeat(fill))}${paint(C.GREY, '─'.repeat(w - fill))}  ${paint(C.GREY, `step ${n} of ${STEPS.length}`)}\n`);
  out(`${paint(C.GHOST, `${n}  ${STEPS[n - 1]}`, true)}\n`);
  if (line) { await ctx.say(line); await ctx.beat(3000); }
}

// keep label short: a wrapped line cannot be redrawn
async function progress(label, work, done = label) {
  const tty = process.stdout.isTTY && MODE !== 'none';
  let i = 0;
  const tick = () => process.stdout.write(`\r   ${paint(C.LILAC, ['•  ', '•• ', '•••', ' ••', '  •', '   '][i++ % 6])} ${label}\x1b[K`);
  const timer = tty ? setInterval(tick, 120) : null;
  if (tty) tick();
  try { return await work; } finally {
    clearInterval(timer);
    out(`${tty ? '\r' : ''}   ${paint(C.GREY, done)}${tty ? '\x1b[K' : ''}\n`);
  }
}

const run = (cmd, args, { cwd, timeout }) => new Promise((res) => {
  let text = '';
  const child = spawn(cmd, args, { cwd, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const timer = setTimeout(() => child.kill(), timeout);
  child.stdout.on('data', (d) => { text += d; });
  child.stderr.on('data', (d) => { text += d; });
  child.on('error', (error) => { clearTimeout(timer); res({ status: null, error, text }); });
  child.on('close', (status) => { clearTimeout(timer); res({ status, text }); });
});

export const hooksJson = (handlers) => ['{', '  "hooks": {', Object.entries(handlers).map(([ev, h]) => [
  `    ${JSON.stringify(ev)}: [{ "hooks": [{`,
  ...Object.entries(h).map(([k, v], i, all) => `      ${JSON.stringify(k)}: ${JSON.stringify(v)}${i < all.length - 1 ? ',' : ''}`),
  '    }] }]',
].join('\n')).join(',\n'), '  }', '}'].join('\n');
export const mcpJson = (entry) => ['{', '  "mcpServers": {', `    ${JSON.stringify(MCP_NAME)}: {`,
  ...Object.entries(entry).map(([k, v], i, all) => `      ${JSON.stringify(k)}: ${JSON.stringify(v)}${i < all.length - 1 ? ',' : ''}`),
  '    }', '  }', '}'].join('\n');

// the demo's script types into the same reader a user types into, so it walks the real prompts
function demoInput(fast) {
  const { stdin } = process;
  let skip = null;
  const beat = (ms) => (fast ? Promise.resolve() : new Promise((r) => {
    const t = setTimeout(go, ms);
    function go() { clearTimeout(t); skip = null; r(); }
    skip = go;
  }));
  const onKey = (k) => (String(k)[0] === '\x03' ? bail() : skip?.()); // raw mode: Ctrl-C arrives as a key
  if (stdin.isTTY) { setRaw(true); stdin.on('data', onKey); stdin.resume(); }
  const fake = (prefix) => [...`${prefix}${'x'.repeat(24 - prefix.length)}`];
  setFeed(async ({ kind, q }) => {
    const keys = kind === 'menu' && /tagger/.test(q) ? [DOWN, DOWN, UP, UP, '\r']
      : kind === 'menu' ? [DOWN, UP, '\r']
      : kind === 'hidden' ? [...fake(/OpenRouter/.test(q) ? 'sk-or-v1-' : 'sk-ant-api03-'), '\r']
      : kind === 'confirm' ? ['y', '\r'] : ['\r'];
    await beat(kind === 'confirm' ? 4000 : 1400);
    for (const k of keys) { press(k); if (k !== '\r') await beat(kind === 'hidden' ? 45 : 800); }
  });
  return { beat, stop() { setFeed(null); if (stdin.isTTY) { stdin.off('data', onKey); stdin.pause(); setRaw(false); } } };
}

const newCtx = (project, scope = 'project') => ({
  project, scope, mem: join(project, '.memory'), harnesses: [], written: [], skipped: [], problems: [], keys: {},
  dry: false, say: async () => {}, beat: async () => {},
});
const rel = (ctx, p) => {
  const r = relative(ctx.project, p), h = relative(homedir(), p);
  return !r ? p : !r.startsWith('..') && !isAbsolute(r) ? r : !h.startsWith('..') && !isAbsolute(h) ? join('~', h) : p;
};
const pidFile = (project) => join(project, '.memory', 'server.pid');
// npm's claude and codex on Windows are .cmd files, which only a shell runs
const version = (cmd) => {
  const r = spawnSync(`${cmd} --version`, { shell: true, encoding: 'utf8', timeout: 5000, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
  return r.status === 0 && r.stdout ? r.stdout.trim().split('\n')[0] : null;
};
// the full path, so the hooks find uv when they start the server from Claude Code's or Codex's own PATH
const which = (cmd) => (process.env.PATH || '').split(delimiter).filter(Boolean)
  .flatMap((d) => (WIN ? ['.exe', '.cmd', ''] : ['']).map((x) => join(d, cmd + x))).find((f) => existsSync(f) && statSync(f).isFile()) || cmd;
const real = (p) => { try { return realpathSync(p); } catch { return p; } };
const readServer = (mem) => { try { return JSON.parse(readFileSync(join(mem, 'server.json'), 'utf8')); } catch { return null; } };

// one call to the server of this memory folder: the port in its server.json, and only if that server says it serves
// this folder (a stale server.json can name a port another folder's server has since)
function call(mem, path, body) {
  const s = readServer(mem);
  if (!Number.isInteger(s?.port)) return Promise.resolve(null);
  return new Promise((res) => {
    const req = http.request(`http://127.0.0.1:${s.port}${path}`, { method: body ? 'POST' : 'GET', timeout: 1500, headers: { 'content-type': 'application/json' } }, (r) => {
      let text = '';
      r.setEncoding('utf8');
      r.on('data', (d) => { text += d; });
      r.on('end', () => {
        const at = r.headers['x-spookling-folder'];
        const mine = (x) => (WIN ? x.toLowerCase() : x);
        if (r.statusCode !== 200 || (at && mine(at) !== mine(real(mem)))) return res(null);
        try { res(JSON.parse(text)); } catch { res({ body: text.slice(0, 80) }); }
      });
    });
    req.on('socket', (sk) => sk.once('connect', () => req.setTimeout(15000)));
    req.on('timeout', () => req.destroy());
    req.on('error', () => res(null));
    req.end(body ? JSON.stringify(body) : undefined);
  });
}
const stats = (mem) => call(mem, '/v1/stats');

function readPid(project) {
  try {
    const pid = Number(readFileSync(pidFile(project), 'utf8').trim());
    if (!pid) return null;
    try { process.kill(pid, 0); return { pid, alive: true }; } catch (e) { return { pid, alive: e.code === 'EPERM' }; }
  } catch { return null; }
}

// mode is POSIX only: on Windows chmod would change only the read-only flag
async function write(ctx, path, content, { what, preview = content, mode } = {}) {
  const name = rel(ctx, path);
  if (WIN) mode = undefined;
  out(`\n   ${paint(C.GHOST, 'write')} ${name}${mode ? paint(C.GREY, ` (mode ${mode.toString(8)})`) : ''}\n`);
  out(preview.trimEnd().split('\n').map((l) => `     ${paint(C.GREY, l)}`).join('\n') + '\n');
  if (!(await confirm('   Write it?'))) {
    ctx.skipped.push(`${name} (${what})`);
    info('skipped');
    return false;
  }
  ctx.written.push([name, what]);
  if (ctx.dry) return info('demo: not written'), true;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content, mode ? { mode } : undefined);
  if (mode) chmodSync(path, mode);
  ok(`wrote ${name}`);
  return true;
}

async function mergeFile(ctx, path, fn, { what, preview, same }) {
  let before, after;
  try { before = ctx.dry ? {} : readJson(path); after = fn(before); } catch (e) {
    warn(e.message);
    return fail(ctx, `${rel(ctx, path)} left untouched: fix it by hand, then run again`);
  }
  if (JSON.stringify(before) === JSON.stringify(after)) return ok(same);
  await write(ctx, path, JSON.stringify(after, null, 2) + '\n', { what, preview });
}

// stdin closed: `claude mcp` waits on an open stdin
async function runCmd(ctx, cmd, args, { what, show, cwd }) {
  out(`\n   ${paint(C.GHOST, 'run')} ${show}\n`);
  if (!(await confirm('   Run it?'))) {
    ctx.skipped.push(what);
    info('skipped');
    return null;
  }
  if (ctx.dry) { ctx.written.push([cmd, what]); info('demo: not run'); return { status: 0 }; }
  const r = spawnSync(cmd, args, { cwd, encoding: 'utf8', timeout: 60_000, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  for (const l of `${r.stdout ?? ''}${r.stderr ?? ''}`.trim().split('\n').filter(Boolean)) info(l);
  if (r.status === 0) { ctx.written.push([cmd, what]); ok('done'); }
  else warn(r.error?.code === 'ENOENT' ? `${cmd} is not on your PATH` : r.error ? `${cmd}: ${r.error.code ?? r.error.message}` : `${cmd} exited ${r.status}`);
  return r;
}
const shq = (s) => (/^[\w./:@=-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);

// the official installers; both put claude in ~/.local/bin, like uv's
export const CLAUDE_INSTALL = WIN
  ? 'powershell -ExecutionPolicy ByPass -c "irm https://claude.ai/install.ps1 | iex"'
  : 'curl -fsSL https://claude.ai/install.sh | bash';

// user scope needs `claude mcp` to add the MCP entry; project scope only writes files, so a missing claude is a warning there
export async function claudeCli(ctx, find = () => version('claude')) {
  let v = find();
  if (v) return ok(`claude ${v}`), v;
  if (ctx.dry) return info('claude is not on your PATH. demo: init would offer to install it here.'), null;
  warn(ctx.scope === 'user' ? 'claude is not on your PATH. Step 5 runs it to add the MCP entry.'
    : 'claude is not on your PATH. Claude Code is what loads these hooks.');
  info(`install it with: ${CLAUDE_INSTALL}`);
  if (await confirm('   Run that now?', false)) {
    spawnSync(CLAUDE_INSTALL, { shell: true, stdio: 'inherit' });
    process.env.PATH = `${join(homedir(), '.local', 'bin')}${delimiter}${process.env.PATH}`;
    v = find();
  }
  if (v) ok(`claude ${v}`);
  else info('Carrying on without it. Install Claude Code, then run init again.');
  return v;
}

// Claude Code writes ~/.claude.json itself, so only `claude mcp` changes it; add-json refuses a name that exists
const userEntry = (project) => { try { return readJson(mcpPath(project, 'user')).mcpServers?.[MCP_NAME]; } catch { return undefined; } };
async function userMcp(ctx, entry) {
  const have = ctx.dry ? undefined : userEntry(ctx.project), json = JSON.stringify(entry);
  if (JSON.stringify(have) === json) return ok(`${rel(ctx, mcpPath(ctx.project, 'user'))} already has the MCP entry`);
  const add = ['mcp', 'add-json', MCP_NAME, json, '--scope', 'user'];
  if (!ctx.dry && !ctx.claude) {
    out(`\n   ${paint(C.GHOST, 'run')} claude ${add.map(shq).join(' ')}\n`);
    warn('not run: claude is not on your PATH');
    return fail(ctx, 'MCP entry not added: install Claude Code, then run init again (or the claude mcp add-json line above)');
  }
  if (have) {
    const r = await runCmd(ctx, 'claude', ['mcp', 'remove', MCP_NAME, '--scope', 'user'],
      { what: `the old ${MCP_NAME} entry removed`, show: `claude mcp remove ${MCP_NAME} --scope user` });
    if (r?.status !== 0) return fail(ctx, `MCP entry not replaced: run claude mcp remove ${MCP_NAME} --scope user, then init again`);
  }
  const r = await runCmd(ctx, 'claude', add, { what: `MCP server ${MCP_NAME}, user scope`, show: `claude ${add.map(shq).join(' ')}` });
  if (r && r.status !== 0) await fail(ctx, 'MCP entry not added: run the claude mcp add-json line above in your shell');
}

// a clone's .venv and caches stay behind: a .venv belongs to the folder it was made in
const LEAVE = new Set(['.venv', '__pycache__', '.pytest_cache', '.memory', 'node_modules']);
export function install(from, dir, band = join(dirname(dir), 'claude-mod')) {
  for (const d of ['server', 'hooks']) cpSync(join(from, d), join(dir, d), { recursive: true, filter: (p) => !LEAVE.has(basename(p)) });
  rmSync(band, { recursive: true, force: true }); // a file an older version had must not outlive it
  cpSync(join(from, 'claude-mod'), band, { recursive: true, filter: (p) => !LEAVE.has(basename(p)) });
  mkdirSync(join(band, 'bin'), { recursive: true });
  writeFileSync(join(band, 'bin', 'hooks.json'), JSON.stringify({ hooks: join(dir, 'hooks') }) + '\n'); // where probe.cjs finds ghost.js
}

// true when the server is in place, copied now or by an earlier init
async function copyOut(ctx) {
  out(`\n   ${paint(C.GHOST, 'copy')} server/ and hooks/ to ${rel(ctx, INSTALL_DIR)}\n`);
  info(`  from ${ROOT}`);
  info('  a folder per version, so Claude Code never runs files from the npx cache; uninstall leaves it');
  if (!(await confirm('   Copy them?'))) {
    ctx.skipped.push(`copying the server to ${rel(ctx, INSTALL_DIR)}`);
    info('skipped');
    return ctx.dry || existsSync(join(SERVER_DIR, 'pyproject.toml'));
  }
  ctx.written.push([rel(ctx, INSTALL_DIR), 'the server and hooks']);
  if (ctx.dry) return info('demo: not copied'), true;
  try { install(ROOT, INSTALL_DIR); } catch (e) { warn(`copy failed: ${e.message}`); return false; }
  ok(`copied to ${rel(ctx, INSTALL_DIR)}`);
  return true;
}

function keysFile(text, keys) {
  const lines = text ? text.replace(/\n$/, '').split('\n') : [];
  for (const [k, v] of Object.entries(keys)) {
    const i = lines.findIndex((l) => l.startsWith(`${k}=`));
    if (i >= 0) lines[i] = `${k}=${v}`; else lines.push(`${k}=${v}`);
  }
  return lines.join('\n') + '\n';
}
const envValue = (text, k) => text.match(new RegExp(`^${k}=(.+)$`, 'm'))?.[1]?.trim();

// the same command the hooks run when they find the server down: the tagger and keys come from the folder
function startServer(ctx) {
  mkdirSync(ctx.mem, { recursive: true });
  const log = openSync(join(ctx.mem, 'server.log'), 'a');
  const args = ['run', '--project', SERVER_DIR, 'spookling-memory', 'serve', '--folder', real(ctx.mem)];
  if (ctx.model) args.push('--model', ctx.model);
  if (ctx.decider) args.push('--decider', ctx.decider);
  // detached, so it outlives the wizard; PYTHONUTF8, or Python on Windows reads files as cp1252
  const child = spawn(which('uv'), args, {
    cwd: ctx.project, detached: true, windowsHide: true, stdio: ['ignore', log, log],
    env: { ...process.env, PYTHONUTF8: '1', ...ctx.keys },
  });
  child.on('error', () => {});
  child.unref();
  closeSync(log);
  if (child.pid) writeFileSync(pidFile(ctx.project), `${child.pid}\n`);
  return child.pid;
}

async function waitForStats(ctx) {
  let d = 250;
  for (let i = 0; i < 10; i++) {
    const r = await stats(ctx.mem);
    if (r) return r;
    if (!ctx.pid || !readPid(ctx.project)?.alive) return null;
    out(paint(C.GREY, '.'));
    await sleep(d + Math.random() * d);
    d = Math.min(d * 2, 3000);
  }
  return null;
}

export async function init({ anim = true, project = process.cwd(), scope = 'project', dry = false, fast = false, ghost = true, yes = false } = {}) {
  const label = dry ? DEMO_LABEL : '';
  if (yes && !dry) setAssume(true);
  await play({ anim: anim && !fast && !yes, keep: fits(anim), label });
  const ctx = { ...newCtx(project, scope), ghost, yes };
  const demo = dry ? demoInput(fast) : null;
  const st = stage({ anim, label, hold: fast ? 0 : 1200 });
  Object.assign(ctx, { dry, say: st.say, beat: demo?.beat ?? ctx.beat });
  try {
    return await steps(ctx, { anim, st });
  } finally {
    st.close();
    demo?.stop();
  }
}

async function steps(ctx, { anim, st }) {
  const { project, scope } = ctx;
  if (ctx.dry && !st.live) info(DEMO_LABEL);
  out(`   Setting up Spookling Memory in ${paint(C.GHOST, project)}${scope === 'user' ? paint(C.GREY, ' (hooks and MCP for all your projects)') : ''}\n`);
  info(ctx.yes ? 'Every file is shown as it is written (--yes: every answer is its default).' : 'Every file is shown before it is written. Answer n to skip it.');

  await step(ctx, 1);
  if (Number(process.versions.node.split('.')[0]) < 18) {
    warn(`Node ${process.version} is too old. Spookling Memory needs Node 18 or newer.`);
    await ctx.say(SAY.fail);
    return 1;
  }
  ok(`node ${process.version}`);
  let uv = version('uv');
  if (!uv && ctx.dry) info('uv is not on your PATH. demo: init would offer to install it here.');
  else if (!uv) {
    const cmd = process.platform === 'win32'
      ? 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
      : 'curl -LsSf https://astral.sh/uv/install.sh | sh';
    warn('uv is not on your PATH. The memory server runs on it.');
    info(`install it with: ${cmd}`);
    if (await confirm('   Run that now?', false)) {
      spawnSync(cmd, { shell: true, stdio: 'inherit' });
      process.env.PATH = `${join(homedir(), '.local', 'bin')}${delimiter}${process.env.PATH}`;
      uv = version('uv');
    }
    if (!uv) { warn('Install uv, then run init again.'); await ctx.say(SAY.fail); return 1; }
  }
  if (uv) ok(uv);
  const git = version('git');
  if (git) ok(git); else warn('git not found. That is fine; .memory/ is just easier to back up inside a repo.');
  await ctx.beat(1200);

  // the demo reads neither the environment nor keys.env: its defaults are the first choices
  const found = ctx.dry ? { claude: true, codex: false, tagger: 0 } : detect(process.env, ctx.mem);
  await step(ctx, 2);
  if (!ctx.dry) info(`found: ${[found.claude && 'Claude Code', found.codex && 'Codex'].filter(Boolean).join(' and ') || 'neither (Claude Code assumed)'}`);
  const both = found.claude && found.codex;
  const pick = await choose('   Which one?', ['Claude Code', 'Codex', 'Both'], both ? 2 : found.codex ? 1 : 0);
  ctx.harnesses = [['claude'], ['codex'], ['claude', 'codex']][pick];
  ok(['Claude Code', 'Codex', 'Claude Code and Codex'][pick]);
  if (ctx.harnesses.includes('claude')) ctx.claude = await claudeCli(ctx);

  await step(ctx, 3);
  if (!ctx.dry) info(`using what you have: ${TAGGERS[found.tagger].label.split(' (')[0].replace(/^Your/, 'your')}`);
  let t = TAGGERS[await choose('   How should the tagger run?', TAGGERS.map((x) => x.label), found.tagger)];
  const keyless = (why) => {
    warn(`${why} Memory starts without a tagger: every message is kept and searched, rules are found by pattern.`);
    info('Run init again once you have it; nothing is lost, later chats get tagged.');
    t = TAGGERS[NONE];
  };
  const envPath = join(ctx.mem, 'keys.env');
  if (t.model === 'none') {
    ok(t.label);
    if (ctx.harnesses.includes('claude')) info('A Claude Pro or Max plan cannot tag for another tool (Anthropic\'s terms); an Anthropic API key can.');
  } else if (t.key) {
    ok(t.label);
    const envText = !ctx.dry && existsSync(envPath) ? readFileSync(envPath, 'utf8') : '';
    const name = t.key, have = envValue(envText, name);
    let v = ctx.dry ? undefined : process.env[name];
    if (v) ok(`${name} found in your environment`);
    else if (have) { ok(`${name} already in .memory/keys.env`); v = have; }
    else if ((v = await askHidden(`   ${t.ask}`))) ok(`${name} received`);
    if (!v) keyless(`No ${name}.`);
    else {
      ctx.keys[name] = v;
      if (name === 'ANTHROPIC_API_KEY' && !v.startsWith('sk-ant-')) warn('That key does not start with sk-ant-. Kept as given.');
      if (v === have) ok('.memory/keys.env already up to date');
      else {
        await write(ctx, envPath, keysFile(envText, { [name]: v }), {
          what: WIN ? 'your key (kept out of git; mind who can read this folder)' : 'your key, readable only by you', mode: 0o600,
          preview: `${name}=********`,
        });
      }
    }
  } else {
    ok(t.label);
    const c = !ctx.dry && found.codex ? found.login : codexLogin();
    out(`   ${paint(C.GREY, 'codex login status')}\n`);
    if (c.ok) {
      ok(c.line || 'logged in');
      if (/api key/i.test(c.line)) warn('That login is an API key, so tagging bills the API, not your plan. codex login switches it.');
      info('That checks a login is saved, not that it still works; the first tag does (errors go to .memory/server.log).');
      info('Each chat then takes several seconds to tag and uses a little of your plan (Plus or higher); its terms apply.');
    } else {
      warn(c.line || 'codex did not answer');
      keyless('Codex is not installed or not logged in (npm install -g @openai/codex, then codex login).');
    }
  }
  ctx.decider = t.decider || null;
  ctx.model = t.model;
  const giPath = join(ctx.mem, '.gitignore');
  const gi = !ctx.dry && existsSync(giPath) ? readFileSync(giPath, 'utf8') : '';
  const missing = IGNORE.filter((l) => !gi.split(/\r?\n/).includes(l));
  if (missing.length) {
    await write(ctx, giPath, gi + (gi && !gi.endsWith('\n') ? '\n' : '') + missing.join('\n') + '\n', {
      what: 'keeps your keys and the caches out of git',
    });
  }

  await step(ctx, 4);
  if (SHARE) {
    info(`The server folder is inside WSL (${SHARE.distro}), so the server runs there. In WSL, from your project:`);
    info(`  node '${SHARE.path.replace(/\/server$/, '')}/bin/spookling-memory.js' init`);
    info('No uv sync from Windows: it would build a Windows .venv over the one WSL uses.');
    ctx.skipped.push('the server install (it runs in WSL)');
  } else if (!existsSync(join(ROOT, 'server', 'pyproject.toml'))) {
    warn(`No memory server found at ${join(ROOT, 'server')}.`);
    await fail(ctx, 'memory server not found');
  } else if (!(await copyOut(ctx))) {
    await fail(ctx, `the server is not in ${rel(ctx, INSTALL_DIR)}: let init copy it there`);
  } else {
    const label = 'installing the server (uv sync)', done = `uv sync in ${rel(ctx, SERVER_DIR)}`;
    const r = ctx.dry ? (await progress(label, ctx.beat(3000), done), null)
      : await progress(label, run('uv', ['sync'], { cwd: SERVER_DIR, timeout: 10 * 60_000 }), done);
    if (ctx.dry) info('demo: skipped');
    else {
      // uv's own summary; the per-package lines only when it failed
      for (const l of r.text.split(/\r?\n/)) if (l.trim() && (r.status !== 0 || !/^\s*[-+~] /.test(l))) info(l.trim());
    }
    if (r && r.status !== 0) {
      warn(r.error ? `uv sync: ${r.error.code ?? r.error.message}` : 'uv sync failed (output above).');
      await fail(ctx, 'uv sync failed');
    } else {
      if (!ctx.dry) ok('server installed');
      // what a server started later by a hook reads: the tagger, and where the server and uv are
      const config = { version: VERSION, server: SERVER_DIR, uv: which('uv'), model: ctx.model, decider: ctx.decider };
      await write(ctx, join(ctx.mem, 'spookling.json'), JSON.stringify(config, null, 2) + '\n', {
        what: 'the tagger and the server, for the hooks to restart it after a reboot',
      });
      let up = !ctx.dry && await stats(ctx.mem);
      if (up?.tagger && (up.tagger.model !== ctx.model || (up.tagger.decider || null) !== ctx.decider)) {
        const p = readPid(ctx.project)?.alive ? readPid(ctx.project) : readServer(ctx.mem); // uv's pid leads the group
        warn(`this folder's server tags with ${taggerText(up.tagger)}.`);
        if (p?.pid && await confirm(`   Restart it (pid ${p.pid}) with ${taggerText({ model: ctx.model, via: viaOf(ctx.model), decider: ctx.decider })}?`)) {
          stopServer(p.pid);
          await gone(p.pid);
          up = null;
        } else await fail(ctx, 'the running server has another tagger');
      }
      if (up) ok(`this folder's server already answers on 127.0.0.1:${readServer(ctx.mem).port}`);
      else if (!ctx.problems.length && await confirm('   Start it in the background? (log in .memory/server.log; the hooks restart it if it stops)')) {
        if (ctx.dry) {
          info('demo: not started');
          ctx.written.push(['memory server', 'its own port, in .memory/server.json']);
        } else {
          ctx.pid = startServer(ctx);
          if (ctx.pid) {
            ok(`started, pid ${ctx.pid}`);
            ctx.written.push(['memory server', `pid ${ctx.pid}, port in .memory/server.json`]);
          } else { warn('could not start uv'); await fail(ctx, 'server did not start'); }
        }
      } else if (!up) ctx.skipped.push('starting the server (the first hook starts it)');
    }
  }

  await step(ctx, 5, ctx.harnesses.includes('claude') ? SAY[5] : SAY.codex);
  // every hook and the status line name this memory folder, so they reach its own server (in WSL: the fixed URL)
  const memory = SHARE ? undefined : real(ctx.mem);
  if (ctx.harnesses.includes('codex')) {
    if (!existsSync(join(ctx.dry ? join(ROOT, 'hooks') : HOOKS_DIR, 'spookling-codex.cjs'))) await fail(ctx, 'Codex hook script missing');
    const handlers = codexHandlers(HOOKS_DIR, undefined, memory), sp = codexHooksPath(project, scope);
    await mergeFile(ctx, sp, (s) => addHooks(s, handlers), {
      what: 'Codex hooks: SessionStart, UserPromptSubmit, PreCompact, Stop',
      preview: `${hooksJson(handlers)}\n  merged in; existing hooks stay`,
      same: `${rel(ctx, sp)} already has the hooks`,
    });
    info('Codex asks you to trust new hooks once: open /hooks in Codex and trust the Spookling ones.');
    info('Then the ghost shows itself at the start of each session, and again whenever memory recalls something.');
  }
  if (ctx.harnesses.includes('claude')) {
    const handlers = hookHandlers(HOOKS_DIR, undefined, memory), hooksAt = ctx.dry ? join(ROOT, 'hooks') : HOOKS_DIR; // the demo copies nothing
    for (const f of [...Object.values(HOOK_FILES), 'pulse.js', 'server.js', 'ghost.js', STATUS_FILE]) {
      if (!existsSync(join(hooksAt, f))) {
        warn(`${join(hooksAt, f)} is missing; Claude Code will report it on each event.`);
        await fail(ctx, `hook script ${f} missing`);
      }
    }
    if (!version('node')) warn('`node` is not on your PATH here. The hooks run `node`, so Claude Code needs it on its PATH.');
    const sp = settingsPath(project, scope);
    // the ghost (on unless --no-ghost: it is how you see memory is on): the band above the prompt, a Claude Code plugin
    // that blinks and turns green when memory recalls; the status line where this Claude Code cannot install plugins
    let band = false;
    if (ctx.ghost && !SHARE) {
      const args = ['plugin', 'install', BAND, '--marketplace', ctx.dry ? join(ROOT, 'claude-mod') : BAND_DIR, '--scope', scope, '--json'];
      const r = await runCmd(ctx, 'claude', args, { what: 'the ghost above the prompt (a Claude Code plugin)', show: `claude ${args.map(shq).join(' ')}`, cwd: project });
      band = r?.status === 0;
      if (r && !band) {
        info('This Claude Code could not install the plugin, so the ghost goes in the status line instead.');
        if (/"name_taken"/.test(r.stdout ?? '')) info('A "spookling" marketplace from another folder is in the way: claude plugin marketplace remove spookling, then init again.');
      }
    }
    const ghost = ctx.ghost && !band;
    let line = '', ours = false;
    try { ours = band && isOurStatus(readJson(sp).statusLine); } catch {}
    try { line = ghost ? addStatusLine(ctx.dry ? {} : readJson(sp), HOOKS_DIR, undefined, memory).statusLine : ''; } catch {} // mergeFile reports it
    const events = Object.keys(HOOK_FILES).join(', ');
    const wire = (s) => (ghost ? addStatusLine(addHooks(s, handlers), HOOKS_DIR, undefined, memory) : band ? removeStatusLine(addHooks(s, handlers)) : addHooks(s, handlers));
    await mergeFile(ctx, sp, wire, {
      what: `hooks: ${events}${ghost ? '; the ghost in the status line' : ''}`,
      preview: `${hooksJson(handlers)}${line ? `\n"statusLine": ${JSON.stringify(line, null, 2)}` : ''}\n  merged in; nothing else in the file changes`
        + (line && / --then /.test(line.command) ? '\n  your status line stays first; uninstall puts it back as it was' : '')
        + (ours ? '\n  the status line ghost of an earlier install comes out: the ghost is above the prompt now' : ''),
      same: `${rel(ctx, sp)} already has the hooks${ghost ? ' and the ghost' : ''}`,
    });
    info(band ? 'The ghost sits above the prompt from the next Claude Code session: it blinks, and turns green when memory recalls.'
      : ghost ? 'The ghost sits in your status line from the next Claude Code session (--no-ghost leaves it out).'
        : 'No ghost (--no-ghost). npx spookling init again adds it.');
    const entry = mcpEntry(SERVER_DIR, SERVER_URL, memory);
    if (scope === 'project') {
      await mergeFile(ctx, mcpPath(project), (c) => addMcp(c, entry), {
        what: `MCP server ${MCP_NAME}`,
        preview: `${mcpJson(entry)}\n  merged in; other servers stay`,
        same: '.mcp.json already has the MCP entry',
      });
    } else await userMcp(ctx, entry);
  }

  // stats, then a recall: a question the way a hook asks it, so the check covers the path every prompt takes. It reads
  // only: no test message lands in the log, which is never edited
  await step(ctx, 6);
  out(`   ${paint(C.GREY, 'GET /v1/stats, POST /v1/retrieve ')}`);
  let res, recall;
  if (ctx.dry) {
    for (let i = 0; i < 3; i++) { await ctx.beat(500); out(paint(C.GREY, '.')); }
    res = { n_events: 0, n_facts: 0, n_rows: 0, tagger: { model: ctx.model, via: viaOf(ctx.model) }, tagging: { waiting: 0, error: null } };
    recall = { context: '' };
  } else {
    res = await waitForStats(ctx);
    recall = res && await call(ctx.mem, '/v1/retrieve', { question: 'what did we decide last time?' });
  }
  out('\n');
  if (res && recall && typeof recall.context === 'string') {
    const port = ctx.dry ? '' : ` on 127.0.0.1:${readServer(ctx.mem)?.port}`;
    ok(`the server answered${port}: ${plural(res.n_events, 'message')} remembered, recall ${/<(recalled-data|invariant)\b/.test(recall.context) ? 'returned context' : 'ran (nothing on record yet)'}`);
    if (res.tagger) ok(`tagger: ${taggerText(res.tagger)}`);
    if (res.tagging?.error) warn(`tagging is failing (${res.tagging.error}); messages are saved and searched meanwhile`);
    if (ctx.dry) info('demo: a sample reply');
  } else {
    warn(`${res ? 'the server answered but recall did not' : 'nothing answered'}. ${SHARE ? 'Start the server in WSL (the command in step 4).' : 'Look in .memory/server.log.'}`);
    await fail(ctx, 'smoke test failed');
  }

  await step(ctx, 7, null);
  await ctx.beat(1000);
  let wired = true;
  if (ctx.harnesses.includes('claude') && !ctx.dry) {
    try {
      const w = wiring(readJson(settingsPath(project, scope)), readJson(mcpPath(project, scope)));
      wired = w.events.length === Object.keys(HOOK_FILES).length && w.mcp;
    } catch { wired = false; }
  }
  if (ctx.harnesses.includes('codex') && !ctx.dry) {
    try { wired = wired && CODEX_EVENTS.every((ev) => wiring(readJson(codexHooksPath(project, scope)), {}).events.includes(ev)); }
    catch { wired = false; }
  }
  st.close();
  await flyOnce(wired && !ctx.problems.length ? SAY[7] : SAY.fail, { anim });
  for (const [name, what] of ctx.written) out(`   ${paint(C.MINT, `✓ ${name.padEnd(24)}`)} ${paint(C.GREY, what)}\n`);
  for (const s of ctx.skipped) out(`   ${paint(C.GREY, `- skipped ${s}`)}\n`);
  for (const p of ctx.problems) out(`   ${paint(C.LILAC, '!')} ${p}\n`);
  if (wired && !ctx.problems.length) {
    out(`\n   ${paint(C.MINT, 'Spookling Memory is in.', true)}\n`);
    out(ctx.dry ? `   ${paint(C.GREY, 'demo: nothing was written, started or run.')}\n\n` : '\n');
    return 0;
  }
  out(`\n   Not quite in yet. Fix the lines marked ! (or the skipped writes) and run init again.\n\n`);
  return 1;
}

const plural = (k, word) => `${k} ${word}${k === 1 ? '' : 's'}`;

export async function status(project = process.cwd(), get = stats) {
  const line = (label, text, good) => out(`   ${good ? paint(C.MINT, '✓') : paint(C.LILAC, '!')} ${label.padEnd(8)} ${text}\n`);
  const ctx = newCtx(project);
  out(`${paint(C.GHOST, 'Spookling Memory', true)}  ${paint(C.GREY, project)}\n`);
  const s = await get(ctx.mem), p = readPid(project);
  line('server', s ? `up on 127.0.0.1:${readServer(ctx.mem)?.port ?? PORT}${p?.alive ? `, pid ${p.pid}` : ''}`
    : `down${p && !p.alive ? ` (stale .memory/server.pid: pid ${p.pid} is not running)` : ''}; the next hook starts it`, !!s);
  const where = { events: [], mcp: [] }, got = new Set();
  for (const scope of ['project', 'user']) {
    try {
      const w = wiring(readJson(settingsPath(project, scope)), readJson(mcpPath(project, scope)));
      if (w.events.length) where.events.push(`${w.events.join(', ')} in ${rel(ctx, settingsPath(project, scope))}`);
      if (w.mcp) where.mcp.push(`${MCP_NAME} in ${rel(ctx, mcpPath(project, scope))}`);
      w.events.forEach((ev) => got.add(ev));
    } catch (e) { line('config', e.message, false); }
  }
  const events = [...got];
  line('hooks', where.events.join('; ') || 'none in .claude/settings.json or ~/.claude/settings.json', events.length === Object.keys(HOOK_FILES).length);
  const ghostIn = ['project', 'user'].filter((sc) => { try { return isOurStatus(readJson(settingsPath(project, sc)).statusLine); } catch { return false; } });
  const bandIn = ['project', 'user'].filter((sc) => { try { return readJson(settingsPath(project, sc)).enabledPlugins?.[BAND_ID] === true; } catch { return false; } });
  line('ghost', bandIn.length ? `above the prompt (the ${BAND_ID} plugin, ${bandIn.map((sc) => rel(ctx, settingsPath(project, sc))).join(', ')})`
    : ghostIn.length ? `in the status line (${ghostIn.map((sc) => rel(ctx, settingsPath(project, sc))).join(', ')})`
      : 'not shown (--no-ghost; init again adds it)', true);
  line('mcp', where.mcp.join('; ') || 'no entry in .mcp.json or ~/.claude.json', where.mcp.length > 0);
  const missing = Object.values(HOOK_FILES).filter((f) => !existsSync(join(HOOKS_DIR, f)));
  if (events.length && missing.length) line('scripts', `missing in ${HOOKS_DIR}: ${missing.join(', ')}`, false);
  const codexGot = new Set(), codexWhere = [];
  for (const scope of ['project', 'user']) {
    const cp = codexHooksPath(project, scope);
    try {
      const w = wiring(readJson(cp), {});
      if (w.events.length) codexWhere.push(`${w.events.join(', ')} in ${rel(ctx, cp)}`);
      w.events.forEach((ev) => codexGot.add(ev));
    } catch (e) { line('config', e.message, false); }
  }
  const codex = CODEX_EVENTS.every((ev) => codexGot.has(ev));
  line('codex', codexWhere.join('; ') || 'no Spookling hooks in project/user hooks.json', codex);
  if (codexGot.size) line('trust', 'review /hooks in Codex; configured hooks are not proof of trust or execution', false);
  const codexScript = existsSync(join(HOOKS_DIR, 'spookling-codex.cjs'));
  if (codexGot.size && !codexScript) line('scripts', `missing ${join(HOOKS_DIR, 'spookling-codex.cjs')}`, false);
  const claude = events.length === Object.keys(HOOK_FILES).length && where.mcp.length > 0;
  line('harness', [claude && 'Claude Code', codex && 'Codex (configured; trust checked in /hooks)'].filter(Boolean).join(', ') || 'none wired', claude || codex);
  line('tagger', s?.tagger ? taggerText(s.tagger) : 'unknown (the server is down, or older than this wizard)', !!s?.tagger);
  if (s?.tagging) {
    const { waiting, error, retry_in: retry } = s.tagging;
    line('tagging', error ? `failing: ${error}; ${plural(waiting, 'item')} saved and waiting, next try in ${retry} s` : waiting ? `${plural(waiting, 'item')} waiting` : 'up to date', !error);
  }
  const need = { openrouter: 'OPENROUTER_API_KEY', anthropic: 'ANTHROPIC_API_KEY' }[s?.tagger?.via];
  const envPath = join(project, '.memory', 'keys.env');
  if (existsSync(envPath)) {
    const names = [...readFileSync(envPath, 'utf8').matchAll(/^([A-Z_][A-Z0-9_]*)=./gm)].map((m) => m[1]);
    const mode = WIN ? null : statSync(envPath).mode & 0o777;
    line('keys', `${names.join(', ') || 'none'} in .memory/keys.env${mode && mode !== 0o600 ? ` (mode ${mode.toString(8)}, should be 600)` : ''}`,
      (!need || names.includes(need)) && (!mode || mode === 0o600));
  } else line('keys', need ? `no .memory/keys.env (the tagger needs ${need})` : 'no .memory/keys.env', !need);
  return s && ((claude && !missing.length) || (codex && codexScript)) ? 0 : 1;
}

// up to 10 s for a stopped server to go (SIGTERM is graceful: uvicorn closes the store first)
async function gone(pid) {
  const dead = () => { try { process.kill(pid, 0); return false; } catch (e) { return e.code !== 'EPERM'; } };
  for (let i = 0; i < 50 && !dead(); i++) await sleep(200);
  return dead();
}

// the whole tree: started detached, its pid leads a process group; on Windows taskkill /T
function stopServer(pid) {
  if (WIN) return spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }).status === 0;
  try { process.kill(-pid, 'SIGTERM'); return true; } catch { try { process.kill(pid, 'SIGTERM'); return true; } catch { return false; } }
}

export async function uninstall(project = process.cwd(), { scope = 'project', harness = 'claude' } = {}) {
  const ctx = newCtx(project, scope);
  const sp = harness === 'codex' ? codexHooksPath(project, scope) : settingsPath(project, scope);
  out(`${paint(C.GHOST, 'Removing Spookling Memory wiring', true)}  ${paint(C.GREY, scope === 'user' ? `${project} (user scope)` : project)}\n`);
  await mergeFile(ctx, sp, harness === 'codex' ? removeHooks : (s) => removeStatusLine(removeHooks(s)), {
    what: 'removed the Spookling hooks',
    preview: `drop only the Spookling hooks${harness === 'codex' ? '' : ' and the ghost (your own status line comes back)'}; everything else stays`,
    same: `no Spookling hooks in ${rel(ctx, sp)}`,
  });
  if (harness !== 'codex') {
    let has = false;
    try { has = readJson(sp).enabledPlugins?.[BAND_ID] === true; } catch {}
    if (has) {
      const args = ['plugin', 'uninstall', BAND_ID, '--scope', scope];
      const r = await runCmd(ctx, 'claude', args, { what: 'removed the ghost plugin', show: `claude ${args.join(' ')}`, cwd: project });
      if (r && r.status !== 0) ctx.problems.push(`run claude plugin uninstall ${BAND_ID} --scope ${scope} yourself`);
    }
  }
  if (harness === 'codex') info('Codex hooks removed; memory and unrelated configuration stay.');
  else if (scope === 'project') {
    await mergeFile(ctx, mcpPath(project), removeMcp, {
      what: `removed MCP server ${MCP_NAME}`,
      preview: `drop mcpServers.${MCP_NAME}; other servers stay`,
      same: `no ${MCP_NAME} entry in .mcp.json`,
    });
  } else if (userEntry(project)) {
    const r = await runCmd(ctx, 'claude', ['mcp', 'remove', MCP_NAME, '--scope', 'user'],
      { what: `removed MCP server ${MCP_NAME}, user scope`, show: `claude mcp remove ${MCP_NAME} --scope user` });
    if (r && r.status !== 0) ctx.problems.push(`run claude mcp remove ${MCP_NAME} --scope user yourself`);
  } else ok(`no ${MCP_NAME} entry in ${rel(ctx, mcpPath(project, 'user'))}`);
  const p = readPid(project);
  if (p?.alive && (await confirm(`   Stop the memory server (pid ${p.pid})?`))) {
    if (stopServer(p.pid)) {
      const went = await gone(p.pid);
      rmSync(pidFile(project), { force: true });
      if (went) ok(`stopped pid ${p.pid}`); else warn(`pid ${p.pid} is still shutting down; check with: status`);
    }
    else warn(`could not stop pid ${p.pid}`);
  } else if (p && !p.alive) rmSync(pidFile(project), { force: true });
  for (const prob of ctx.problems) warn(prob);
  if (!SHARE && existsSync(INSTALL_DIR)) info(`The server and hooks stay in ${rel(ctx, INSTALL_DIR)}; delete that folder to remove them too.`);
  out(`\n   Your memory stays in .memory/. Run ${paint(C.GHOST, `npx spookling init${scope === 'user' ? ' --scope user' : ''}`)} to wire it back.\n`);
  return ctx.problems.length ? 1 : 0;
}
