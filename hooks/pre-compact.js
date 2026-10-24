#!/usr/bin/env node
// Claude Code PreCompact hook, any OS (Node 18+, no dependencies): log the session's newest turns, then keep its rules, decisions, values, dates, paths
// and names verbatim before the summary drops them. Prints nothing: PreCompact output would only block or be discarded.
// stdin: the hook's JSON, sent as is to the local Spookling Memory server (SPOOKLING_MEMORY_URL, default
// http://127.0.0.1:7447). Never blocks Claude: with no server, or on any error, it prints nothing and exits 0.
// A connect gets 1 s (a dead port on WSL's mirrored network is never refused, only dropped), the reply
// 60 s. Settings run it in exec form, {"command": "node", "args": ["<this file>"]}, so no shell ever parses the path.
// Same contract as pre-compact.sh; the logic lives in the server (server/spookling_memory/hooks.py).
if (process.env.SPOOKLING_TAGGING) process.exit(0); // a tagging call the server started: nothing to log or recall
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
    req.on('socket', (s) => s.once('connect', () => req.setTimeout(60000)));
    req.on('timeout', () => req.destroy());
    req.on('error', done);
    req.end(Buffer.concat(chunks));
  } catch { done(); }
});
