#!/usr/bin/env node
// Claude Code UserPromptSubmit hook, any OS (Node 18+, no dependencies): recall memory for the prompt and hand it to Claude.
// stdout: the server's reply, {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}} or {}
// (at most 10,000 characters, Claude Code's cap). Claude Code's own timeout for this event is 30 s; this gives up at 15.
// stdin: the hook's JSON, sent as is to the local Spookling Memory server (SPOOKLING_MEMORY_URL, default
// http://127.0.0.1:7447). Never blocks Claude: with no server, or on any error, it prints nothing and exits 0.
// A connect gets 1 s (a dead port on WSL's mirrored network is never refused, only dropped), the reply
// 15 s. Settings run it in exec form, {"command": "node", "args": ["<this file>"]}, so no shell ever parses the path.
// Same contract as user-prompt-submit.sh; the logic lives in the server (server/spookling_memory/hooks.py).
const http = require('node:http');
const url = (process.env.SPOOKLING_MEMORY_URL || 'http://127.0.0.1:7447').replace(/\/+$/, '') + '/v1/hook/user-prompt-submit';
const done = () => process.exit(0);
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', () => {
  try {
    const req = http.request(url, { method: 'POST', headers: { 'content-type': 'application/json' }, timeout: 1000 }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (d) => { body += d; });
      res.on('end', () => {
      if (res.statusCode === 200 && body) return process.stdout.write(body, done); // exit once stdout has flushed
        done();
      });
    });
    req.on('socket', (s) => s.once('connect', () => req.setTimeout(15000)));
    req.on('timeout', () => req.destroy());
    req.on('error', done);
    req.end(Buffer.concat(chunks));
  } catch { done(); }
});
