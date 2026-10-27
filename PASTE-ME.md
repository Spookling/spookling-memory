# Paste this into your assistant

**Read this first.** Claude Code's shell has no live terminal. When Claude Code runs the install, there is no cutscene: the steps print as plain text, and a question nobody answers for 20 seconds takes its default. To see the ghost fly, run `npx spookling demo` in your own terminal. It plays the whole install and writes nothing. Or run `npx spookling init` there yourself and skip the paste.

`init --yes` asks nothing. It wires whichever of Claude Code and Codex it finds on the machine, puts the ghost in, and picks the tagger you already have: your Codex plan if `codex` is logged in with ChatGPT, else an `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` in the environment Claude Code was started from (or in `.memory/keys.env`), else no tagger, which works at once and costs nothing. Add `--scope user` to the init line to wire every project instead of this one.

## Claude Code

```text
Install Spookling Memory in this project.
1. Run `npx spookling init --yes` in the foreground and wait for it to finish. Never type, print or repeat my API keys.
2. Run `npx spookling status`.
3. Check that the hooks line lists SessionStart, PreCompact, UserPromptSubmit and Stop, and the mcp line lists spookling-memory.
4. If both are there, reply "Spookling Memory is in." and three lines: what went into the settings file, the MCP config and `.memory/`. If not, quote the status lines marked ! instead.
5. Tell me to approve the spookling-memory server if Claude Code asks; /hooks and /mcp show the wiring.
```

## Codex

`npx spookling init --yes` wires Codex whenever `codex` (or `$CODEX_HOME`) is on the machine. Add `--scope user` to wire user hooks and keep memory in your home folder.

```text
Install Spookling Memory here for Codex.
1. Run `npx spookling init --yes` in the foreground and wait for it to finish; never print or repeat my keys.
2. When it finishes, run `npx spookling status` and report the Codex hooks and server lines.
3. Tell me to open /hooks and review/trust the installed commands. Do not claim they are live until trusted and actually invoked.
4. The ghost reports ready after a successful server health check, active after successful recall, or unavailable if either fails. The hooks use the same local server and .memory/ data as Claude Code.
```
