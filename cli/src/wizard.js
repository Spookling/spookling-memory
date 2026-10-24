// init, status and uninstall; demo is init with dry: the same prompts, answered by a script, nothing written or run.
import process from 'node:process';
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, closeSync, cpSync, existsSync, mkdirSync, openSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { basename, delimiter, dirname, isAbsolute, join, relative } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { C, DOWN, MODE, UP, askHidden, bail, choose, confirm, paint, press, setFeed, setRaw, sleep, write as out } from './term.js';
import { play } from './cutscene.js';
import { flyOnce } from './ghost.js';
import { fits, stage } from './stage.js';
import {
  HOOK_FILES, MCP_NAME, addHooks, addMcp, hookHandlers, mcpEntry, mcpPath, readJson, removeHooks, removeMcp, settingsPath,
  wiring, wslShare,
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
export const PORT = 7447, SERVER_URL = `http://127.0.0.1:${PORT}`, STATS = `${SERVER_URL}/v1/stats`;
const STEPS = ['Checks', 'Harness', 'Tagger', 'Server', 'Wire Claude Code', 'Smoke test', 'Done'];
export const DEMO_LABEL = 'demo, nothing is written';
const IGNORE = ['keys.env', 'server.pid', 'server.log', 'cache.sqlite*', 'vectors.sqlite*'];

// at most 52 characters each, so the bubble fits beside the ghost at 80 columns (tested)
export const SAY = {
  1: 'Hello. First I check what this machine has.',
  2: 'Pick the tool you talk to. I will sit beside it.',
  3: 'A small model tags each chat for me. Pick one.',
  4: 'Moving in. Everything I remember goes in .memory/',
  5: 'I will remind Claude of what you told it before.',
  codex: 'Nothing to wire for Codex yet. I will wait.',
  6: 'A quick knock, to check I am home.',
  7: 'That is all. Carry on as you always do.',
  fail: 'Sorry, that failed. The line marked ! says why.',
};

export const TAGGERS = [
  { label: 'Jev through an OpenRouter key (cheapest, about $0.002 a chat)', model: 'typesafe/jev-router', decider: 'typesafe/jev-1.14', key: 'OPENROUTER_API_KEY', ask: 'OpenRouter API key for Jev:' },
  { label: 'Anthropic API key (Haiku 4.5)', model: 'anthropic/claude-haiku-4.5', key: 'ANTHROPIC_API_KEY', ask: 'Anthropic API key:' },
  { label: 'Your Codex subscription (the codex CLI, logged in)', model: 'codex', key: null },
];
export const taggerText = (t) => (t.via === 'codex' ? 'your Codex subscription (codex exec)'
  : `${t.model} through ${t.via === 'anthropic' ? 'the Anthropic API' : 'OpenRouter'}`) + (t.decider ? `, decisions by ${t.decider}` : '');

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
  `      "type": ${JSON.stringify(h.type)}, "command": ${JSON.stringify(h.command)},`,
  `      "args": ${JSON.stringify(h.args)}`,
  '    }] }]',
].join('\n')).join(',\n'), '  }', '}'].join('\n');
export const mcpJson = (entry) => ['{', '  "mcpServers": {', `    ${JSON.stringify(MCP_NAME)}: {`,
  ...Object.entries(entry).map(([k, v], i, all) => `      ${JSON.stringify(k)}: ${JSON.stringify(v)}${i < all.length - 1 ? ',' : ''}`),
  '    }', '  }', '}'].join('\n');

const SAMPLE = { n_events: 0, n_facts: 0, n_rows: 0, reset: null };

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
  project, scope, mem: join(project, '.memory'), written: [], skipped: [], problems: [], keys: {},
  dry: false, say: async () => {}, beat: async () => {},
});
const rel = (ctx, p) => {
  const r = relative(ctx.project, p), h = relative(homedir(), p);
  return !r ? p : !r.startsWith('..') && !isAbsolute(r) ? r : !h.startsWith('..') && !isAbsolute(h) ? join('~', h) : p;
};
const pidFile = (project) => join(project, '.memory', 'server.pid');
const version = (cmd) => {
  const r = spawnSync(cmd, ['--version'], { encoding: 'utf8', timeout: 5000, windowsHide: true });
  return r.status === 0 ? r.stdout.trim().split('\n')[0] : null;
};

