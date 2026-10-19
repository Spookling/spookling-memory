// The Spookling ghost: 26 x 14 pixels, drawn as 7 text rows of half blocks.
// K hat and eyes, M hat band, G body, L tail streaks, . transparent.
import { C, canvas, dot, mix, render, size, sleep, MODE } from './term.js';

const PAL = { K: C.BLACK, M: C.PURPLE_MID, G: C.GHOST, L: C.LILAC };

const HEAD = [
  '.......KKKK...............',
  '.....KK..KKKKK............',
  '..........MMMMMMM.........',
  '......KKKKKKKKKKKKKKKKKKKK',
  '.KKKKKKKKKKKKKKKKKKKKKK...',
  '.......GGGGGGGGGGGGGGGG...',
  '......GGGGGGGGKKGGGKKGGG..',
  '......GGGGGGGGKKGGGKKGGG..',
  '.....GGGGGGGGGKKGGGKKGGG..',
];
// Rows 9 to 13: the body, and the tail tip (first five columns) that waves.
const BODY = [
  'GGGGGGGGGGGGGGGGGGG..',
  'GGLGGGGGGGKKKKGGGGG..',
  'GLGGGLGGGGGKKGGGGG...',
  'LGGGLGGGGGGGGGGGG....',
  '.GGLGGGGGGGGGGGG.....',
];
// One tail tip every 0.15 s: high, middle, low, middle.
const TIPS = [
  ['..GGG', 'LGGGG', '.LGGG', '...GG', '.....'],
  ['.....', '..GGG', 'LLGGG', '..GGG', '.....'],
  ['.....', '....G', '..GGG', 'LLGGG', '.....'],
  ['.....', '..GGG', 'LLGGG', '..GGG', '.....'],
];
const TAILS = TIPS.map((tip) => tip.map((t, i) => t + BODY[i]));

export const FRAMES = TAILS.map((t) => [...HEAD, ...t].map((row) => [...row].map((p) => PAL[p] ?? null)));
export const W = 26, H = 14;

// Draw the ghost on cv at time s (seconds) of a pass across the canvas.
// y is the pixel row of the flight line; flip flies right to left.
// A few LILAC sparks trail behind the tail. Returns false once the ghost has left.
export function fly(cv, s, { dur = 1.7, y = 0, flip = false, stopAt = null } = {}) {
  const p = Math.min(s / dur, 1);
  const end = stopAt ?? cv.w;
  const at = (t) => Math.round(-W + Math.min(t / dur, 1) * (end + W));
  const bob = (t) => y + Math.round(2 * Math.sin(t * 8));
  const X = (x, w = 1) => (flip ? cv.w - x - w : x);
  const frame = FRAMES[Math.floor(s / 0.15) % FRAMES.length];
  for (let i = 1; i <= 5; i++) {
    const t = s - i * 0.03;
    if (t < 0) break;
    const bg = cv.px[bob(t) * cv.w] ?? C.FIELD;
    dot(cv, X(at(t) - i), bob(t) + 11 + (i & 1), mix(C.LILAC, bg, i / 6));
  }
  const x0 = at(s), y0 = bob(s);
  frame.forEach((row, r) => row.forEach((c, x) => c && dot(cv, X(x0 + x), y0 + r, c)));
  return p < 1;
}

// The done screen: the ghost flies across once in the normal screen and parks on the right.
export async function flyOnce(out = process.stdout, { anim = true } = {}) {
  const { cols } = size();
  const cv = () => canvas(Math.min(cols, 100), H + 4, C.FIELD); // the hat needs the purple behind it
  if (!anim || MODE === 'none' || cols < 40) {
    if (MODE !== 'none') {
      const c = cv();
      fly(c, 1.7, { y: 2, stopAt: c.w - W - 2 });
      out.write(render(c).join('\n') + '\n');
    }
    return;
  }
  const rows = (H + 4) / 2;
  out.write('\n'.repeat(rows) + `\x1b[${rows}A\x1b[?25l`);
  for (let s = 0; ; s += 0.05) {
    const c = cv();
    const more = fly(c, s, { y: 2, stopAt: c.w - W - 2 });
    out.write(render(c).map((l) => `\r${l}\x1b[K`).join('\n') + `\x1b[${rows - 1}A`);
    if (!more) break;
    await sleep(50);
  }
  out.write(`\x1b[${rows - 1}B\n\x1b[?25h`);
}
