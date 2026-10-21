// init, status and uninstall. Every file write is shown first and can be skipped with n.
import process from 'node:process';
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { delimiter, dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { C, ask, askHidden, confirm, paint, sleep } from './term.js';
import { play } from './cutscene.js';
import { flyOnce } from './ghost.js';
import {
  HOOK_FILES, MCP_NAME, addHooks, addMcp, hookHandlers, mcpEntry, mcpPath, readJson, removeHooks, removeMcp, settingsPath,
  showHandler, wiring,
} from './hooks.js';

// server/ and hooks/ sit beside cli/ in the repo, or inside the package once bundled.
const PKG = fileURLToPath(new URL('..', import.meta.url));
const ROOT = existsSync(join(PKG, 'server')) ? PKG : resolve(PKG, '..');
export const SERVER_DIR = join(ROOT, 'server'), HOOKS_DIR = join(ROOT, 'hooks');
export const PORT = 7447, SERVER_URL = `http://127.0.0.1:${PORT}`, STATS = `${SERVER_URL}/v1/stats`;
const STEPS = ['Checks', 'Harness', 'Keys', 'Server', 'Wire Claude Code', 'Smoke test', 'Done'];
const WIN = process.platform === 'win32';

const out = (s) => process.stdout.write(s);
const ok = (s) => out(`   ${paint(C.MINT, '✓')} ${s}\n`);
const warn = (s) => out(`   ${paint(C.LILAC, '!')} ${s}\n`);
const info = (s) => out(`   ${paint(C.GREY, s)}\n`);

function step(n) {
  const w = 28, fill = Math.round((w * n) / STEPS.length);
  out(`\n${paint(C.LILAC, '━'.repeat(fill))}${paint(C.GREY, '─'.repeat(w - fill))}  ${paint(C.GREY, `step ${n} of ${STEPS.length}`)}\n`);
  out(`${paint(C.GHOST, `${n}  ${STEPS[n - 1]}`, true)}\n`);
}

const newCtx = (project, scope = 'project') => ({ project, scope, mem: join(project, '.memory'), written: [], skipped: [], problems: [], keys: {} });
// Inside the project: relative. Elsewhere (the user files): from ~, or absolute.
const rel = (ctx, p) => {
  const r = relative(ctx.project, p), h = relative(homedir(), p);
  return !r ? p : !r.startsWith('..') && !isAbsolute(r) ? r : !h.startsWith('..') && !isAbsolute(h) ? join('~', h) : p;
};
const pidFile = (project) => join(project, '.memory', 'server.pid');
const version = (cmd) => {
  const r = spawnSync(cmd, ['--version'], { encoding: 'utf8', timeout: 5000, windowsHide: true });
  return r.status === 0 ? r.stdout.trim().split('\n')[0] : null;
};

// GET /v1/stats; the parsed body, or null if nothing answers within 1.5 s.
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

// Show the write, ask, then write. preview defaults to the content. mode is POSIX only: on Windows the file keeps the
// folder's access rules (your user profile's, inside it), and chmod would change only the read-only flag.
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
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content, mode ? { mode } : undefined);
  if (mode) chmodSync(path, mode);
  ctx.written.push([name, what]);
  ok(`wrote ${name}`);
  return true;
}

// Read JSON, apply fn, and write only if something changed. A file we cannot parse is left alone.
async function mergeFile(ctx, path, fn, { what, preview, same }) {
  let before, after;
  try { before = readJson(path); after = fn(before); } catch (e) {
    warn(e.message);
    ctx.problems.push(`${rel(ctx, path)} left untouched: fix it by hand, then run again`);
    return;
  }
  if (JSON.stringify(before) === JSON.stringify(after)) return ok(same);
  await write(ctx, path, JSON.stringify(after, null, 2) + '\n', { what, preview });
}

