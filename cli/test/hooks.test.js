import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  addHooks, removeHooks, addMcp, removeMcp, hookCommands, mcpEntry, wiring, readJson, isOurs, MCP_NAME,
} from '../src/hooks.js';

const cmds = hookCommands('/opt/spookling-memory/hooks');
const theirs = { type: 'command', command: 'npm run lint' };
const user = {
  model: 'opus',
  permissions: { allow: ['Bash(ls:*)'] },
  hooks: {
    Stop: [{ hooks: [theirs] }],
    PreToolUse: [{ matcher: 'Bash', hooks: [{ type: 'command', command: 'echo hi' }] }],
  },
};

test('hook commands point at the package hooks folder', () => {
  assert.deepEqual(cmds, {
    PreCompact: '/opt/spookling-memory/hooks/pre-compact.sh',
    UserPromptSubmit: '/opt/spookling-memory/hooks/user-prompt-submit.sh',
    Stop: '/opt/spookling-memory/hooks/stop.sh',
  });
  assert.equal(hookCommands('/my spookling/hooks').Stop, "'/my spookling/hooks/stop.sh'");
});

test('addHooks on an empty file adds the three events', () => {
  const out = addHooks({}, cmds);
  assert.deepEqual(Object.keys(out.hooks), ['PreCompact', 'UserPromptSubmit', 'Stop']);
  assert.deepEqual(out.hooks.Stop, [{ hooks: [{ type: 'command', command: cmds.Stop }] }]);
});

test('addHooks keeps every existing key and hook', () => {
  const out = addHooks(user, cmds);
  assert.equal(out.model, 'opus');
  assert.deepEqual(out.permissions, user.permissions);
  assert.deepEqual(out.hooks.PreToolUse, user.hooks.PreToolUse);
  assert.deepEqual(out.hooks.Stop[0], { hooks: [theirs] });
  assert.equal(out.hooks.Stop.length, 2);
  assert.equal(user.hooks.Stop.length, 1, 'input not mutated');
});

test('addHooks twice is a no-op', () => {
  const once = addHooks(user, cmds);
  assert.deepEqual(addHooks(once, cmds), once);
});

test('addHooks replaces a Spookling hook from an older install path', () => {
  const old = addHooks(user, hookCommands('/old/npx/@spookling/memory/hooks'));
  const out = addHooks(old, cmds);
  const all = Object.values(out.hooks).flat().flatMap((g) => g.hooks.map((h) => h.command));
  assert.ok(!all.some((c) => c.startsWith('/old/')));
  assert.ok(all.includes(cmds.Stop) && all.includes('npm run lint'));
});

test('removeHooks restores the original settings exactly', () => {
  assert.deepEqual(removeHooks(addHooks(user, cmds)), user);
  assert.deepEqual(removeHooks(addHooks({}, cmds)), {});
});

test('removeHooks keeps a foreign hook that shares a group with ours', () => {
  const s = { hooks: { Stop: [{ hooks: [theirs, { type: 'command', command: cmds.Stop }] }] } };
  assert.deepEqual(removeHooks(s), { hooks: { Stop: [{ hooks: [theirs] }] } });
});

test('malformed settings throw instead of being overwritten', () => {
  assert.throws(() => addHooks([], cmds));
  assert.throws(() => addHooks({ hooks: [] }, cmds));
  assert.throws(() => addHooks({ hooks: { Stop: {} } }, cmds));
  assert.throws(() => removeHooks({ hooks: { Stop: 'x' } }));
});

test('isOurs only matches our scripts under a spookling path', () => {
  assert.ok(isOurs(cmds.Stop));
  assert.ok(isOurs('C:\\Users\\a\\spookling-memory\\hooks\\stop.sh'));
  assert.ok(!isOurs('/home/a/other/hooks/stop.sh'));
  assert.ok(!isOurs('npm run lint'));
  assert.ok(!isOurs(undefined));
});

test('MCP entry is added, kept beside others, and removed cleanly', () => {
  const cfg = { mcpServers: { github: { command: 'gh-mcp' } } };
  const entry = mcpEntry('/opt/spookling-memory/server');
  const out = addMcp(cfg, entry);
  assert.deepEqual(out.mcpServers.github, cfg.mcpServers.github);
  assert.deepEqual(out.mcpServers[MCP_NAME], {
    type: 'stdio', command: 'uv',
    args: ['run', '--project', '/opt/spookling-memory/server', 'spookling-memory', 'mcp', '--folder', '.memory'],
  });
  assert.deepEqual(removeMcp(out), cfg);
  assert.deepEqual(addMcp({}, entry), { mcpServers: { [MCP_NAME]: entry } });
  assert.throws(() => addMcp({ mcpServers: [] }, entry));
  assert.deepEqual(removeMcp({}), {});
});

test('wiring reports what is in place', () => {
  assert.deepEqual(wiring({}, {}), { events: [], mcp: false });
  const s = addHooks(user, cmds), m = addMcp({}, mcpEntry('/x'));
  const w = wiring(s, m);
  assert.deepEqual([w.events.sort(), w.mcp], [['PreCompact', 'Stop', 'UserPromptSubmit'], true]);
});

test('readJson: missing or empty is {}, broken JSON throws', () => {
  const d = mkdtempSync(join(tmpdir(), 'spk-'));
  assert.deepEqual(readJson(join(d, 'nope.json')), {});
  writeFileSync(join(d, 'empty.json'), '\n');
  assert.deepEqual(readJson(join(d, 'empty.json')), {});
  writeFileSync(join(d, 'bad.json'), '{ "a": ');
  assert.throws(() => readJson(join(d, 'bad.json')), /left it untouched/);
});
