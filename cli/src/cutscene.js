// The intro: the field fades in, the big ghost crosses, the small ghost flies in and parks above SPOOKLING, which
// builds letter by letter, the tagline types out, then the MINT line. About 7 s at 20 fps; any key skips.
// The tagline is shown once in the whole flow: here, or in the still when there is no animation.
import process from 'node:process';
import { C, LEGACY, MODE, bail, canvas, dot, enterScreen, mix, paint, render, restore, setRaw, size, write } from './term.js';
import { BIG, LITTLE, draw, fly } from './ghost.js';

export const TAGLINE = 'An AI assistant that works for you while you sleep';
export const PROMISE = 'Memory that stays right for years.';

// Block letters, 7 px tall, GHOST.
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

// The first n letters at pixel (x, y), on a band of colour band (null: no band).
function wordmark(cv, x, y, n, band) {
  if (band) for (let j = 0; j < BAND_H; j++) for (let i = 0; i < BAND_W; i++) dot(cv, x + i, y + j, band);
  let cx = x + PAD;
  [...WORD].slice(0, n).forEach((ch) => {
    FONT[ch].forEach((row, r) => [...row].forEach((p, i) => p === 'X' && dot(cv, cx + i, y + 2 + r, C.GHOST)));
    cx += FONT[ch][0].length + GAP;
  });
}

// When things happen, in seconds.
const T = { fade: 0.7, pass: 0.3, park: 2.4, mark: 3.2, type: 4.6, promise: 6.0, end: 7.0 };

// One frame at time s for a cols x rows screen, as a single string. label: a quiet note in the top right corner.
export function frame(s, cols, rows, label = '') {
  // No colour: no field. 256 colours: one step, since a blend would pass through the wrong colours.
  const field = MODE === 'none' ? null : MODE === '256' ? (s < T.fade / 2 ? C.BLACK : C.FIELD) : mix(C.BLACK, C.FIELD, s / T.fade);
  const cv = canvas(cols, rows * 2, field);
  const big = cols >= BAND_W + 2, perched = rows >= 19; // room for the band, and for the small ghost above it
  const top = Math.max(0, (rows - (perched ? 18 : 10)) >> 1); // text row the lockup starts on
  const band = top + (perched ? 8 : 0), bx = Math.max(0, (cols - (big ? BAND_W : 21)) >> 1);
  // The pass: the big ghost when it fits (20 rows), else the small one.
  const g = rows >= 22 && cols >= 60 ? BIG : LITTLE;
  if (s >= T.pass && s < T.park) fly(cv, g, s - T.pass, { dur: T.park - T.pass, y: Math.max(1, rows - (g.h >> 1)) - 1 });
  if (perched && s >= T.park) {
    const x = (cols - LITTLE.w) >> 1;
    if (s < T.mark) fly(cv, LITTLE, s - T.park, { dur: T.mark - T.park, y: top * 2 + 1, stopAt: x });
    else draw(cv, LITTLE, x, top * 2 + 1 + Math.round(Math.sin((s - T.park) * 5)), s);
  }
  const text = {};
  const put = (row, str, c, col = Math.max(0, (cols - str.length) >> 1)) => (text[row] ??= []).push([col, str, c]);
  if (s >= T.mark) {
    const n = Math.floor((s - T.mark) / 0.15);
    if (big) wordmark(cv, bx, band * 2, n, null);
    else put(band + 3, [...WORD].slice(0, n).join(' ').padEnd(17), C.GHOST, bx + 2);
  }
  if (s >= T.type) {
    const n = Math.floor((s - T.type) * 38), line = TAGLINE.slice(0, n);
    put(band + 7, (line + (n < TAGLINE.length ? '▌' : ' ')).padEnd(TAGLINE.length + 1), C.GHOST);
  }
  if (s >= T.promise) put(band + 9, PROMISE.slice(0, Math.floor((s - T.promise) * 60)).padEnd(PROMISE.length), C.MINT);
  if (label && cols >= label.length + 4) put(0, label, mix(C.FIELD, C.GHOST, 0.55), cols - label.length - 2);
  return render(cv, text).join('\r\n');
}

// The last frame as a still, for the normal screen. tagline: false after the animation, which already showed it.
export function still({ tagline = true } = {}) {
  const { cols } = size();
  const out = [''];
  if (cols >= BAND_W + 2) {
    const cv = canvas(BAND_W, BAND_H);
    wordmark(cv, 0, 0, WORD.length, MODE === 'none' ? null : C.PURPLE);
    out.push(...render(cv).filter((l) => l.trim()).map((l) => '  ' + l));
  } else {
    out.push('  ' + paint(C.GHOST, [...WORD].join(' '), true));
  }
  out.push('');
  if (tagline) out.push('  ' + paint(C.GHOST, TAGLINE));
  out.push('  ' + paint(C.MINT, PROMISE), '');
  return out.join('\n') + '\n';
}

export const canAnimate = (anim) => {
  const { cols, rows } = size();
  return anim && MODE !== 'none' && !LEGACY && process.stdout.isTTY && cols >= 40 && rows >= 16;
};

// Play the intro. keep: stay in the alternate screen afterwards (the install stage follows); otherwise leave it and
// print the still without the tagline. No animation: print the still with it. Resolves to true if it animated.
export function play({ anim = true, keep = false, label = '' } = {}) {
  if (!canAnimate(anim)) {
    write(still());
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const { stdin, stdout } = process;
    const t0 = Date.now();
    let last = '', timer, done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearInterval(timer);
      if (stdin.isTTY) { stdin.off('data', onKey); stdin.pause(); setRaw(false); }
      if (keep) stdout.write('\x1b[0m\x1b[2J\x1b[H');
      else { restore(); write(still({ tagline: false })); }
      resolve(true);
    };
    const onKey = (k) => (k[0] === 3 ? bail() : finish());
    enterScreen();
    if (stdin.isTTY) { setRaw(true); stdin.on('data', onKey); stdin.resume(); }
    timer = setInterval(() => {
      const { cols, rows } = size();
      if (cols < 40 || rows < 16) return finish();
      const dims = `${cols}x${rows}`, s = (Date.now() - t0) / 1000; // wall clock, so a slow terminal drops frames, not time
      stdout.write((dims !== last ? '\x1b[2J' : '') + '\x1b[H' + frame(s, cols, rows, label));
      last = dims;
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
