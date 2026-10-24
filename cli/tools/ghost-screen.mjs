// Dev only: a Claude Code screen with the ghost in its status line, as ANSI bytes for `render-frames.py raw`.
//   node cli/tools/ghost-screen.mjs LOOK COLS ROWS [STATE] > screen.ans
// LOOK: emoji, pixel, meter or two-line. STATE: sample (default), idle, spinner, recalled, saved, compact or asleep.
// The status line is the real hooks/status-line.js output; the transcript lines use the real hooks wording.
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HOOKS = fileURLToPath(new URL('../../hooks/', import.meta.url));
const { said } = createRequire(import.meta.url)(join(HOOKS, 'pulse.js'));
const [look = 'pixel', cols = 80, rows = 24, state = 'sample'] = process.argv.slice(2).map((a, i) => (i === 1 || i === 2 ? Number(a) : a));

const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)).join(';');
const fg = (h) => `\x1b[38;2;${rgb(h)}m`, bg = (h) => `\x1b[48;2;${rgb(h)}m`, R = '\x1b[0m';
const UI = { TEXT: '#D4D4D4', BRIGHT: '#ECECEC', GREY: '#8A8A8A', RULE: '#4A4A4A', USER: '#262626', SPIN: '#D77757' };

const last = { event: 'prompt', facts: 1, rules: 1, chats: 2, chars: 4300, cap: 10000 };
// what the hooks would have left in pulse.json; null is no pulse yet
const PULSE = {
  sample: last, idle: null, spinner: null, recalled: { event: 'prompt', facts: 2, rules: 1, chars: 2600, cap: 10000 },
  saved: { event: 'stop' }, compact: { event: 'compact', kept: 3, rules: 2, lines: 40 }, asleep: { event: 'prompt', up: false },
};

function statusLine(pulse) {
  const d = mkdtempSync(join(tmpdir(), 'spk-screen-'));
  mkdirSync(join(d, '.memory'));
  if (pulse) writeFileSync(join(d, '.memory', 'pulse.json'), JSON.stringify({ at: Date.now(), session: 's', up: true, ...pulse }));
  return spawnSync(process.execPath, [join(HOOKS, 'status-line.js'), '--look', look], {
    input: JSON.stringify({ session_id: 's', workspace: { project_dir: d } }),
    env: { PATH: process.env.PATH, HOME: d, COLORTERM: 'truecolor' }, encoding: 'utf8',
  }).stdout.trimEnd().split('\n');
}

const wrap = (text, w) => text.split(' ').reduce((ls, word) => {
  if (ls.length && (ls.at(-1) + ' ' + word).length <= w) ls[ls.length - 1] += ' ' + word; else ls.push(word);
  return ls;
}, []);
const user = (t) => ['', `${bg(UI.USER)}${fg(UI.GREY)}> ${fg(UI.BRIGHT)}${t.padEnd(cols - 2)}${R}`];
const system = (counts) => (counts ? [`  ${fg(UI.GREY)}⎿  👻 ${said(counts)} from memory${R}`] : []);
const reply = (t) => ['', ...wrap(t, cols - 4).map((l, i) => `${i ? '  ' : `${fg(UI.BRIGHT)}⏺ `}${fg(UI.TEXT)}${l}${R}`)];
const tool = (call, result) => ['', `${fg('#4EBA65')}⏺ ${fg(UI.BRIGHT)}${call}${R}`, `  ${fg(UI.GREY)}⎿  ${result}${R}`];

const talk = [
  ...user('morning! where were we?'), ...reply('Morning. Last time we were speeding up the order reports for the shop.'),
  ...user('what car do I drive, and which units do I want?'), ...system({ facts: 2, rules: 1 }),
  ...reply('A Toyota Corolla: you said so on 22 May and again on 3 Jun. You asked for metric units, so its tank holds about 50 litres and it uses roughly 6 litres per 100 km.'),
  ...user('what did we pick for the shop database?'), ...system(last),
  ...(rows >= 30 ? tool('Read(db/schema.sql)', 'Read 42 lines') : []),
  ...reply('Postgres 16. You decided it on 12 Oct, after the MySQL trial was too slow on the order reports.'),
];
const rule = `${fg(UI.RULE)}${'─'.repeat(cols)}${R}`;
// the spinner row shows a hook's statusMessage while it runs
const spin = state === 'spinner' ? `${fg(UI.SPIN)}✻ ${fg(UI.TEXT)}👻 remembering…${R}` : '';
const bottom = [spin, rule, `${fg(UI.GREY)}> ${bg(UI.TEXT)} ${R}`, rule, ...statusLine(PULSE[state]).map((l) => `  ${l}`), ''];
const screen = [...talk, ...Array(Math.max(0, rows - talk.length - bottom.length)).fill('')].slice(-(rows - bottom.length)).concat(bottom);
process.stdout.write('\x1b[2J\x1b[H' + screen.join('\r\n'));
