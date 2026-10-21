// Terminal helpers: brand colours, a half-block pixel canvas, prompts, and state restore.
// Built-ins only.
import process from 'node:process';

const { stdin, stdout, env } = process;

export const C = {
  PURPLE: '#4E19CF', PURPLE_MID: '#7F52EF', LILAC: '#B28AFF', GHOST: '#DFD4FF',
  MINT: '#42C8A7', BLACK: '#020202', GREY: '#8A8A8A', FIELD: '#1F0A53',
};

const forced = env.FORCE_COLOR && env.FORCE_COLOR !== '0';
// 'none' (NO_COLOR, or piped output), 'true' (24-bit), or '256'.
export const MODE = env.NO_COLOR ? 'none'
  : !stdout.isTTY && !forced ? 'none'
  : /^(truecolor|24bit)$/i.test(env.COLORTERM ?? '') ? 'true' : '256';

// The 256 cube has no dark purple; #30087F lands on 54 (#5F0087), the closest purple.
if (MODE === '256') C.FIELD = '#30087F';

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
  if (state.alt) s += '\x1b[?1049l';
  if (state.cursor) s += '\x1b[?25h';
  if (s) stdout.write('\x1b[0m' + s);
  state.alt = state.cursor = false;
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

// One shared line reader so piped answers are never lost between prompts.
// Raw mode is used only for hidden input; cooked mode lets the terminal echo and edit.
let buf = '', waiter = null, ended = false, listening = false;
// No terminal and no answer (an assistant's shell can hold stdin open and never write): take the default after this
// long, once, instead of waiting forever; later prompts then take theirs at once. Every write is still previewed first, so the log shows what a default wrote.
const NO_TTY_WAIT = 20_000;
function pump() {
  while (waiter) {
    if (!buf.length) return ended && waiter.done(waiter.text || null);
    const ch = buf[0];
    buf = buf.slice(1);
    if (ch === '\x03') bail();
    else if (ch === '\r' || ch === '\n') {
      if (ch === '\r' && buf[0] === '\n') buf = buf.slice(1); // CRLF in one chunk; a later Enter is its own answer
      waiter.done(waiter.text);
    }
    else if (ch === '\x7f' || ch === '\b') waiter.text = waiter.text.slice(0, -1);
    else if (ch === '\x15') waiter.text = '';
    else if (ch === '\x04' && !waiter.text) waiter.done(null);
    else if (ch >= ' ') waiter.text += ch;
  }
}
function readLine(hidden) {
  if (!listening) {
    listening = true;
    stdin.setEncoding('utf8');
    stdin.on('data', (d) => { buf += d; pump(); });
    stdin.on('end', () => { ended = true; pump(); });
  }
  return new Promise((resolve) => {
    const raw = hidden && stdin.isTTY;
    if (raw) setRaw(true);
    const timer = stdin.isTTY ? null : setTimeout(() => {
      if (waiter !== w) return;
      stdout.write(paint(C.GREY, `(no answer in ${NO_TTY_WAIT / 1000} s) `));
      ended = true; // the rest take their defaults at once
      w.done(w.text || null);
    }, NO_TTY_WAIT);
    const w = waiter = {
      text: '',
      done(v) {
        clearTimeout(timer);
        waiter = null;
        if (raw) setRaw(false);
        stdin.pause();
        // End of input at a real terminal (Ctrl-D) aborts, so no write is ever confirmed by default.
        // Piped or closed stdin takes the defaults, which is how a non-interactive run completes.
        if (v === null && stdin.isTTY) bail();
        resolve(v);
      },
    };
    stdin.resume();
    pump();
  });
}

// Returns the trimmed answer, or def on Enter or end of input.
export async function ask(q, def = '') {
  stdout.write(`${q}${def ? paint(C.GREY, ` [${def}]`) : ''} `);
  const a = await readLine(false);
  if (!stdin.isTTY) stdout.write((a ?? paint(C.GREY, '(no input)')) + '\n');
  return (a ?? '').trim() || def;
}

// Never echoes, not even as stars.
export async function askHidden(q) {
  stdout.write(`${q} ${paint(C.GREY, '(hidden)')} `);
  const a = await readLine(true);
  stdout.write('\n');
  return (a ?? '').trim();
}

export async function confirm(q, yes = true) {
  const a = (await ask(`${q} ${paint(C.GREY, yes ? '[Y/n]' : '[y/N]')}`)).toLowerCase();
  return a ? a.startsWith('y') : yes;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