export const stats = () => new Promise((res) => {
  const req = http.get(STATS, { timeout: 1500 }, (r) => {
    let body = '';
    r.setEncoding('utf8');
    r.on('data', (d) => { body += d; });
    r.on('end', () => {
      if (r.statusCode !== 200) return res(null);
      try { res(JSON.parse(body)); } catch { res({ body: body.slice(0, 80) }); }
    });
  });
  req.on('timeout', () => req.destroy());
  req.on('error', () => res(null));
});

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
async function runCmd(ctx, cmd, args, { what, show }) {
  out(`\n   ${paint(C.GHOST, 'run')} ${show}\n`);
  if (!(await confirm('   Run it?'))) {
    ctx.skipped.push(what);
    info('skipped');
    return null;
  }
  if (ctx.dry) { ctx.written.push([cmd, what]); info('demo: not run'); return { status: 0 }; }
  const r = spawnSync(cmd, args, { encoding: 'utf8', timeout: 60_000, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  for (const l of `${r.stdout ?? ''}${r.stderr ?? ''}`.trim().split('\n').filter(Boolean)) info(l);
  if (r.status === 0) { ctx.written.push([cmd, what]); ok('done'); } else warn(r.error ? `${cmd}: ${r.error.code ?? r.error.message}` : `${cmd} exited ${r.status}`);
  return r;
}
const shq = (s) => (/^[\w./:@=-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);

// Claude Code writes ~/.claude.json itself, so only `claude mcp` changes it; add-json refuses a name that exists
const userEntry = (project) => { try { return readJson(mcpPath(project, 'user')).mcpServers?.[MCP_NAME]; } catch { return undefined; } };
async function userMcp(ctx, entry) {
  const have = ctx.dry ? undefined : userEntry(ctx.project), json = JSON.stringify(entry);
  if (JSON.stringify(have) === json) return ok(`${rel(ctx, mcpPath(ctx.project, 'user'))} already has the MCP entry`);
  const add = ['mcp', 'add-json', MCP_NAME, json, '--scope', 'user'];
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
export function install(from, dir) {
  for (const d of ['server', 'hooks']) cpSync(join(from, d), join(dir, d), { recursive: true, filter: (p) => !LEAVE.has(basename(p)) });
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

function startServer(ctx) {
  mkdirSync(ctx.mem, { recursive: true });
  const log = openSync(join(ctx.mem, 'server.log'), 'a');
  const args = ['run', '--project', SERVER_DIR, 'spookling-memory', 'serve', '--folder', '.memory', '--port', String(PORT)];
  if (ctx.model) args.push('--model', ctx.model);
  if (ctx.decider) args.push('--decider', ctx.decider);
  // detached, so it outlives the wizard; PYTHONUTF8, or Python on Windows reads files as cp1252
  const child = spawn('uv', args, {
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
  for (let i = 0; i < 8; i++) {
    const r = await stats();
    if (r) return r;
    if (!ctx.pid || !readPid(ctx.project)?.alive) return null;
    out(paint(C.GREY, '.'));
    await sleep(d + Math.random() * d);
    d = Math.min(d * 2, 3000);
  }
  return null;
}

export async function init({ anim = true, project = process.cwd(), scope = 'project', dry = false, fast = false } = {}) {
  const label = dry ? DEMO_LABEL : '';
  await play({ anim: anim && !fast, keep: fits(anim), label });
  const ctx = newCtx(project, scope);
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
  info('Every file is shown before it is written. Answer n to skip it.');

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

  await step(ctx, 2);
  const pick = await choose('   Which one?', ['Claude Code', 'Codex (wiring comes later)', 'Spookling app']);
  ctx.harness = ['claude', 'codex', 'app'][pick];
  if (ctx.harness === 'app') {
    ok('Spookling Memory ships with the app. Nothing to install here.');
    return 0;
  }
  ok(ctx.harness === 'claude' ? 'Claude Code' : 'Codex');
  if (ctx.harness === 'codex') {
    await write(ctx, join(ctx.mem, 'CODEX-TODO.md'), [
      '# Codex: not wired yet', '',
      `Spookling Memory runs for this project (server on 127.0.0.1:${PORT}, data in .memory/),`,
      'but the Codex hooks are not built yet. When they land, run `npx @spookling/memory init`',
      'again and pick Codex.', '',
    ].join('\n'), { what: 'Codex TODO note' });
  }

  // the demo reads neither the environment nor keys.env, so the key prompt shows
  await step(ctx, 3);
  const t = TAGGERS[await choose('   How should the tagger run?', TAGGERS.map((x) => x.label))];
  ctx.decider = t.decider || null;
  ctx.model = t.model;
  ok(t.label);
  const envPath = join(ctx.mem, 'keys.env');
  if (t.key) {
    const envText = !ctx.dry && existsSync(envPath) ? readFileSync(envPath, 'utf8') : '';
    const name = t.key, have = envValue(envText, name);
    let v = ctx.dry ? undefined : process.env[name];
    if (v) ok(`${name} found in your environment`);
    else if (have) { ok(`${name} already in .memory/keys.env`); v = have; }
    else if ((v = await askHidden(`   ${t.ask}`))) ok(`${name} received`);
    if (!v) {
      warn(`${name} not set. Put it in .memory/keys.env later; the server needs it to tag.`);
      await fail(ctx, `no ${name}`);
    } else {
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
    const c = codexLogin();
    out(`   ${paint(C.GREY, 'codex login status')}\n`);
    if (c.ok) {
      ok(c.line || 'logged in');
      if (/api key/i.test(c.line)) warn('That login is an API key, so tagging bills the API, not your plan. codex login switches it.');
      info('That checks a login is saved, not that it still works; the first tag does (errors go to .memory/server.log).');
      info('Each chat then takes several seconds to tag and uses a little of your plan (Plus or higher); its terms apply.');
    } else {
      warn(c.line || 'codex did not answer');
      info('Install the codex CLI (npm install -g @openai/codex), run codex login, then run init again.');
      await fail(ctx, 'codex is not installed or not logged in');
    }
  }
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
      const up = !ctx.dry && await stats();
      if (up?.tagger && up.tagger.model !== ctx.model) {
        const p = readPid(ctx.project);
        warn(`a server already answers on 127.0.0.1:${PORT}, tagging with ${taggerText(up.tagger)}.`);
        info(`Stop it${p?.alive ? ` (pid ${p.pid})` : ''}, then run init again to switch the tagger.`);
        await fail(ctx, 'the running server has another tagger');
      } else if (up) ok(`a server already answers on 127.0.0.1:${PORT}`);
      else if (await confirm('   Start it in the background? (pid in .memory/server.pid, log in .memory/server.log)')) {
        if (ctx.dry) {
          info('demo: not started');
          ctx.written.push(['memory server', `127.0.0.1:${PORT}`]);
        } else {
          ctx.pid = startServer(ctx);
          if (ctx.pid) {
            ok(`started, pid ${ctx.pid}`);
            ctx.written.push(['memory server', `127.0.0.1:${PORT}, pid ${ctx.pid}`]);
          } else { warn('could not start uv'); await fail(ctx, 'server did not start'); }
        }
      } else ctx.skipped.push('starting the server');
    }
  }

  await step(ctx, 5, ctx.harness === 'claude' ? SAY[5] : SAY.codex);
  if (ctx.harness !== 'claude') info('Codex: nothing to wire yet (see .memory/CODEX-TODO.md).');
  else {
    const handlers = hookHandlers(HOOKS_DIR), hooksAt = ctx.dry ? join(ROOT, 'hooks') : HOOKS_DIR; // the demo copies nothing
    for (const f of Object.values(HOOK_FILES)) {
      if (!existsSync(join(hooksAt, f))) {
        warn(`${join(hooksAt, f)} is missing; Claude Code will report it on each event.`);
        await fail(ctx, `hook script ${f} missing`);
      }
    }
    if (!version('node')) warn('`node` is not on your PATH here. The hooks run `node`, so Claude Code needs it on its PATH.');
    const sp = settingsPath(project, scope);
    await mergeFile(ctx, sp, (s) => addHooks(s, handlers), {
      what: 'hooks: PreCompact, UserPromptSubmit, Stop',
      preview: `${hooksJson(handlers)}\n  merged in; nothing else in the file changes`,
      same: `${rel(ctx, sp)} already has the hooks`,
    });
    const entry = mcpEntry(SERVER_DIR, SERVER_URL);
    if (scope === 'project') {
      await mergeFile(ctx, mcpPath(project), (c) => addMcp(c, entry), {
        what: `MCP server ${MCP_NAME}`,
        preview: `${mcpJson(entry)}\n  merged in; other servers stay`,
        same: '.mcp.json already has the MCP entry',
      });
    } else await userMcp(ctx, entry);
  }

  await step(ctx, 6);
  out(`   ${paint(C.GREY, `GET ${STATS} `)}`);
  let res;
  if (ctx.dry) {
    for (let i = 0; i < 3; i++) { await ctx.beat(500); out(paint(C.GREY, '.')); }
    res = SAMPLE;
  } else res = await waitForStats(ctx);
  out('\n');
  const short = (j) => (j.length > 50 ? `${j.slice(0, 49)}…` : j);
  if (res) {
    ok(`the server answered: ${short(JSON.stringify(res))}`);
    if (ctx.dry) info('demo: a sample reply');
  } else {
    warn(`nothing answered on 127.0.0.1:${PORT}. ${SHARE ? 'Start the server in WSL (the command in step 4).' : 'Look in .memory/server.log.'}`);
    await fail(ctx, 'smoke test failed');
  }

  await step(ctx, 7, null);
  await ctx.beat(1000);
  let wired = true;
  if (ctx.harness === 'claude' && !ctx.dry) {
    try {
      const w = wiring(readJson(settingsPath(project, scope)), readJson(mcpPath(project, scope)));
      wired = w.events.length === 3 && w.mcp;
    } catch { wired = false; }
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

export async function status(project = process.cwd(), get = stats) {
  const line = (label, text, good) => out(`   ${good ? paint(C.MINT, '✓') : paint(C.LILAC, '!')} ${label.padEnd(8)} ${text}\n`);
  const ctx = newCtx(project);
  out(`${paint(C.GHOST, 'Spookling Memory', true)}  ${paint(C.GREY, project)}\n`);
  const s = await get(), p = readPid(project);
  line('server', s ? `up on 127.0.0.1:${PORT}${p?.alive ? `, pid ${p.pid}` : ''}`
    : `down${p && !p.alive ? ` (stale .memory/server.pid: pid ${p.pid} is not running)` : ''}`, !!s);
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
  line('hooks', where.events.join('; ') || 'none in .claude/settings.json or ~/.claude/settings.json', events.length === 3);
  line('mcp', where.mcp.join('; ') || 'no entry in .mcp.json or ~/.claude.json', where.mcp.length > 0);
  const missing = Object.values(HOOK_FILES).filter((f) => !existsSync(join(HOOKS_DIR, f)));
  if (events.length && missing.length) line('scripts', `missing in ${HOOKS_DIR}: ${missing.join(', ')}`, false);
  const codex = existsSync(join(project, '.memory', 'CODEX-TODO.md'));
  const claude = events.length === 3 && where.mcp.length > 0;
  line('harness', claude ? 'Claude Code' : codex ? 'Codex (TODO note only, no wiring yet)' : 'none wired', claude || codex);
  line('tagger', s?.tagger ? taggerText(s.tagger) : 'unknown (the server is down, or older than this wizard)', !!s?.tagger);
  const need = { openrouter: 'OPENROUTER_API_KEY', anthropic: 'ANTHROPIC_API_KEY' }[s?.tagger?.via];
  const envPath = join(project, '.memory', 'keys.env');
  if (existsSync(envPath)) {
    const names = [...readFileSync(envPath, 'utf8').matchAll(/^([A-Z_][A-Z0-9_]*)=./gm)].map((m) => m[1]);
    const mode = WIN ? null : statSync(envPath).mode & 0o777;
    line('keys', `${names.join(', ') || 'none'} in .memory/keys.env${mode && mode !== 0o600 ? ` (mode ${mode.toString(8)}, should be 600)` : ''}`,
      (!need || names.includes(need)) && (!mode || mode === 0o600));
  } else line('keys', need ? `no .memory/keys.env (the tagger needs ${need})` : 'no .memory/keys.env', !need);
  return s && (claude || codex) && !missing.length ? 0 : 1;
}

// the whole tree: started detached, its pid leads a process group; on Windows taskkill /T
function stopServer(pid) {
  if (WIN) return spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }).status === 0;
  try { process.kill(-pid, 'SIGTERM'); return true; } catch { try { process.kill(pid, 'SIGTERM'); return true; } catch { return false; } }
}

export async function uninstall(project = process.cwd(), { scope = 'project' } = {}) {
  const ctx = newCtx(project, scope);
  const sp = settingsPath(project, scope);
  out(`${paint(C.GHOST, 'Removing Spookling Memory wiring', true)}  ${paint(C.GREY, scope === 'user' ? `${project} (user scope)` : project)}\n`);
  await mergeFile(ctx, sp, removeHooks, {
    what: 'removed the Spookling hooks',
    preview: 'drop the Spookling PreCompact, UserPromptSubmit and Stop hooks; everything else stays',
    same: `no Spookling hooks in ${rel(ctx, sp)}`,
  });
  if (scope === 'project') {
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
      // SIGTERM is graceful (uvicorn closes the store first): wait up to 10 s
      const gone = () => { try { process.kill(p.pid, 0); return false; } catch (e) { return e.code !== 'EPERM'; } };
      for (let i = 0; i < 50 && !gone(); i++) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 200);
      rmSync(pidFile(project), { force: true });
      if (gone()) ok(`stopped pid ${p.pid}`); else warn(`pid ${p.pid} is still shutting down; check with: status`);
    }
    else warn(`could not stop pid ${p.pid}`);
  } else if (p && !p.alive) rmSync(pidFile(project), { force: true });
  for (const prob of ctx.problems) warn(prob);
  if (!SHARE && existsSync(INSTALL_DIR)) info(`The server and hooks stay in ${rel(ctx, INSTALL_DIR)}; delete that folder to remove them too.`);
  out(`\n   Your memory stays in .memory/. Run ${paint(C.GHOST, `npx @spookling/memory init${scope === 'user' ? ' --scope user' : ''}`)} to wire it back.\n`);
  return ctx.problems.length ? 1 : 0;
}
