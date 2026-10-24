# Claude Code facts the plugin depends on

Checked 21 Oct 2026 against the official docs. `docs.claude.com/en/docs/claude-code/<page>` now redirects to `code.claude.com/docs/en/<page>`; the raw pages were read in full as markdown (`<page>.md`). One line per fact: the fact, its source, and what it meant for our code. **changed** means a commit on 21 Oct 2026 made the code match. **matched** means it already did. **noted** means no code depends on it, or it is documented in a README. **observed** marks what the docs do not say and was seen on this machine instead (WSL2 Ubuntu 24.04, Claude Code 2.1.283; Windows 11, Claude Code 2.1.305, Node 24.7).

Sources: [H] https://code.claude.com/docs/en/hooks , [G] https://code.claude.com/docs/en/hooks-guide , [S] https://code.claude.com/docs/en/settings , [M] https://code.claude.com/docs/en/mcp , [MQ] https://code.claude.com/docs/en/mcp-quickstart , [SE] https://code.claude.com/docs/en/sessions , [CD] https://code.claude.com/docs/en/claude-directory , [SU] https://code.claude.com/docs/en/setup , [EV] https://code.claude.com/docs/en/env-vars , [CL] https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md , [N] https://nodejs.org/api/process.html#a-note-on-process-io . All checked 2026-10-21.

## Hook input

