# Paste this into your assistant

**Read this first.** Claude Code's shell has no live terminal. When Claude Code runs the install, there is no cutscene: the steps print as plain text, and a question nobody answers for 20 seconds takes its default. To see the ghost fly, run `npx spookling demo` in your own terminal. It plays the whole install and writes nothing. Or run `npx spookling init` there yourself and skip the paste.

The defaults are Claude Code, the Jev tagger, and yes to every write. Jev needs `OPENROUTER_API_KEY` in the environment Claude Code was started from, or in `.memory/keys.env`. For Haiku or your Codex subscription, run `init` in your own terminal and pick from the menu. Add `--scope user` to the init line to wire every project instead of this one.

## Claude Code

```text
Install Spookling Memory in this project.
1. Run `npx spookling init` in the foreground and wait for it to finish. Do not answer its prompts yourself; with no answer it takes each default after 20 seconds. Never type, print or repeat my API keys.
2. Run `npx spookling status`.
3. Check that the hooks line lists PreCompact, UserPromptSubmit and Stop, and the mcp line lists spookling-memory.
4. If both are there, reply "Spookling Memory is in." and three lines: what went into the settings file, the MCP config and `.memory/`. If not, quote the status lines marked ! instead.
5. Tell me to approve the spookling-memory server if Claude Code asks; /hooks and /mcp show the wiring.
```

## Codex

Codex can run the same install. It wires Claude Code: the Codex hooks are not built yet, so Codex itself gets no memory yet.

```text
Install Spookling Memory here.
1. Run `npx spookling init` in the foreground and wait for it to finish. If your sandbox blocks the network, ask me to run it myself. Do not answer its prompts yourself; with no answer it takes each default after 20 seconds. Never type, print or repeat my API keys.
2. Run `npx spookling status`.
3. Check that the hooks line lists PreCompact, UserPromptSubmit and Stop, and the mcp line lists spookling-memory.
4. If both are there, reply "Spookling Memory is in." and three lines: what went into the Claude Code settings file, the MCP config and `.memory/`. If not, quote the status lines marked ! instead.
```
