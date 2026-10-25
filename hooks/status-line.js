#!/usr/bin/env node
// Claude Code status line: the ghost and the last thing memory did, read from .memory/pulse.json. No network.
// --then CMD runs the status line you had before and keeps it in front; --look ghost|emoji|pixel|meter|two-line picks
// the ghost (ghost, the default: the pixel ghost of ghost.js with its state and the pulse's numbers beside it).
const pulse = require('./pulse.js');
const pixelGhost = require('./ghost.js');

const STALE = 10 * 60 * 1000; // ms with nothing new before the ghost just keeps you company
const C = { PURPLE: '#4E19CF', PURPLE_MID: '#7F52EF', LILAC: '#B28AFF', GHOST: '#DFD4FF', GREY: '#8A8A8A', DEEP: '#2A1466' };
const { env } = process;
const args = process.argv.slice(2);
const arg = (k) => { const i = args.indexOf(k); return i < 0 ? undefined : args[i + 1]; };
const look = arg('--look') || 'ghost';

const colour = !env.NO_COLOR;
const truecolor = /^(truecolor|24bit)$/i.test(env.COLORTERM || '') || !!env.WT_SESSION;
const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const q6 = (v) => (v < 48 ? 0 : v < 115 ? 1 : Math.floor((v - 35) / 40));
const sgr = (h, base) => (truecolor ? `${base};2;${rgb(h).join(';')}` : `${base};5;${16 + rgb(h).reduce((n, v) => n * 6 + q6(v), 0)}`);
const paint = (s, fg, bg) => (colour ? `\x1b[${[sgr(fg, 38), bg && sgr(bg, 48)].filter(Boolean).join(';')}m${s}\x1b[0m` : s);

// four cells: arms, two big eyes on a lilac-white face, arms; the arms go up on odd seconds, the words never move
function ghost(asleep, now) {
  if (!colour || !truecolor || look === 'emoji') return '👻';
  const arm = paint(!asleep && Math.floor(now / 1000) % 2 ? '▀' : '▄', C.LILAC);
  const eye = paint(asleep ? '-' : '●', asleep ? C.PURPLE_MID : C.PURPLE, C.GHOST);
  return arm + eye + eye + arm;
}

// look two-line: five by four pixels over two rows, a purple hat, dark eyes, a lilac hem that sways each second
const PX = { P: C.PURPLE, M: C.PURPLE_MID, L: C.LILAC, G: C.GHOST, K: '#020202' };
function tall(asleep, now) {
  const px = ['.PPP.', asleep ? 'GMGMG' : 'GKGKG', 'GGGGG', !asleep && Math.floor(now / 1000) % 2 ? '.L.L.' : 'L.L.L'];
  return [0, 2].map((r) => [...px[r]].map((c, x) => {
    const top = PX[c], bot = PX[px[r + 1][x]];
    return top && bot ? (top === bot ? paint('█', top) : paint('▀', top, bot)) : top ? paint('▀', top) : bot ? paint('▄', bot) : ' ';
  }).join(''));
}

// six cells of eighth blocks: how much of the hook's room the recall filled
function meter(p) {
  const e = Math.round(48 * Math.min(1, (p.chars || 0) / (p.cap || 10000)));
  const cells = [0, 1, 2, 3, 4, 5].map((i) => { const k = Math.max(0, Math.min(8, e - 8 * i)); return k ? ' ▏▎▍▌▋▊▉█'[k] : ' '; });
  return colour ? ` ${paint(cells.join(''), C.LILAC, C.DEEP)}` : ` [${cells.map((c) => (c === ' ' ? '.' : '#')).join('')}]`;
}

// [text, colour] pieces of what the ghost says; p is this session's pulse or null
function words(p, now) {
  const { n } = pulse;
  if (p?.up === false) return [['asleep, server off', C.GREY]];
  if (!p || now - p.at > STALE) return [['remembering you', C.LILAC]];
  if (p.event === 'stop') return [['saved', C.LILAC]];
  if (p.event === 'compact') {
    const rest = (p.lines || 0) - (p.rules || 0);
    return [[p.rules ? `kept your ${n(p.rules, 'rule')}${rest ? ` and ${n(rest, 'line')}` : ''}`
      : p.lines ? `kept ${n(p.lines, 'line')} through compaction` : 'compacted', C.LILAC]];
  }
  if (!(p.facts || p.events || p.chats)) return [['listening', C.LILAC]];
  const parts = [[p.facts, 'fact'], [p.events, 'event'], [p.rules, 'rule'], [p.chats, 'past chat']].filter(([k]) => k);
  return [...parts.flatMap(([k, w], i) => [...(i ? [[' · ', C.GREY]] : []), [n(k, w), C.LILAC]]), [' recalled', C.GREY]];
}

