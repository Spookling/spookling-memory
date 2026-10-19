// Merge and unmerge Spookling's entries in .claude/settings.json and .mcp.json.
// Pure functions on parsed JSON; they never drop a key they did not add.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// Claude Code hook event -> script in the package's hooks/ folder.
export const HOOK_FILES = {
  PreCompact: 'pre-compact.sh',
  UserPromptSubmit: 'user-prompt-submit.sh',
  Stop: 'stop.sh',
};
export const MCP_NAME = 'spookling-memory';

const shq = (p) => (/^[\w./-]+$/.test(p) ? p : `'${p.replace(/'/g, `'\\''`)}'`);
export const hookCommands = (hooksDir) =>
  Object.fromEntries(Object.entries(HOOK_FILES).map(([ev, f]) => [ev, shq(join(hooksDir, f))]));

export const mcpEntry = (serverDir) => ({
  type: 'stdio',
  command: 'uv',
  args: ['run', '--project', serverDir, 'spookling-memory', 'mcp', '--folder', '.memory'],
});

// Ours = a command naming one of our scripts under a spookling path (any install location).
export const isOurs = (cmd) => typeof cmd === 'string' && /spookling/i.test(cmd)
  && Object.values(HOOK_FILES).some((f) => cmd.replace(/\\/g, '/').includes(`/hooks/${f}`));

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
const ours = (g) => (g.hooks ?? []).some((h) => isOurs(h.command));
// Drop our hooks from a group list (except those keep() spares); drop a group only if we emptied it.
const strip = (groups, keep = () => false) => groups.flatMap((g) => {
  if (!ours(g)) return [g];
  const hooks = g.hooks.filter((h) => !isOurs(h.command) || keep(h));
  return hooks.length ? [{ ...g, hooks }] : [];
});

// Add our three hooks. Leaves every other hook alone; replaces an old Spookling path.
export function addHooks(settings, cmds) {
  checkShape(settings);
  const out = structuredClone(settings);
  out.hooks ??= {};
  for (const [ev, cmd] of Object.entries(cmds)) {
    const groups = strip(out.hooks[ev] ?? [], (h) => h.command === cmd);
    if (!groups.some((g) => (g.hooks ?? []).some((h) => h.command === cmd))) {
      groups.push({ hooks: [{ type: 'command', command: cmd }] });
    }
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

export const settingsPath = (project) => join(project, '.claude', 'settings.json');
export const mcpPath = (project) => join(project, '.mcp.json');
