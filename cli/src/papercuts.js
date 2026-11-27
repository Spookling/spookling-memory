import { homedir } from 'node:os';
import { createHash } from 'node:crypto';
import { CloudError, clean, createCloud } from './cloud.js';
import { maskSecrets } from './mask.js';
import { client, explain, pickOrg } from './share.js';
import { orgRecords, plain, UNTRUSTED_NOTE } from './team.js';

export const PAPERCUTS_USAGE = `spookling papercuts [--org <id or name>] [--cursor <cursor>]
spookling papercuts search "<text>" [--org <id or name>] [--cursor <cursor>]
spookling papercuts add "<symptom>" "<verified fix>" [--project <label>] [--source <evidence>] [--org <id or name>]`;

const QUERY_MAX = 200;
const LIMITS = { symptom: 500, fix: 1500, project: 100, source: 400 };
const ERROR_CODE = /\b(?:ERR_[A-Z][A-Z0-9_]{2,48}|TS\d{4}|E(?:ACCES|PERM|ADDRINUSE|CONNREFUSED|TIMEDOUT|NOENT|PIPE|AI_AGAIN))\b/;

function errorText(error, env, home) {
  if (error instanceof CloudError && error.status === 404) {
    return 'Papercuts is unavailable on this Spookling API.';
  }
  return explain(error, env, home);
}

async function target({ org, env, home, cloud: injected }) {
  const { cloud, error } = client(env, home, injected);
  if (error) return { error };
  try {
    const orgs = orgRecords(await cloud.listOrgs());
    if (!orgs.length) return { error: 'Join or create an org in Spookling to use team papercuts.' };
    const wanted = org.trim();
    const byId = orgs.find((candidate) => candidate.id === wanted);
    if (!byId && wanted && orgs.filter((candidate) => candidate.name.toLowerCase() === wanted.toLowerCase()).length > 1) {
      return { error: 'Several orgs have that name. Run `spookling orgs` and pass the org id with --org.' };
    }
    // A project or saved default never chooses the team whose log this command opens.
    const picked = byId ? { org: byId } : pickOrg(orgs, wanted);
    if (picked.error) return { error: picked.error };
    return { cloud, orgId: picked.org.id, orgName: clean(picked.org.name).replace(/\s+/g, ' ').trim() };
  } catch (e) {
    return { error: errorText(e, env, home) };
  }
}

async function readPage({ org = '', query = '', cursor, limit, env = process.env, home = homedir(), cloud } = {}) {
  const text = query.trim();
  if (text.length > QUERY_MAX) return { error: `Search text must be at most ${QUERY_MAX} characters.` };
  const team = await target({ org, env, home, cloud });
  if (team.error) return team;
  try {
    const page = await team.cloud.listPapercuts({ orgId: team.orgId, query: text, cursor, limit });
    if (!Array.isArray(page?.entries)) return { error: 'Spookling returned an invalid Papercuts page.' };
    return { orgId: team.orgId, orgName: team.orgName, entries: page.entries, nextCursor: page.nextCursor };
  } catch (e) {
    return { error: errorText(e, env, home) };
  }
}

export async function papercutsForTool({ org, query, cursor } = {}) {
  const page = await readPage({ org, query, cursor, limit: 10 });
  if (page.error) return page;
  return {
    ...page,
    orgName: plain(page.orgName, 100),
    note: UNTRUSTED_NOTE,
    untrusted: true,
    entries: page.entries.map(({ id, createdAt, symptom, fix, project, verifiedAt, source, supersededBy }) => ({
      id, createdAt, symptom: plain(symptom, LIMITS.symptom), fix: plain(fix, LIMITS.fix),
      project: project === null ? null : plain(project, LIMITS.project),
      ...(verifiedAt && { verifiedAt }),
      ...(source && { source: plain(source, LIMITS.source) }),
      ...(supersededBy && { superseded: true }),
    })),
  };
}