// the ghost's state, from the pulse alone: the server off, no hook yet, recall completed for this prompt, or ready
function state(p, now) {
  if (p?.up === false) return 'degraded';
  if (!p) return 'waiting';
  if (now - p.at <= STALE && p.event === 'prompt') return 'active';
  return 'ready';
}

const { kilo } = pixelGhost;
const ago = (ms) => (ms < 60000 ? `${Math.max(1, Math.round(ms / 1000))} s ago` : ms < 3600000 ? `${Math.round(ms / 60000)} min ago` : `${Math.round(ms / 3600000)} h ago`);

// the words beside the ghost, every one from the pulse or where it lives: what memory did, how much of the recall's
// room it took, when, and which .memory it was
function beside(st, p, said, now, project) {
  const nothing = st === 'active' && !(p.facts || p.events || p.chats);
  const news = st === 'degraded' ? [['server off', C.GREY]] : nothing ? [['nothing on record for this prompt', C.GREY]] : said;
  const out = [[[`spookling ${st}`, pixelGhost.PALETTE[st].O], [' · ', C.GREY], ...news]];
  if (st === 'degraded') out.push([['the local server did not answer the last hook', C.GREY]]);
  else if (st === 'waiting') out.push([['no hook has run in this session yet', C.GREY]]);
  else if (st === 'active' && !nothing && p.cap) out.push([[`≈${kilo(p.chars || 0)} of the ${kilo(p.cap)} characters of room`, C.GREY]]);
  if (p?.at) out.push([[`last hook ${ago(now - p.at)}`, C.GREY]]);
  const file = pulse.where(project);
  if (file) out.push([[file.replace(/[\\/]pulse\.json$/, '').replace(require('node:os').homedir(), '~'), C.GREY]]);
  return out;
}

// as Claude Code runs a status line: sh on macOS and Linux; Git Bash on Windows, else PowerShell
// ponytail: Git Bash found by CLAUDE_CODE_GIT_BASH_PATH or its default path only, like most installs
function before(cmd, input) {
  const { spawnSync } = require('node:child_process');
  const shell = process.platform !== 'win32' ? true
    : env.CLAUDE_CODE_GIT_BASH_PATH || ['C:\\Program Files\\Git\\bin\\bash.exe'].find((f) => require('node:fs').existsSync(f)) || 'powershell.exe';
  const r = spawnSync(cmd, { input, shell, encoding: 'utf8', timeout: 10000, windowsHide: true });
  return r.status === 0 && r.stdout ? r.stdout.replace(/\s+$/, '') : '';
}

const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', () => {});
process.stdin.on('end', () => {
  const input = Buffer.concat(chunks);
  let s = {};
  try { s = JSON.parse(input) || {}; } catch {}
  const now = Date.now();
  const all = pulse.read(s.workspace?.project_dir || s.cwd || env.CLAUDE_PROJECT_DIR);
  const p = all && !(s.session_id && all.session !== s.session_id) ? all : null; // another session's news is not ours
  const said = words(p, now), text = said.map(([t, c]) => paint(t, c)).join('');
  const then = arg('--then') ? before(arg('--then'), input) : '';
  if (look === 'ghost' && colour) { // yours keeps its rows above; the ghost takes its own
    const st = state(p, now);
    const rows = pixelGhost.card(st, beside(st, p, said, now, s.workspace?.project_dir || s.cwd || env.CLAUDE_PROJECT_DIR));
    return process.stdout.write(`${then ? `${then}\n` : ''}${rows.join('\n')}\n`);
  }
  if (look === 'two-line' && colour && truecolor) { // yours keeps its rows above; the ghost takes two of its own
    const [top, hem] = tall(p?.up === false, now);
    return process.stdout.write(`${then ? `${then}\n` : ''}${top} ${text}\n${hem}\n`);
  }
  const ours = ghost(p?.up === false, now) + (look === 'meter' && said.at(-1)[0] === ' recalled' ? meter(p) : '') + ` ${text}`;
  process.stdout.write(`${then ? `${then}  ` : ''}${ours}\n`);
});
