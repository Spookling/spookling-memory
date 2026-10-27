#!/usr/bin/env node
// What the band needs from outside Claude Code, as one JSON line: this session's .memory folder (where the hooks write
// their pulse: the project's, else ~/.memory), how the terminal can draw the ghost (hooks/ghost.js: cells where the
// font has no half blocks), and the memory server's stats. Never starts the server. node probe.cjs <cwd>
const http = require('node:http');
const { readFileSync, realpathSync } = require('node:fs');
const { homedir } = require('node:os');
const { dirname, join } = require('node:path');

// the package's hooks: init names its version's in bin/hooks.json; in a clone, the ones beside this mod
let hooks = join(__dirname, '..', '..', 'hooks');
try { hooks = JSON.parse(readFileSync(join(__dirname, 'hooks.json'), 'utf8')).hooks || hooks; } catch {}
const ghost = require(join(hooks, 'ghost.js'));
const pulse = require(join(hooks, 'pulse.js'));
const url = (process.env.SPOOKLING_MEMORY_URL || 'http://127.0.0.1:7447').replace(/\/+$/, '');

const real = (p) => { try { return realpathSync(p); } catch { return p; } };

// the server's /v1/stats, or null when nothing answered in 3 s
const stats = () => new Promise((res) => {
  try {
    const req = http.get(`${url}/v1/stats`, { timeout: 3000 }, (r) => {
      let text = '';
      r.setEncoding('utf8');
      r.on('data', (d) => { text += d; });
      r.on('error', () => res(null));
      r.on('end', () => { try { res(r.statusCode === 200 ? JSON.parse(text) : null); } catch { res(null); } });
    });
    req.on('timeout', () => req.destroy());
    req.on('error', () => res(null));
  } catch { res(null); }
});

(async () => {
  const file = pulse.where(process.argv[2] || process.cwd());
  const dir = file ? real(dirname(file)) : null;
  const s = dir ? await stats() : null;
  process.stdout.write(JSON.stringify({
    memory: dir, shown: dir && dir.replace(real(homedir()), '~'), render: ghost.mode(), up: !!s,
    stats: s && { n_events: s.n_events, n_facts: s.n_facts, n_rules: s.n_rules, tagging: s.tagging },
  }) + '\n');
})();