/** Only a recognized error identifier leaves this machine; tool input and output stay local. */
export async function papercutsFromFailure({ error, org = '', home = homedir(), env = process.env, cloud } = {}) {
  const code = typeof error === 'string' && ERROR_CODE.exec(error.slice(0, 4000))?.[0];
  if (!code) return '';
  try {
    const page = await readPage({ org, query: code, limit: 5, home, env,
      cloud: cloud ?? createCloud({ home, env, timeoutMs: 600 }) });
    const hits = page.entries?.filter((entry) => !entry.supersededBy).slice(0, 2) ?? [];
    if (!hits.length) return '';
    const fixes = hits.map((entry) => `${plain(entry.symptom, 240)}\nFix: ${plain(entry.fix, 500)}`);
    return `${UNTRUSTED_NOTE}\n<papercuts team="${plain(page.orgName, 100)}" untrusted="true">\n${fixes.join('\n\n')}\n</papercuts>`;
  } catch { return ''; }
}

export async function papercutsCommand({
  sub = 'list', words = [], org = '', project, source, cursor, env = process.env, home = homedir(), cloud,
  tty = false, ask, write = (s) => process.stdout.write(s),
} = {}) {
  const say = (s) => write(`${clean(s)}\n`);
  if (sub === 'help') { say(PAPERCUTS_USAGE); return 0; }
  if (!['list', 'search', 'add'].includes(sub)
      || words.length !== ({ list: 0, search: 1, add: 2 })[sub]
      || (sub !== 'add' && (project !== undefined || source !== undefined)) || (sub === 'add' && cursor !== undefined)
      || words.some((word) => !word.trim())) {
    say(PAPERCUTS_USAGE);
    return 2;
  }
  if (sub !== 'add') {
    const page = await readPage({ org, query: words[0], cursor, env, home, cloud });
    if (page.error) { say(page.error); return 1; }
    say(`Papercuts · ${page.orgName}`);
    for (const entry of page.entries) {
      say(`\n${entry.createdAt.slice(0, 10)}${entry.project ? ` · ${entry.project}` : ''}${entry.supersededBy ? ' · Superseded' : ''}\n${entry.symptom}\nFix · ${entry.fix}${entry.source ? `\nSource · ${entry.source}` : ''}`);
    }
    if (!page.entries.length) say(page.nextCursor ? 'No matches on this page.' : sub === 'search' ? 'No matching entries.' : 'No papercuts yet.');
    if (page.nextCursor) say(`\nContinue with the same command and --cursor '${page.nextCursor}'.`);
    return 0;
  }

  const entry = { symptom: words[0].trim(), fix: words[1].trim(), ...(project !== undefined && { project: project.trim() }),
    ...(source !== undefined && { source: source.trim() }) };
  for (const [field, value] of Object.entries(entry)) {
    if (!value || value.length > LIMITS[field]) {
      say(`${field} must be 1 to ${LIMITS[field]} characters.`);
      return 2;
    }
    entry[field] = maskSecrets(value);
  }
  if (!tty) { say('Run `spookling papercuts add` in a terminal to review the team and entry before posting.'); return 1; }
  const team = await target({ org, env, home, cloud });
  if (team.error) { say(team.error); return 1; }
  say(`Add to ${team.orgName}'s Papercuts log${entry.project ? ` · ${entry.project}` : ''}\n${entry.symptom}\nFix · ${entry.fix}${entry.source ? `\nSource · ${entry.source}` : ''}`);
  const confirm = ask || (async (question) => (await import('./term.js')).confirm(question, false));
  if (!(await confirm('Add this papercut?'))) { say('Papercut discarded.'); return 1; }
  try {
    const idempotencyKey = createHash('sha256').update(JSON.stringify(entry)).digest('hex');
    await team.cloud.appendPapercut({ orgId: team.orgId, entry: { ...entry, idempotencyKey } });
    say(`Added to ${team.orgName}'s Papercuts log.`);
    return 0;
  } catch (e) {
    say(e instanceof CloudError && e.status && e.status < 500
      ? errorText(e, env, home)
      : 'Spookling could not confirm the write. Check the team log before adding it again.');
    return 1;
  }
}