// Show a command, ask, then run it (no shell, stdin closed: `claude mcp` waits on an open stdin). Its output is shown.
async function runCmd(ctx, cmd, args, { what, show }) {
  out(`\n   ${paint(C.GHOST, 'run')} ${show}\n`);
  if (!(await confirm('   Run it?'))) {
    ctx.skipped.push(what);
    info('skipped');
    return null;
  }
  const r = spawnSync(cmd, args, { encoding: 'utf8', timeout: 60_000, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  for (const l of `${r.stdout ?? ''}${r.stderr ?? ''}`.trim().split('\n').filter(Boolean)) info(l);
  if (r.status === 0) { ctx.written.push([cmd, what]); ok('done'); } else warn(r.error ? `${cmd}: ${r.error.code ?? r.error.message}` : `${cmd} exited ${r.status}`);
  return r;
}
const shq = (s) => (/^[\w./:@=-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);

// User scope: Claude Code keeps user MCP servers in ~/.claude.json, a file it writes itself, so the wizard only reads it
// and changes it through `claude mcp` (add-json refuses a name that exists, hence remove first).
const userEntry = (project) => { try { return readJson(mcpPath(project, 'user')).mcpServers?.[MCP_NAME]; } catch { return undefined; } };
async function userMcp(ctx, entry) {
  const have = userEntry(ctx.project), json = JSON.stringify(entry);
  if (JSON.stringify(have) === json) return ok(`${rel(ctx, mcpPath(ctx.project, 'user'))} already has the MCP entry`);
  const add = ['mcp', 'add-json', MCP_NAME, json, '--scope', 'user'];
  if (have) {
    const r = await runCmd(ctx, 'claude', ['mcp', 'remove', MCP_NAME, '--scope', 'user'],
      { what: `the old ${MCP_NAME} entry removed`, show: `claude mcp remove ${MCP_NAME} --scope user` });
    if (r?.status !== 0) return void ctx.problems.push(`MCP entry not replaced: run claude mcp remove ${MCP_NAME} --scope user, then init again`);
  }
  const r = await runCmd(ctx, 'claude', add, { what: `MCP server ${MCP_NAME}, user scope`, show: `claude ${add.map(shq).join(' ')}` });
  if (r && r.status !== 0) ctx.problems.push(`MCP entry not added: run the claude mcp add-json line above in your shell`);
}

// Keys: replace our lines, keep anything else in the file.
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
  if (ctx.keys.OPENROUTER_API_KEY) args.push('--model', 'typesafe/jev-router'); // Jev tags when an OpenRouter key was given
  // detached: its own process group (POSIX) or console (Windows), so it outlives this wizard; windowsHide keeps that
  // console from opening a window. PYTHONUTF8: memory-bench reads its data files in the locale encoding (cp1252 on
  // Windows); UTF-8 mode makes that UTF-8 everywhere.
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

// Backoff with jitter, capped at 8 tries (about 15 to 30 s); stops early if the server died.
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

export async function init({ anim = true, project = process.cwd(), scope = 'project' } = {}) {
  await play({ anim });
  const ctx = newCtx(project, scope);
  out(`   Setting up Spookling Memory in ${paint(C.GHOST, project)}${scope === 'user' ? paint(C.GREY, ' (hooks and MCP for all your projects)') : ''}\n`);
  info('Every file is shown before it is written. Answer n to skip it.');

  // 1. Checks
  step(1);
  if (Number(process.versions.node.split('.')[0]) < 18) {
    warn(`Node ${process.version} is too old. Spookling Memory needs Node 18 or newer.`);
    return 1;
  }
  ok(`node ${process.version}`);
  let uv = version('uv');
  if (!uv) {
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
    if (!uv) { warn('Install uv, then run init again.'); return 1; }
  }
  ok(uv);
  const git = version('git');
  if (git) ok(git); else warn('git not found. That is fine; .memory/ is just easier to back up inside a repo.');

  // 2. Harness
  step(2);
  out('   1  Claude Code\n   2  Codex (wiring comes later)\n   3  Spookling app\n');
  const pick = await ask('   Which one?', '1');
  ctx.harness = { 1: 'claude', 2: 'codex', 3: 'app' }[pick] ?? (/codex/i.test(pick) ? 'codex' : /app/i.test(pick) ? 'app' : 'claude');
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

  // 3. Keys
  step(3);
  const envPath = join(ctx.mem, 'keys.env');
  const envText = existsSync(envPath) ? readFileSync(envPath, 'utf8') : '';
  const fresh = {};
  for (const [name, label, optional] of [
    ['ANTHROPIC_API_KEY', 'Anthropic API key (tagger and reader):', false],
    ['OPENROUTER_API_KEY', 'OpenRouter API key for the Jev tagger (optional, Enter skips):', true],
  ]) {
    const have = envValue(envText, name);
    let v = process.env[name];
    if (v) ok(`${name} found in your environment`);
    else if (have) { ok(`${name} already in .memory/keys.env`); ctx.keys[name] = have; continue; }
    else v = await askHidden(`   ${label}`);
    if (v) {
      if (v !== process.env[name]) ok(`${name} received`);
      ctx.keys[name] = v;
      if (v !== have) fresh[name] = v;
      if (name === 'ANTHROPIC_API_KEY' && !v.startsWith('sk-ant-')) warn('That key does not start with sk-ant-. Kept as given.');
    } else if (optional) info(`${name} skipped`);
    else {
      warn(`${name} not set. Put it in .memory/keys.env later; the server needs it to tag and read.`);
      ctx.problems.push('no ANTHROPIC_API_KEY');
    }
  }
  if (Object.keys(fresh).length) {
    await write(ctx, envPath, keysFile(envText, fresh), {
      what: WIN ? 'your keys (kept out of git; mind who can read this folder)' : 'your keys, readable only by you', mode: 0o600,
      preview: Object.keys(fresh).map((k) => `${k}=********`).join('\n'),
    });
  } else if (Object.keys(ctx.keys).length) ok('.memory/keys.env already up to date');
  const giPath = join(ctx.mem, '.gitignore');
  const gi = existsSync(giPath) ? readFileSync(giPath, 'utf8') : '';
  if (!/^keys\.env$/m.test(gi)) {
    await write(ctx, giPath, gi + (gi && !gi.endsWith('\n') ? '\n' : '') + 'keys.env\nserver.pid\nserver.log\n', {
      what: 'keeps your keys out of git',
    });
  }

  // 4. Server
  step(4);
  if (!existsSync(join(SERVER_DIR, 'pyproject.toml'))) {
    warn(`No memory server found at ${SERVER_DIR}.`);
    ctx.problems.push('memory server not found');
  } else {
    info(`uv sync in ${SERVER_DIR}`);
    const r = spawnSync('uv', ['sync'], { cwd: SERVER_DIR, stdio: ['ignore', 'inherit', 'inherit'], timeout: 10 * 60_000, windowsHide: true });
    if (r.status !== 0) {
      warn('uv sync failed (output above).');
      ctx.problems.push('uv sync failed');
    } else {
      ok('server installed');
      if (await stats()) ok(`a server already answers on 127.0.0.1:${PORT}`);
      else if (await confirm('   Start it in the background? (pid in .memory/server.pid, log in .memory/server.log)')) {
        ctx.pid = startServer(ctx);
        if (ctx.pid) {
          ok(`started, pid ${ctx.pid}`);
          ctx.written.push(['memory server', `127.0.0.1:${PORT}, pid ${ctx.pid}`]);
        } else { warn('could not start uv'); ctx.problems.push('server did not start'); }
      } else ctx.skipped.push('starting the server');
    }
  }

  // 5. Wire Claude Code
  step(5);
  if (ctx.harness !== 'claude') info('Codex: nothing to wire yet (see .memory/CODEX-TODO.md).');
  else {
    const handlers = hookHandlers(HOOKS_DIR);
    for (const f of Object.values(HOOK_FILES)) {
      if (!existsSync(join(HOOKS_DIR, f))) {
        warn(`${join(HOOKS_DIR, f)} is missing; Claude Code will report it on each event.`);
        ctx.problems.push(`hook script ${f} missing`);
      }
    }
    if (!version('node')) warn('`node` is not on your PATH here. The hooks run `node`, so Claude Code needs it on its PATH.');
    const sp = settingsPath(project, scope);
    await mergeFile(ctx, sp, (s) => addHooks(s, handlers), {
      what: 'hooks: PreCompact, UserPromptSubmit, Stop',
      preview: Object.entries(handlers).map(([ev, h]) => `+ hooks.${ev.padEnd(17)} ${showHandler(h)}`).join('\n')
        + '\n  merged in; nothing else in the file changes',
      same: `${rel(ctx, sp)} already has the hooks`,
    });
    const entry = mcpEntry(SERVER_DIR, SERVER_URL);
    if (scope === 'project') {
      await mergeFile(ctx, mcpPath(project), (c) => addMcp(c, entry), {
        what: `MCP server ${MCP_NAME}`,
        preview: `+ mcpServers.${MCP_NAME}  (stdio) ${entry.command} ${entry.args.join(' ')}\n    env SPOOKLING_MEMORY_URL=${SERVER_URL}\n  other servers stay`,
        same: '.mcp.json already has the MCP entry',
      });
    } else await userMcp(ctx, entry);
  }

  // 6. Smoke test
  step(6);
  out(`   ${paint(C.GREY, `GET ${STATS} `)}`);
  const res = await waitForStats(ctx);
  out('\n');
  if (res) ok(`the server answered: ${JSON.stringify(res).slice(0, 72)}`);
  else {
    warn(`nothing answered on 127.0.0.1:${PORT}. Look in .memory/server.log.`);
    ctx.problems.push('smoke test failed');
  }

  // 7. Done
  step(7);
  await flyOnce(process.stdout, { anim });
  for (const [name, what] of ctx.written) out(`   ${paint(C.MINT, `✓ ${name.padEnd(24)}`)} ${paint(C.GREY, what)}\n`);
  for (const s of ctx.skipped) out(`   ${paint(C.GREY, `- skipped ${s}`)}\n`);
  for (const p of ctx.problems) out(`   ${paint(C.LILAC, '!')} ${p}\n`);
  let wired = true;
  if (ctx.harness === 'claude') {
    try {
      const w = wiring(readJson(settingsPath(project, scope)), readJson(mcpPath(project, scope)));
      wired = w.events.length === 3 && w.mcp;
    } catch { wired = false; }
  }
  if (wired && !ctx.problems.length) {
    out(`\n   ${paint(C.MINT, 'Spookling Memory is in.', true)}\n\n`);
    return 0;
  }
  out(`\n   Not quite in yet. Fix the lines marked ! (or the skipped writes) and run init again.\n\n`);
  return 1;
}

export async function status(project = process.cwd()) {
  const line = (label, text, good) => out(`   ${good ? paint(C.MINT, '✓') : paint(C.LILAC, '!')} ${label.padEnd(8)} ${text}\n`);
  const ctx = newCtx(project);
  out(`${paint(C.GHOST, 'Spookling Memory', true)}  ${paint(C.GREY, project)}\n`);
  const s = await stats(), p = readPid(project);
  line('server', s ? `up on 127.0.0.1:${PORT}${p?.alive ? `, pid ${p.pid}` : ''}`
    : `down${p && !p.alive ? ` (stale .memory/server.pid: pid ${p.pid} is not running)` : ''}`, !!s);
  // Both scopes count: project files, or the user files that reach every project.
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
  const envPath = join(project, '.memory', 'keys.env');
  if (existsSync(envPath)) {
    const names = [...readFileSync(envPath, 'utf8').matchAll(/^([A-Z_][A-Z0-9_]*)=./gm)].map((m) => m[1]);
    const mode = WIN ? null : statSync(envPath).mode & 0o777;
    line('keys', `${names.join(', ') || 'none'} in .memory/keys.env${mode && mode !== 0o600 ? ` (mode ${mode.toString(8)}, should be 600)` : ''}`,
      names.includes('ANTHROPIC_API_KEY') && (!mode || mode === 0o600));
  } else line('keys', 'no .memory/keys.env', !!process.env.ANTHROPIC_API_KEY);
  return s && (claude || codex) && !missing.length ? 0 : 1;
}

// Stop the server and the Python under it. POSIX: it was started detached, so its pid leads a process group.
// Windows: process.kill would end uv.exe alone; taskkill /T ends the tree.
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
    if (stopServer(p.pid)) { rmSync(pidFile(project), { force: true }); ok(`stopped pid ${p.pid}`); }
    else warn(`could not stop pid ${p.pid}`);
  } else if (p && !p.alive) rmSync(pidFile(project), { force: true });
  for (const prob of ctx.problems) warn(prob);
  out(`\n   Your memory stays in .memory/. Run ${paint(C.GHOST, `npx @spookling/memory init${scope === 'user' ? ' --scope user' : ''}`)} to wire it back.\n`);
  return ctx.problems.length ? 1 : 0;
}
