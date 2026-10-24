#!/usr/bin/env node
// Spookling's native Codex hooks, Node 18+, no dependencies. Shares Claude Code's HTTP server.
// Only a checked server response can say ready/active. Errors are visible but never block a turn.
// Stop and PreCompact print nothing: a Stop decision must never create a continuation loop.
if (process.env.SPOOKLING_TAGGING) process.exit(0);
const http = require('node:http');
const base = (process.env.SPOOKLING_MEMORY_URL || 'http://127.0.0.1:7447').replace(/\/+$/, '');
const events = { UserPromptSubmit: 'user-prompt-submit', PreCompact: 'pre-compact', Stop: 'stop' };
const chunks = [];
const done = () => process.exit(0);
const emit = (value) => process.stdout.write(JSON.stringify(value), done);
let visible = false;
const unavailable = () => visible ? emit({ systemMessage: '👻 Spookling memory unavailable; check the server with spookling-memory status.' }) : done();
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('error', done);
process.stdin.on('end', () => {
  let p;
  try { p = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { return done(); }
  if (!p || typeof p !== 'object' || Array.isArray(p)) return done();
  const start = p.hook_event_name === 'SessionStart';
  if (!start && !Object.hasOwn(events, p.hook_event_name)) return done();
  if (p.hook_event_name === 'UserPromptSubmit' && (typeof p.prompt !== 'string' || !p.prompt.trim())) return done();
  visible = start || p.hook_event_name === 'UserPromptSubmit';
  const timeout = p.hook_event_name === 'PreCompact' ? 60000 : 15000;
  // A wall-clock deadline also covers a connected server that never finishes its response.
  const deadline = setTimeout(unavailable, timeout + 1000);
  const fail = () => { clearTimeout(deadline); unavailable(); };
  try {
    const req = http.request(base + (start ? '/v1/stats' : `/v1/hook/${events[p.hook_event_name]}`), {
      method: start ? 'GET' : 'POST', timeout: 1000, headers: { 'content-type': 'application/json' },
    }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('error', fail);
      res.on('data', (d) => { body += d; });
      res.on('end', () => {
        clearTimeout(deadline);
        if (res.statusCode !== 200) return unavailable();
        let reply;
        try { reply = JSON.parse(body); } catch { return unavailable(); }
        if (!reply || typeof reply !== 'object' || Array.isArray(reply)) return unavailable();
        if (!visible) return done();
        if (start) {
          if (!Number.isInteger(reply.n_events) || !reply.tagger || typeof reply.folder !== 'string') return unavailable();
          return emit({ systemMessage: '👻 Spookling memory ready — local server connected.' });
        }
        // Pass the server's bounded recall block through unchanged, including a successful empty search.
        const context = reply.hookSpecificOutput;
        if (Object.keys(reply).length && !(context?.hookEventName === 'UserPromptSubmit' && typeof context.additionalContext === 'string')) return unavailable();
        emit({ ...reply, systemMessage: '👻 Spookling memory active — recall completed for this prompt.' });
      });
    });
    req.on('socket', (s) => s.once('connect', () => req.setTimeout(timeout)));
    req.on('timeout', () => req.destroy());
    req.on('error', fail);
    req.end(start ? undefined : JSON.stringify({ ...p, harness: 'codex' }));
  } catch { fail(); }
});
