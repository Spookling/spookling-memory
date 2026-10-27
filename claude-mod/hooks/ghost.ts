// The Spookling ghost: one 16×16 pixel sprite, drawn as ANSI half-blocks on the terminal (a Raster, two pixels per
// cell, so 16 columns × 8 rows) and as an SVG everywhere else. Its state is the memory's health, set only from runtime
// evidence; each state is the same ghost with a different expression, so it reads without color too.
//
// The sprite and the way it is built follow pixel-profile by sknoyon324234234234 (MIT,
// https://github.com/TheMysteriousGhost/pixel-profile): a dark outline around the silhouette, shading down one
// side, 2×2 eyes with a white highlight, blush under them, a scalloped hem, a blink drawn as one line on the
// lowest eye row, and a bob in whole-pixel steps.

import type { GhostState } from '../types'

export type { GhostState }

export const GHOST_STATES: readonly GhostState[] = ['ready', 'searching', 'active', 'degraded', 'paused']

/** What each state claims; the ghost shows a state only when the hooks have the evidence for it. */
export const MEANING: Record<GhostState, string> = {
  ready: 'Hook connected and self-test passed',
  searching: 'Retrieval in progress',
  active: 'Retrieval completed for this prompt',
  degraded: 'Memory failed or context delivery is unverified',
  paused: 'You disabled memory',
}

// O outline, W body, S shade, B blush, E eye, H eye highlight, D sweat drop, . nothing.
const SPRITE = [
  '.....OOOOOO.....',
  '...OOWWWWWWOO...',
  '..OWWWWWWWWWWO..',
  '.OWWWWWWWWWWWWO.',
  '.OWWWWWWWWWWWWO.',
  '.OWWWHEWWWHEWWO.',
  '.OWWWEEWWWEEWWO.',
  '.OWBBWWWWWWWBBO.',
  '.OWWWWWWWWWWWWO.',
  '.OWWWWWWWWWWWSO.',
  '.OWWWWWWWWWWWSO.',
  '.OWWWWWWWWWWSSO.',
  '.OWWWWWWWWWWWSO.',
  '.OWWOOOWWOOOWSO.',
  '..OO...OO...OO..',
  '................',
]

/** Rows 5 and 6 are the eyes; each expression redraws them. Other rows a state changes come after. */
type Face = { eyes: readonly [string, string]; extra?: Record<number, string> }

const OPEN: Face = { eyes: ['.OWWWHEWWWHEWWO.', '.OWWWEEWWWEEWWO.'] }
const SHUT: Face = { eyes: ['.OWWWWWWWWWWWWO.', '.OWWWEEWWWEEWWO.'] } // the blink: one line on the lowest eye row

const FACE: Record<GhostState, Face> = {
  ready: OPEN,
  searching: { eyes: ['.OWWWWHEWWWHEWO.', '.OWWWWEEWWWEEWO.'] }, // glancing aside, at the work
  active: OPEN, // the ready face, turned green
  degraded: {
    ...OPEN,
    // a sweat drop beside the head, and a small "o" mouth: uh-oh
    extra: { 1: '...OOWWWWWWOO..D', 2: '..OWWWWWWWWWWODD', 3: '.OWWWWWWWWWWWWOD', 8: '.OWWWWWEEWWWWWO.' },
  },
  paused: { ...SHUT, extra: { 7: '.OWWWWWWWWWWWWO.' } }, // asleep: eyes shut, no blush
}

type Colors = { O: number; W: number; S: number; B: number; E: number; H: number; D: number }

// Chosen to stay apart where a terminal has only 256 colors (macOS Terminal.app before 26): each state's outline
// and body round to different palette entries, so the states differ by color there too, not only by expression.
export const PALETTE: Record<GhostState, Colors> = {
  // eye inks stay under 0x1a a channel, so a 256-color terminal draws them black, never in the outline's color
  ready: { O: 0x2a1747, W: 0xf4f1ff, S: 0xc9c1f0, B: 0xff8fab, E: 0x120d18, H: 0xffffff, D: 0x7fd1ff },
  searching: { O: 0x1f4f7a, W: 0xe3f1ff, S: 0xa9cff2, B: 0xff9fb8, E: 0x0f1419, H: 0xffffff, D: 0x7fd1ff },
  active: { O: 0x1f6b35, W: 0xd9ffdc, S: 0xa8e6b8, B: 0xff8fab, E: 0x0e1610, H: 0xffffff, D: 0x7fd1ff },
  degraded: { O: 0x6b3d00, W: 0xfff4dc, S: 0xf2c88f, B: 0xff9f8a, E: 0x18120c, H: 0xffffff, D: 0x7fd1ff },
  paused: { O: 0x4a4652, W: 0xd6d3df, S: 0xaaa5b8, B: 0xd6d3df, E: 0x3a3644, H: 0xd6d3df, D: 0x7fd1ff },
}

