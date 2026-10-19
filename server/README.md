# spookling-memory server

The local memory server: memory-bench's `spookling` adapter behind an HTTP API on 127.0.0.1, a stdio MCP server, and the
server side of three Claude Code hooks.

## Run

```bash
cd server
uv sync
uv run spookling-memory serve --folder .memory          # http://127.0.0.1:7447, 127.0.0.1 only
uv run spookling-memory serve --folder .memory --port 8800 --model anthropic/claude-haiku-4.5
uv run spookling-memory mcp                             # stdio MCP; talks to serve at SPOOKLING_MEMORY_URL
uv run spookling-memory export --folder .memory memory.spookling   # server stopped
uv run spookling-memory import --folder .memory memory.spookling   # into an empty folder
uv run pytest                                           # offline: stub triage, no model calls
```

Tagging and the slow path call the writer model through memory-bench's `bench.models`: `OPENROUTER_API_KEY` (or
`~/spookling-rig/openrouter.env`). Without a key, a call that needs the model answers 503 with the reason and the
server keeps running. One process owns a folder (a lock file); run `serve` once and point the hooks and MCP at it.

## Endpoints

| method and path | body | answer |
|---|---|---|
| `POST /v1/ingest` | `{id, ts?, turns: [{role, content}], agent?, tail?}` | `{logged}`: new turns. `tail: true` means `turns` is the whole conversation so far; only the unlogged end is logged, as `<id>#<n>` stamped now |
| `POST /v1/retrieve` | `{question, ts?}` | `{context, digest, strong}` |
| `POST /v1/compact` | `{session_id}` | `{block, kept}`: the `<invariant>` survivor block, and how many items were kept |
| `POST /v1/reset` | `{note?}` | the reset ruling |
| `POST /v1/reset/undo` | `{}` | the undo ruling |
| `GET /v1/export` | | the archive (`application/gzip`) |
| `POST /v1/import` | the archive, `application/octet-stream` | `{events, judgements}`; the memory must be empty |
| `GET /v1/stats` | | store counts, `reset`, `before_reset` |
| `POST /v1/hook/{user-prompt-submit,pre-compact,stop}` | the Claude Code hook's stdin JSON | what the hook prints |

`ts` is ISO 8601, default now. A refused change (a reset that predates the log, an import into a memory that is not
empty, nothing to undo, a bad timestamp) is 409 with the reason. Every POST must be `application/json` (or
`application/octet-stream` for import) and the Host must be 127.0.0.1 or localhost; with no CORS configured, a web page
cannot drive the server. The MCP tools are `memory_retrieve`, `memory_ingest`, `memory_compact`, `memory_reset`
(`undo: true` reverses), `memory_export`, `memory_import`, `memory_stats`.

## Hooks

`../hooks/*.sh` pipe the hook's JSON to `/v1/hook/<event>` with curl and always exit 0, so a stopped server never
blocks Claude. Stop logs the turns not yet logged (the server reads `transcript_path`), PreCompact does the same and
then keeps the session's rules, decisions, values, dates, paths and names, and UserPromptSubmit adds the recalled
block as `additionalContext` (at most 50,000 characters, about 12,500 tokens). In `.claude/settings.json`:

```json
{"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/path/to/hooks/user-prompt-submit.sh"}]}],
  "PreCompact": [{"matcher": "", "hooks": [{"type": "command", "command": "/path/to/hooks/pre-compact.sh"}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "/path/to/hooks/stop.sh"}]}]
}}
```

To confirm against the current Claude Code docs: the `additionalContext` size cap, and the transcript line shape
`hooks.turns()` reads.

## Reset

`POST /v1/reset` appends one ruling `{step: "reset", at: T, upto: the log's last seq, note}` to the judgement log.
Nothing is deleted.

- **Facts.** Every fact row open at T is closed at T (true until T's day). The adapter's fold is wrapped
  (`reset.fold`) so the cut is applied at the ruling's place in the log. Every full tidy and every restart rebuilds the
  facts with the reset in place. With no active reset, the fold is the adapter's own.
- **Turns and events** recorded before the reset are flagged `before_reset`. A question whose date reaches before T (a
  date, a period, "since X" or an anchor that starts before T's day) is answered from the whole memory. Every other
  question is answered from a view rebuilt from the records after the reset only, so nothing earlier can reach it.
- **Rules and keepers** from before T are not pinned in `<rules>` or `<invariant>`. They stay in the data.
- **Undo.** `POST /v1/reset/undo` appends the reverse ruling. The fold skips that cut and every view is as before.
- **Guards.** A reset cannot predate a turn already logged. After a reset, a session dated before it is refused.

So the facts follow a ruling the fold replays, and retrieval follows a view chosen by the latest active ruling.

## Archive

A `.spookling` file is a tar.gz with three members:

| member | contents |
|---|---|
| `manifest.json` | `{"format": "spookling-memory", "version": 1, "created", "adapter", "counts": {"log", "judgements"}}` |
| `log.jsonl` | the log, one turn per line (`seq, hash, agent, session_id, turn_index, role, ts, text`) |
| `judgements.jsonl` | every adapter decision and every server ruling (`compact`, `reset`, `reset_undo`), one per line |

These are the same two files a memory folder holds, line for line. Import reads the members in memory only, checks the
format, version and line counts, and rebuilds every view with `settle()`. The same question gets the same context
before and after.

## Limits

- The after-reset view is rebuilt from zero after every change, as the adapter's full sleep is.
- How often each fact was used (the INDEX page's ordering) is not persisted. After a restart, the page's order can
  differ until the facts are used again.
- If PreCompact runs during a turn, the assistant's partial reply is logged. The rest of that reply is not logged
  later.
