#!/usr/bin/env node
// Claude Code SessionStart hook: wake this folder's memory server (start it if it is down, so it has opened by the
// first prompt) and give the status line the server's own counts. Prints nothing: SessionStart output becomes context.
if (process.env.SPOOKLING_TAGGING) process.exit(0); // a tagging call the server started
const pulse = require('./pulse.js');
const server = require('./server.js');
const done = () => process.exit(0);
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', async () => {
  let p = {};
  try { p = JSON.parse(Buffer.concat(chunks)) || {}; } catch {}
  // a big memory may still be opening after 3 s: the first prompt waits for it, not the session
  const r = await server.request(server.folder(p), '/v1/stats', { wait: 15000, timeout: 3000 });
  let s = null;
  try { s = r.status === 200 ? JSON.parse(r.body) : null; } catch {}
  if (r.down || !s) return pulse.write(p, { event: 'start', up: !r.down || r.connected }), done();
  pulse.write(p, { event: 'start', up: true, remembered: s.n_events, facts_kept: s.n_facts, rules_kept: s.n_rules,
    ...(s.tagging?.error && { tagging: String(s.tagging.error).slice(0, 160) }) });
  done();
});
