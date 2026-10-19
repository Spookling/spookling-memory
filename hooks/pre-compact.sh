#!/usr/bin/env bash
# Claude Code PreCompact hook: log the session's newest turns, then keep its rules, decisions, values, dates, paths and
# names verbatim before the summary drops them (they lead every later context in <invariant>).
# stdin: the hook's JSON (session_id, transcript_path, trigger, ...). Waits for the server; exits 0 whatever happens.
curl -sf --noproxy '*' --max-time 60 -H 'content-type: application/json' --data-binary @- \
  "${SPOOKLING_MEMORY_URL:-http://127.0.0.1:7447}/v1/hook/pre-compact" >/dev/null || true
exit 0
