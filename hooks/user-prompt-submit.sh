#!/usr/bin/env bash
# Claude Code UserPromptSubmit hook: recall memory for the prompt and hand it to Claude as additional context.
# stdin: the hook's JSON (session_id, transcript_path, prompt, ...). stdout: {"hookSpecificOutput": {"hookEventName":
# "UserPromptSubmit", "additionalContext": "..."}} or {} (the server cuts the block at 10,000 characters, Claude Code's
# cap). Never blocks the prompt: with no server, or on any error, it prints nothing and exits 0.
# macOS and Linux only; hooks/user-prompt-submit.js does the same on any OS with node.
[ -n "${SPOOKLING_TAGGING:-}" ] && exit 0  # a tagging call the server started: nothing to log or recall
curl -sf --noproxy '*' --connect-timeout 1 --max-time 15 -H 'content-type: application/json' --data-binary @- \
  "${SPOOKLING_MEMORY_URL:-http://127.0.0.1:7447}/v1/hook/user-prompt-submit" || true
exit 0
