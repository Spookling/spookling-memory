# @spookling/memory

The install wizard for Spookling Memory. Node 18 or newer, no dependencies.

```bash
npx @spookling/memory init        # the intro, then the step-by-step install
npx @spookling/memory demo        # the intro only
npx @spookling/memory status      # is the server up, which harness is wired (exit 0 when all is well)
npx @spookling/memory uninstall   # remove the hooks and the MCP entry, keep .memory/
```

## Flags

- `--no-anim` skips the animation and prints the wordmark as a still.
- `--frames N` (demo only) prints N frames from across the intro, one after another. Useful for checking the drawing without a live terminal.

The animation also skips itself when `NO_COLOR` is set, when output is not a terminal, or when the terminal is smaller than 40 x 16. Colour is 24-bit when `COLORTERM` is `truecolor` or `24bit`, 256 colours otherwise. `FORCE_COLOR=1` keeps colour in piped output. Any key skips the intro; Ctrl-C restores the terminal and exits.

## What `init` writes

Run it from your project folder. Every write is shown before it happens; answer `n` to skip it.

| Where | What |
| --- | --- |
| `.memory/keys.env` | `ANTHROPIC_API_KEY`, plus `OPENROUTER_API_KEY` if you give one. Mode 600. Taken from your environment, or typed at a hidden prompt; never printed. |
| `.memory/.gitignore` | `keys.env`, `server.pid`, `server.log` |
| `.memory/server.pid`, `.memory/server.log` | the background server: `uv run --project <server> spookling-memory serve --folder .memory --port 7447` |
| `.claude/settings.json` | three hooks merged in: `PreCompact`, `UserPromptSubmit`, `Stop`, each `SPOOKLING_MEMORY_URL=http://127.0.0.1:7447 <hooks>/<event>.sh` |
| `.mcp.json` | `mcpServers.spookling-memory`: stdio, `uv run --project <server> spookling-memory mcp`, with `SPOOKLING_MEMORY_URL` in its `env` |
| `.memory/CODEX-TODO.md` | Codex only: a note that the Codex wiring is not built yet |

It also runs `uv sync` in the server folder. `<server>` and `<hooks>` are the `server/` and `hooks/` folders next to `cli/` in the repo, or inside the package once they are bundled. Every other key in `settings.json` and `.mcp.json` is kept. A file that is not valid JSON is left untouched and reported, never overwritten.

## Undo

`npx @spookling/memory uninstall` removes only the Spookling hooks and the `spookling-memory` MCP entry; everything else in both files stays exactly as it was. It also offers to stop the server named in `.memory/server.pid`. Your memory in `.memory/` stays, keys included. To remove that too, delete the folder yourself.

## Develop

```bash
npm test                                   # node:test, no network
node bin/spookling-memory.js demo --frames 3
node bin/spookling-memory.js demo --no-anim
```
