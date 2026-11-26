#!/usr/bin/env node
if (!process.env.SPOOKLING_TAGGING) {
  const { readFileSync } = require('node:fs');
  const cloud = require('./cloud.cjs');
  async function main() {
    const payload = JSON.parse(readFileSync(0, 'utf8'));
    if (payload.is_interrupt || !(await cloud.on(payload))) return;
    const context = await cloud.papercuts(payload.error);
    if (context) process.stdout.write(JSON.stringify({
      hookSpecificOutput: { hookEventName: 'PostToolUseFailure', additionalContext: context },
    }));
  }
  main().catch(() => {});
}