1. Command hooks get the event JSON on stdin. Common fields: `session_id`, `prompt_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `permission_mode`, `effort`, `hook_event_name` (plus `agent_id`, `agent_type` inside subagents). [H, Common input fields] **matched**: we read `session_id`, `transcript_path`, `prompt`.
2. `UserPromptSubmit` adds `prompt` (pasted text expanded, possibly between `<pasted_content>` lines) and `session_title`. [H, UserPromptSubmit input] **matched**.
3. `UserPromptSubmit` also fires on scheduled tasks, `/loop` turns, background subagents reporting back, and messages from other sessions. [H, UserPromptSubmit] **noted**: memory is recalled for those turns too.
4. `PreCompact` adds `trigger` (`manual` or `auto`) and `custom_instructions` (`null` for auto or when none given). [H, PreCompact input] **matched** (not used).
5. `Stop` adds `stop_hook_active`, `last_assistant_message`, `background_tasks`, `session_crons`. [H, Stop input] **changed**: Stop now uses `last_assistant_message` (server/spookling_memory/hooks.py).
6. The transcript file is written asynchronously and may lag; at Stop it may not hold the final reply, and `last_assistant_message` is the documented way to get it. [H, Common input fields; Stop input] **changed**: Stop adds the final reply when the file has not caught up, so it is logged once instead of lost.
7. Stop does not fire on a user interrupt; an API error fires `StopFailure` instead. [H, Stop] **noted**: an interrupted turn is logged at the next Stop or PreCompact.

## Hook output and exit codes

8. Exit 0 applies the JSON on stdout; exit 2 blocks (UserPromptSubmit drops the prompt, PreCompact blocks compaction, Stop keeps Claude working); any other code is a non-blocking error shown as a `<hook> hook error` notice. [H, Exit code output] **matched**: every script exits 0 on every path.
9. Stdout counts as JSON only when it is one JSON object with nothing but whitespace around it. [H, Exit code 0] **matched**: the scripts print the server's reply alone, or nothing.
10. On `UserPromptSubmit`, plain-text stdout is added as context too, but `hookSpecificOutput.additionalContext` (with `hookEventName`) is the structured form; a top-level `additionalContext` is silently ignored. [H, UserPromptSubmit decision control; G, Structured JSON output] **matched**: we keep the JSON form.
11. `additionalContext` (and plain stdout) is capped at 10,000 characters. Over that, Claude Code saves it to a file and gives Claude the path and a 2,000-character preview, without asking Claude to read it. The cap has no setting. [H, JSON output; Add context for Claude] **changed**: the server cut at 50,000; it now cuts at 10,000, counted in UTF-16 units and never mid-character (hooks.py `cap`).
12. Context is wrapped in a system reminder; `<system-reminder>` tags in it are escaped; text framed as system commands can trip prompt-injection defences, so write facts, not orders. [H, Add context for Claude] **noted**: our recalled block is data inside tags; its wording was not reviewed here.
13. Injected context is saved in the transcript and replayed on `--resume`. [H, Add context for Claude] **matched**, and **observed**: in 2.1.283 it is stored as `type: "attachment"` lines (`attachment.type: "hook_additional_context"`), which `turns()` skips; the `OURS` strip stays as a second guard.
14. `{}` is a valid JSON output with no effect. [H, JSON output] **matched**: the server returns `{}` when it has nothing to add.
15. Stop output with `decision: "block"` or `additionalContext` keeps Claude going; PreCompact discards `systemMessage` and `continue`. [H, Stop decision control; PreCompact] **matched**: stop.js and pre-compact.js print nothing.
16. HTTP hooks (`type: "http"`) POST the same JSON, but a refused connection is a non-blocking error with a notice. [H, HTTP response handling] **noted**: this is why we keep command hooks, which stay silent when the server is down.
17. `onFailure` (v2.1.295+) can make a failed hook block; unset means continue, except on Windows without Git Bash for shell-form PreToolUse and PermissionRequest hooks. [H, Block the action when a hook fails] **matched**: not set, and our events are not those two.

## Timeouts

18. Default timeout 600 s for command hooks, lowered to 30 s on `UserPromptSubmit`; the `timeout` field (seconds) overrides. A timed-out UserPromptSubmit hook's output is discarded and the prompt goes ahead. [H, Common fields; UserPromptSubmit] **matched**: the scripts give up first (15 s for UserPromptSubmit and Stop, 60 s for PreCompact); no `timeout` field is written.

## Settings shape and execution

19. `hooks` maps an event to a list of matcher groups, each with a `hooks` list of handlers `{type, command, ...}`. [H, Configuration] **matched**.
20. A `matcher` of `"*"`, `""` or none matches everything. `UserPromptSubmit` and `Stop` have no matcher support (a matcher is ignored); `PreCompact` matches `manual` or `auto`. [H, Matcher patterns] **matched**: groups are written with no matcher, which covers both PreCompact triggers.
21. Exec form: when `args` is set, `command` is spawned directly with `args` as its argv, no shell on any OS, so paths need no quoting; `node` plus a script path works on every platform because node.exe is a real executable, while `.cmd`/`.bat` shims cannot be exec'd. [H, Exec form and shell form] **changed**: handlers were the shell strings `SPOOKLING_MEMORY_URL=... <hooks>/x.sh` (POSIX only), then `node "<path>"`; now `{"command": "node", "args": ["<hooks>/x.js"]}`, `<hooks>` being `~/.spookling-memory/<version>/hooks` since 24 Oct 2026.
22. Shell form (no `args`) runs `sh -c` on macOS and Linux, Git Bash on Windows, or PowerShell when Git Bash is missing; the `shell` field picks. [H, Exec form and shell form; Command hook fields] **changed** (shell form dropped). Inferred: Git Bash would turn `"\\wsl.localhost\..."` into `\wsl.localhost\...`, and PowerShell expands `$` inside double quotes.
23. `args` arrived in Claude Code 2.1.139. [CL, 2.1.139] **noted**: the minimum version for our settings (here: 2.1.305 on Windows, 2.1.283 in WSL).
24. A handler defined in more than one settings file runs once; hook entries from user, project and local settings merge. [H, Hook handler fields; Hook locations] **noted**: wiring both scopes does not fire twice.
25. Handlers run in the current directory with Claude Code's environment, plus `CLAUDE_PROJECT_DIR` (and the plugin variables); `OTEL_*` exporter variables are removed. [H, Hook handler fields; Common input fields] **matched**: the scripts read `SPOOKLING_MEMORY_URL` from it, default `http://127.0.0.1:7447`.
26. Hooks have no controlling terminal on macOS and Linux; Windows has no `/dev/tty`. [H, Hook input and output] **matched**.
27. In an interactive session, hooks from every settings file wait until the folder is trusted; `-p` treats it as trusted. Edits to settings files are picked up live. [H, Workspace trust; S, When edits take effect] **noted** in cli/README.md.
28. `/hooks` lists configured hooks; `claude --debug` writes `~/.claude/debug/<session-id>.txt`. [H, The /hooks menu; Debug hooks] **noted** in cli/README.md.

## Settings files

29. User `~/.claude/settings.json` (every project), project `.claude/settings.json` (committable), local `.claude/settings.local.json` (kept out of git when Claude Code creates it), managed. Precedence: managed, command line, local, project, user. [S, Settings files; Settings precedence] **changed**: `--scope user` added beside the default `--scope project`.
30. On Windows `~/.claude` is `%USERPROFILE%\.claude`; `CLAUDE_CONFIG_DIR` moves settings, history and plugins. [S, Find or create your settings files; EV] **changed**: user-scope paths honour `CLAUDE_CONFIG_DIR`.

## MCP