/** The states whose ghost blinks: awake ones. Paused sleeps; searching keeps its eyes on the work. */
export const BLINKS: ReadonlySet<GhostState> = new Set<GhostState>(['ready', 'active', 'degraded'])

export const WIDTH = 16
export const HEIGHT = SPRITE.length

export type Frame = 'open' | 'blink'

export function rows(state: GhostState, frame: Frame = 'open'): string[] {
  const face = frame === 'blink' ? { ...FACE[state], eyes: SHUT.eyes } : FACE[state]
  return SPRITE.map((row, y) => face.extra?.[y] ?? (y === 5 ? face.eyes[0] : y === 6 ? face.eyes[1] : row))
}

/**
 * The small size: the same ghost drawn by hand at 10×9 for terminals that can show only one pixel per cell. Halving
 * the 16×16 sprite would blur its face, so this keeps what makes it read at this size: the outline, shade, blush and
 * three feet, 1-pixel eyes, closed eyes as 2-pixel lines with room around them, and the same expression per state.
 */
const SMALL = [
  '..OOOOOO..',
  '.OWWWWWWO.',
  'OWWWWWWWWO',
  'OWWEWWEWWO',
  'OWWEWWEWWO',
  'OWBWWWWBWO',
  'OWWWWWWWSO',
  'OWWWWWWWSO',
  'OO..OO..OO',
]

export const SMALL_WIDTH = 10
export const SMALL_HEIGHT = SMALL.length

export function smallRows(state: GhostState, frame: Frame = 'open'): string[] {
  const g = SMALL.map(row => [...row])
  const set = (r: number, c: number, ch: string) => {
    g[r]![c] = ch
  }
  const clear = () => [3, 4].forEach(r => [3, 6].forEach(c => set(r, c, 'W')))
  const shut = () => {
    clear()
    ;[2, 3, 6, 7].forEach(c => set(4, c, 'E'))
  }
  if (frame === 'blink' && BLINKS.has(state)) shut()
  else if (state === 'searching') {
    clear() // looking up, at the work
    ;[2, 3].forEach(r => [3, 6].forEach(c => set(r, c, 'E')))
  } else if (state === 'paused') {
    shut() // asleep, no blush
    set(5, 2, 'W')
    set(5, 7, 'W')
  }
  if (state === 'degraded') [[6, 4], [6, 5], [0, 9], [1, 9]].forEach(([r, c]) => set(r!, c!, r! < 2 ? 'D' : 'E')) // "o" and a drop
  return g.map(row => row.join(''))
}

export type Size = 'large' | 'small'

/** Each pixel's color (0xRRGGBB), or null where the ghost is not. */
export function pixels(state: GhostState, frame: Frame = 'open', size: Size = 'large'): (number | null)[][] {
  const colors = PALETTE[state]
  const grid = size === 'small' ? smallRows(state, frame) : rows(state, frame)
  return grid.map(row => [...row].map(ch => (ch === '.' ? null : colors[ch as keyof Colors])))
}

/** A run of terminal cells drawn alike: its characters, and the colors as `#rrggbb` (absent: the terminal's own). */
export type Run = { text: string; fg?: string; bg?: string }

/**
 * How the terminal draws the ghost:
 * - `half`: two pixels per cell with ▀ ▄, 16 columns × 8 rows; needs a font that has those characters
 * - `cells`: the small ghost, one pixel per two cells, background-colored spaces only, 20 columns × 9 rows; draws
 *   exactly on any font (macOS Terminal.app's Monaco has no ▀ ▄, and its fallback glyphs drift off the grid)
 */
export type Render = 'half' | 'cells'

export function terminalSize(render: Render): { columns: number; rows: number } {
  return render === 'half' ? { columns: WIDTH, rows: HEIGHT / 2 } : { columns: SMALL_WIDTH * 2, rows: SMALL_HEIGHT }
}

/** Dispatches to the renderer the terminal can draw. */
export function terminalArt(state: GhostState, frame: Frame, render: Render): Run[][] {
  return render === 'half' ? terminalRows(state, frame) : terminalCells(state, frame)
}

/** The `cells` renderer: the small ghost, each pixel two background-colored spaces wide and one row tall; no glyph. */
export function terminalCells(state: GhostState, frame: Frame = 'open'): Run[][] {
  return pixels(state, frame, 'small').map(row => {
    const runs: Run[] = []
    for (const color of row) {
      const bg = color === null ? undefined : hex(color)
      const last = runs[runs.length - 1]
      if (last && last.bg === bg) last.text += '  '
      else runs.push(bg === undefined ? { text: '  ' } : { text: '  ', bg })
    }
    return runs
  })
}

