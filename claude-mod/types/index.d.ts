// The state the band keeps for the session, and what it reads from Spookling Memory's hooks and server.

export type GhostState = 'ready' | 'searching' | 'active' | 'degraded' | 'paused'

export type GhostStatus = { state: GhostState; reason: string; at: string }

/** What the hooks last did, from <memory>/pulse.json (hooks/pulse.js writes it). */
export type Pulse = {
  at: number
  session?: string
  event: 'start' | 'prompt' | 'stop' | 'compact'
  up: boolean
  facts?: number
  rules?: number
  events?: number
  chats?: number
  chars?: number
  cap?: number
  kept?: number
  lines?: number
  tagging?: string
}

/** bin/probe.cjs: this session's memory folder, how the terminal can draw the ghost, and the server's stats. */
export type Probe = {
  memory: string | null
  shown: string | null
  render: 'half' | 'cells'
  up: boolean
  stats: { n_events: number; n_facts: number; n_rules: number; tagging?: { error: string | null } } | null
}

/** This session's recalls that brought anything along, their characters, and the last one: its size and what it held. */
export type Tally = { recalls: number; chars: number; last: { chars: number; what: string } | null }

declare module 'claude-code' {
  interface PluginState {
    spookling: {
      ghost: Shaped<GhostStatus>
      blinkShut: boolean
      render: 'half' | 'cells'
      probe: Shaped<Probe | null>
      tally: Shaped<Tally>
    }
  }
}
