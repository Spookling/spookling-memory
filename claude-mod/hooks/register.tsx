import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, ResolveInput } from 'claude-code'

import type { GhostState, GhostStatus, Probe, Pulse, Tally } from '../types'
import { BLINKS, HEIGHT, MEANING, WIDTH, svg, terminalArt, terminalSize } from './ghost'
import type { Frame, Render } from './ghost'

// The band above the prompt: the Spookling ghost and what memory did, read from Spookling Memory's own hooks (their
// .memory/pulse.json) and server (/v1/stats). It recalls and saves nothing itself: the hooks init wired do that.
// Change a shape tag when that value's fields change: an earlier version's value outlives a reload.
const ghost = atom(
  { plugin: 'spookling', key: 'ghost' } as const,
  { state: 'degraded', reason: 'not started: the session-start check has not run yet', at: '' } as GhostStatus,
  { shape: 'ghost-1' },
)
const blinkShut = atom({ plugin: 'spookling', key: 'blinkShut' } as const, false)
const render = atom({ plugin: 'spookling', key: 'render' } as const, 'half' as Render)
const probe = atom({ plugin: 'spookling', key: 'probe' } as const, null as Probe | null, { shape: 'probe-1' })
const tally = atom({ plugin: 'spookling', key: 'tally' } as const, { recalls: 0, chars: 0 } as Tally, { shape: 'tally-1' })

const STATE_COLOR: Record<GhostState, string> = {
  ready: 'claude',
  searching: 'suggestion',
  active: 'success',
  degraded: 'warning',
  paused: 'inactive',
}

const n = (k: number, word: string) => `${k} ${word}${k === 1 ? '' : 's'}`
// 298, 4.2k, 10k: a count of characters as the ghost says it (hooks/ghost.js kilo)
const kilo = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(v))

