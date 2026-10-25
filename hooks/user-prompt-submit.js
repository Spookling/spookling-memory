#!/usr/bin/env node
// Claude Code UserPromptSubmit hook: POST stdin to this folder's memory server (started if down), print its reply, exit 0.
if (process.env.SPOOKLING_TAGGING) process.exit(0); // a tagging call the server started
const pulse = require('./pulse.js');
const server = require('./server.js');
const done = () => process.exit(0);
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', async () => {
  const input = Buffer.concat(chunks);
  let p = {};
  try { p = JSON.parse(input) || {}; } catch {}
  const r = await server.request(server.folder(p), '/v1/hook/user-prompt-submit', { method: 'POST', body: input });
  if (r.down) return pulse.write(p, { event: 'prompt', up: r.connected }), done();
  let body = r.body;
  if (r.status !== 200 || !body) return pulse.write(p, { event: 'prompt', up: true }), done();
  const c = pulse.counts(r.headers['x-spookling-pulse']);
  pulse.write(p, { event: 'prompt', up: true, ...c });
  // a transcript line only for recall about this prompt: standing rules alone come with every prompt
  if (c.facts || c.events || c.chats) {
    try { body = JSON.stringify({ ...JSON.parse(body), systemMessage: `👻 ${pulse.said(c)} from memory` }); } catch {}
  }
  process.stdout.write(body, done); // exit only once stdout has flushed
});
