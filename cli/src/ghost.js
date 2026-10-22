// The Spookling ghost, generated from brand/logos/ghost-mark.png by tools/make-ghost.py (see src/ghost-sprite.js).
// BIG is 38 x 40 pixels (20 text rows), downsampled from the logo. LITTLE (13 x 14, 7 rows) and MID (19 x 20, 10 rows)
// are drawn by hand in the same script, since a downsample that small turns the face into a smudge.
// The mark is cropped at its left and bottom edges, as the brand mark is; it is drawn as it is, never mirrored.
import { C, MODE, canvas, cursor, dot, render, size, sleep, write } from './term.js';
import { FRAMES, H, MID as MID_FRAMES, MID_H, MID_W, PALETTE, SMALL, SMALL_H, SMALL_W, W } from './ghost-sprite.js';

const grid = (f) => f.map((row) => [...row].map((p) => PALETTE[p]));
export const BIG = { w: W, h: H, frames: FRAMES.map(grid) };
export const LITTLE = { w: SMALL_W, h: SMALL_H, frames: SMALL.map(grid) };
export const MID = { w: MID_W, h: MID_H, frames: MID_FRAMES.map(grid) };
export const TAIL = 0.18; // seconds per tail frame

// The ghost at pixel (x, y) at time s: only the tail streaks change between frames.
export function draw(cv, g, x, y, s) {
  const f = g.frames[Math.floor(s / TAIL) % g.frames.length];
  f.forEach((row, r) => row.forEach((c, i) => c && dot(cv, x + i, y + r, c)));
}

// Fly g left to right across cv over dur seconds at flight line y, bobbing; stopAt parks it there (pixel x).
// Returns false once it has left (or parked).
export function fly(cv, g, s, { dur = 1.7, y = 0, stopAt = null } = {}) {
  const end = stopAt ?? cv.w;
  const t = Math.min(s / dur, 1), ease = stopAt == null ? t : 1 - (1 - t) ** 2; // a parked ghost slows to a stop
  draw(cv, g, Math.round(-g.w + ease * (end + g.w)), y + Math.round(Math.sin(s * 5)), s);
  return t < 1;
}

// A speech bubble on cv: GHOST fill, BLACK text on text row `row`, one pixel of padding above and below, rounded
// corners, and a tail from a bottom corner down toward the ghost (side: where the ghost is). x is the first text
// column. Returns the text overlay for render().
export function bubble(cv, text, x, row, side = 'left') {
  const y0 = row * 2 - 1, y1 = row * 2 + 2, x0 = x - 2, x1 = x + text.length + 1; // pixel box
  for (let y = y0; y <= y1; y++) {
    for (let i = x0; i <= x1; i++) if (!((y === y0 || y === y1) && (i === x0 || i === x1))) dot(cv, i, y, C.GHOST);
  }
  const d = side === 'left' ? -1 : 1, cx = side === 'left' ? x0 : x1;
  [[0, 0], [1, 1], [2, 1], [3, 2]].forEach(([i, j]) => dot(cv, cx + d * i, y1 + j, C.GHOST));
  return { [row]: [[x, text, C.BLACK]] };
}

// The done screen: on a band of FIELD, the small ghost flies in and parks on the right with its last line beside it.
// Printed into the normal screen, so the last frame stays in scrollback. No colour: just the line.
export async function flyOnce(line, { anim = true } = {}) {
  const { cols } = size();
  const w = Math.min(cols, 100), rows = 8, g = LITTLE, stopAt = w - g.w - 3, y = 1;
  const frame = (s) => {
    const cv = canvas(w, rows * 2, C.FIELD);
    const moving = fly(cv, g, s, { dur: 1.6, y, stopAt });
    const text = !moving && line.length + 8 < stopAt ? bubble(cv, line, stopAt - line.length - 5, 3, 'right') : {};
    return { lines: render(cv, text), moving };
  };
  if (MODE === 'none') return write(`   spookling: ${line}\n`);
  const lines = (s) => frame(s).lines.map((l) => `\r${l}\x1b[K`).join('\n');
  if (!anim || !process.stdout.isTTY) return write(lines(10) + '\n');
  write('\n'.repeat(rows) + `\x1b[${rows}A`);
  cursor(false);
  for (let s = 0; frame(s).moving; s += 1 / 12) {
    process.stdout.write(lines(s) + `\x1b[${rows - 1}A`); // frames are not kept in the log; the last one is
    await sleep(83);
  }
  write(lines(10) + '\n');
  cursor(true);
}
