// The Spookling ghost for terminal text: Claude Code's status line and Codex's hook messages. Node 18+, no
// dependencies. One design at two sizes:
//   16x16, drawn with half blocks (two pixels a cell): 16 columns x 8 rows
//   10x9, drawn by hand for terminals whose font lacks half blocks (one pixel a cell, two cells wide): 20 x 9
// Built the way pixel-profile (MIT, https://github.com/TheMysteriousGhost/pixel-profile) builds its ghost: a dark
// outline, shading down one side, small eyes, blush, a scalloped hem. Each state is the same ghost with its own
// face and colours, so it reads without colour too.
//   ready     hooks reached the local server
//   active    memory recalled something for this prompt (green, the same face)
//   degraded  the server did not answer (one worried look and a sweat drop)
//   waiting   no hook has run yet in this session (asleep, grey)
const { spawnSync } = require('node:child_process');
const { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } = require('node:fs');
const { homedir, tmpdir } = require('node:os');
const { join } = require('node:path');

// O outline, W body, S shade, B blush, E eye, H eye highlight, D sweat drop
const LARGE = [
  '.....OOOOOO.....',
  '...OOWWWWWWOO...',
  '..OWWWWWWWWWWO..',
  '.OWWWWWWWWWWWWO.',
  '.OWWWWWWWWWWWWO.',
  '.OWWWHEWWWHEWWO.',
  '.OWWWEEWWWEEWWO.',
  '.OWBBWWWWWWWBBO.',
  '.OWWWWWWWWWWWWO.',
  '.OWWWWWWWWWWWSO.',
  '.OWWWWWWWWWWWSO.',
  '.OWWWWWWWWWWSSO.',
  '.OWWWWWWWWWWWSO.',
  '.OWWOOOWWOOOWSO.',
  '..OO...OO...OO..',
  '................',
];
const LARGE_FACE = {
  ready: {},
  active: {},
  degraded: { 1: '...OOWWWWWWOO..D', 2: '..OWWWWWWWWWWODD', 3: '.OWWWWWWWWWWWWOD', 8: '.OWWWWWEEWWWWWO.' },
  waiting: { 5: '.OWWWWWWWWWWWWO.', 7: '.OWWWWWWWWWWWWO.' }, // asleep: lids down, no blush
};

const SMALL = [
  '..OOOOOO..',
  '.OWWWWWWO.',
  'OWWWWWWWWO',
  'OWWEWWEWWO',
  'OWWEWWEWWO',
  'OWBWWWWBWO',
  'OWWWWWWWSO',
  'OWWWWWWWSO',
  'OO..OO..OO',
];
const SMALL_FACE = {
  ready: {},
  active: {},
  degraded: { 0: '..OOOOOO.D', 1: '.OWWWWWWOD', 6: 'OWWWEEWWSO' },
  waiting: { 3: 'OWWWWWWWWO', 4: 'OWEEWWEEWO', 5: 'OWWWWWWWWO' }, // asleep: closed-line eyes, no blush
};

// Each state's colours stay apart when a terminal has only 256 colours (as rounded by sgr below: purple, green,
// amber and grey outlines), and eye inks round to black there.
const PALETTE = {
  ready: { O: '#3a1a6e', W: '#f4f1ff', S: '#c9c1f0', B: '#ff8fab', E: '#120d18', H: '#ffffff', D: '#7fd1ff' },
  active: { O: '#1f8a3c', W: '#d9ffdc', S: '#a8e6b8', B: '#ff8fab', E: '#0e1610', H: '#ffffff', D: '#7fd1ff' },
  degraded: { O: '#8a5200', W: '#fff4dc', S: '#f2c88f', B: '#ff9f8a', E: '#18120c', H: '#ffffff', D: '#7fd1ff' },
  waiting: { O: '#4a4652', W: '#d6d3df', S: '#aaa5b8', B: '#d6d3df', E: '#2a2730', H: '#d6d3df', D: '#7fd1ff' },
};
const STATES = Object.keys(PALETTE);

function grid(state, size) {
  const [base, faces] = size === 'small' ? [SMALL, SMALL_FACE] : [LARGE, LARGE_FACE];
  return base.map((row, y) => faces[state][y] ?? row);
}

// colours the way the terminal can show them: 24-bit where it says so, else the xterm 256 palette
const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const q6 = (v) => (v < 48 ? 0 : v < 115 ? 1 : Math.floor((v - 35) / 40));
const sgr = (h, base, level) => (level === 'truecolor' ? `${base};2;${rgb(h).join(';')}` : `${base};5;${16 + rgb(h).reduce((n, v) => n * 6 + q6(v), 0)}`);
const paint = (s, level, fg, bg) => `\x1b[${[fg && sgr(fg, 38, level), bg && sgr(bg, 48, level)].filter(Boolean).join(';')}m${s}\x1b[0m`;

function colourLevel(env = process.env) {
  if (env.NO_COLOR) return 'none';
  return /^(truecolor|24bit)$/i.test(env.COLORTERM || '') || env.WT_SESSION ? 'truecolor' : '256';
}

