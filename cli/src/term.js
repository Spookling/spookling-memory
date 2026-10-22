// Terminal helpers: brand colours, a half-block pixel canvas, prompts, and state restore.
// Built-ins only.
import process from 'node:process';
import { release } from 'node:os';

const { stdin, stdout, env } = process;

// FIELD is what the ghost flies on: the brand PURPLE, the app icon's background (the hat is BLACK, so it needs one).
export const C = {
  PURPLE: '#4E19CF', PURPLE_MID: '#7F52EF', LILAC: '#B28AFF', GHOST: '#DFD4FF',
  MINT: '#42C8A7', BLACK: '#020202', GREY: '#8A8A8A', FIELD: '#4E19CF',
};

// A Windows console older than Windows 10 1511 (build 10586) has no escape sequences at all. Node 18 still runs on
// Windows 8.1, so that is the one case left: plain text, no colour, no animation. Windows Terminal (WT_SESSION) and
// any Windows 10 or 11 console take the sequences.
export const LEGACY = process.platform === 'win32' && Number(release().split('.')[2] ?? 0) < 10586;
const forced = env.FORCE_COLOR && env.FORCE_COLOR !== '0';
// 'none' (NO_COLOR, piped output, legacy console), 'true' (24-bit), or '256'. Windows Terminal does 24-bit.
export const MODE = env.NO_COLOR || LEGACY ? 'none'
  : !stdout.isTTY && !forced ? 'none'
  : /^(truecolor|24bit)$/i.test(env.COLORTERM ?? '') || env.WT_SESSION ? 'true' : '256';

const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const q6 = (v) => (v < 48 ? 0 : v < 115 ? 1 : Math.floor((v - 35) / 40));
// SGR parameter for a colour; base 38 = foreground, 48 = background, null = terminal default.
const code = (hex, base) => hex == null ? `${base + 1}`
  : MODE === 'true' ? `${base};2;${rgb(hex).join(';')}`
  : `${base};5;${16 + rgb(hex).reduce((n, v) => n * 6 + q6(v), 0)}`;

export const paint = (hex, s, bold = false) =>
  MODE === 'none' ? s : `\x1b[${bold ? '1;' : ''}${code(hex, 38)}m${s}\x1b[0m`;

export const mix = (a, b, t) => {
  const k = Math.min(1, Math.max(0, t)), B = rgb(b);
  return '#' + rgb(a).map((v, i) => Math.round(v + (B[i] - v) * k).toString(16).padStart(2, '0')).join('');
};

export const size = () => ({ cols: stdout.columns || 80, rows: stdout.rows || 24 });

// Pixel canvas: two pixel rows per text row, null = transparent (terminal background).
export const canvas = (w, h, fill = null) => ({ w, h, px: new Array(w * h).fill(fill) });
export const dot = (cv, x, y, c) => {
  if (x >= 0 && y >= 0 && x < cv.w && y < cv.h) cv.px[y * cv.w + x] = c;
};

// Canvas to text lines. Each cell is '▀' with fg = top pixel, bg = bottom pixel.
// text: { [row]: [[col, string, colour], ...] } is painted over the pixels.
export function render(cv, text = {}) {
  const lines = [];
  for (let r = 0; r < cv.h >> 1; r++) {
    const over = [];
    for (const [col, s, c] of text[r] ?? []) [...s].forEach((ch, i) => { over[col + i] = [ch, c]; });
    let line = '', fg, bg;
    for (let x = 0; x < cv.w; x++) {
      const top = cv.px[2 * r * cv.w + x], bot = cv.px[(2 * r + 1) * cv.w + x];
      if (MODE === 'none') {
        line += over[x]?.[0] ?? (top && bot ? '█' : top ? '▀' : bot ? '▄' : ' ');
        continue;
      }
      let ch, f = fg, b;
      if (over[x]) [ch, f] = over[x], b = top;
      else if (top === bot) ch = ' ', b = top;
      else if (top == null) ch = '▄', f = bot, b = null;
      else ch = '▀', f = top, b = bot;
      const p = [];
      if (f !== fg) p.push(code(f, 38)), fg = f;
      if (b !== bg) p.push(code(b, 48)), bg = b;
      line += (p.length ? `\x1b[${p.join(';')}m` : '') + ch;
    }
    lines.push(MODE === 'none' ? line.trimEnd() : line + '\x1b[0m');
  }
  return lines;
}

