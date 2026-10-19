// The intro: field fades in, the ghost crosses twice, SPOOKLING builds letter by letter,
// the tagline types out, then the MINT line. About 7.5 s at 20 fps, any key skips.
import process from 'node:process';
import { C, MODE, bail, canvas, dot, enterScreen, mix, paint, render, restore, setRaw, size } from './term.js';
import { fly } from './ghost.js';

export const TAGLINE = 'An AI assistant that works for you while you sleep';
export const PROMISE = 'Memory that stays right for years.';

// Block letters, 7 px tall, drawn GHOST on a PURPLE band.
const FONT = {
  S: ['.XXXX.', 'XX..XX', 'XX....', '.XXXX.', '....XX', 'XX..XX', '.XXXX.'],
  P: ['XXXXX.', 'XX..XX', 'XX..XX', 'XXXXX.', 'XX....', 'XX....', 'XX....'],
  O: ['.XXXX.', 'XX..XX', 'XX..XX', 'XX..XX', 'XX..XX', 'XX..XX', '.XXXX.'],
  K: ['XX..XX', 'XX.XX.', 'XXXX..', 'XXX...', 'XXXX..', 'XX.XX.', 'XX..XX'],
  L: ['XX....', 'XX....', 'XX....', 'XX....', 'XX....', 'XX....', 'XXXXXX'],
  I: ['XX', 'XX', 'XX', 'XX', 'XX', 'XX', 'XX'],
  N: ['XX..XX', 'XXX.XX', 'XXXXXX', 'XX.XXX', 'XX..XX', 'XX..XX', 'XX..XX'],
  G: ['.XXXX.', 'XX..XX', 'XX....', 'XX.XXX', 'XX..XX', 'XX..XX', '.XXXX.'],
};
const WORD = 'SPOOKLING', GAP = 2, PAD = 4;
const TEXT_W = [...WORD].reduce((n, ch) => n + FONT[ch][0].length, 0) + GAP * (WORD.length - 1);
export const BAND_W = TEXT_W + PAD * 2, BAND_H = 12; // pixels; 6 text rows

// Draw the band at pixel (x, y) with the first n letters; fade 0..1 blends the band in.
function wordmark(cv, x, y, n, fade = 1, field = C.FIELD) {
  const band = field === null ? null : mix(field, C.PURPLE, fade);
  if (band) for (let j = 0; j < BAND_H; j++) for (let i = 0; i < BAND_W; i++) dot(cv, x + i, y + j, band);
  let cx = x + PAD;
  [...WORD].slice(0, n).forEach((ch) => {
    FONT[ch].forEach((row, r) => [...row].forEach((p, i) => p === 'X' && dot(cv, cx + i, y + 2 + r, C.GHOST)));
    cx += FONT[ch][0].length + GAP;
  });
}

// When things happen, in seconds.
const T = { fade: 0.7, pass1: 0.3, pass2: 2.0, mark: 3.6, type: 5.1, promise: 6.6, end: 7.6 };

// One frame at time s for a cols x rows screen, as a single string.
export function frame(s, cols, rows) {
  // No colour: no field. 256 colours: one step, since a blend would pass through navy.
  const field = MODE === 'none' ? null : MODE === '256' ? (s < T.fade / 2 ? C.BLACK : C.FIELD) : mix(C.BLACK, C.FIELD, s / T.fade);
  const cv = canvas(cols, rows * 2, field);
  const top = Math.max(0, (rows >> 1) - 5); // text row of the band
  const big = cols >= BAND_W + 2;
  const bx = Math.max(0, (cols - (big ? BAND_W : 21)) >> 1);
  if (s >= T.mark) {
    const fade = (s - T.mark) / 0.3, n = Math.floor((s - T.mark) / 0.15);
    if (big) wordmark(cv, bx, top * 2, n, fade, field);
    else if (field) for (let j = 0; j < 6; j++) for (let i = 0; i < 21; i++) dot(cv, bx + i, top * 2 + 3 + j, mix(field, C.PURPLE, fade));
  }
  if (s >= T.pass1 && s < T.pass2 + 1.7) {
    if (s < T.pass2) fly(cv, s - T.pass1, { y: Math.round(rows * 0.3) });
    else fly(cv, s - T.pass2, { y: Math.round(rows * 1.1), flip: true });
  }
  const text = {};
  const put = (row, str, c) => (text[row] ??= []).push([Math.max(0, (cols - str.length) >> 1), str, c]);
  if (!big && s >= T.mark) {
    const n = Math.floor((s - T.mark) / 0.15);
    text[top + 3] = [[bx + 2, [...WORD].slice(0, n).join(' '), C.GHOST]];
  }
  if (s >= T.type) {
    const n = Math.floor((s - T.type) * 38), line = TAGLINE.slice(0, n);
    put(top + 8, (line + (n < TAGLINE.length ? '▌' : ' ')).padEnd(TAGLINE.length + 1), C.GHOST);
  }
  if (s >= T.promise) put(top + 10, PROMISE.slice(0, Math.floor((s - T.promise) * 60)).padEnd(PROMISE.length), C.MINT);
  return render(cv, text).join('\r\n');
}

// The last frame, printed into the normal screen so it stays in scrollback.
export function still() {
  const { cols } = size();
  const out = [''];
  if (cols >= BAND_W + 2) {
    const cv = canvas(BAND_W, BAND_H);
    wordmark(cv, 0, 0, WORD.length, 1, MODE === 'none' ? null : C.PURPLE);
    out.push(...render(cv).filter((l) => l.trim()).map((l) => '  ' + l));
  } else {
    out.push('  ' + paint(C.GHOST, [...WORD].join(' '), true));
  }
  out.push('', '  ' + paint(C.GHOST, TAGLINE), '  ' + paint(C.MINT, PROMISE), '');
  return out.join('\n') + '\n';
}

export const canAnimate = (anim) => {
  const { cols, rows } = size();
  return anim && MODE !== 'none' && process.stdout.isTTY && cols >= 40 && rows >= 16;
};

// Play the cutscene, then leave the still behind. Resolves when done or skipped.
export function play({ anim = true } = {}) {
  if (!canAnimate(anim)) {
    process.stdout.write(still());
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const { stdin, stdout } = process;
    let s = 0, last = '', timer;
    const finish = () => {
      clearInterval(timer);
      if (stdin.isTTY) { stdin.off('data', onKey); stdin.pause(); }
      restore();
      stdout.write(still());
      resolve();
    };
    const onKey = (k) => (k[0] === 3 ? bail() : finish());
    enterScreen();
    if (stdin.isTTY) { setRaw(true); stdin.on('data', onKey); stdin.resume(); }
    timer = setInterval(() => {
      const { cols, rows } = size();
      if (cols < 40 || rows < 16) return finish();
      const dims = `${cols}x${rows}`;
      stdout.write((dims !== last ? '\x1b[2J' : '') + '\x1b[H' + frame(s, cols, rows));
      last = dims;
      s += 0.05;
      if (s > T.end) finish();
    }, 50);
  });
}

// For tests and docs: n frames spread across the timeline, printed one after another.
export function frames(n, cols = size().cols, rows = size().rows) {
  for (let i = 0; i < n; i++) {
    const s = n === 1 ? T.end : (T.end * i) / (n - 1);
    process.stdout.write(`-- t=${s.toFixed(2)}s --\n${frame(s, cols, rows)}\n`);
  }
}