// Fonts known to lack U+2580 and U+2584 (Monaco, the font of Terminal.app's "Pro" profile: it has the full block
// and the space, not the halves). A terminal then borrows them from another font, whose glyphs sit off the grid:
// rows come out of line and the top row floats off the head.
const NO_HALF_BLOCKS = ['Monaco'];

// Terminal.app's default profile font, read once per change of its settings file and cached, so the status line
// stays fast. Any failure answers null: the half-block ghost.
function terminalFont(env = process.env) {
  if (env.TERM_PROGRAM !== 'Apple_Terminal' || process.platform !== 'darwin') return null;
  const plist = env.SPOOKLING_TERMINAL_PLIST || join(homedir(), 'Library/Preferences/com.apple.Terminal.plist');
  try {
    const mtime = statSync(plist).mtimeMs;
    const cache = join(tmpdir(), 'spookling-terminal-font.json');
    try {
      const c = JSON.parse(readFileSync(cache, 'utf8'));
      if (c.plist === plist && c.mtime === mtime) return c.font;
    } catch {}
    const run = (cmd, args) => spawnSync(cmd, args, { encoding: 'utf8', timeout: 2000 }).stdout || '';
    const domain = plist.replace(/\.plist$/, '');
    const profile = (run('defaults', ['read', domain, 'Startup Window Settings']) || run('defaults', ['read', domain, 'Default Window Settings'])).trim();
    const data = profile && run('plutil', ['-extract', `Window Settings.${profile}.Font`, 'raw', '-o', '-', plist]).trim();
    const archived = data ? Buffer.from(data, 'base64').toString('latin1') : '';
    const font = NO_HALF_BLOCKS.find((f) => archived.includes(f)) || (archived ? 'other' : null);
    try { writeFileSync(cache, JSON.stringify({ plist, mtime, font })); } catch {}
    return font;
  } catch {
    return null;
  }
}

// 'half' (16x8, crisp) or 'cells' (20x9, any font). SPOOKLING_GHOST=half or cells chooses by hand.
function mode(env = process.env) {
  if (env.SPOOKLING_GHOST === 'half' || env.SPOOKLING_GHOST === 'cells') return env.SPOOKLING_GHOST;
  return NO_HALF_BLOCKS.includes(terminalFont(env)) ? 'cells' : 'half';
}

// Claude Code strips the leading spaces of each status line row, which pulls the head's top rows left and squares it
// off (seen by running Claude Code 2.1.310 in a terminal emulator). A row that starts with a blank cell starts with a
// reset first: drawn as nothing, and not a space, so the blank cells stay.
const KEEP = '\x1b[0m';
const keep = (row) => (row.startsWith(' ') ? KEEP + row : row);

/**
 * The ghost as terminal rows. Half blocks: where a cell's two pixels share a colour it is a background-coloured
 * space (nothing for a terminal to dim or misdraw); two colours, ▀ with the top in front and the bottom behind; one,
 * ▀ or ▄ over the terminal's own background. Cells: every pixel two background-coloured spaces, no glyph at all.
 */
function rows(state, { mode: m = 'half', level = '256' } = {}) {
  const colours = PALETTE[state];
  const px = (ch) => (ch === '.' ? null : colours[ch]);
  if (m === 'cells') {
    return grid(state, 'small').map((row) => keep([...row].map((ch) => (px(ch) ? paint('  ', level, null, px(ch)) : '  ')).join('')));
  }
  const g = grid(state, 'large');
  const out = [];
  for (let r = 0; r < g.length; r += 2) {
    out.push(keep([...g[r]].map((ch, x) => {
      const top = px(ch), bottom = px(g[r + 1][x]);
      if (top && top === bottom) return paint(' ', level, null, top);
      if (top && bottom) return paint('▀', level, top, bottom);
      if (top) return paint('▀', level, top);
      if (bottom) return paint('▄', level, bottom);
      return ' ';
    }).join('')));
  }
  return out;
}

/**
 * The ghost with words beside it: `lines` are [text, colour] pieces per row, set from the third row down so they sit
 * by its face. Under NO_COLOR, one line: the emoji and the first row's words.
 */
function card(state, lines, { env = process.env } = {}) {
  const level = colourLevel(env);
  const say = (pieces) => pieces.map(([t, c]) => (level === 'none' || !c ? t : paint(t, level, c))).join('');
  if (level === 'none') return [`👻 ${lines.map((pieces) => pieces.map(([t]) => t).join('')).filter(Boolean).join(' · ')}`];
  const ghost = rows(state, { mode: mode(env), level });
  const at = ghost.length > 8 ? 2 : 1;
  return ghost.map((row, i) => (lines[i - at]?.length ? `${row}  ${say(lines[i - at])}` : row).trimEnd());
}

// 298, 4.2k, 10k: a count of characters as the ghost says it
const kilo = (v) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(v));

module.exports = { PALETTE, STATES, card, colourLevel, grid, kilo, mode, paint, rows, terminalFont };
