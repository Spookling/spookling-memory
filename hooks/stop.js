#!/usr/bin/env node
// Claude Code Stop hook: POST stdin to this folder's memory server (started if down), print nothing (Stop output could
// keep Claude going), exit 0.
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
  const r = await server.request(server.folder(p), '/v1/hook/stop', { method: 'POST', body: input });
  pulse.write(p, { event: 'stop', up: !r.down || r.connected });
  done();
});