/**
 * The ghost as terminal text: two pixel rows per terminal row, colored through the renderer's own color handling
 * (which downsamples to 256 or 16 colors where the terminal has no 24-bit color, as macOS Terminal.app before 26 does).
 *
 * - both pixels one color: a space on that background, no glyph, so nothing depends on how a font draws blocks
 *   or on line spacing, and no foreground color a terminal might dim or re-contrast
 * - two colors: ▀, the top pixel in front, the bottom one behind
 * - one pixel: ▀ or ▄ in that color over the terminal's own background
 * - none: a plain space
 */
export function terminalRows(state: GhostState, frame: Frame = 'open'): Run[][] {
  const px = pixels(state, frame)
  const out: Run[][] = []
  for (let r = 0; r < HEIGHT / 2; r++) {
    const runs: Run[] = []
    for (let x = 0; x < WIDTH; x++) {
      const top = px[2 * r]?.[x] ?? null
      const bottom = px[2 * r + 1]?.[x] ?? null
      const cell: Run =
        top !== null && top === bottom
          ? { text: ' ', bg: hex(top) }
          : top !== null && bottom !== null
            ? { text: '▀', fg: hex(top), bg: hex(bottom) }
            : top !== null
              ? { text: '▀', fg: hex(top) }
              : bottom !== null
                ? { text: '▄', fg: hex(bottom) }
                : { text: ' ' }
      const last = runs[runs.length - 1]
      if (last && last.fg === cell.fg && last.bg === cell.bg && (cell.fg === undefined ? cell.text === ' ' : last.text.endsWith(cell.text))) {
        last.text += cell.text
      } else {
        runs.push(cell)
      }
    }
    out.push(runs)
  }
  return out
}

function hex(color: number): string {
  return `#${color.toString(16).padStart(6, '0')}`
}

/** One path per color, each row's runs merged into rectangles (as pixel-profile's sprite_paths does). */
function paths(grid: string[], colors: Colors, keep: (ch: string) => boolean): string {
  const runs = new Map<string, string[]>()
  grid.forEach((row, y) => {
    let x = 0
    while (x < row.length) {
      const ch = row[x]!
      if (ch === '.' || !keep(ch)) {
        x += 1
        continue
      }
      const start = x
      while (x < row.length && row[x] === ch) x += 1
      const list = runs.get(ch) ?? []
      list.push(`M${start} ${y}h${x - start}v1h${start - x}z`)
      runs.set(ch, list)
    }
  })
  return [...runs].map(([ch, d]) => `<path d="${d.join('')}" fill="${hex(colors[ch as keyof Colors])}"/>`).join('')
}

/**
 * The same sprite as an SVG. With `animate`, it bobs in whole-pixel steps and, when awake, blinks (SMIL, no script),
 * as pixel-profile's mascot does; a surface draws the animation only where it runs the SVG interactively.
 */
export function svg(state: GhostState, animate = false): string {
  const colors = PALETTE[state]
  const open = rows(state, 'open')
  const isEye = (ch: string) => ch === 'E' || ch === 'H'
  const body = paths(open.map(r => r.replace(/[EH]/g, 'W')), colors, ch => !isEye(ch))
  const eyesOpen = paths(open, colors, isEye)
  const eyesShut = paths(rows(state, 'blink'), colors, ch => ch === 'E')
  const blink = animate && BLINKS.has(state)
  const bob = animate && state !== 'paused'
  const step = state === 'searching' ? '0.8s' : '1.6s'
  const eyes = blink
    ? `<g><animate attributeName="opacity" values="1;0;1" keyTimes="0;0.92;0.97" dur="4s" calcMode="discrete" ` +
      `repeatCount="indefinite"/>${eyesOpen}</g><g opacity="0"><animate attributeName="opacity" values="0;1;0" ` +
      `keyTimes="0;0.92;0.97" dur="4s" calcMode="discrete" repeatCount="indefinite"/>${eyesShut}</g>`
    : eyesOpen
  const motion = bob
    ? `<animateTransform attributeName="transform" type="translate" values="0 0;0 -0.5;0 -1;0 -0.5" dur="${step}" ` +
      `calcMode="discrete" repeatCount="indefinite"/>`
    : ''
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -1 ${WIDTH} ${HEIGHT + 1}" shape-rendering="crispEdges">` +
    `<title>Spookling: ${state}</title><g>${motion}${body}${eyes}</g></svg>`
  )
}
