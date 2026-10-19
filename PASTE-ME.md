# Paste this into Claude Code

What you will see: a dark purple screen fades in and the Spookling ghost, witch hat and all, flies across your terminal twice with a lilac trail behind it. The SPOOKLING wordmark builds letter by letter, the tagline "An AI assistant that works for you while you sleep" types out, and a mint line follows: "Memory that stays right for years." Any key skips it. Then seven numbered steps with a progress bar: checks, harness, keys (typed hidden, saved to `.memory/keys.env`), the local memory server, the Claude Code hooks and MCP entry, a one-line test, and a done screen where the ghost flies once more. Every file is shown before it is written, and `n` skips it. If your assistant's shell has no live terminal, you get a still picture and it should ask you to run the command yourself.

## Claude Code

```text
Install Spookling Memory in this project.
1. Run `npx @spookling/memory init` in the foreground so its intro and steps show in my terminal. If your shell cannot take my keyboard input, stop and ask me to run that command myself, then continue from step 3.
2. Do not answer its prompts unless I ask you to. Never type, print or repeat my API keys.
3. When it finishes, run `npx @spookling/memory status` and confirm `.claude/settings.json` has the PreCompact, UserPromptSubmit and Stop hooks and `.mcp.json` has the `spookling-memory` entry.
4. If both are there, reply with exactly "Spookling Memory is in." and three lines: what went into `.claude/settings.json`, `.mcp.json` and `.memory/`. If anything is missing, say what instead.
```

## Codex

```text
Install Spookling Memory here. Run `npx @spookling/memory init` in the foreground so its intro and prompts show in my terminal; if your sandbox blocks the network or my input, ask me to run it myself. Do not answer its prompts unless I ask, and never repeat my keys. Choose Codex when it asks: Codex wiring is not built yet, so it starts the local server and writes `.memory/CODEX-TODO.md`. Then run `npx @spookling/memory status`. If the server is up, reply "Spookling Memory is in." and three lines on what was written.
```
