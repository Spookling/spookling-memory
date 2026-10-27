// .memory/pulse.json: what the hooks last did, for the status line. The --memory folder; else the project's .memory,
// else ~/.memory, as status finds it.
const { appendFileSync, existsSync, readFileSync, renameSync, writeFileSync } = require('node:fs');
const { homedir } = require('node:os');
const { dirname, join } = require('node:path');

// --memory <folder>, as init wires every hook and the status line, wins
const given = () => { const i = process.argv.indexOf('--memory'); return i < 0 ? undefined : process.argv[i + 1]; };
const where = (project) => {
  const dir = given() || [project, homedir()].filter(Boolean).map((d) => join(d, '.memory')).find((d) => existsSync(d));
  return dir && join(dir, 'pulse.json');
};

// p: the hook's stdin JSON; never throws, never creates .memory
exports.write = (p, fields) => {
  try {
    const f = where(process.env.CLAUDE_PROJECT_DIR || p.cwd);
    if (!f) return;
    writeFileSync(`${f}.${process.pid}`, JSON.stringify({ at: Date.now(), session: p.session_id, ...fields }));
    renameSync(`${f}.${process.pid}`, f); // whole or not at all for the status line's read
  } catch {}
};

exports.where = where;

// .memory/usage.jsonl: a line per prompt the recall hook ran for, appended, for `npx spookling usage`: when, which
// session and harness, the prompt's start, and what reached the model (the server's own counts and size, or up: false)
exports.log = (p, fields, harness = 'claude') => {
  try {
    const f = where(process.env.CLAUDE_PROJECT_DIR || p.cwd);
    if (!f) return;
    const prompt = String(p.prompt || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    appendFileSync(join(dirname(f), 'usage.jsonl'), `${JSON.stringify({ at: Date.now(), session: p.session_id, harness, prompt, ...fields })}\n`);
  } catch {}
};

exports.read = (project) => { try { return JSON.parse(readFileSync(where(project), 'utf8')); } catch { return null; } };

// the server's x-spookling-pulse header; older servers send none
exports.counts = (header) => {
  try { const n = JSON.parse(header); return n && typeof n === 'object' && !Array.isArray(n) ? n : {}; } catch { return {}; }
};

const n = (k, word) => `${k} ${word}${k === 1 ? '' : 's'}`;
// "3 facts, 1 rule and 2 past chats": what reached Claude, biggest news first
exports.said = (c) => {
  const parts = [c.facts && n(c.facts, 'fact'), c.events && n(c.events, 'event'), c.rules && n(c.rules, 'rule'),
    c.chats && n(c.chats, 'past chat')].filter(Boolean);
  return parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}` : parts[0] || '';
};
exports.n = n;
