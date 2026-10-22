#!/usr/bin/env python3
"""Build cli/src/ghost-sprite.js from the brand mark, and render it back to PNG to check it by eye.

Dev only, not shipped. Needs Pillow.  python3 tools/make-ghost.py [H ...]   (default: 40 48 56; the first is written)
Writes renders/ghost-sprite-WxH.png (12x, on PURPLE) and renders/ghost-compare-WxH.png (logo | sprite).
"""
import sys
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
CLI = HERE.parent
LOGO = CLI.parent.parent / 'brand' / 'logos' / 'ghost-mark.png'
OUT_JS = CLI / 'src' / 'ghost-sprite.js'
RENDERS = CLI / 'renders'

# Palette index: 0 transparent, then the four brand colours (BRAND.md).
PAL = [None, '#020202', '#7F52EF', '#B28AFF', '#DFD4FF']
T, K, M, L, G = range(5)
PURPLE = '#4E19CF'
rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
ALPHA = 128  # below this, transparent


def quantise(im):
    """Every source pixel to a palette index (full resolution)."""
    w, h = im.size
    px = im.load()
    cols = [rgb(c) for c in PAL[1:]]
    q = [[T] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < ALPHA:
                continue
            q[y][x] = 1 + min(range(4), key=lambda i: sum((v - c) ** 2 for v, c in zip((r, g, b), cols[i])))
    return q


def downsample(q, W, H, weight):
    """Coverage vote per output pixel; thin features (band, streaks, eyes) get a weight so they survive."""
    h, w = len(q), len(q[0])
    grid = [[T] * W for _ in range(H)]
    for j in range(H):
        y0, y1 = j * h // H, (j + 1) * h // H
        for i in range(W):
            x0, x1 = i * w // W, (i + 1) * w // W
            n = [0] * 5
            for y in range(y0, y1):
                row = q[y]
                for x in range(x0, x1):
                    n[row[x]] += 1
            grid[j][i] = max(range(5), key=lambda c: n[c] * weight[c])
    return grid


def clean(grid, keep=(K, L, M)):
    """Drop isolated strays: a pixel with no 4-neighbour of its colour takes its neighbours' majority.
    Colours in keep are spared inside the body (eyes, smile and streaks can be one pixel wide)."""
    H, W = len(grid), len(grid[0])
    out = [r[:] for r in grid]
    for y in range(H):
        for x in range(W):
            c = grid[y][x]
            nb = [grid[y + dy][x + dx] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if 0 <= x + dx < W and 0 <= y + dy < H]
            if c in nb:
                continue
            if c in keep and nb.count(G) >= 2:
                continue
            out[y][x] = max(set(nb), key=nb.count)
    return out


def components(grid, colour):
    """Connected groups (8-way) of one colour, largest first."""
    H, W = len(grid), len(grid[0])
    seen, comps = set(), []
    for y in range(H):
        for x in range(W):
            if grid[y][x] != colour or (x, y) in seen:
                continue
            stack, comp = [(x, y)], []
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen and grid[ny][nx] == colour:
                            seen.add((nx, ny))
                            stack.append((nx, ny))
            comps.append(comp)
    return sorted(comps, key=len, reverse=True)


def tail_frames(grid, shifts):
    """One frame per (dy_a, dy_b): the two lilac streaks moved by that many rows. Only GHOST pixels are ever
    painted over, and the vacated streak pixels become GHOST, so the hat, eyes and smile never change."""
    streaks = sorted(components(grid, L)[:2], key=lambda c: min(x for x, _ in c))
    frames = []
    for dys in shifts:
        f = [r[:] for r in grid]
        for comp in streaks:
            for x, y in comp:
                f[y][x] = G
        for comp, dy in zip(streaks, dys):
            for x, y in comp:
                ny = y + dy
                if 0 <= ny < len(f) and f[ny][x] == G:
                    f[ny][x] = L
        frames.append(f)
    return frames


def to_image(grid, scale, field=PURPLE):
    H, W = len(grid), len(grid[0])
    im = Image.new('RGB', (W * scale, H * scale), rgb(field))
    px = im.load()
    for y in range(H):
        for x in range(W):
            if grid[y][x]:
                c = rgb(PAL[grid[y][x]])
                for yy in range(y * scale, (y + 1) * scale):
                    for xx in range(x * scale, (x + 1) * scale):
                        px[xx, yy] = c
    return im


def compare(src, grid, scale):
    """Logo (box-filtered to the same size, then blown up) beside the sprite."""
    H, W = len(grid), len(grid[0])
    bg = Image.new('RGBA', src.size, rgb(PURPLE) + (255,))
    small = Image.alpha_composite(bg, src).convert('RGB').resize((W, H), Image.BOX).resize((W * scale, H * scale), Image.NEAREST)
    big = Image.alpha_composite(bg, src).convert('RGB').resize((W * scale, H * scale), Image.LANCZOS)
    out = Image.new('RGB', (W * scale * 3 + 2 * scale, H * scale), (40, 40, 40))
    out.paste(big, (0, 0))
    out.paste(small, (W * scale + scale, 0))
    out.paste(to_image(grid, scale), (2 * W * scale + 2 * scale, 0))
    return out


WEIGHT = [1.0, 1.15, 1.6, 1.8, 1.0]  # transparent, BLACK, PURPLE_MID, LILAC, GHOST

# Hand fixes, per sprite size, placed from the SVG's own coordinates (ghost-mark.svg, 46 units = 1104 px).
# Each fix: (colour, x, y, rows) stamped on the grid; 'X' paints the colour, '.' paints GHOST, ' ' leaves the pixel.
# 38 x 40 (27.1 px per pixel): the downsample leaves the eyes uneven (a light pixel in one, a blob for the other), so
# both are stamped as the same solid oval, 3 wide by 4 tall, leaning right like the logo's. In the SVG the right eye
# is the left one moved by (7.081, 1.643) units = (6.27, 1.46) pixels: here 6 columns right and one row lower. The
# smile's flat top slopes down to the right, (21.4, 28.2) to (24.7, 29.1), over a bowl that bottoms out at (23.1, 30.8).
EYE = ['.XX', 'XXX', 'XXX', 'XX.']
FIXES = {
    (38, 40): [
        (K, 18, 20, ['.' * 13] * 12),  # repaint the face area to body, then stamp
        (K, 20, 22, EYE), (K, 26, 23, EYE), (K, 21, 28, ['XX..', 'XXXX', '.XX.']),
    ],
}

# The small ghosts are drawn by hand: pooled down from the big one, the face and the streaks are a smudge.
# Laid out from the big sprite's own rows and columns (divided by 2.9 for 13 x 14, by 2 for 19 x 20). Same letters
# as the printouts: . transparent, K BLACK, M PURPLE_MID (the band), L LILAC (the streaks), # GHOST.
# 13 x 14 (7 text rows): band of 2, single-pixel eyes one row apart, a 2-pixel smile below and between them (a row
# clear of the eyes, or it reads as a smirk), one 3-pixel streak lower left. The logo's hook is filled in: at this
# size its notch reads as a hole in the hat.
SMALL_ART = [
    '....KK.......',
    '...KKKK......',
    '..KKKKKK.....',
    '..KKKKMMKKKK.',
    '...KKKKKKKKKK',
    '.KKKK#####KKK',
    'KKKK######KK.',
    '.KKK#######..',
    '...####K###..',
    '..#######K#..',
    '###L#######..',
    '##L####KK##..',
    '#L#########..',
    '##########...',
]
# 19 x 20 (10 text rows): band of 4, 2 x 2 eyes one row apart, a 3-pixel smile (a straight line reads as a flat
# mouth, so it dips in the middle), two streaks.
MID_ART = [
    '.....KKK...........',
    '....KKKKK..........',
    '...KKKKKKK.........',
    '...KKKKKKKK........',
    '..KKKKKKKMM........',
    '...K.KKKMMKKKKKKK..',
    '.....KKKKKKKKKKKKKK',
    '...KKKKK#####KKKKKK',
    '.KKKKKK########KKK.',
    'KKKKKK#########KK..',
    '.KKKK###########...',
    '..KKK#####KK####...',
    '....######KK#KK#...',
    '...##########KK#...',
    '..##############...',
    '####L######K#K##...',
    '###L##L#####K###...',
    '##L##L#########....',
    '#L##L##########....',
    '###############....',
]
art = lambda rows: [['.KML#'.index(ch) for ch in r] for r in rows]


def stamp(grid, fixes):
    for colour, x, y, rows in fixes:
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch != ' ':
                    grid[y + j][x + i] = colour if ch == 'X' else G
    return grid


def build(src, q, H):
    W = round(H * src.width / src.height)
    grid = clean(downsample(q, W, H, WEIGHT))
    return stamp(grid, FIXES.get((W, H), []))


def js(sets):
    """One FRAMES array per sprite, with its W and H: '' is the big one (W, H, FRAMES), 'SMALL' and 'MID' prefix theirs."""
    rows = lambda g: '\n'.join(f"    '{''.join(str(c) for c in r)}'," for r in g)
    out = [
        '// Generated by tools/make-ghost.py from brand/logos/ghost-mark.png. Do not edit by hand; edit the script and run it.',
        '// Each row is a string of palette indices: 0 transparent, 1 BLACK, 2 PURPLE_MID, 3 LILAC, 4 GHOST.',
        '// Pixels are half-block cells: W columns, H / 2 text rows. One frame per tail position; only the LILAC streaks move.',
        '// The big ghost is downsampled from the logo with hand-placed eyes and smile; SMALL and MID are drawn by hand.',
        "export const PALETTE = [null, '#020202', '#7F52EF', '#B28AFF', '#DFD4FF'];",
    ]
    for name, frames in sets.items():
        pre = f'{name}_' if name else ''
        out.append(f'export const {pre}W = {len(frames[0][0])}, {pre}H = {len(frames[0])};')
        out.append(f'export const {name or "FRAMES"} = [\n' + '\n'.join(f'  [\n{rows(f)}\n  ],' for f in frames) + '\n];')
    return '\n'.join(out) + '\n'


# Tail frames as (rows the left streak moves, rows the right one moves): rest, left up, both up, right up.
SHIFTS = [(0, 0), (-1, 0), (-1, -1), (0, -1)]
SHIFTS_ONE = [(0,), (-1,), (0,), (1,)]  # one streak: rest, up, rest, down
SHIFTS_TOGETHER = [(0, 0), (-1, -1), (0, 0), (1, 1)]  # 19 x 20: its streaks sit close, so they move as one


def strip(frames, scale, gap=1):
    """Frames side by side on PURPLE, for checking the tail by eye."""
    H, W = len(frames[0]), len(frames[0][0])
    out = Image.new('RGB', ((W + gap) * len(frames) * scale, H * scale), rgb(PURPLE))
    for i, f in enumerate(frames):
        out.paste(to_image(f, scale), (i * (W + gap) * scale, 0))
    return out


def main():
    sizes = [int(a) for a in sys.argv[1:]] or [40, 48, 56]
    im = Image.open(LOGO).convert('RGBA')
    src = im.crop(im.getchannel('A').getbbox())
    q = quantise(src)
    RENDERS.mkdir(exist_ok=True)
    for H in sizes:
        grid = build(src, q, H)
        W = len(grid[0])
        to_image(grid, 12).save(RENDERS / f'ghost-sprite-{W}x{H}.png')
        compare(src, grid, 12).save(RENDERS / f'ghost-compare-{W}x{H}.png')
        print(f'{W}x{H}: renders/ghost-sprite-{W}x{H}.png, renders/ghost-compare-{W}x{H}.png')
    # The first size is the one written to src/ghost-sprite.js.
    grid = build(src, q, sizes[0])
    W, H = len(grid[0]), len(grid)
    frames = tail_frames(grid, SHIFTS)
    smalls = tail_frames(art(SMALL_ART), SHIFTS_ONE)
    mids = tail_frames(art(MID_ART), SHIFTS_TOGETHER)
    strip(frames, 12).save(RENDERS / f'ghost-frames-{W}x{H}.png')
    for f in (smalls, mids):
        strip(f, 24).save(RENDERS / f'ghost-frames-{len(f[0][0])}x{len(f[0])}.png')
    OUT_JS.write_text(js({'': frames, 'SMALL': smalls, 'MID': mids}))
    print(f'wrote {OUT_JS.relative_to(CLI)}: {W}x{H}, 13x14, 19x20, {len(frames)} frames each; renders/ghost-frames-*.png')


if __name__ == '__main__':
    main()