// While the install stage is up, everything written below it is also kept here, to print again in the normal screen
// when the alternate screen closes (at the end, on Ctrl-C, or on a crash), so the log of what was written survives.
let tape = null;
export const write = (s) => { tape?.push(s); stdout.write(s); };
export const note = (s) => { tape?.push(s); }; // kept for the log, not shown now
export const record = (on) => { tape = on ? [] : null; };

// Terminal state, restored on exit, Ctrl-C and SIGTERM.
const state = { raw: false, cursor: false, alt: false };
export function setRaw(on) {
  if (!stdin.isTTY) return;
  stdin.setRawMode(on);
  state.raw = on;
}
export function enterScreen() {
  stdout.write('\x1b[?1049h\x1b[?25l\x1b[2J');
  state.alt = state.cursor = true;
}
export function cursor(show) {
  stdout.write(show ? '\x1b[?25h' : '\x1b[?25l');
  state.cursor = !show;
}
export function restore() {
  let s = '';
  if (state.alt) s += '\x1b[r\x1b[?1049l'; // the scroll region too, then back to the normal screen
  if (state.cursor) s += '\x1b[?25h';
  if (s) stdout.write('\x1b[0m' + s);
  state.alt = state.cursor = false;
  if (tape) { stdout.write(tape.join('')); tape = null; }
  if (state.raw) setRaw(false);
}
export function bail() {
  restore();
  stdout.write('\n');
  process.exit(130);
}
// Piped into head or a closed pager: stop quietly.
stdout.on('error', (e) => { if (e.code === 'EPIPE') process.exit(0); throw e; });
process.on('SIGINT', bail);
process.on('SIGTERM', bail);
process.on('exit', restore);

