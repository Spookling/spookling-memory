// The Spookling ghost, drawn as it is, never mirrored: the mark is cropped at its left and bottom edges.
import { C, MODE, canvas, cursor, dot, render, size, sleep, write } from './term.js';
import { FRAMES, H, PALETTE, SMALL, SMALL_H, SMALL_W, W } from './ghost-sprite.js';

const grid = (f) => f.map((row) => [...row].map((p) => PALETTE[p]));
export const BIG = { w: W, h: H, frames: FRAMES.map(grid) };
export const LITTLE = { w: SMALL_W, h: SMALL_H, frames: SMALL.map(grid) };
// text rows a band needs to hold g: 8 for the 13 x 14 ghost, half its height for a bigger one
export const bandRows = (g) => Math.max(8, (g.h + 1) >> 1);
const TAIL = 0.18; // seconds per tail frame

export function draw(cv, g, x, y, s) {
  const f = g.frames[Math.floor(s / TAIL) % g.frames.length];
  f.forEach((row, r) => row.forEach((c, i) => c && dot(cv, x + i, y + r, c)));
}

// false once it has left, or parked at stopAt
export function fly(cv, g, s, { dur = 1.7, y = 0, stopAt = null } = {}) {
  const end = stopAt ?? cv.w;
  const t = Math.min(s / dur, 1), ease = stopAt == null ? t : 1 - (1 - t) ** 2;
  draw(cv, g, Math.round(-g.w + ease * (end + g.w)), y + Math.round(Math.sin(s * 5)), s);
  return t < 1;
}

// a line broken at spaces into rows of at most width characters
function greedy(text, width) {
  const out = [''];
  for (const word of text.split(' ')) {
    const last = out[out.length - 1];
    if (last && last.length + 1 + word.length > width) out.push(word);
    else out[out.length - 1] = last ? `${last} ${word}` : word;
  }
  return out;
}
// as few rows as greedy, but as even as they go, so no word is left alone on the last row
export function wrap(text, width) {
  const n = greedy(text, width).length;
  for (let w = Math.ceil(text.length / n); w < width; w++) if (greedy(text, w).length === n) return greedy(text, w);
  return greedy(text, width);
}

// x is the first text column, row the first text row; returns the text overlay for render()
export function bubble(cv, text, x, row, side = 'left', width = Infinity) {
  const lines = wrap(text, width), n = lines.length, tw = Math.max(...lines.map((l) => l.length));
  const y0 = row * 2 - 1, y1 = (row + n - 1) * 2 + 2, x0 = x - 2, x1 = x + tw + 1;
  for (let y = y0; y <= y1; y++) {
    for (let i = x0; i <= x1; i++) if (!((y === y0 || y === y1) && (i === x0 || i === x1))) dot(cv, i, y, C.GHOST);
  }
  const d = side === 'left' ? -1 : 1, cx = side === 'left' ? x0 : x1;
  [[0, 0], [1, 1], [2, 1], [3, 2]].forEach(([i, j]) => dot(cv, cx + d * i, y1 + j, C.GHOST));
  return Object.fromEntries(lines.map((l, i) => [row + i, [[x, l, C.BLACK]]]));
}

export async function flyOnce(line, { anim = true } = {}) {
  const { cols } = size();
  const w = Math.min(cols, 100), g = LITTLE, rows = bandRows(g), stopAt = w - g.w - 3, y = (rows * 2 - g.h) >> 1;
  const said = wrap(line, stopAt - 8), tw = Math.max(...said.map((l) => l.length));
  const frame = (s) => {
    const cv = canvas(w, rows * 2, C.FIELD);
    const moving = fly(cv, g, s, { dur: 1.6, y, stopAt });
    const text = !moving && tw + 8 < stopAt ? bubble(cv, line, stopAt - tw - 5, (rows - said.length) >> 1, 'right', stopAt - 8) : {};
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
