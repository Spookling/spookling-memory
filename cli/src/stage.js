// The install stage: the top rows of the screen, where the small ghost drifts and bobs on a purple band and says one
// line per step in a speech bubble, while the steps scroll below it (a scroll region in the alternate screen).
// Redrawn at 12 fps while a step runs and held still while a prompt waits, so typing is never disturbed. When the
// stage closes, the log of the steps is printed again in the normal screen. Without the room (80 x 24), colour, a
// terminal or animation, the ghost's lines print as plain text among the steps instead.
import process from 'node:process';
import { C, LEGACY, MODE, canvas, cursor, enterScreen, mix, note, paint, record, render, restore, setOnPrompt, size, sleep, write } from './term.js';
import { LITTLE, bubble, draw } from './ghost.js';

export const ROWS = 9; // 8 rows of band, then a blank row
const said = (line) => `   ${paint(C.LILAC, 'spookling:')} ${line}\n`;

export const fits = (anim = true) => {
  const { cols, rows } = size();
  return anim && MODE !== 'none' && !LEGACY && !!process.stdout.isTTY && cols >= 80 && rows >= 24;
};

// hold: ms a line stays up before the next one may replace it, so a quick step does not swallow its line.
export function stage({ anim = true, label = '', hold = 1200 } = {}) {
  const plain = { live: false, async say(line) { write(said(line)); }, busy() {}, close() {} };
  if (!fits(anim)) { restore(); return plain; } // restore: leave a cutscene's alternate screen, if one was kept
  const { stdout } = process, t0 = Date.now();
  let text = '', shownAt = 0, timer = null, open = true;

  const paintStage = () => {
    const { cols } = size(), s = (Date.now() - t0) / 1000;
    const cv = canvas(cols, (ROWS - 1) * 2, C.FIELD);
    // Drift between the left edge and the room the bubble and the label leave; about 12 s a lap. Bob a pixel.
    const room = Math.max(0, Math.min(cols - (text ? text.length + 4 : 0) - LITTLE.w - 8, cols - label.length - LITTLE.w - 6));
    const gx = 2 + Math.round(room * (0.5 - 0.5 * Math.cos(s * 0.5))), gy = 1 + Math.round(Math.sin(s * 2.5));
    draw(cv, LITTLE, gx, gy, s);
    const over = text ? bubble(cv, text, gx + LITTLE.w + 5, 3, 'left') : {};
    if (label) (over[0] ??= []).push([cols - label.length - 2, label, mix(C.FIELD, C.GHOST, 0.55)]);
    // Save the cursor, draw at absolute rows, restore it: the steps below never see the stage move.
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
    restore(); // scroll region off, normal screen back, cursor shown, and the log printed again
  };
  const onResize = () => {
    if (!fits()) return close(); // too small now: the rest prints plainly
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
    // Waits until the previous line has been up for hold ms. The line is kept in the log as plain text.
    async say(line) {
      if (!open) return plain.say(line);
      const wait = shownAt + hold - Date.now();
      if (wait > 0) await sleep(wait);
      text = line;
      shownAt = Date.now();
      note(said(line));
      if (open) paintStage();
    },
    busy,
    close,
  };
}
