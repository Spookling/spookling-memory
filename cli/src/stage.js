// The install stage: the ghost on a band at the top of the alternate screen while the steps scroll below it.
import process from 'node:process';
import { C, LEGACY, MODE, canvas, cursor, enterScreen, mix, note, paint, record, render, restore, setOnPrompt, size, sleep, write } from './term.js';
import { LITTLE, MID, bubble, draw } from './ghost.js';

const layout = (rows) => (rows >= 30 ? { g: MID, band: 10, talk: 5 } : { g: LITTLE, band: 8, talk: 3 });
const said = (line) => `   ${paint(C.LILAC, 'spookling:')} ${line}\n`;

export const fits = (anim = true) => {
  const { cols, rows } = size();
  return anim && MODE !== 'none' && !LEGACY && !!process.stdout.isTTY && cols >= 80 && rows >= 24;
};

export function stage({ anim = true, label = '', hold = 1200 } = {}) {
  const plain = { live: false, async say(line) { write(said(line)); }, close() {} };
  if (!fits(anim)) { restore(); return plain; } // leave a kept cutscene's alternate screen
  const { stdout } = process, t0 = Date.now(), { g, band, talk } = layout(size().rows), ROWS = band + 1;
  let text = '', shownAt = 0, timer = null, open = true;

  const paintStage = () => {
    const { cols } = size(), s = (Date.now() - t0) / 1000;
    const cv = canvas(cols, band * 2, C.FIELD);
    const room = Math.max(0, Math.min(cols - (text ? text.length + 4 : 0) - g.w - 8, cols - label.length - g.w - 6));
    const gx = 2 + Math.round(room * (0.5 - 0.5 * Math.cos(s * 0.5)));
    const gy = Math.round((Math.max(band * 2 - g.h, 1) / 2) * (1 + Math.sin(s * 2.5)));
    draw(cv, g, gx, gy, s);
    const over = text ? bubble(cv, text, gx + g.w + 5, talk, 'left') : {};
    if (label) (over[0] ??= []).push([cols - label.length - 2, label, mix(C.FIELD, C.GHOST, 0.55)]);
    // save the cursor, draw at absolute rows, restore it: the steps below never see the stage move
    stdout.write('\x1b7' + render(cv, over).map((l, i) => `\x1b[${i + 1};1H${l}`).join('') + `\x1b[${ROWS};1H\x1b[0m\x1b[K\x1b8`);
  };
  const busy = (on) => {
    clearInterval(timer);
    timer = null;
    if (!open) return;
    paintStage();
    cursor(!on);
    if (on) timer = setInterval(paintStage, 83);
  };
  const close = () => {
    if (!open) return;
    open = false;
    clearInterval(timer);
    setOnPrompt(() => {});
    stdout.off('resize', onResize);
    restore();
  };
  const onResize = () => {
    if (!fits()) return close();
    stdout.write(`\x1b7\x1b[${ROWS + 1};${size().rows}r\x1b8`);
    paintStage();
  };

  enterScreen();
  stdout.write(`\x1b[2J\x1b[${ROWS + 1};${size().rows}r\x1b[${ROWS + 1};1H`);
  record(true);
  setOnPrompt((waiting) => busy(!waiting));
  stdout.on('resize', onResize);
  busy(true);

  return {
    get live() { return open; },
    // waits until the previous line has been up for hold ms, so a quick step does not swallow it
    async say(line) {
      if (!open) return plain.say(line);
      const wait = shownAt + hold - Date.now();
      if (wait > 0) await sleep(wait);
      text = line;
      shownAt = Date.now();
      note(said(line));
      if (open) paintStage();
    },
    close,
  };
}
