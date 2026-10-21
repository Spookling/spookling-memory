#!/usr/bin/env node
// Claude Code PreCompact hook, any OS (Node 18+, no dependencies): log the session's newest turns, then keep its rules, decisions, values, dates, paths
// and names verbatim before the summary drops them. Prints nothing: PreCompact output would only block or be discarded.
// stdin: the hook's JSON, sent as is to the local Spookling Memory server (SPOOKLING_MEMORY_URL, default
// http://127.0.0.1:7447). Never blocks Claude: with no server, or on any error, it prints nothing and exits 0.
// Settings run it in exec form, {"command": "node", "args": ["<this file>"]}, so no shell ever parses the path.
// Same contract as pre-compact.sh; the logic lives in the server (server/spookling_memory/hooks.py).
const url = (process.env.SPOOKLING_MEMORY_URL || 'http://127.0.0.1:7447').replace(/\/+$/, '') + '/v1/hook/pre-compact';
const done = () => process.exit(0);
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', async () => {
  try {
    const r = await fetch(url, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: Buffer.concat(chunks),
      signal: AbortSignal.timeout(60000),
    });
    await r.arrayBuffer();
  } catch {}
  done();
});
