// Spookling's entries in settings.json and .mcp.json: pure functions that never drop a key they did not add.
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import path, { join } from 'node:path';

export const HOOK_FILES = {
  PreCompact: 'pre-compact.js',
  UserPromptSubmit: 'user-prompt-submit.js',
  Stop: 'stop.js',
};
export const MCP_NAME = 'spookling-memory';

export const CODEX_EVENTS = ['SessionStart', 'UserPromptSubmit', 'PreCompact', 'Stop'];
// Codex command hooks use a shell command, not Claude Code's command + args form.
// Single quotes protect spaces, apostrophes and shell substitutions in POSIX paths.
export const codexHandlers = (hooksDir, p = path) => {
  const script = p.join(hooksDir, 'spookling-codex.cjs');
  const quote = (s) => `'${s.replaceAll("'", "'\\''")}'`;
  // Use a quoted native Windows path; reject shell expansion/control characters rather than misquote them.
  if (p === path.win32 && /["%$`!\r\n]/.test(script)) throw new Error('Codex hook path contains unsupported Windows shell characters');
  const command = p === path.win32 ? `node "${script}"` : `node ${quote(script)}`;
  return Object.fromEntries(CODEX_EVENTS.map((ev) => [ev, {
    type: 'command', command, timeout: ev === 'PreCompact' ? 65 : 20, statusMessage: '👻 Spookling memory',
    ...(ev === 'UserPromptSubmit' && { additionalContextLimit: 4000 }),
  }]));
};
export const codexHooksPath = (project, scope = 'project', env = process.env) =>
  join(scope === 'user' ? (env.CODEX_HOME || join(homedir(), '.codex')) : join(project, '.codex'), 'hooks.json');

// exec form (Claude Code v2.1.139+): no shell on any OS, so the path needs no quoting
export const hookHandlers = (hooksDir, p = path) => Object.fromEntries(Object.entries(HOOK_FILES)
  .map(([ev, f]) => [ev, { type: 'command', command: 'node', args: [p.join(hooksDir, f)] }]));

// \\wsl.localhost\<distro>\... or \\wsl$\<distro>\... -> { distro, path }
export const wslShare = (dir) => {
  const m = /^\\\\wsl(?:\.localhost|\$)\\([^\\]+)((?:\\[^\\]+)*)\\?$/i.exec(dir);
  return m && { distro: m[1], path: m[2].replace(/\\/g, '/') || '/' };
};

// a folder inside WSL runs in WSL: a Windows uv would build a Windows .venv over WSL's; bash -l puts uv on PATH
export const mcpEntry = (serverDir, url) => {
  const w = wslShare(serverDir);
  if (!w) return { type: 'stdio', command: 'uv', args: ['run', '--project', serverDir, 'spookling-memory', 'mcp'], env: { SPOOKLING_MEMORY_URL: url } };
  const q = `'${w.path.replace(/'/g, `'\\''`)}'`;
  return {
    type: 'stdio', command: 'wsl.exe', args: ['-d', w.distro, '--', 'bash', '-lc', `exec uv run --project ${q} spookling-memory mcp`],
    env: { SPOOKLING_MEMORY_URL: url, WSLENV: 'SPOOKLING_MEMORY_URL' },
  };
};

// ours: one of our scripts (.js, or .sh from an older install) under a spookling path
const SCRIPT = /[\\/]hooks[\\/](?:(pre-compact|user-prompt-submit|stop)\.(js|sh)|spookling-codex\.cjs)\b/;
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
// drop a group only if we emptied it
const strip = (groups, keep = () => false) => groups.flatMap((g) => {
  if (!ours(g)) return [g];
  const hooks = g.hooks.filter((h) => !isOurs(h) || keep(h));
  return hooks.length ? [{ ...g, hooks }] : [];
});

// no matcher: on PreCompact none means manual and auto
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

export function wiring(settings, cfg) {
  const events = Object.entries(isObj(settings?.hooks) ? settings.hooks : {})
    .filter(([, gs]) => Array.isArray(gs) && gs.some((g) => isObj(g) && ours(g)))
    .map(([ev]) => ev);
  return { events, mcp: isObj(cfg?.mcpServers) && MCP_NAME in cfg.mcpServers };
}

// unreadable JSON throws, so no caller overwrites a file it could not parse
export function readJson(path) {
  if (!existsSync(path)) return {};
  const text = readFileSync(path, 'utf8');
  if (!text.trim()) return {};
  try { return JSON.parse(text); } catch (e) { throw new Error(`${path} is not valid JSON (${e.message}); left it untouched`); }
}

const configDir = (env) => env.CLAUDE_CONFIG_DIR || join(homedir(), '.claude');
export const settingsPath = (project, scope = 'project', env = process.env) =>
  scope === 'user' ? join(configDir(env), 'settings.json') : join(project, '.claude', 'settings.json');
export const mcpPath = (project, scope = 'project', env = process.env) =>
  scope === 'user' ? (env.CLAUDE_CONFIG_DIR ? join(env.CLAUDE_CONFIG_DIR, '.claude.json') : join(homedir(), '.claude.json'))
    : join(project, '.mcp.json');
