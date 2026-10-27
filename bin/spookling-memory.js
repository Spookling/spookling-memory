#!/usr/bin/env node
import { homedir } from 'node:os';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import process from 'node:process';
import { frames } from '../cli/src/cutscene.js';
import { init, status, uninstall } from '../cli/src/wizard.js';

const HELP = `Spookling Memory

  npx spookling init        intro, then the step-by-step install
  npx spookling demo        the whole install as you would see it, answered for you; writes nothing
  npx spookling status      is the server up, which harness is wired (project and user files)
  npx spookling uninstall   remove the hooks and MCP entry, keep .memory/

  --scope project   init/uninstall: project harness files (.codex/hooks.json for Codex), the default
  --scope user      init/uninstall: user harness files ($CODEX_HOME/hooks.json for Codex)
  --harness codex   uninstall: remove Codex hooks (default: claude); init asks which harness to use
  --no-ghost        init: do not ask to put the ghost above the Claude Code prompt
  --no-anim         skip the animation (also skipped when NO_COLOR is set or output is not a terminal)
  --fast            demo only: no pauses (any key also skips the current one)
  --frames N        demo only: print N frames from across the intro, one after another, and stop
`;

const args = process.argv.slice(2);
const cmd = args[0] && !args[0].startsWith('-') ? args[0] : 'help';
const anim = !args.includes('--no-anim'), fast = args.includes('--fast'), ghost = !args.includes('--no-ghost');
const fi = args.indexOf('--frames');
const si = args.findIndex((a) => a === '--scope' || a.startsWith('--scope='));
const scope = si < 0 ? 'project' : args[si].includes('=') ? args[si].split('=')[1] : args[si + 1];
if (!['project', 'user'].includes(scope)) {
  process.stderr.write(`--scope takes project or user, not ${scope ?? 'nothing'}\n`);
  process.exit(2);
}

const hi = args.findIndex((a) => a === '--harness' || a.startsWith('--harness='));
const harness = hi < 0 ? 'claude' : args[hi].includes('=') ? args[hi].split('=')[1] : args[hi + 1];
if (!['claude', 'codex'].includes(harness) || (hi >= 0 && cmd !== 'uninstall')) {
  process.stderr.write('--harness takes claude or codex and is only used by uninstall; init asks which harness to use\n');
  process.exit(2);
}

const home = homedir(), cwd = process.cwd();
const folder = scope === 'user' ? home : cwd; // user scope keeps the memory in the home folder
if (cmd === 'init') process.exitCode = await init({ anim, scope, project: folder, ghost });
else if (cmd === 'demo') process.exitCode = fi >= 0 ? (frames(Math.max(1, Number(args[fi + 1]) || 3)), 0) : await init({ anim, scope, dry: true, fast, ghost });
else if (cmd === 'status') process.exitCode = await status(existsSync(join(cwd, '.memory')) || !existsSync(join(home, '.memory')) ? cwd : home);
else if (cmd === 'uninstall') process.exitCode = await uninstall(folder, { scope, harness });
else {
  process.stdout.write(HELP);
  process.exitCode = cmd === 'help' || args.includes('--help') || args.includes('-h') ? 0 : 1;
}
