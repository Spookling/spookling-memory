import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { writeFileSecure } from './credentials.js';
import { accountScope, claimSession } from './upload-state.js';

const MAX_MUTED = 50;

export function recallScope(home, env, projectKey, cwd) {
  const project = projectKey.startsWith('git:') ? projectKey : resolve(cwd || env.CLAUDE_PROJECT_DIR || process.cwd());
  const key = createHash('sha256').update(JSON.stringify([accountScope(home, env), project])).digest('hex');
  return { home, key };
}

function file(scope, name) {
  return join(scope.home, '.spookling', 'recall', `${scope.key}-${name}.json`);
}

function read(path) {
  try { return JSON.parse(readFileSync(path, 'utf8')); } catch { return null; }
}

const validPart = (value) => typeof value === 'string' && value.length > 0 && value.length <= 420;
const validSource = (item) => validPart(item?.spaceId) && validPart(item?.sid);
export const sameSource = (a, b) => a.spaceId === b.spaceId && a.sid === b.sid;

export function readRecall(scope) {
  const saved = read(file(scope, 'muted'));
  const muted = Array.isArray(saved) ? saved.filter(validSource).slice(0, MAX_MUTED) : [];
  return { last: read(file(scope, 'last')), muted };
}

/** Save references only. Titles and excerpts are fetched from the API when the user inspects them. */
export function recordRecall(scope, answer, at) {
  const items = answer.items.filter((item) => validPart(item?.id) && validPart(item?.spaceId)
    && (validSource(item) || (item.sid === '' && item.id.startsWith('rule:')))
    && Number.isSafeInteger(item.seq) && item.seq >= 0).slice(0, 20)
    .map(({ id, spaceId, sid, seq }) => ({ id, spaceId, sid, seq }));
  try {
    writeFileSecure(file(scope, 'last'), JSON.stringify({ at, tier: answer.tier, status: answer.status, items }));
  } catch { /* diagnostics must not interrupt recall */ }
}

export async function changeMuted(scope, source, mute) {
  if (!validSource(source)) throw new Error('Only transcript sources can be muted.');
  const unlock = await claimSession(scope.home, `recall-mutes:${scope.key}`);
  if (!unlock) throw new Error('Another recall preference is being saved. Try again.');
  try {
    const next = readRecall(scope).muted.filter((row) => !sameSource(row, source));
    if (mute) {
      if (next.length >= MAX_MUTED) throw new Error('Unmute a source before adding another.');
      next.push({ spaceId: source.spaceId, sid: source.sid });
    }
    writeFileSecure(file(scope, 'muted'), JSON.stringify(next));
  } finally {
    unlock();
  }
}
