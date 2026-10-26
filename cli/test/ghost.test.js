import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync, utimesSync } from 'node:fs';
import { createRequire } from 'node:module';
import http from 'node:http';
import { tmpdir } from 'node:os';
import path, { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { addHooks, addStatusLine, hookHandlers, isOurStatus, removeHooks, removeStatusLine, statusCommand } from '../src/hooks.js';

const HOOKS = fileURLToPath(new URL('../../hooks/', import.meta.url));
const LINE = join(HOOKS, 'status-line.js');
const DIR = '/opt/spookling-memory/hooks';
const strip = (s) => s.replace(/\x1b\[[0-9;]*m/g, '');

// a project with .memory and a home of its own, so nothing touches the real ~/.memory
function project(pulse) {
  const d = mkdtempSync(join(tmpdir(), 'spk-ghost-'));
  mkdirSync(join(d, '.memory'));
  if (pulse) writeFileSync(join(d, '.memory', 'pulse.json'), JSON.stringify({ at: Date.now(), session: 's1', up: true, ...pulse }));
  return d;
}
// The tests up to the ghost look's are the older looks': pixel unless they name another. The default is the ghost look.
const line = (d, { env = {}, args = [], session = 's1', look = 'pixel' } = {}) => spawnSync(process.execPath, [LINE, ...(look && !args.includes('--look') ? ['--look', look] : []), ...args], {
  input: JSON.stringify({ session_id: session, cwd: d, workspace: { current_dir: d, project_dir: d } }),
  env: { PATH: process.env.PATH, HOME: d, USERPROFILE: d, COLORTERM: 'truecolor', ...env }, encoding: 'utf8', timeout: 15000,
}).stdout;

test('statusLine: none before, so ours with a 5 s refresh; uninstall leaves no statusLine', () => {
  const s = { model: 'opus' };
  const out = addStatusLine(s, DIR, path.posix);
  assert.deepEqual(out.statusLine, { type: 'command', command: `node '${DIR}/status-line.js'`, refreshInterval: 5 });
  assert.ok(isOurStatus(out.statusLine));
  assert.deepEqual(addStatusLine(out, DIR, path.posix), out, 'twice is a no-op');
  assert.deepEqual(removeStatusLine(out), s);
  assert.deepEqual(removeStatusLine(s), s);
});

test('statusLine: yours runs first through --then, keeps its own keys, and comes back exactly on uninstall', () => {
  const mine = { type: 'command', command: "~/.claude/statusline.sh --name 'it''s' \"$HOME\"", padding: 2 };
  const s = { statusLine: mine, model: 'opus' };
  const out = addStatusLine(s, DIR, path.posix);
  assert.equal(out.statusLine.padding, 2);
  assert.equal(out.statusLine.refreshInterval, undefined, 'your cadence stays yours');
  assert.match(out.statusLine.command, /^node '\/opt\/spookling-memory\/hooks\/status-line\.js' --then '/);
  // a new install path keeps the status line you had, never wraps ours in ours
  const moved = addStatusLine(out, '/home/a/.spookling-memory/0.2.0/hooks', path.posix);
  assert.equal(moved.statusLine.command.match(/--then/g).length, 1);
  assert.deepEqual(removeStatusLine(moved), s);
  assert.deepEqual(removeStatusLine(removeHooks(addStatusLine(addHooks(s, hookHandlers(DIR, path.posix)), DIR, path.posix))), s);
  assert.throws(() => addStatusLine({ statusLine: 'x' }, DIR), /not an object/);
});

test('statusLine: the shell hands status-line.js your command unchanged (sh)', { skip: process.platform === 'win32' && 'POSIX sh' }, () => {
  const prev = "printf '%s|' \"$HOME\" it's `x` $(y)", d = mkdtempSync(join(tmpdir(), "spk-it's $dir-"));
  writeFileSync(join(d, 'status-line.js'), 'console.log(JSON.stringify(process.argv.slice(2)))');
  const r = spawnSync('sh', ['-c', statusCommand(d, prev, path.posix)], { encoding: 'utf8' });
  assert.deepEqual(JSON.parse(r.stdout), ['--then', prev], r.stderr);
});

test('statusLine on Windows: forward slashes in double quotes; what Git Bash and PowerShell read differently is refused', () => {
  const dir = "C:\\Users\\Jane O'Neil\\AppData\\Local\\spookling-memory\\0.1.1\\hooks";
  assert.equal(statusCommand(dir, null, path.win32), `node "C:/Users/Jane O'Neil/AppData/Local/spookling-memory/0.1.1/hooks/status-line.js"`);
  const s = addStatusLine({ statusLine: { type: 'command', command: 'powershell -NoProfile -File C:/x/line.ps1' } }, dir, path.win32);
  assert.match(s.statusLine.command, / --then "powershell -NoProfile -File C:\/x\/line\.ps1"$/);
  assert.equal(removeStatusLine(s).statusLine.command, 'powershell -NoProfile -File C:/x/line.ps1');
  assert.throws(() => statusCommand(dir, 'echo $HOME', path.win32), /--no-ghost/);
  assert.throws(() => statusCommand('C:\\Users\\$me\\hooks', null, path.win32), /--no-ghost/);
});

test('the ghost line: idle, recalled, saved, compacted, asleep; another session\'s news is not shown', () => {
  assert.match(strip(line(project())), /^[▀▄]●●[▀▄] remembering you\n$/);
  const said = (pulse, opts) => strip(line(project(pulse), opts)).slice(5).trim();
  assert.equal(said({ event: 'prompt', facts: 3, rules: 1 }), '3 facts · 1 rule recalled');
  assert.equal(said({ event: 'prompt', chats: 2, events: 1, rules: 0 }), '1 event · 2 past chats recalled');
  assert.equal(said({ event: 'prompt', rules: 2 }), 'listening', 'standing rules alone are not news');
  assert.equal(said({ event: 'stop' }), 'saved');
  assert.equal(said({ event: 'compact', rules: 2, lines: 40, kept: 3 }), 'kept your 2 rules and 38 lines');
  assert.equal(said({ event: 'compact', rules: 0, lines: 1 }), 'kept 1 line through compaction');
  assert.equal(said({ event: 'prompt', up: false }), 'asleep, server off');
  assert.equal(said({ event: 'prompt', facts: 3 }, { session: 's2' }), 'remembering you');
  assert.equal(said({ event: 'prompt', facts: 3, at: Date.now() - 11 * 60_000 }), 'remembering you', 'old news fades');
});

test('the ghost line: pixels in truecolour, the emoji otherwise, no escapes under NO_COLOR', () => {
  const d = project({ event: 'prompt', facts: 3, rules: 1, chars: 5000, cap: 10000 });
  const tc = line(d);
  assert.match(tc, /\x1b\[38;2;178;138;255m[▀▄]\x1b\[0m\x1b\[38;2;78;25;207;48;2;223;212;255m●/);
  assert.match(line(d, { env: { COLORTERM: '' } }), /^👻 \x1b\[38;5;\d+m3 facts/);
  assert.equal(line(d, { env: { NO_COLOR: '1' } }), '👻 3 facts · 1 rule recalled\n');
  assert.equal(line(d, { env: { NO_COLOR: '1' }, args: ['--look', 'meter'] }), '👻 [###...] 3 facts · 1 rule recalled\n');
  assert.match(strip(line(d, { args: ['--look', 'meter'] })), /^.{4} ███    3 facts/);
  assert.equal(strip(line(d, { args: ['--look', 'emoji'] })), '👻 3 facts · 1 rule recalled\n');
  assert.match(strip(line(project({ up: false }))), /^▄--▄ asleep, server off/);
});

test('look two-line: hat, eyes and hem over two rows with the words on the first; one emoji line without truecolour', () => {
  const d = project({ event: 'prompt', facts: 3, rules: 1 }), two = ['--look', 'two-line'];
  assert.deepEqual(strip(line(d, { args: two })).split('\n'), ['▄▀▀▀▄ 3 facts · 1 rule recalled', '▀▀▀▀▀', '']);
  assert.match(line(d, { args: two }), /\x1b\[38;2;78;25;207;48;2;2;2;2m▀/, 'a purple hat over a dark eye');
  assert.equal(line(d, { args: two, env: { NO_COLOR: '1' } }), '👻 3 facts · 1 rule recalled\n');
  assert.equal(line(d, { args: two, env: { COLORTERM: '' } }).split('\n').length, 2, 'one row and the newline');
  assert.deepEqual(strip(line(d, { args: [...two, '--then', 'printf "[Opus] app\\n"'] })).split('\n'),
    ['[Opus] app', '▄▀▀▀▄ 3 facts · 1 rule recalled', '▀▀▀▀▀', '']);
});

test('the ghost line: your old status line first, two spaces, then the ghost', () => {
  const d = project({ event: 'stop' });
  assert.equal(line(d, { env: { NO_COLOR: '1' }, args: ['--then', 'printf "[Opus] app\\n"'] }), '[Opus] app  👻 saved\n');
  assert.equal(line(d, { env: { NO_COLOR: '1' }, args: ['--then', 'exit 3'] }), '👻 saved\n', 'a failing one is left out');
});

test('the ghost bobs between seconds and the words stay put', async () => {
  const d = project({ event: 'prompt', facts: 1 });
  const at = () => new Promise((r) => setTimeout(r, 1100 - (Date.now() % 1000))); // just past the next second
  const two = { args: ['--look', 'two-line'] };
  await at();
  const [a, ta] = [strip(line(d)), line(d, two).split('\n')];
  await at();
  const [b, tb] = [strip(line(d)), line(d, two).split('\n')];
  assert.notEqual(a.slice(0, 4), b.slice(0, 4));
  assert.equal(a.slice(4), b.slice(4));
  assert.notEqual(ta[1], tb[1], 'the hem sways');
  assert.equal(ta[0], tb[0]);
});

// node's own start varies by host (19 ms to 90 ms here), so this times what the script adds to it
test('the ghost line takes under 50 ms beyond starting node', () => {
  const d = project({ event: 'prompt', facts: 3, rules: 1 });
  const ms = (f) => Math.min(...Array.from({ length: 7 }, () => { const t = process.hrtime.bigint(); f(); return Number(process.hrtime.bigint() - t) / 1e6; }));
  const bare = ms(() => spawnSync(process.execPath, ['-e', '0'])), ours = ms(() => line(d)), two = ms(() => line(d, { args: ['--look', 'two-line'] }));
  const ghost = ms(() => line(d, { look: null }));
  for (const t of [ours, two, ghost]) assert.ok(t - bare < 50, `fastest of 7: ${t.toFixed(1)} ms, node -e 0 ${bare.toFixed(1)} ms`);
});

test('status reports the ghost; uninstall takes it out and puts your status line back', () => {
  const d = mkdtempSync(join(tmpdir(), 'spk-ghost-wiz-'));
  const url = (f) => JSON.stringify(new URL(`../src/${f}`, import.meta.url).href);
  const script = `
    import assert from 'node:assert/strict';
    import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
    import { join } from 'node:path';
    import { status, uninstall, HOOKS_DIR } from ${url('wizard.js')};
    import { addHooks, addStatusLine, hookHandlers, settingsPath } from ${url('hooks.js')};
    import { setFeed, press } from ${url('term.js')};
    const project = process.cwd(), sp = settingsPath(project), mine = { type: 'command', command: 'echo mine', padding: 1 };
    mkdirSync(join(project, '.claude'), { recursive: true });
    writeFileSync(sp, JSON.stringify(addStatusLine(addHooks({ statusLine: mine }, hookHandlers(HOOKS_DIR)), HOOKS_DIR)));
    await status(project, async () => null);
    setFeed(() => { press('y'); press('\\r'); });
    assert.equal(await uninstall(project), 0);
    setFeed(null);
    assert.deepEqual(JSON.parse(readFileSync(sp, 'utf8')), { statusLine: mine });
    await status(project, async () => null);
  `;
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    cwd: d, encoding: 'utf8', timeout: 30000,
    env: { ...process.env, HOME: d, USERPROFILE: d, LOCALAPPDATA: d, CLAUDE_CONFIG_DIR: join(d, 'claude-home'), NO_COLOR: '1' },
  });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /✓ ghost +in the status line \(\.claude[\\/]settings\.json\)/);
  assert.match(r.stdout, /and the ghost \(your own status line comes back\)/);
  assert.match(r.stdout, /✓ ghost +not in the status line/);
});

// a fake memory server: the reply body plus the pulse header the real one sends
async function serve(reply, pulse) {
  const srv = http.createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      res.setHeader('content-type', 'application/json');
      if (pulse) res.setHeader('x-spookling-pulse', JSON.stringify(pulse));
      res.end(JSON.stringify(reply));
    });
  });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  return { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
}
const hook = (file, url, d, input) => new Promise((res) => {
  const c = spawn(process.execPath, [join(HOOKS, file)], { env: { PATH: process.env.PATH, HOME: d, USERPROFILE: d, SPOOKLING_MEMORY_URL: url } });
  let out = '';
  c.stdout.on('data', (x) => { out += x; });
  c.on('close', (code) => res({ code, out, pulse: existsSync(join(d, '.memory', 'pulse.json')) ? JSON.parse(readFileSync(join(d, '.memory', 'pulse.json'), 'utf8')) : null }));
  c.stdin.end(JSON.stringify({ session_id: 's1', cwd: d, ...input }));
});

test('UserPromptSubmit: the pulse gets the counts, and the transcript a line only when this prompt recalled something', async () => {
  const ctx = { hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: '<recalled-data>…</recalled-data>' } };
  let s = await serve(ctx, { facts: 3, rules: 1, events: 0, chats: 0, chars: 31, cap: 10000 });
  try {
    const d = project(), r = await hook('user-prompt-submit.js', s.url, d, { prompt: 'what car?' });
    assert.equal(r.code, 0);
    assert.deepEqual(JSON.parse(r.out), { ...ctx, systemMessage: '👻 3 facts and 1 rule from memory' });
    assert.deepEqual({ ...r.pulse, at: 0 }, { at: 0, session: 's1', event: 'prompt', up: true, facts: 3, rules: 1, events: 0, chats: 0, chars: 31, cap: 10000 });
  } finally { await s.close(); }
  s = await serve(ctx, { facts: 0, rules: 2, events: 0, chats: 0 });
  try {
    const r = await hook('user-prompt-submit.js', s.url, project(), { prompt: 'hi' });
    assert.deepEqual(JSON.parse(r.out), ctx, 'standing rules come with every prompt: no line for them');
  } finally { await s.close(); }
  s = await serve(ctx, null); // an older server: no header, the reply passes through untouched
  try { assert.equal((await hook('user-prompt-submit.js', s.url, project(), { prompt: 'hi' })).out, JSON.stringify(ctx)); } finally { await s.close(); }
});

test('PreCompact and Stop: a pulse each, nothing printed; a dead server is "asleep"; no .memory, no file', async () => {
  const s = await serve({}, { kept: 3, rules: 2, lines: 40 });
  try {
    const d = project();
    const pre = await hook('pre-compact.js', s.url, d, { hook_event_name: 'PreCompact', trigger: 'manual' });
    assert.equal(pre.out, '');
    assert.deepEqual([pre.pulse.event, pre.pulse.kept, pre.pulse.rules, pre.pulse.lines, pre.pulse.up], ['compact', 3, 2, 40, true]);
    const stop = await hook('stop.js', s.url, d, {});
    assert.deepEqual([stop.out, stop.pulse.event, stop.pulse.up], ['', 'stop', true]);
  } finally { await s.close(); }
  for (const f of ['user-prompt-submit.js', 'pre-compact.js', 'stop.js']) {
    const r = await hook(f, 'http://127.0.0.1:9', project(), { prompt: 'hi' });
    assert.deepEqual([r.code, r.out, r.pulse.up], [0, '', false], f);
  }
  const bare = mkdtempSync(join(tmpdir(), 'spk-bare-'));
  await hook('stop.js', 'http://127.0.0.1:9', bare, {});
  assert.ok(!existsSync(join(bare, '.memory')), 'a hook never creates .memory');
});

test('user scope: a project without .memory uses ~/.memory, for the hooks and the status line alike', async () => {
  const home = project(), work = mkdtempSync(join(tmpdir(), 'spk-work-'));
  await new Promise((res) => {
    const c = spawn(process.execPath, [join(HOOKS, 'stop.js')], { env: { PATH: process.env.PATH, HOME: home, USERPROFILE: home, SPOOKLING_MEMORY_URL: 'http://127.0.0.1:9' } });
    c.on('close', res);
    c.stdin.end(JSON.stringify({ session_id: 's1', cwd: work }));
  });
  assert.equal(JSON.parse(readFileSync(join(home, '.memory', 'pulse.json'), 'utf8')).event, 'stop');
  const out = spawnSync(process.execPath, [LINE], {
    input: JSON.stringify({ session_id: 's1', workspace: { project_dir: work } }),
    env: { PATH: process.env.PATH, HOME: home, USERPROFILE: home, NO_COLOR: '1' }, encoding: 'utf8',
  }).stdout;
  assert.equal(out, '👻 asleep, server off\n');
});

// the ghost look, the default: the pixel ghost of hooks/ghost.js, its state and the pulse's numbers beside it
const pixelGhost = createRequire(import.meta.url)('../../hooks/ghost.js');
const card = (pulse, { env = {}, session = 's1' } = {}) => {
  const d = project(pulse);
  return { d, rows: line(d, { look: null, env, session }) };
};
const words = (rows) => strip(rows).split('\n').map((l) => l.trim()).filter((l) => /[a-z~]/i.test(l))
  .map((l) => l.replace(/^[▀▄ ]+(?=[a-z~/≈])/i, ''));

test('the ghost look is the default: the state and the pulse\'s own numbers beside the ghost', () => {
  const active = card({ event: 'prompt', facts: 3, rules: 1, chats: 2, chars: 4200, cap: 10000, at: Date.now() - 12_000 });
  assert.deepEqual(words(active.rows).slice(0, 3), [
    'spookling active · 3 facts · 1 rule · 2 past chats recalled', '≈4.2k of the 10k characters of room', 'last hook 12 s ago']);
  assert.ok(words(active.rows)[3].endsWith('.memory'), 'which .memory it read');
  assert.equal(strip(active.rows).split('\n').length, 9, 'eight rows of ghost and the newline');
  assert.equal(words(card({ event: 'prompt', rules: 2 }).rows)[0], 'spookling active · nothing on record for this prompt');
  assert.equal(words(card({ event: 'stop' }).rows)[0], 'spookling ready · saved');
  assert.equal(words(card({ event: 'compact', rules: 2, lines: 40 }).rows)[0], 'spookling ready · kept your 2 rules and 38 lines');
  assert.deepEqual(words(card({ event: 'prompt', up: false }).rows).slice(0, 2),
    ['spookling degraded · server off', 'the local server did not answer the last hook']);
  assert.deepEqual(words(card(null).rows).slice(0, 2), ['spookling waiting · remembering you', 'no hook has run in this session yet']);
  assert.equal(words(card({ event: 'prompt', facts: 3 }, { session: 's2' }).rows)[0], 'spookling waiting · remembering you', 'not another session\'s news');
});

test('the ghost look: 24-bit colour where the terminal says so, the xterm 256 palette otherwise, one emoji line under NO_COLOR', () => {
  const pulse = { event: 'prompt', facts: 3, rules: 1 };
  assert.match(card(pulse).rows, /\x1b\[38;2;31;138;60/, 'a green outline in 24-bit');
  const plain256 = card(pulse, { env: { COLORTERM: '' } }).rows;
  assert.match(plain256, /\x1b\[48;5;\d+m/);
  assert.doesNotMatch(plain256, /;2;\d+;\d+;\d+m/, 'no 24-bit codes for a terminal that has none');
  assert.equal(card(pulse, { env: { NO_COLOR: '1' } }).rows, '👻 3 facts · 1 rule recalled\n');
});

test('the ghost look: plain cells, no half blocks, where the font has none', () => {
  const cells = card({ event: 'prompt', facts: 1 }, { env: { SPOOKLING_GHOST: 'cells' } }).rows;
  assert.doesNotMatch(cells, /[▀▄]/);
  assert.equal(strip(cells).split('\n').length, 10, 'nine rows of the small ghost and the newline');
  for (const s of pixelGhost.STATES) {
    for (const row of pixelGhost.rows(s, { mode: 'cells', level: '256' })) assert.equal(strip(row).length, 20);
    for (const row of pixelGhost.rows(s, { mode: 'half', level: '256' })) assert.equal(strip(row).length, 16);
  }
});

test('Terminal.app with Monaco gets plain cells, read from its own settings', { skip: process.platform !== 'darwin' && 'plutil and defaults are macOS' }, () => {
  const d = mkdtempSync(join(tmpdir(), 'spk-term-'));
  const plist = join(d, 'com.apple.Terminal.plist');
  const font = (name) => Buffer.from(`bplist00\x00\x00${name}\x00NSFont\x00`, 'latin1').toString('base64');
  const write = (name) => writeFileSync(plist, `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Default Window Settings</key><string>Pro</string>
<key>Window Settings</key><dict><key>Pro</key><dict><key>Font</key><data>${font(name)}</data></dict></dict></dict></plist>`);
  write('Monaco');
  const env = { TERM_PROGRAM: 'Apple_Terminal', SPOOKLING_TERMINAL_PLIST: plist };
  assert.equal(pixelGhost.terminalFont(env), 'Monaco');
  assert.equal(pixelGhost.mode(env), 'cells');
  write('SFMono-Regular');
  utimesSync(plist, new Date(), new Date(Date.now() + 5000)); // a changed file is read again
  assert.equal(pixelGhost.mode(env), 'half');
  assert.equal(pixelGhost.mode({ TERM_PROGRAM: 'iTerm.app' }), 'half');
});

test('active is the ready ghost turned green: the same face, no smile', () => {
  assert.deepEqual(pixelGhost.grid('active', 'large'), pixelGhost.grid('ready', 'large'));
  assert.deepEqual(pixelGhost.grid('active', 'small'), pixelGhost.grid('ready', 'small'));
  assert.notEqual(pixelGhost.PALETTE.active.O, pixelGhost.PALETTE.ready.O);
});
