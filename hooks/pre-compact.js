#!/usr/bin/env node
// Claude Code PreCompact hook: POST stdin to the local server, print nothing, exit 0.
if (process.env.SPOOKLING_TAGGING) process.exit(0); // a tagging call the server started
const http = require('node:http');
const url = (process.env.SPOOKLING_MEMORY_URL || 'http://127.0.0.1:7447').replace(/\/+$/, '') + '/v1/hook/pre-compact';
const done = () => process.exit(0);
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', () => {
  try {
    const req = http.request(url, { method: 'POST', headers: { 'content-type': 'application/json' }, timeout: 1000 }, (res) => {
      res.resume();
      res.on('end', done);
    });
    // 1 s to connect: WSL drops a dead port instead of refusing it
    req.on('socket', (s) => s.once('connect', () => req.setTimeout(60000)));
    req.on('timeout', () => req.destroy());
    req.on('error', done);
    req.end(Buffer.concat(chunks));
  } catch { done(); }
});
