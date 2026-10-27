import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine as Kit } from 'claude-code/testing'

import type { Probe, Pulse } from '../types'

// Nothing sits beneath the plugin: each hook here stands for the engine. bin/probe.cjs is answered by the
// `process.run` hook and .memory/pulse.json by `fs.read`; a `prompt.submit` hook beneath stands for Spookling
// Memory's UserPromptSubmit hook, which writes the pulse while the prompt goes down.

const UP: Probe = {
  memory: '/repo/.memory',
  shown: '~/repo/.memory',
  render: 'cells',
  up: true,
  stats: { n_events: 20, n_facts: 1, n_rules: 2, tagging: { error: null } },
}

type Fake = { probe?: Probe; pulse?: Pulse | null; writes?: (now: number) => Pulse | null }

function fake(on: On, f: Fake = {}) {
  let pulse = f.pulse ?? null
  on('process.run', () => ({
    value: { exitCode: 0, stdout: JSON.stringify(f.probe ?? UP), stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('fs.read', (_, e) => {
    if (e.path !== '/repo/.memory/pulse.json' || !pulse) throw new Error('ENOENT')
    return { value: JSON.stringify(pulse) }
  })
  const clock = mock.clock(on)
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('prompt.submit', (_, e) => {
    if (f.writes) pulse = f.writes(clock.now())
    return { text: e.text, context: e.context }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))
  return clock
}

const PROMPT = { text: 'why are invoice totals in cents?', wait: false, origin: { kind: 'composer' } } as const
const BAND = {
  plugin: 'spookling',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120, scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const
const recalled = (at: number): Pulse => ({ at, session: 's1', event: 'prompt', up: true, facts: 1, rules: 2, chats: 2, chars: 5646, cap: 10000 })

async function start($: Kit) {
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
}

async function band($: Kit, maxRows = 10) {
  const ui = await $.ui.mount({ ...BAND, props: { ...BAND.props, maxRows }, surface: 'terminal' })
  const label = (await ui.find({ type: 'Text', text: /^spookling / }))?.text ?? ''
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  await ui.unmount()
  return { label, texts }
}

test('ready once the memory server answers, with its own counts', async ($, on) => {
  fake(on)
  await start($)
  const b = await band($)
  expect(b.label).toBe('spookling ready · server connected · 20 messages remembered')
  expect(b.texts).toContain('20 messages remembered · 1 fact in force · 2 standing rules')
  expect(b.texts).toContain('memory in ~/repo/.memory · npx spookling usage')
  expect(b.texts).toContain('no recall yet this session')
})

test('degraded when the server does not answer, or there is no memory here', async ($, on) => {
  fake(on, { probe: { ...UP, up: false, stats: null } })
  await start($)
  expect((await band($)).label).toContain('spookling degraded · the memory server did not answer')
})

test('searching while the hook recalls, then active and green with what it recalled', async ($, on) => {
  fake(on, { writes: recalled })
  await start($)
  await $.prompt.submit(PROMPT)
  const b = await band($)
  expect(b.label).toBe('spookling active · 1 fact, 2 rules and 2 past chats recalled (≈5.6k chars), delivered with the prompt')
  // the last recall in one line beside the ghost, in tokens (characters ÷ 4)
  expect(b.texts).toContain('last recall ≈1.4k tokens: 1 fact, 2 rules and 2 past chats · 1 recall this session ≈1.4k tokens')
})

test('a prompt the hook did not report for reads degraded, not active', async ($, on) => {
  fake(on, { pulse: recalled(-60_000) }) // an old pulse: this prompt's hook never wrote one
  await start($)
  await $.prompt.submit(PROMPT)
  expect((await band($)).label).toContain('spookling degraded · the memory hook did not report for this prompt')
})

test('back to ready when the turn ends, saying what the last prompt got', async ($, on) => {
  fake(on, { writes: now => ({ at: now, session: 's1', event: 'prompt', up: true, rules: 2, chars: 300, cap: 10000 }) })
  await start($)
  await $.prompt.submit(PROMPT)
  await $.turn.complete({ answer: 'ok', durationMs: 10, isAborted: false, turnId: 't1', reason: 'answer' })
  const b = await band($)
  expect(b.label).toBe('spookling ready · idle · last prompt: 2 standing rules; nothing else on record for this prompt')
  expect(b.texts).toContain('last recall ≈75 tokens: 2 rules · 1 recall this session ≈75 tokens') // the rules went in with the prompt
})

test('awake, it blinks: eyes shut for a moment, then open again', async ($, on) => {
  const writes: boolean[] = []
  on('state.set', (_, e, next) => {
    if (e.key === 'blinkShut') writes.push(e.value === true)
    return next(e)
  })
  const clock = fake(on)
  await start($)
  await clock.advance(4000)
  expect(writes).toEqual([true])
  await clock.advance(200)
  expect(writes).toEqual([true, false])
})

test('the plain-cell ghost the band draws: 9 rows of colored spaces beside the words', async ($, on) => {
  fake(on)
  await start($)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const rows = (await ui.findAll({ type: 'Text', text: /^ {20}$/ })).length
  expect(rows).toBe(9)
  await ui.unmount()
})

test('a band shorter than the ghost draws the one-line form', async ($, on) => {
  fake(on)
  await start($)
  const b = await band($, 4)
  expect(b.texts.some(t => /^ {20}$/.test(t))).toBe(false)
  expect(b.label).toContain('spookling ready')
})
