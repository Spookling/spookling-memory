#!/usr/bin/env bash
# Claude Code UserPromptSubmit hook: recall memory for the prompt and hand it to Claude as additional context.
# stdin: the hook's JSON (session_id, transcript_path, prompt, ...). stdout: {"hookSpecificOutput": {"hookEventName":
# "UserPromptSubmit", "additionalContext": "..."}} or {} (the server cuts the block at 50,000 characters, about
# 12,500 tokens). Never blocks the prompt: with no server, or on any error, it prints nothing and exits 0.
# TODO(confirm with the current Claude Code hooks docs): additionalContext's size cap.
curl -sf --noproxy '*' --max-time 15 -H 'content-type: application/json' --data-binary @- \
  "${SPOOKLING_MEMORY_URL:-http://127.0.0.1:7447}/v1/hook/user-prompt-submit" || true
exit 0
