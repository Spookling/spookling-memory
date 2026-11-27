// Install the exact release archive and run its device login, uploader and Papercuts client.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const temp = await mkdtemp(join(tmpdir(), 'spookling-release-'));
const home = join(temp, 'user');
const project = join(temp, 'project');
const installed = join(temp, 'install');
const token = 'spk_release_smoke_fixture';
const entry = { id: 'entry-1', symptom: 'ERR_SMOKE', fix: 'Use the fixture', project: null, createdAt: '2026-11-26T09:00:00.000Z' };
const captured = [];
let published;
let base;
const server = createServer(async (req, res) => {
  const url = new URL(req.url, base);
  const method = req.method;
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null;
  const send = (data, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ success: status < 400, data }));
  };
  if (url.pathname === '/cli/device/start') return send({ deviceCode: 'fixture', userCode: 'ABCD-EFGH', verificationUri: base + '/cli', expiresIn: 60, interval: 0.01 });
  if (url.pathname === '/cli/device/token' && body?.deviceCode === 'fixture') return send({ status: 'approved', token, user: { id: 'smoke-user' } });
  if (req.headers.authorization !== `Bearer ${token}`) return send(null, 401);
  if (url.pathname === '/cli/orgs') return send({ orgs: [{ id: 'smoke-team', name: 'Smoke team', membership: { status: 'active' } }] });
  if (url.pathname === '/memory/default-org') return send({ orgId: 'smoke-team', source: 'only' });
  if (url.pathname === '/memory/sessions/smoke-session' && method === 'PUT') return send({ id: 'smoke-session' });
  if (url.pathname === '/memory/sessions/smoke-session/turns' && method === 'POST') {
    captured.push(...body.turns);
    return send({ lastSeq: captured.at(-1).seq });
  }
  if (url.pathname === '/memory/orgs/smoke-team/papercuts') {
    if (method === 'POST') {
      published = body;
      return send({ entry }, 201);
    }
    return send({ entries: published && url.searchParams.get('q') === 'ERR_SMOKE' ? [entry] : [], nextCursor: null });
  }
  return send(null, 404);
});

try {
  await Promise.all([mkdir(home), mkdir(project)]);
  const archive = process.argv[2] ? resolve(process.argv[2]) : join(temp, JSON.parse(execFileSync(
    'npm', ['pack', '--json', '--pack-destination', temp], { encoding: 'utf8' }
  ))[0].filename);
  execFileSync('npm', ['install', '--offline', '--no-audit', '--no-fund', '--ignore-scripts', '--prefix', installed, archive], { stdio: 'inherit' });
  const root = join(installed, 'node_modules', 'spookling');
  const load = (name) => import(pathToFileURL(join(root, name)).href);
  const [{ main }, { login }, { pushFromLog }] = await Promise.all([
    load('cli/src/main.js'), load('cli/src/account.js'), load('cli/src/push.js'),
  ]);
  execFileSync(process.execPath, [join(root, 'bin', 'spookling-memory.js'), '--help']);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  base = `http://127.0.0.1:${server.address().port}`;
  const common = { home, cwd: project, env: {}, write: () => {} };
  assert.equal(await login({ ...common, apiUrl: base, tty: false, sleep: async () => {} }), 0);
  const upload = await pushFromLog({
    ...common, project: 'smoke', projectDir: project,
    logText: JSON.stringify({ agent: 'codex', session_id: 'smoke-session', turn_index: 0, role: 'user', ts: entry.createdAt, text: 'A captured release turn' }),
  });
  assert.equal(upload.pushed, 1);
  assert.equal(captured[0]?.text, 'A captured release turn');
  const run = (args, deps) => main(['node', 'spookling', 'papercuts', ...args], { ...common, ...deps });
  const add = ['add', entry.symptom, entry.fix];
  await run(add, { tty: true, confirm: async () => false });
  assert.equal(published, undefined);
  assert.equal(await run(add, { tty: true, confirm: async () => true }), 0);
  assert.match(published.idempotencyKey, /^[a-f0-9]{64}$/);
  let output = '';
  assert.equal(await run(['search', entry.symptom], { write: (line) => { output += line; } }), 0);
  assert(output.includes(entry.fix));
  console.log('Installed package smoke passed: device login, capture upload and reviewed Papercuts.');
} finally {
  if (server.listening) await new Promise((done) => server.close(done));
  await rm(temp, { recursive: true, force: true });
}
