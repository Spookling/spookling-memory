import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FRAMES, MID, SMALL } from '../src/ghost-sprite.js';
import { TAGLINE } from '../src/cutscene.js';
import { DEMO_LABEL, SAY, hooksJson, mcpJson } from '../src/wizard.js';
import { hookHandlers, mcpEntry, MCP_NAME } from '../src/hooks.js';

const BIN = fileURLToPath(new URL('../bin/spookling-memory.js', import.meta.url));
const HYPE = /\b(seamless(ly)?|robust|leverag\w*|supercharg\w*|unlock\w*|empower\w*|revolutionary|effortless(ly)?|game.?chang\w*|magic(al)?)\b/i;

test('the ghost says one plain line per step, and one for trouble', () => {
  for (const k of [1, 2, 3, 4, 5, 6, 7, 'codex', 'fail']) assert.ok(SAY[k], `no line for ${k}`);
  for (const [k, line] of Object.entries(SAY)) {
    assert.ok(line.length <= 52, `${k}: ${line.length} chars, the bubble fits 52 at 80 columns`);
    assert.doesNotMatch(line, /[—–]/, `${k}: dash`);
    assert.doesNotMatch(line, /!!|!\s*$/, `${k}: exclamation`);
    assert.doesNotMatch(line, HYPE, `${k}: hype word`);
  }
});

test('sprite: every frame keeps the hat, eyes and smile; only the tail streaks move', () => {
  for (const frames of [FRAMES, SMALL, MID]) {
    assert.ok(frames.length >= 3);
    const [rest] = frames;
    for (const f of frames) {
      assert.equal(f.length, rest.length);
      f.forEach((row, y) => {
        assert.equal(row.length, rest[y].length);
        assert.match(row, /^[0-4]+$/);
        // 0 transparent, 1 BLACK, 2 PURPLE_MID never change; 3 LILAC and 4 GHOST swap where the streaks move
        [...row].forEach((p, x) => { if ('012'.includes(rest[y][x]) || '012'.includes(p)) assert.equal(p, rest[y][x], `(${x}, ${y})`); });
      });
    }
    assert.ok(frames.some((f) => f.join() !== rest.join()), 'the tail never moves');
  }
});

test('the JSON shown before a merge parses to exactly what is merged', () => {
  const hs = hookHandlers('/opt/my spookling/hooks');
  assert.deepEqual(JSON.parse(hooksJson(hs)), { hooks: Object.fromEntries(Object.entries(hs).map(([ev, h]) => [ev, [{ hooks: [h] }]])) });
  const e = mcpEntry('/opt/my spookling/server', 'http://127.0.0.1:7447');
  assert.deepEqual(JSON.parse(mcpJson(e)), { mcpServers: { [MCP_NAME]: e } });
});

test('demo walks every step, writes nothing, and shows the tagline once', () => {
  const dir = mkdtempSync(join(tmpdir(), 'spk-demo-'));
  const env = { ...process.env, HOME: dir, USERPROFILE: dir, CLAUDE_CONFIG_DIR: join(dir, 'cfg'), NO_COLOR: '1' };
  delete env.ANTHROPIC_API_KEY;
  const r = spawnSync(process.execPath, [BIN, 'demo', '--fast', '--no-anim'], { cwd: dir, env, encoding: 'utf8', timeout: 30_000, stdio: ['ignore', 'pipe', 'pipe'] });
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(readdirSync(dir), [], 'demo wrote into the folder');
  const o = r.stdout;
  for (let n = 1; n <= 7; n++) assert.match(o, new RegExp(`step ${n} of 7`));
  for (const k of [1, 2, 3, 4, 5, 6, 7]) assert.ok(o.includes(`spookling: ${SAY[k]}`), `line ${k}`);
  assert.equal(o.split(TAGLINE).length - 1, 1, 'the tagline once');
  assert.equal(o.match(/demo: not written/g)?.length, 4); // keys.env, .gitignore, settings.json, .mcp.json
  // Step 4: a clone reached through \\wsl.localhost from Windows runs its server in WSL, so there is nothing to install.
  if (/so the server runs there/.test(o)) assert.doesNotMatch(o, /uv sync in/);
  else { assert.match(o, /uv sync in .*\n.*demo: skipped/); assert.match(o, /demo: not started/); }
  assert.match(o, /"hooks": \{/);
  assert.match(o, /"mcpServers": \{/);
  assert.match(o, /demo: a sample reply/);
  assert.ok(o.includes(DEMO_LABEL));
  assert.match(o, /Spookling Memory is in\./);
});
