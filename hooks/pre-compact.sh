#!/usr/bin/env bash
# Claude Code PreCompact hook: log the session's newest turns, then keep its rules, decisions, values, dates, paths and
# names verbatim before the summary drops them (they lead every later context in <invariant>).
# stdin: the hook's JSON (session_id, transcript_path, trigger, ...). Waits for the server; exits 0 whatever happens.
[ -n "${SPOOKLING_TAGGING:-}" ] && exit 0  # a tagging call the server started: nothing to log or recall
curl -sf --noproxy '*' --connect-timeout 1 --max-time 60 -H 'content-type: application/json' --data-binary @- \
  "${SPOOKLING_MEMORY_URL:-http://127.0.0.1:7447}/v1/hook/pre-compact" >/dev/null || true
exit 0
