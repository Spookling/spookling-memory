import { homedir } from 'node:os';
import { clean, createCloud } from './cloud.js';
import { identify } from './cloud-retrieve.js';
import { readCredentials } from './credentials.js';
import { changeMuted, readRecall, recallScope, sameSource } from './recall-state.js';
import { orgRecords, plain } from './team.js';

const USAGE = 'spookling recall [why | mute <number> | muted | unmute <number>]';

function location(item) {
  const kind = item.spaceId.split('/')[0];
  if (kind === 'org' || kind === 'rules') return item.spaceId.split('/')[1];
  return null;
}

async function explainSources(items, cloud) {
  const needsTeams = items.some(location);
  const orgs = needsTeams ? orgRecords(await cloud.listOrgs().catch(() => null)) : [];
  const names = new Map(orgs.map(({ id, name }) => [id, name]));
  return Promise.all(items.map(async (item, index) => {
    const number = index + 1;
    const org = location(item);
    if (org && !names.has(org)) return `${number}. Team source unavailable`;
    const space = org ? plain(names.get(org), 80) : 'Private memory';
    if (!item.sid) return `${number}. ${space} · Project rule`;
    try {
      const data = await cloud.getSession({ sid: item.sid, ...(item.seq > 0 && { after: item.seq - 1 }), limit: 1 });
      const turn = data.turns?.find((row) => row.seq === item.seq);
      const source = plain(data.session?.title || item.sid, 110);
      const details = [space, source, turn?.ts?.slice(0, 10)].filter(Boolean).join(' · ');
      return `${number}. ${details}\n   Source: ${item.sid}, turn ${item.seq}${turn?.text ? `\n   ${plain(turn.text, 260)}` : ''}`;
    } catch { return `${number}. ${space} · Source unavailable`; }
  }));
}

export async function recallCommand({
  words = [], cwd, env = process.env, home = homedir(), cloud,
  write = (s) => process.stdout.write(s),
} = {}) {
  const say = (line) => write(`${clean(line)}\n`);
  const [sub = 'why', selection] = words;
  const mutating = sub === 'mute' || sub === 'unmute';
  if (!['why', 'mute', 'muted', 'unmute'].includes(sub) || words.length > (mutating ? 2 : 1) || (mutating && !/^[1-9]\d*$/.test(selection ?? ''))) {
    say(USAGE);
    return sub === 'help' ? 0 : 2;
  }
  if (!readCredentials({ home, env }).token) {
    say('Run spookling login to inspect cloud recall.');
    return 1;
  }

  const { projectKey } = await identify(cwd, { home, env });
  const scope = recallScope(home, env, projectKey, cwd);
  const { last, muted } = readRecall(scope);
  if (sub === 'muted') {
    say(muted.length ? muted.map(({ spaceId, sid }, i) => `${i + 1}. ${sid} · ${spaceId}`).join('\n') : 'No muted sources in this project.');
    return 0;
  }
  if (mutating) {
    const source = (sub === 'mute' ? last?.items : muted)?.[Number(selection) - 1];
    if (!source) { say('No source at that number.'); return 1; }
    try {
      await changeMuted(scope, source, sub === 'mute');
      say(sub === 'mute' ? 'Muted that transcript source for this project.' : 'Unmuted that transcript source.');
      return 0;
    } catch (error) { say(error.message); return 1; }
  }

  if (!last?.items?.length) {
    say('The latest cloud recall has no cited sources in this project.');
    return 0;
  }
  const client = cloud ?? createCloud({ env, home, timeoutMs: 4000 });
  const reason = last.tier && last.tier !== 'none' ? ` · match: ${last.tier}` : '';
  say(`Cloud recall · ${new Date(last.at).toISOString()}${reason}`);
  const lines = await explainSources(last.items, client);
  lines.forEach((line, index) => say(muted.some((source) => sameSource(source, last.items[index])) ? `${line}\n   Muted` : line));
  if (last.items.some((item) => item.sid)) say('Use spookling recall mute <number> to hide that source in this project.');
  return 0;
}
