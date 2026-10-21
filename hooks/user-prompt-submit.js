#!/usr/bin/env node
// Claude Code UserPromptSubmit hook, any OS (Node 18+, no dependencies): recall memory for the prompt and hand it to Claude.
// stdout: the server's reply, {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}} or {}
// (at most 10,000 characters, Claude Code's cap). Claude Code's own timeout for this event is 30 s; this gives up at 15.
// stdin: the hook's JSON, sent as is to the local Spookling Memory server (SPOOKLING_MEMORY_URL, default
// http://127.0.0.1:7447). Never blocks Claude: with no server, or on any error, it prints nothing and exits 0.
// Settings run it in exec form, {"command": "node", "args": ["<this file>"]}, so no shell ever parses the path.
// Same contract as user-prompt-submit.sh; the logic lives in the server (server/spookling_memory/hooks.py).
const url = (process.env.SPOOKLING_MEMORY_URL || 'http://127.0.0.1:7447').replace(/\/+$/, '') + '/v1/hook/user-prompt-submit';
const done = () => process.exit(0);
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', async () => {
  try {
    const r = await fetch(url, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: Buffer.concat(chunks),
      signal: AbortSignal.timeout(15000),
    });
    const text = await r.text();
    if (r.ok && text) return process.stdout.write(text, done); // exit once stdout has flushed: a pipe write is async on macOS and Linux
  } catch {}
  done();
});
