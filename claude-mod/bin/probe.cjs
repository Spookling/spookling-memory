#!/usr/bin/env node
// What the band needs from outside Claude Code, as one JSON line: this session's .memory folder (found the way the
// hooks find it), how the terminal can draw the ghost (hooks/ghost.js: cells where the font has no half blocks), and
// the folder's server stats. Never starts the server: the hooks do that. node probe.cjs <cwd>
const { existsSync, readFileSync, realpathSync } = require('node:fs');
const { homedir } = require('node:os');
const { dirname, join } = require('node:path');

// the package's hooks: init names its version's in bin/hooks.json; in a clone, the ones beside this mod
let hooks = join(__dirname, '..', '..', 'hooks');
try { hooks = JSON.parse(readFileSync(join(__dirname, 'hooks.json'), 'utf8')).hooks || hooks; } catch {}
const ghost = require(join(hooks, 'ghost.js'));
const server = require(join(hooks, 'server.js'));

const real = (p) => { try { return realpathSync(p); } catch { return p; } };
function memory(cwd) {
  for (let d = cwd; d; d = dirname(d) === d ? '' : dirname(d)) {
    const m = join(d, '.memory');
    if (existsSync(join(m, 'spookling.json')) || existsSync(join(m, 'log.jsonl'))) return real(m);
  }
  const home = join(homedir(), '.memory');
  return existsSync(home) ? real(home) : null;
}

(async () => {
  const dir = memory(process.argv[2] || process.cwd());
  let stats = null;
  if (dir) {
    const r = await server.request(dir, '/v1/stats', { wait: 0, timeout: 3000 });
    try { stats = !r.down && r.status === 200 ? JSON.parse(r.body) : null; } catch {}
  }
  const shown = dir && dir.replace(real(homedir()), '~');
  process.stdout.write(JSON.stringify({
    memory: dir, shown, render: ghost.mode(), up: !!stats,
    stats: stats && { n_events: stats.n_events, n_facts: stats.n_facts, n_rules: stats.n_rules, tagging: stats.tagging },
  }) + '\n');
})();