/** "3 facts, 1 rule and 2 past chats": what reached Claude, biggest news first (hooks/pulse.js said). */
function said(p: Pulse): string {
  const parts = [
    p.facts ? n(p.facts, 'fact') : '',
    p.events ? n(p.events, 'event') : '',
    p.rules ? n(p.rules, 'rule') : '',
    p.chats ? n(p.chats, 'past chat') : '',
  ].filter(Boolean)
  return parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}` : (parts[0] ?? '')
}

async function setGhost($: EngineInterface, state: GhostState, reason: string): Promise<void> {
  const at = new Date(await $.clock.now()).toISOString()
  await update($, ghost, () => ({ state, reason, at }))
}

/** bin/probe.cjs: the memory folder, the renderer the font can draw, the server's stats. Null when it cannot run. */
async function runProbe($: EngineInterface): Promise<Probe | null> {
  try {
    const ran = await $.process.run(['node', `${$.plugin.root}/bin/probe.cjs`, await $.session.cwd()], { timeoutMs: 8000 })
    const found = JSON.parse(ran.stdout) as Probe
    await update($, probe, () => found)
    await update($, render, () => found.render)
    return found
  } catch {
    return null
  }
}

/** The hooks' last pulse for this session, or null: none yet, another session's, or unreadable. */
async function pulse($: EngineInterface): Promise<Pulse | null> {
  const memory = (await read($, probe))?.memory
  if (!memory) return null
  try {
    const p = JSON.parse(await $.fs.read(`${memory}/pulse.json`)) as Pulse
    return p.session && p.session !== (await $.session.id()) ? null : p
  } catch {
    return null
  }
}

/** Ready only on evidence: a memory folder and its server answering. */
async function check($: EngineInterface): Promise<void> {
  const found = await runProbe($)
  if (!found) await setGhost($, 'degraded', 'the probe could not run: is node on the PATH Claude Code started with?')
  else if (!found.memory) await setGhost($, 'degraded', 'no .memory folder here or in your home folder: run npx spookling init')
  else if (!found.up) await setGhost($, 'degraded', 'the memory server did not answer (npx spookling status says why)')
  else await setGhost($, 'ready', `server connected · ${n(found.stats?.n_events ?? 0, 'message')} remembered`)
}

const BLINK_TIME = 4000
const BLINK_SHUT = 160
let blinker: { cancel: () => void } | null = null

/** The frame an awake ghost shows now: shut for the moment of a blink, open otherwise. */
function frameOf(state: GhostState, shut: boolean): Frame {
  return shut && BLINKS.has(state) ? 'blink' : 'open'
}

/**
 * A blink: the eyes shut for a moment, then open. Only awake states blink, and only the plugin's own timer drives
 * it, so a ghost that has stopped blinking is a plugin that has stopped running.
 */
async function blink($: EngineInterface): Promise<void> {
  const g = await read($, ghost)
  if (!BLINKS.has(g.state)) return
  await update($, blinkShut, () => true)
  $.clock.after(BLINK_SHUT, () => void update($, blinkShut, () => false))
}

function startBlinking($: EngineInterface): void {
  blinker?.cancel()
  blinker = $.clock.every(BLINK_TIME, () => void blink($))
}

/**
 * The ghost on the surface at hand. In the terminal: rows of text colored through the renderer's own color
 * handling, which downsamples where the terminal has no 24-bit color. Elsewhere: the same pixels as an SVG.
 */
function ghostNode($: EngineInterface, e: ResolveInput, state: GhostState, width: number, frame: Frame = 'open', how: Render = 'half') {
  if (e.surface === 'terminal') {
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column" flexShrink={0} width={terminalSize(how).columns}>
        {terminalArt(state, frame, how).map(runs => (
          <Text wrap="truncate">
            {runs.map(run => (
              <Text color={run.fg} backgroundColor={run.bg}>
                {run.text}
              </Text>
            ))}
          </Text>
        ))}
      </Box>
    )
  }
  const { Svg } = $.ui.resolve(e)
  return (
    <Svg
      source={svg(state, true)}
      alt={`Spookling ghost: ${state}`}
      width={width}
      height={Math.round((width * (HEIGHT + 1)) / WIDTH)}
      isInteractive
    />
  )
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const done = await next(e) // the SessionStart hook wakes the server first
    startBlinking($)
    await check($)
    return done
  })

  // Searching while the UserPromptSubmit hook recalls (next runs it), then active only once that hook has reported
  // for this prompt: what it did not report is not claimed.
  on('prompt.submit', async ($, e, next) => {
    if (e.command || e.isMeta || !e.text.trim()) return next(e)
    const asked = await $.clock.now()
    await setGhost($, 'searching', 'recalling for this prompt')
    const entered = await next(e)
    if (entered.drop !== undefined) {
      await setGhost($, 'ready', 'the prompt did not enter, so nothing was delivered')
      return entered
    }
    const p = await pulse($)
    if (!p || p.event !== 'prompt' || p.at < asked - 1000) {
      await setGhost($, 'degraded', 'the memory hook did not report for this prompt (is it wired? npx spookling status)')
    } else if (!p.up) {
      await setGhost($, 'degraded', 'the memory server did not answer this prompt')
    } else if (p.facts || p.events || p.chats) {
      await update($, tally, t => ({ recalls: t.recalls + 1, chars: t.chars + (p.chars ?? 0) }))
      await setGhost($, 'active', `${said(p)} recalled (≈${kilo(p.chars ?? 0)} chars), delivered with the prompt`)
    } else {
      await setGhost($, 'active', p.rules ? `${n(p.rules, 'standing rule')}; nothing else on record for this prompt` : 'nothing on record for this prompt')
    }
    return entered
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (e.agentId) return done
    const g = await read($, ghost)
    if (g.state === 'active' || g.state === 'searching') await setGhost($, 'ready', `idle · last prompt: ${g.reason}`)
    await runProbe($) // the Stop hook saved the turn: fresh counts
    return done
  })

  on('session.compact', async ($, e, next) => {
    const done = await next(e)
    const p = await pulse($)
    if (p?.event === 'compact' && p.up && p.kept !== undefined) {
      const rest = (p.lines ?? 0) - (p.rules ?? 0)
      await setGhost($, 'ready', `kept ${n(p.rules ?? 0, 'rule')}${rest ? ` and ${n(rest, 'line')}` : ''} through compaction`)
    }
    return done
  })

  // The ghost is 9 rows tall in the terminal (8 with half blocks); where the band has less room than that, it draws
  // the one-line form instead of squeezing the sprite.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const how = await read($, render)
    const room = e.surface === 'terminal' ? terminalSize(how).rows : 0
    const { Box, Text } = $.ui.resolve(e)
    const g = await read($, ghost)
    const found = await read($, probe)
    const t = await read($, tally)
    const s = found?.stats
    const label = (
      <Text wrap="truncate">
        <Text color={STATE_COLOR[g.state]} weight="bold">
          spookling {g.state}
        </Text>
        <Text dimColor> · {g.reason}</Text>
      </Text>
    )
    const held = s ? `${n(s.n_events, 'message')} remembered · ${n(s.n_facts, 'fact')} in force · ${n(s.n_rules, 'standing rule')}` : MEANING[g.state]
    const tagging = s?.tagging?.error ? ` · saving, not tagging: ${s.tagging.error.slice(0, 60)}` : ''
    const work = `${n(t.recalls, 'recall')} this session ≈${kilo(t.chars)} chars${tagging}`
    if (e.props.maxRows < room) {
      return (
        <Box flexDirection="row" gap={1}>
          {label}
          <Text dimColor wrap="truncate">
            {work}
          </Text>
        </Box>
      )
    }
    return (
      <Box flexDirection="row" gap={1} alignItems="center">
        {ghostNode($, e, g.state, 48, frameOf(g.state, await read($, blinkShut)), how)}
        <Box flexDirection="column" flexShrink={1}>
          {label}
          <Text dimColor wrap="truncate">
            {held}
          </Text>
          <Text wrap="truncate">{work}</Text>
          <Text dimColor wrap="truncate">
            {found?.shown ? `memory in ${found.shown} · npx spookling status` : 'npx spookling init sets memory up here'}
          </Text>
        </Box>
      </Box>
    )
  })
}
