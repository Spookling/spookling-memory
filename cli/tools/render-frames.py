#!/usr/bin/env python3
"""Render what the terminal shows to PNG, by replaying the CLI's own bytes through a small VT interpreter.

Dev only, not shipped. Needs Pillow; POSIX (it runs the CLI under a pseudo-terminal).
  python3 tools/render-frames.py                  renders/cutscene-frame.png, cutscene-end.png, wizard-frame.png (80x24),
                                                  wizard-frame-30.png (80x30)
  python3 tools/render-frames.py raw FILE OUT [COLSxROWS]   any captured byte stream (for example from `script`) to OUT
  python3 tools/render-frames.py snap FILE TEXT OUT   the same, stopped at the first TEXT (and the next stage redraw)
  python3 tools/render-frames.py video OUT.mp4 [ROOT] [COLSxROWS]  `demo` (intro, then the steps) from ROOT, every
                                                  frame at 20 fps, 80x24 by default: OUT/ holds the PNGs, OUT.mp4 is
                                                  H.264 yuv420p (needs ffmpeg)

Each cell is 10 x 20 px: a half block is two 10 x 10 pixels, left and eighth blocks are drawn as rectangles,
a wide character (emoji) takes two cells in Noto Color Emoji, any other character is drawn in DejaVu Sans Mono
(Segoe UI Symbol, then DejaVu Sans, for glyphs it lacks, when fontTools is installed).
Default colours are a dark terminal's (#0C0C0C on #CCCCCC).
"""
import codecs, functools, os, pty, re, select, subprocess, sys, time, fcntl, struct, tempfile, termios, unicodedata
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

