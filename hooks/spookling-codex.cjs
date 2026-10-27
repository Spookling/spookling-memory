#!/usr/bin/env node
// Spookling's native Codex hooks, Node 18+, no dependencies. Shares Claude Code's HTTP server: the one serving this
// hook's .memory folder, started here when it is down.
// Only a checked server response can say ready/active. Errors are visible but never block a turn.
// Stop and PreCompact print nothing: a Stop decision must never create a continuation loop.
if (process.env.SPOOKLING_TAGGING) process.exit(0);
const { homedir } = require('node:os');
const ghost = require('./ghost.js');
const pulse = require('./pulse.js');
const server = require('./server.js');
const events = { UserPromptSubmit: 'user-prompt-submit', PreCompact: 'pre-compact', Stop: 'stop' };
const chunks = [];
const done = () => process.exit(0);
let printed = false; // the deadline and a late reply must not both print
const emit = (value) => { if (printed) return; printed = true; process.stdout.write(JSON.stringify(value), done); };
let visible = false;
// What Codex shows: its hook messages render ANSI colour and keep every line, so the ghost is drawn under a first line
// of words (Codex puts "↳ Hook · " before that one). Only the server's own answers fill it.
const GREY = '#8a8a8a';
const said = (state, title, details) => {
  const level = ghost.colourLevel();
  const head = level === 'none' ? `spookling ${state} · ${title}`
    : `\x1b[1m${ghost.paint(`spookling ${state}`, level, ghost.PALETTE[state].O)}\x1b[0m${ghost.paint(` · ${title}`, level, GREY)}`;
  return details ? [head, ...ghost.card(state, details.map((t) => [[t, GREY]]))].join('\n') : head;
};
const unavailable = () => visible
  ? emit({ systemMessage: said('degraded', 'memory unavailable', ['the local server did not answer', 'check it with: npx spookling status']) })
  : done();
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', async () => {
  let p;
  try { p = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { return done(); }
  if (!p || typeof p !== 'object' || Array.isArray(p)) return done();
  const start = p.hook_event_name === 'SessionStart';
  if (!start && !Object.hasOwn(events, p.hook_event_name)) return done();
  if (p.hook_event_name === 'UserPromptSubmit' && (typeof p.prompt !== 'string' || !p.prompt.trim())) return done();
  visible = start || p.hook_event_name === 'UserPromptSubmit';
  const timeout = p.hook_event_name === 'PreCompact' ? 60000 : 15000;
  // A wall-clock deadline also covers a connected server that never finishes its response.
  const deadline = setTimeout(unavailable, timeout + (start ? 16000 : 11000));
  const res = await server.request(server.folder(p), start ? '/v1/stats' : `/v1/hook/${events[p.hook_event_name]}`, start
    ? { wait: 15000, timeout } : { method: 'POST', body: JSON.stringify({ ...p, harness: 'codex' }), timeout });
  clearTimeout(deadline);
  if (res.down || res.status !== 200) return unavailable();
  let reply;
  try { reply = JSON.parse(res.body); } catch { return unavailable(); }
  if (!reply || typeof reply !== 'object' || Array.isArray(reply)) return unavailable();
  if (!visible) return done();
  if (start) {
    if (!Number.isInteger(reply.n_events) || !reply.tagger || typeof reply.folder !== 'string') return unavailable();
    const { n } = pulse;
    return emit({ systemMessage: said('ready', 'local server connected', [
      `${n(reply.n_events, 'message')} remembered`,
      `${n(reply.n_facts || 0, 'fact')} in force · ${n(reply.n_rules || 0, 'standing rule')}`,
      reply.tagging?.error ? `saving, not tagging: ${String(reply.tagging.error).slice(0, 60)}` : reply.folder.replace(homedir(), '~'),
    ]) });
  }
  // Pass the server's bounded recall block through unchanged, including a successful empty search.
  const context = reply.hookSpecificOutput;
  if (Object.keys(reply).length && !(context?.hookEventName === 'UserPromptSubmit' && typeof context.additionalContext === 'string')) return unavailable();
  const c = pulse.counts(res.headers['x-spookling-pulse']);
  const room = c.cap ? `≈${ghost.kilo(c.chars || 0)} of the ${ghost.kilo(c.cap)} characters of room` : null;
  emit({ ...reply, systemMessage: c.facts || c.events || c.chats
    ? said('active', `${pulse.said(c)} recalled`, [room].filter(Boolean))
    : said('active', 'recall ran: nothing on record for this prompt') }); // a line, not a ghost every prompt
});