// One shared reader so piped answers are never lost between prompts. Raw mode for keys and hidden input (shown as
// bullets); cooked mode for the rest, so the terminal echoes and edits. In the demo a script types into the same
// buffer (feed), so the demo walks the real prompts and echoes what it types.
let buf = '', waiter = null, ended = false, listening = false, feed = null;
export const setFeed = (f) => { feed = f; };
export const press = (k) => { buf += k; pump(); };
export const UP = '\x1b[A', DOWN = '\x1b[B';
// Called with true before a prompt waits and false after, so the stage can hold still while you type.
let onPrompt = () => {};
export const setOnPrompt = (f) => { onPrompt = f; };
// No terminal and no answer (an assistant's shell can hold stdin open and never write): take the default after this
// long, once, instead of waiting forever; later prompts then take theirs at once. Every write is still previewed first, so the log shows what a default wrote.
const NO_TTY_WAIT = 20_000;
function pump() {
  while (waiter) {
    if (!buf.length) return ended && waiter.done(waiter.key ? null : waiter.text || null);
    if (buf[0] === '\x03') bail();
    const k = /^\x1b(?:\[[\d;]*[A-Za-z~]|O.)?/.exec(buf)?.[0] ?? buf[0]; // an arrow key is one key
    buf = buf.slice(k.length);
    if (waiter.key) waiter.done(k);
    else if (k === '\r' || k === '\n') {
      if (k === '\r' && buf[0] === '\n') buf = buf.slice(1); // CRLF in one chunk; a later Enter is its own answer
      waiter.done(waiter.text);
    }
    else if (k === '\x7f' || k === '\b') {
      if (waiter.text && (waiter.mask || feed)) write('\b \b');
      waiter.text = waiter.text.slice(0, -1);
    }
    else if (k === '\x15') {
      if (waiter.mask || feed) write('\b \b'.repeat(waiter.text.length));
      waiter.text = '';
    }
    else if (k === '\x04' && !waiter.text) waiter.done(null);
    else if (k >= ' ') {
      waiter.text += k;
      if (waiter.mask) write('•'); else if (feed) write(k);
    }
  }
}
// One answer: a line, or a single key (key = true). hidden lines are masked as bullets.
function read({ hidden = false, key = false } = {}) {
  if (!listening && !feed) {
    listening = true;
    stdin.setEncoding('utf8');
    stdin.on('data', (d) => { buf += d; pump(); });
    stdin.on('end', () => { ended = true; pump(); });
  }
  return new Promise((resolve) => {
    const raw = (hidden || key) && stdin.isTTY && !feed;
    if (raw) setRaw(true);
    const timer = stdin.isTTY || feed ? null : setTimeout(() => {
      if (waiter !== w) return;
      stdout.write(paint(C.GREY, `(no answer in ${NO_TTY_WAIT / 1000} s) `));
      ended = true; // the rest take their defaults at once
      w.done(w.text || null);
    }, NO_TTY_WAIT);
    const w = waiter = {
      text: '', key, mask: hidden && (stdin.isTTY || !!feed),
      done(v) {
        clearTimeout(timer);
        waiter = null;
        if (raw) setRaw(false);
        if (!feed) stdin.pause();
        // End of input at a real terminal (Ctrl-D) aborts, so no write is ever confirmed by default.
        // Piped or closed stdin takes the defaults, which is how a non-interactive run completes.
        if (v === null && stdin.isTTY && !feed) bail();
        resolve(v);
      },
    };
    if (!feed) stdin.resume();
    pump();
  });
}

// Returns the trimmed answer, or def on Enter or end of input.
export async function ask(q, def = '', kind = 'line') {
  write(`${q}${def ? paint(C.GREY, ` [${def}]`) : ''} `);
  onPrompt(true);
  feed?.({ kind, q });
  const a = await read();
  onPrompt(false);
  // The terminal echoed a typed answer itself; keep it for the log. Piped input was not echoed, so print it.
  if (feed) write('\n');
  else if (!stdin.isTTY) write((a ?? paint(C.GREY, '(no input)')) + '\n');
  else note(`${a ?? ''}\n`);
  return (a ?? '').trim() || def;
}

// Shown as bullets while you type or paste; the key itself is never printed.
export async function askHidden(q) {
  write(`${q} `);
  onPrompt(true);
  feed?.({ kind: 'hidden', q });
  const a = await read({ hidden: true });
  onPrompt(false);
  write('\n');
  return (a ?? '').trim();
}

export async function confirm(q, yes = true) {
  const a = (await ask(`${q} ${paint(C.GREY, yes ? '[Y/n]' : '[y/N]')}`, '', 'confirm')).toLowerCase();
  return a ? a.startsWith('y') : yes;
}

// A menu: arrows (or j, k, or the number) move, Enter picks. Returns the index. Without a terminal to draw on or to
// read keys from: a numbered list and a typed answer, a number or part of a name; anything else takes def.
export async function choose(q, opts, def = 0) {
  if (!stdout.isTTY || (!stdin.isTTY && !feed)) {
    write(opts.map((o, i) => `   ${i + 1}  ${o}\n`).join(''));
    const a = (await ask(q, String(def + 1))).toLowerCase();
    const i = /^\d+$/.test(a) ? Number(a) - 1 : opts.findIndex((o) => o.toLowerCase().includes(a));
    return opts[i] === undefined ? def : i;
  }
  let i = def;
  const draw = () => write(opts.map((o, j) =>
    `\r\x1b[K   ${j === i ? `${paint(C.LILAC, '›')} ${paint(C.GHOST, o, true)}` : `  ${o}`}\n`).join(''));
  write(`${q} ${paint(C.GREY, '(arrows, then Enter)')}\n`);
  draw();
  onPrompt(true);
  cursor(false);
  feed?.({ kind: 'menu', q });
  for (let k = await read({ key: true }); k !== '\r' && k !== '\n'; k = await read({ key: true })) {
    if (k === null || k === '\x04') bail();
    const n = Number(k);
    if (n >= 1 && n <= opts.length) i = n - 1;
    else if (k === UP || k === '\x1bOA' || k === 'k') i = (i + opts.length - 1) % opts.length;
    else if (k === DOWN || k === '\x1bOB' || k === 'j') i = (i + 1) % opts.length;
    else continue;
    write(`\x1b[${opts.length}A`);
    draw();
  }
  cursor(true);
  onPrompt(false);
  return i;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
