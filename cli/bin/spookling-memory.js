#!/usr/bin/env node
import process from 'node:process';
import { frames, play } from '../src/cutscene.js';
import { init, status, uninstall } from '../src/wizard.js';

const HELP = `Spookling Memory

  npx @spookling/memory init        intro, then the step-by-step install
  npx @spookling/memory demo        play the intro only
  npx @spookling/memory status      is the server up, which harness is wired (project and user files)
  npx @spookling/memory uninstall   remove the hooks and MCP entry, keep .memory/

  --scope project   init and uninstall: .claude/settings.json and .mcp.json in this folder (the default)
  --scope user      init and uninstall: ~/.claude/settings.json and the user MCP list, for every project
  --no-anim         skip the animation (also skipped when NO_COLOR is set or output is not a terminal)
  --frames N        demo only: print N frames from across the intro, one after another
`;

const args = process.argv.slice(2);
const cmd = args[0] && !args[0].startsWith('-') ? args[0] : 'help';
const anim = !args.includes('--no-anim');
const fi = args.indexOf('--frames');
const si = args.findIndex((a) => a === '--scope' || a.startsWith('--scope='));
const scope = si < 0 ? 'project' : args[si].includes('=') ? args[si].split('=')[1] : args[si + 1];
if (!['project', 'user'].includes(scope)) {
  process.stderr.write(`--scope takes project or user, not ${scope ?? 'nothing'}\n`);
  process.exit(2);
}

if (cmd === 'init') process.exitCode = await init({ anim, scope });
else if (cmd === 'demo') fi >= 0 ? frames(Math.max(1, Number(args[fi + 1]) || 3)) : await play({ anim });
else if (cmd === 'status') process.exitCode = await status();
else if (cmd === 'uninstall') process.exitCode = await uninstall(process.cwd(), { scope });
else {
  process.stdout.write(HELP);
  process.exitCode = cmd === 'help' || args.includes('--help') || args.includes('-h') ? 0 : 1;
}