31. Scopes: local (the default, `~/.claude.json` under the project's path), project (`.mcp.json` at the project root), user (`~/.claude.json`, top-level `mcpServers`). [M, MCP installation scopes; MQ] **changed**: `--scope user` writes the user scope; project stays `.mcp.json`.
32. `.mcp.json` is `{"mcpServers": {"<name>": {"type": "stdio", "command": ..., "args": [...], "env": {...}}}}`; an entry with no `type` is stdio. [M, Project scope; add-json examples] **matched**.
33. Claude Code writes `~/.claude.json` itself ("you don't need to edit it"); on Windows it is `%USERPROFILE%\.claude.json`, inside `CLAUDE_CONFIG_DIR` when set. [S; MQ] **changed**: the wizard only reads it and changes it through `claude mcp`.
34. `claude mcp add-json <name> '<json>' --scope user` adds a user server; `claude mcp remove <name> --scope user` removes it. [M, Add MCP servers from JSON configuration; Managing your servers] **changed**: the wizard runs these. **observed**: add-json refuses a name that exists (exit 1), so the wizard removes first.
35. **observed**: `claude mcp add-json` and `remove` hung past 30 s with an open stdin in a non-interactive shell, and finished in 6 s with stdin closed. **changed**: spawned with stdin ignored.
36. Project `.mcp.json` servers ask for approval in an interactive session; `-p` loads them without asking. [M, Project scope] **noted** in cli/README.md.
37. `${VAR}` and `${VAR:-default}` expand in `command`, `args`, `env`, `url`, `headers`. [M, Environment variable expansion] **noted** (not used).
38. Claude Code sets `CLAUDE_PROJECT_DIR` in a stdio server's environment. [M, Option 3] **noted**.
39. Stdio shutdown: SIGINT, SIGTERM, SIGKILL within 500 ms on macOS and Linux; on Windows stdin is closed, then the tree is ended after 300 ms (2.1.296+). [M, How Claude Code stops a stdio server] **matched**: the MCP process is a stateless client of `serve`.
40. Server startup timeout is 30 s (`MCP_TIMEOUT`, ms). [MQ; EV] **noted**: a first `uv run` on a fresh install can exceed it; init runs `uv sync` first, in the folder the MCP entry names (`~/.spookling-memory/<version>/server` since 24 Oct 2026, a copy out of the package, never the npx cache).
41. The "Windows requires 'cmd /c' wrapper" MCP warning was a false positive and was removed; a real `.exe` such as `uv.exe` runs directly. [CL] **matched**.

## Transcripts

42. Transcripts are JSONL at `~/.claude/projects/<project>/<session-id>.jsonl`; "the entry format is internal to Claude Code and changes between versions". [SE, Where transcripts are stored] **noted**: `turns()` depends on an internal format, a standing risk.
43. **observed** in 40 local 2.1.283 transcripts: `type` is `user` or `assistant` for messages (`message.content` a string or a list of `text`, `tool_use`, `tool_result`, `thinking`, `image` blocks), plus many metadata types; `isMeta` and `isCompactSummary` appear on user lines; no `isSidechain` in main transcripts (subagents write `<session>/subagents/*.jsonl`). [CD] **matched** (inferred from files, not documented).
44. Claude Code writes JSON with raw U+2028 and U+2029 inside strings (JSON.stringify does not escape them). Inferred. **changed**: transcripts and our own records are split on `\n` only; `str.splitlines()` broke such lines, and on our records refused to open the folder.

## Windows and WSL

45. Native Windows needs no Git for Windows; without it the shell tool is PowerShell. [SU, Set up on Windows] **matched**: exec form needs neither shell.
46. Hook commands with UNC paths, and Windows hooks reaching a server in WSL, are not covered by the docs. [H; SU; desktop-wsl] **observed**: Windows Node 24.7 ran `hooks\*.js` from `\\wsl.localhost\Ubuntu-24.04\...` (a path with a space) in 1 to 3 s, reached a WSL server on 127.0.0.1 under `networkingMode=mirrored`, and the server read the `C:\...` transcript through `/mnt/c`. **changed**: `local_path()` maps `C:\` paths to `/mnt/c` in WSL.
47. **observed**: under WSL mirrored networking, a connect from inside WSL to a 127.0.0.1 port with nothing listening is dropped, not refused: curl hung to its 20 s limit, Python 15 s, Node fetch 10 s. From Windows it is refused in 0.09 s. **changed**: the .js hooks connect within 1 s (node:http), the .sh hooks use `curl --connect-timeout 1`, the MCP client probes for 2 s; before, a stopped server stalled every prompt 10 to 15 s.
48. Node writes to a pipe synchronously on Windows and asynchronously on POSIX, so `process.exit()` right after `write()` can drop output on macOS and Linux. [N] **changed**: the hooks exit in the write callback.
