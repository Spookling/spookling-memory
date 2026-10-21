#!/usr/bin/env bash
# Claude Code Stop hook: log the turns of this session not yet logged. The server reads transcript_path itself, keeps
# only what is new, and replies at once (the tagging runs after the reply), so Claude is not held.
# stdin: the hook's JSON (session_id, transcript_path, stop_hook_active, ...). Exits 0 whatever happens.
curl -sf --noproxy '*' --connect-timeout 1 --max-time 15 -H 'content-type: application/json' --data-binary @- \
  "${SPOOKLING_MEMORY_URL:-http://127.0.0.1:7447}/v1/hook/stop" >/dev/null || true
exit 0
