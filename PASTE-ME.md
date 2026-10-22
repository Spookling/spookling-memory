# Paste this into Claude Code

What you will see: a dark purple screen fades in and the Spookling ghost, witch hat and all, flies across your terminal twice with a lilac trail behind it. The SPOOKLING wordmark builds letter by letter, the tagline "An AI assistant that works for you while you sleep" types out, and a mint line follows: "Memory that stays right for years." Any key skips it. Then seven numbered steps with a progress bar: checks, harness, the tagger (Jev on an OpenRouter key, Haiku on an Anthropic key, or your Codex subscription through a logged-in `codex` CLI; a key is typed hidden and saved to `.memory/keys.env`), the local memory server, the Claude Code hooks and MCP entry, a one-line test, and a done screen where the ghost flies once more. Every file is shown before it is written, and `n` skips it. If your assistant's shell has no live terminal, you get a still picture and it should ask you to run the command yourself. Without a terminal the wizard never hangs: when nobody answers for 20 seconds it takes the defaults, and the log still shows every write first.

The same prompt works in Claude Code on macOS, Linux, WSL and native Windows (PowerShell). Add `--scope user` to the init command to wire every project instead of this one.

**Before the npm package exists**, clone `spookling-memory` and `memory-bench` side by side (see `cli/README.md`) and put `node <clone>/cli/bin/spookling-memory.js` where the prompt says `npx @spookling/memory`:

- macOS, Linux, WSL: `node ~/src/spookling-memory/cli/bin/spookling-memory.js init`
- Windows PowerShell: `node $HOME\src\spookling-memory\cli\bin\spookling-memory.js init`

## Claude Code

```text
Install Spookling Memory in this project.
1. Run `npx @spookling/memory init` in the foreground so its intro and steps show in my terminal. If your shell cannot take my keyboard input, stop and ask me to run that command myself, then continue from step 3.
2. Do not answer its prompts unless I ask you to. Never type, print or repeat my API keys.
3. When it finishes, run `npx @spookling/memory status` and confirm the hooks line lists PreCompact, UserPromptSubmit and Stop (in `.claude/settings.json`, or `~/.claude/settings.json` for `--scope user`) and the mcp line lists `spookling-memory`.
4. If both are there, reply with exactly "Spookling Memory is in." and three lines: what went into the settings file, the MCP config and `.memory/`. If anything is missing, say what instead. Then tell me I can check them with `/hooks` and `/mcp`, and that Claude Code asks me once to approve a project `.mcp.json` server.
```

## Codex

```text
Install Spookling Memory here. Run `npx @spookling/memory init` in the foreground so its intro and prompts show in my terminal; if your sandbox blocks the network or my input, ask me to run it myself. Do not answer its prompts unless I ask, and never repeat my keys. Choose Codex when it asks: Codex wiring is not built yet, so it starts the local server and writes `.memory/CODEX-TODO.md`. Then run `npx @spookling/memory status`. If the server is up, reply "Spookling Memory is in." and three lines on what was written.
```
