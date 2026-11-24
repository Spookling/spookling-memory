import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { setDefaultOrg, writeCredentials } from '../src/credentials.js';
import { main } from '../src/main.js';
import { install } from '../src/wizard.js';
import { envelope, listen, readReq, tmpHome } from './helpers.mjs';

const org = (id, name) => ({ org: { id, name }, membership: { status: 'active' } });
const nextCursor = '2026-11-24T12:00:00.000Z#3ef76ae7-028a-4415-874b-c6bb0420f249';

async function setup(t, orgs = [org('o1', 'Acme')]) {
  const requests = [];
  const entries = [{ id: 'entry-1', createdAt: '2026-11-24T12:00:00.000Z', symptom: 'Build timed out <worker>', fix: 'Restart it', project: 'Web' }];
  const server = await listen(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const body = req.method === 'POST' ? await readReq(req) : null;
    requests.push({ method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), body, auth: req.headers.authorization });
    if (url.pathname === '/cli/orgs') return envelope(res, 200, { success: true, data: { orgs } });
    if (req.method === 'POST') return envelope(res, 201, { success: true, data: { entry: { ...entries[0], ...body } } });
    return envelope(res, 200, { success: true, data: { entries, nextCursor } });
  });
  t.after(() => new Promise((resolve) => { server.closeAllConnections?.(); server.close(resolve); }));
  const home = tmpHome('spk-papercuts-home-');
  const apiUrl = `http://127.0.0.1:${server.address().port}`;
  writeCredentials({ token: 'spk_papercuts_test', user: { id: 'test-user' }, apiUrl }, { home });
  return { requests, home };
}

async function cli(home, args, deps = {}) {
  let output = '';
  const write = (text) => { output += text; };
  const code = await main(['node', 'spookling', 'papercuts', ...args], { home, env: {}, tty: true, write, ...deps });
  return { code, output };
}

test('lists and searches across projects in one team, with an optional cursor', async (t) => {
  const { home, requests } = await setup(t);
  const list = await cli(home, []);
  assert.equal(list.code, 0, list.output);
  assert.match(list.output, /Papercuts · Acme/);
  assert.match(list.output, /Fix · Restart it/);
  assert.match(list.output, /Continue with the same command/);
  const search = await cli(home, ['search', 'timed out <worker>', '--cursor', nextCursor]);
  assert.equal(search.code, 0, search.output);
  assert.deepEqual(requests.filter((r) => r.path.includes('/papercuts')).map(({ path, query, auth }) => ({ path, query, auth })), [
    { path: '/memory/orgs/o1/papercuts', query: {}, auth: 'Bearer spk_papercuts_test' },
    { path: '/memory/orgs/o1/papercuts', query: { q: 'timed out <worker>', cursor: nextCursor }, auth: 'Bearer spk_papercuts_test' },
  ]);
});

test('several teams require an explicit org and a duplicate name requires the id', async (t) => {
  const { home, requests } = await setup(t, [org('o1', 'Acme'), org('o2', 'Acme')]);
  setDefaultOrg({ id: 'o1', name: 'Acme' }, { home });
  assert.match((await cli(home, [])).output, /Run again with --org/);
  assert.match((await cli(home, ['--org', 'Acme'])).output, /pass the org id/);
  assert.deepEqual(requests.map((r) => r.path), ['/cli/orgs', '/cli/orgs']);
  const result = await cli(home, ['--org', 'o2']);
  assert.equal(result.code, 0, result.output);
  assert.equal(requests.at(-1).path, '/memory/orgs/o2/papercuts');
});

test('shows the team and posts only the confirmed entry', async (t) => {
  const { home, requests } = await setup(t);
  const args = ['add', ' Build timed out ', 'Restart the worker', '--project', 'Web'];
  const unattended = await cli(home, args, { tty: false });
  assert.equal(unattended.code, 1);
  assert.deepEqual(requests, []);
  await cli(home, args, { confirm: async () => false });
  assert.equal(requests.filter((r) => r.method === 'POST').length, 0);
  const added = await cli(home, args, { confirm: async (question) => {
    assert.equal(question, 'Add this papercut?');
    return true;
  } });
  assert.equal(added.code, 0, added.output);
  assert.match(added.output, /Add to Acme's Papercuts log · Web/);
  assert.deepEqual(requests.at(-1).body, { symptom: 'Build timed out', fix: 'Restart the worker', project: 'Web' });
});

test('installed agent bridge searches, quotes colleague text, and exposes no append call', async (t) => {
  const { home, requests } = await setup(t);
  const source = fileURLToPath(new URL('../..', import.meta.url));
  const dest = tmpHome('spk-papercuts-install-');
  install(source, dest);
  const script = join(dest, 'hooks', 'lib', 'team-invoke.js');
  const run = promisify(execFile);
  const env = { PATH: process.env.PATH, HOME: home, USERPROFILE: home };
  const { stdout } = await run(process.execPath, [script, 'papercuts', JSON.stringify({ query: 'worker' })], { env });
  const result = JSON.parse(stdout);
  assert.equal(result.untrusted, true);
  assert.equal(result.entries[0].symptom, 'Build timed out &lt;worker&gt;');
  assert.equal(result.nextCursor, nextCursor);
  assert.deepEqual(requests.at(-1).query, { q: 'worker', limit: '10' });
  await assert.rejects(run(process.execPath, [script, 'papercuts-add'], { env }), /Command failed/);
  assert.equal(requests.filter((r) => r.method === 'POST').length, 0);
});
