// Merge and unmerge Spookling's entries in Claude Code's settings.json and .mcp.json.
// Pure functions on parsed JSON; they never drop a key they did not add.
// Shapes checked against the Claude Code hooks, settings and MCP docs on 21 Oct 2026 (docs/claude-code-facts.md).
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import path, { join } from 'node:path';

// Claude Code hook event -> script in the package's hooks/ folder.
export const HOOK_FILES = {
  PreCompact: 'pre-compact.js',
  UserPromptSubmit: 'user-prompt-submit.js',
  Stop: 'stop.js',
};
export const MCP_NAME = 'spookling-memory';

// Exec form: with `args` set, Claude Code spawns `command` directly with `args` as its argv, no shell on any OS, so a
// path with spaces, quotes, $ or a \\wsl.localhost\ share is passed exactly as written. Shell form would go through
// sh, Git Bash or PowerShell, each with its own quoting. node.exe is a real executable, which exec form needs on
// Windows. Exec form needs Claude Code v2.1.139 or later. No URL here: the scripts default to http://127.0.0.1:7447
// and read SPOOKLING_MEMORY_URL from the environment. `p` is node:path; tests pass path.win32.
export const hookHandlers = (hooksDir, p = path) => Object.fromEntries(Object.entries(HOOK_FILES)
  .map(([ev, f]) => [ev, { type: 'command', command: 'node', args: [p.join(hooksDir, f)] }]));
export const showHandler = (h) => [h.command, ...(h.args ?? [])].map((a) => (/[\s"']/.test(a) ? JSON.stringify(a) : a)).join(' ');

// `mcp` is a thin client of `serve`, so it takes the URL, not the folder.
export const mcpEntry = (serverDir, url) => ({
  type: 'stdio',
  command: 'uv',
  args: ['run', '--project', serverDir, 'spookling-memory', 'mcp'],
  env: { SPOOKLING_MEMORY_URL: url },
});

// Ours = a handler naming one of our scripts (.js, or .sh from an older install) under a spookling path, in its
// command or its args. Takes a handler or a bare command string.
const SCRIPT = /[\\/]hooks[\\/](pre-compact|user-prompt-submit|stop)\.(js|sh)\b/;
export const isOurs = (h) => {
  const s = typeof h === 'string' ? h : [h?.command, ...(Array.isArray(h?.args) ? h.args : [])].filter((x) => typeof x === 'string').join(' ');
  return /spookling/i.test(s) && SCRIPT.test(s);
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
function checkShape(settings) {
  if (!isObj(settings)) throw new Error('settings.json is not a JSON object');
  if (settings.hooks === undefined) return;
  if (!isObj(settings.hooks)) throw new Error('"hooks" in settings.json is not an object');
  for (const [ev, groups] of Object.entries(settings.hooks)) {
    if (!Array.isArray(groups)) throw new Error(`hooks.${ev} in settings.json is not a list`);
    for (const g of groups) if (!isObj(g) || !Array.isArray(g.hooks ?? [])) throw new Error(`hooks.${ev} has an entry without a "hooks" list`);
  }
}
const ours = (g) => (g.hooks ?? []).some(isOurs);
// Drop our hooks from a group list (except those keep() spares); drop a group only if we emptied it.
const strip = (groups, keep = () => false) => groups.flatMap((g) => {
  if (!ours(g)) return [g];
  const hooks = g.hooks.filter((h) => !isOurs(h) || keep(h));
  return hooks.length ? [{ ...g, hooks }] : [];
});

// Add our three handlers, each in a group of its own with no matcher (UserPromptSubmit and Stop take none; on
// PreCompact none means manual and auto). Leaves every other hook alone; replaces an old Spookling path or form.
export function addHooks(settings, handlers) {
  checkShape(settings);
  const out = structuredClone(settings);
  out.hooks ??= {};
  for (const [ev, handler] of Object.entries(handlers)) {
    const groups = strip(out.hooks[ev] ?? [], (h) => same(h, handler));
    if (!groups.some((g) => (g.hooks ?? []).some((h) => same(h, handler)))) groups.push({ hooks: [handler] });
    out.hooks[ev] = groups;
  }
  return out;
}

// Remove only our hooks; drop an event or the "hooks" key only if we emptied it.
export function removeHooks(settings) {
  checkShape(settings);
  const out = structuredClone(settings);
  if (!out.hooks) return out;
  for (const [ev, groups] of Object.entries(out.hooks)) {
    if (!groups.some(ours)) continue;
    const kept = strip(groups);
    if (kept.length) out.hooks[ev] = kept;
    else delete out.hooks[ev];
  }
  if (!Object.keys(out.hooks).length) delete out.hooks;
  return out;
}

export function addMcp(cfg, entry) {
  if (!isObj(cfg)) throw new Error('.mcp.json is not a JSON object');
  if (cfg.mcpServers !== undefined && !isObj(cfg.mcpServers)) throw new Error('"mcpServers" in .mcp.json is not an object');
  return { ...structuredClone(cfg), mcpServers: { ...structuredClone(cfg.mcpServers ?? {}), [MCP_NAME]: entry } };
}

export function removeMcp(cfg) {
  if (!isObj(cfg) || !isObj(cfg.mcpServers) || !(MCP_NAME in cfg.mcpServers)) return cfg;
  const out = structuredClone(cfg);
  delete out.mcpServers[MCP_NAME];
  return out;
}

// What is wired: the events that carry one of our hooks, and whether the MCP entry exists.
export function wiring(settings, cfg) {
  const events = Object.entries(isObj(settings?.hooks) ? settings.hooks : {})
    .filter(([, gs]) => Array.isArray(gs) && gs.some((g) => isObj(g) && ours(g)))
    .map(([ev]) => ev);
  return { events, mcp: isObj(cfg?.mcpServers) && MCP_NAME in cfg.mcpServers };
}

// Missing file -> {}. Unreadable JSON throws, so callers never overwrite a file they could not parse.
export function readJson(path) {
  if (!existsSync(path)) return {};
  const text = readFileSync(path, 'utf8');
  if (!text.trim()) return {};
  try { return JSON.parse(text); } catch (e) { throw new Error(`${path} is not valid JSON (${e.message}); left it untouched`); }
}

// --scope project (default): <project>/.claude/settings.json and <project>/.mcp.json.
// --scope user: ~/.claude/settings.json (every project), and the MCP entry in ~/.claude.json's top-level mcpServers.
// Claude Code writes ~/.claude.json itself, so the wizard only reads it and changes it through `claude mcp`.
// CLAUDE_CONFIG_DIR moves both (on Windows ~ is %USERPROFILE%).
const configDir = (env) => env.CLAUDE_CONFIG_DIR || join(homedir(), '.claude');
export const settingsPath = (project, scope = 'project', env = process.env) =>
  scope === 'user' ? join(configDir(env), 'settings.json') : join(project, '.claude', 'settings.json');
export const mcpPath = (project, scope = 'project', env = process.env) =>
  scope === 'user' ? (env.CLAUDE_CONFIG_DIR ? join(env.CLAUDE_CONFIG_DIR, '.claude.json') : join(homedir(), '.claude.json'))
    : join(project, '.mcp.json');
