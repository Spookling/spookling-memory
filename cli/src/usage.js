// npx spookling usage: every recall the hooks logged in .memory/usage.jsonl, oldest first: when, which harness, about how
// many tokens reached the model and what they were (the server's own counts), and how the prompt began.
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { C, paint, write as out } from './term.js';

const { said, n } = createRequire(import.meta.url)('../../hooks/pulse.js');
const LAST = 20; // lines shown without --all

// tokens as the band says them: characters ÷ 4, the usual rough rate for English and code
export const tokens = (chars) => Math.round((chars || 0) / 4);
const kilo = (v) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(v));

/** The log's lines, oldest first; a line that does not parse is skipped. */
export function read(mem) {
  try {
    return readFileSync(join(mem, 'usage.jsonl'), 'utf8').split('\n').filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter((u) => u && Number.isFinite(u.at));
  } catch { return []; }
}

/** What one prompt's recall delivered, in words. */
export function what(u) {
  if (u.up === false) return 'the memory server did not answer';
  return said(u) || (u.chars ? 'recalled context' : 'nothing on record for this prompt');
}

const when = (at) => {
  const d = new Date(at);
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })} ${d.toLocaleTimeString('en-GB', { hour12: false })}`;
};
const cut = (s, w) => (s.length > w ? `${s.slice(0, w - 1)}…` : s);

export function usage(project, { all = false } = {}) {
  const mem = join(project, '.memory');
  const shown = mem.replace(homedir(), '~');
  out(`\n   ${paint(C.LILAC, 'Spookling Memory usage', true)} ${paint(C.GREY, `· ${shown}/usage.jsonl`)}\n\n`);
  const log = existsSync(mem) ? read(mem) : [];
  if (!log.length) {
    out(`   ${existsSync(mem) ? 'No recalls logged here yet: each prompt in Claude Code or Codex adds a line.' : 'No .memory here: npx spookling init sets memory up.'}\n\n`);
    return 0;
  }
  const rows = all ? log : log.slice(-LAST);
  out(paint(C.GREY, `   ${'when'.padEnd(16)} ${'harness'.padEnd(7)} ${'tokens'.padStart(7)}  ${'what reached the model'.padEnd(36)} prompt\n`));
  for (const u of rows) {
    const t = u.up === false || !u.chars ? '—' : `≈${kilo(tokens(u.chars))}`;
    out(`   ${when(u.at).padEnd(16)} ${String(u.harness || 'claude').padEnd(7)} ${t.padStart(7)}  ${cut(what(u), 36).padEnd(36)} `
      + `${paint(C.GREY, u.prompt ? `"${cut(u.prompt, 48)}"` : '')}\n`);
  }
  const recalled = log.filter((u) => u.up !== false && u.chars);
  const sum = recalled.reduce((s, u) => s + tokens(u.chars), 0);
  out(`\n   ${n(log.length, 'prompt')} since ${when(log[0].at)} · ${recalled.length} brought memory along · ≈${kilo(sum)} tokens in all\n`);
  out(paint(C.GREY, `   tokens ≈ characters ÷ 4 of what the hook added to the prompt, standing rules included${rows.length < log.length ? ` · the last ${rows.length}; --all for every one` : ''}\n\n`));
  return 0;
}
