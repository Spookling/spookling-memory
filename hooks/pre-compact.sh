#!/usr/bin/env bash
# Claude Code PreCompact hook: POST stdin to the local server, print nothing, exit 0.
[ -n "${SPOOKLING_TAGGING:-}" ] && exit 0  # a tagging call the server started
curl -sf --noproxy '*' --connect-timeout 1 --max-time 60 -H 'content-type: application/json' --data-binary @- \
  "${SPOOKLING_MEMORY_URL:-http://127.0.0.1:7447}/v1/hook/pre-compact" >/dev/null || true
exit 0
