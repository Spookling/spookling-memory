#!/usr/bin/env bash
# Claude Code UserPromptSubmit hook (macOS and Linux; the .js is for any OS): print the server's reply, exit 0.
[ -n "${SPOOKLING_TAGGING:-}" ] && exit 0  # a tagging call the server started
curl -sf --noproxy '*' --connect-timeout 1 --max-time 15 -H 'content-type: application/json' --data-binary @- \
  "${SPOOKLING_MEMORY_URL:-http://127.0.0.1:7447}/v1/hook/user-prompt-submit" || true
exit 0