CLI = Path(__file__).resolve().parent.parent
RENDERS = CLI / 'renders'
COLS, ROWS = 80, 24
CW, CH = 10, 20
BG, FG = (12, 12, 12), (204, 204, 204)
FONT = next((p for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',) if os.path.exists(p)), None)
FALLBACK = [p for p in ('/mnt/c/Windows/Fonts/seguisym.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf') if os.path.exists(p)]
EMOJI = next((p for p in ('/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',) if os.path.exists(p)), None)
LEFT = {'▏': 1, '▎': 2, '▍': 3, '▌': 4, '▋': 5, '▊': 6, '▉': 7}  # eighths of the cell, from the left
wide = lambda ch: unicodedata.east_asian_width(ch) in 'WF'


def font_for(ch, cache={}):
    """DejaVu Sans Mono, or the first fallback that has the glyph."""
    if ch not in cache:
        cache[ch] = FONT
        try:
            from fontTools.ttLib import TTFont
            maps = cache.setdefault('maps', {p: TTFont(p).getBestCmap() for p in [FONT] + FALLBACK if p})
            cache[ch] = next((p for p in [FONT] + FALLBACK if p and ord(ch) in maps[p]), FONT)
        except ImportError:
            pass
    return cache[ch]


def xterm256(n):
    if n < 16:
        base = [(0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
                (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0), (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)]
        return base[n]
    if n < 232:
        n -= 16
        v = [0, 95, 135, 175, 215, 255]
        return (v[n // 36], v[n // 6 % 6], v[n % 6])
    g = 8 + (n - 232) * 10
    return (g, g, g)


class Screen:
    """The subset the CLI writes: CUP, CUU/CUD/CUF/CUB, EL, ED, DECSTBM, SGR (24-bit, 256, 16), DECSC/DECRC,
    ?1049 (alternate screen), CR, LF, BS, autowrap."""

    def __init__(self, cols=COLS, rows=ROWS):
        self.cols, self.rows = cols, rows
        self.main = self.blank()
        self.alt = None
        self.x = self.y = 0
        self.fg = self.bg = None
        self.top, self.bot = 0, rows - 1
        self.saved = (0, 0, None, None)
        self.wrap = False

    def blank(self):
        return [[(' ', None, None) for _ in range(self.cols)] for _ in range(self.rows)]

    @property
    def buf(self):
        return self.alt if self.alt is not None else self.main

    def scroll(self):
        b = self.buf
        del b[self.top]
        b.insert(self.bot, [(' ', None, None) for _ in range(self.cols)])

    def lf(self):
        if self.y == self.bot:
            self.scroll()
        elif self.y < self.rows - 1:
            self.y += 1

    def put(self, ch):
        if unicodedata.category(ch) in ('Mn', 'Cf') or ch == '️':
            return  # zero width: variation selectors, joiners
        if self.wrap:
            self.x = 0
            self.lf()
            self.wrap = False
        self.buf[self.y][self.x] = (ch, self.fg, self.bg)
        if wide(ch) and self.x < self.cols - 1:
            self.x += 1
            self.buf[self.y][self.x] = ('', self.fg, self.bg)  # the wide character's right half
        if self.x == self.cols - 1:
            self.wrap = True
        else:
            self.x += 1

    def sgr(self, ps):
        ps = ps or [0]
        i = 0
        while i < len(ps):
            p = ps[i]
            if p == 0:
                self.fg = self.bg = None
            elif p in (38, 48) and i + 1 < len(ps):
                if ps[i + 1] == 2:
                    c = tuple(ps[i + 2:i + 5]); i += 4
                else:
                    c = xterm256(ps[i + 2]); i += 2
                if p == 38: self.fg = c
                else: self.bg = c
            elif p == 39: self.fg = None
            elif p == 49: self.bg = None
            elif 30 <= p <= 37: self.fg = xterm256(p - 30)
            elif 40 <= p <= 47: self.bg = xterm256(p - 40)
            elif 90 <= p <= 97: self.fg = xterm256(p - 82)
            i += 1

    def feed(self, data):
        for m in re.finditer(r'\x1b\[([?]?)([\d;]*)([A-Za-z@])|\x1b([78])|\x1b.|([\r\n\b])|([^\x1b\r\n\b\x00-\x1f])', data):
            priv, params, cmd, esc, ctl, ch = m.groups()
            if ch:
                self.put(ch)
            elif ctl:
                self.wrap = False
                if ctl == '\r': self.x = 0
                elif ctl == '\n': self.lf()
                elif ctl == '\b': self.x = max(0, self.x - 1)
            elif esc == '7':
                self.saved = (self.x, self.y, self.fg, self.bg)
            elif esc == '8':
                self.x, self.y, self.fg, self.bg = self.saved
                self.wrap = False
            elif cmd:
                ps = [int(v) if v else 0 for v in params.split(';')] if params else []
                n = (ps[0] if ps else 0) or 1
                if priv == '?':
                    if 1049 in ps and cmd == 'h':
                        self.alt = self.blank()
                    elif 1049 in ps and cmd == 'l':
                        self.alt = None
                    continue
                self.wrap = False if cmd != 'm' else self.wrap
                if cmd == 'H':
                    self.y = min(self.rows, (ps[0] if ps else 1) or 1) - 1
                    self.x = min(self.cols, (ps[1] if len(ps) > 1 else 1) or 1) - 1
                elif cmd == 'A': self.y = max(self.top if self.y >= self.top else 0, self.y - n)
                elif cmd == 'B': self.y = min(self.bot if self.y <= self.bot else self.rows - 1, self.y + n)
                elif cmd == 'C': self.x = min(self.cols - 1, self.x + n)
                elif cmd == 'D': self.x = max(0, self.x - n)
                elif cmd == 'K':
                    mode = ps[0] if ps else 0
                    rng = range(self.x, self.cols) if mode == 0 else range(0, self.x + 1) if mode == 1 else range(self.cols)
                    for i in rng: self.buf[self.y][i] = (' ', None, self.bg)
                elif cmd == 'J':
                    if (ps[0] if ps else 0) == 2:
                        for r in range(self.rows):
                            self.buf[r] = [(' ', None, self.bg) for _ in range(self.cols)]
                elif cmd == 'r':
                    self.top = (ps[0] if ps else 1) - 1
                    self.bot = (ps[1] if len(ps) > 1 else self.rows) - 1
                    self.x = self.y = 0
                elif cmd == 'm':
                    self.sgr(ps)

    def png(self, path):
        im = Image.new('RGB', (self.cols * CW, self.rows * CH), BG)
        d = ImageDraw.Draw(im)
        font = _font
        glyphs = []
        for r, row in enumerate(self.buf):
            for c, (ch, fg, bg) in enumerate(row):
                fg, bg = fg or FG, bg or BG
                x, y = c * CW, r * CH
                top, bottom = {'▀': (fg, bg), '▄': (bg, fg), '█': (fg, fg), ' ': (bg, bg)}.get(ch, (bg, bg))
                d.rectangle([x, y, x + CW - 1, y + CH // 2 - 1], fill=top)
                d.rectangle([x, y + CH // 2, x + CW - 1, y + CH - 1], fill=bottom)
                if ch in LEFT:
                    d.rectangle([x, y, x + CW * LEFT[ch] // 8 - 1, y + CH - 1], fill=fg)
                elif ch == '▐':
                    d.rectangle([x + CW // 2, y, x + CW - 1, y + CH - 1], fill=fg)
                elif ch and ch not in '▀▄█ ':
                    glyphs.append((x, y, ch, fg))
        for x, y, ch, fg in glyphs:  # after every background, so a wide glyph is not painted over by its right half
            if wide(ch) and EMOJI:
                e = ImageFont.truetype(EMOJI, 109)  # the only size the bitmap font has
                g = Image.new('RGBA', (136, 128))
                ImageDraw.Draw(g).text((0, 0), ch, font=e, embedded_color=True)
                g = g.crop(g.getbbox() or (0, 0, 1, 1))
                s = min(2 * CW / g.width, (CH - 2) / g.height)
                g = g.resize((max(1, round(g.width * s)), max(1, round(g.height * s))), Image.LANCZOS)
                im.paste(g, (x + (2 * CW - g.width) // 2, y + (CH - g.height) // 2), g)
            else:
                d.text((x, y + 1), ch, font=font(font_for(ch)), fill=fg)
        im.save(path)
        return im.size


@functools.cache
def _font(path):
    return ImageFont.truetype(path, 16) if path else ImageFont.load_default()


def run_pty(args, env, until=None, timeout=60, cols=COLS, rows=ROWS, stamps=False):
    """Run under a pseudo-terminal of cols x rows; return everything it wrote (stamps: as (seconds, text) pairs)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(env.pop('_CWD', str(CLI)))
        os.execvpe(args[0], args, env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    dec, parts, t0 = codecs.getincrementaldecoder('utf8')('replace'), [], time.time()
    end = t0 + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            parts.append((time.time() - t0, dec.decode(chunk)))
    os.waitpid(pid, 0)
    return parts if stamps else ''.join(t for _, t in parts)


def env(**kw):
    e = {k: v for k, v in os.environ.items() if k not in ('NO_COLOR', 'CI')}
    e.update(TERM='xterm-256color', COLORTERM='truecolor', **kw)
    return e


def node_frame(s, label=''):
    js = f"import('./src/cutscene.js').then(m => process.stdout.write(m.frame({s}, {COLS}, {ROWS}, {label!r})))"
    return subprocess.run(['node', '-e', js], cwd=CLI, env=env(FORCE_COLOR='1'), capture_output=True, check=True).stdout.decode()


def upto(stream, marker, after='\x1b8'):
    """The stream up to the first marker, plus through the next stage redraw (so the stage is drawn as it was)."""
    i = stream.index(marker)
    j = stream.find(after, i)
    return stream[:(j + len(after)) if j >= 0 else i + len(marker)]


def video(out, root, fps=20, cols=COLS, rows=ROWS):
    """Replay the demo's timed bytes and snapshot the screen every 1/fps s, then encode."""
    with tempfile.TemporaryDirectory() as tmp:
        parts = run_pty(['node', str(Path(root) / 'bin' / 'spookling-memory.js'), 'demo'], env(_CWD=tmp, HOME=tmp),
                        cols=cols, rows=rows, timeout=400, stamps=True)
    out = Path(out)
    frames = out.with_suffix('')
    frames.mkdir(parents=True, exist_ok=True)
    sc, i, pending = Screen(cols=cols, rows=rows), 0, ''
    for k in range(int(parts[-1][0] * fps) + 2 * fps):  # two more seconds on the last screen
        while i < len(parts) and parts[i][0] <= k / fps:
            pending += parts[i][1]
            i += 1
        cut = re.search(r'\x1b[\[\d;?]*$', pending)  # an escape split across reads waits for its end
        sc.feed(pending[:cut.start()] if cut else pending)
        pending = pending[cut.start():] if cut else ''
        sc.png(frames / f'{k:05d}.png')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(fps), '-i', str(frames / '%05d.png'),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-movflags', '+faststart', str(out)], check=True)
    print(out, f'{k + 1} frames, {parts[-1][0]:.1f} s of demo; PNGs in {frames}')


def main():
    RENDERS.mkdir(exist_ok=True)
    if len(sys.argv) > 2 and sys.argv[1] == 'video':
        size = map(int, sys.argv[4].split('x')) if len(sys.argv) > 4 else (COLS, ROWS)
        return video(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else CLI.parent, 20, *size)
    if len(sys.argv) > 1 and sys.argv[1] in ('raw', 'snap'):
        stream = open(sys.argv[2], encoding='utf8', errors='replace', newline='').read()  # keep \r\n as written
        size = re.fullmatch(r'(\d+)x(\d+)', sys.argv[-1]) if sys.argv[1] == 'raw' and len(sys.argv) > 4 else None
        sc = Screen(int(size[1]), int(size[2])) if size else Screen()
        sc.feed(upto(stream, sys.argv[3]) if sys.argv[1] == 'snap' else stream)
        out = sys.argv[-2] if size else sys.argv[-1]
        print(out, sc.png(out))
        return
    for name, s in (('cutscene-frame', 1.2), ('cutscene-end', 7.0)):
        sc = Screen()
        sc.feed('\x1b[H' + node_frame(s, 'demo, nothing is written'))
        print(f'renders/{name}.png', sc.png(RENDERS / f'{name}.png'), f'(t = {s} s)')
    # 80 x 24: the 13 x 14 ghost on a 9-row stage. 80 x 30: the 19 x 20 ghost on an 11-row stage.
    for rows, name in ((24, 'wizard-frame'), (30, 'wizard-frame-30')):
        with tempfile.TemporaryDirectory() as tmp:
            stream = run_pty(['node', str(CLI.parent / 'bin' / 'spookling-memory.js'), 'demo', '--fast'], env(_CWD=tmp, HOME=tmp), rows=rows)
            left = os.listdir(tmp)
        log = Path(tempfile.gettempdir()) / f'spookling-{name}.log'  # for `snap`
        open(log, 'w', encoding='utf8', newline='').write(stream)
        sc = Screen(rows=rows)
        sc.feed(upto(stream, '.mcp.json'))
        print(f'renders/{name}.png', sc.png(RENDERS / f'{name}.png'), f'(demo at 80 x {rows}, step 5; log {log})',
              'files left in the demo folder:', left)


if __name__ == '__main__':
    main()
