# @spookling/memory

The install wizard for Spookling Memory. Node 18 or newer, no dependencies. Works on macOS, Linux (WSL included) and native Windows; what was run on which is listed under [Tested](#tested).

```bash
npx @spookling/memory init                 # the intro, then the step-by-step install, for this project
npx @spookling/memory init --scope user    # the same, wired for every project you open in Claude Code
npx @spookling/memory demo                 # the whole install as you would see it, answered for you; writes nothing
npx @spookling/memory status               # is the server up, what is wired (project and user files), which tagger; exit 0 when all is well
npx @spookling/memory uninstall            # remove the hooks and the MCP entry (add --scope user for the user files), keep .memory/
```

Until the npm package exists, run the same commands from a clone: `node <clone>/cli/bin/spookling-memory.js init` (below).

## Install from a clone

The server imports memory-bench through the path `../../memory-bench`, relative to `server/`. Clone the two repos side by side, in any folder:

```text
src/
  spookling-memory/
  memory-bench/
```

Neither repo has a public remote yet, so the clone URLs below are placeholders (to confirm with Mohammed). Run `init` from the project folder you want the memory in; `.memory/` is created there.

### macOS (zsh)

```bash
brew install node                                   # Node 18+, or the installer from nodejs.org
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv, which runs the Python server
mkdir -p ~/src && cd ~/src
git clone <spookling-memory repo URL> spookling-memory
git clone <memory-bench repo URL> memory-bench
cd ~/code/your-project
node ~/src/spookling-memory/cli/bin/spookling-memory.js init                 # this project
node ~/src/spookling-memory/cli/bin/spookling-memory.js init --scope user    # or every project
```

### Linux and WSL (bash)

The same commands as macOS, with Node from your package manager or nvm. In WSL, run everything in the WSL shell, with Claude Code installed in WSL, and keep the clones and the project on the Linux side (`~/...`), not under `/mnt/c`.

### Windows, native Claude Code (PowerShell)

```powershell
winget install OpenJS.NodeJS.LTS                                       # Node 18+, or the installer from nodejs.org
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# open a new PowerShell window so node and uv are on PATH
mkdir $HOME\src; cd $HOME\src
git clone <spookling-memory repo URL> spookling-memory
git clone <memory-bench repo URL> memory-bench
cd $HOME\code\your-project
node $HOME\src\spookling-memory\cli\bin\spookling-memory.js init                # this project
node $HOME\src\spookling-memory\cli\bin\spookling-memory.js init --scope user   # or every project
```

Git for Windows is not needed: the hooks run `node` directly, with no shell. The first `uv sync` downloads Python 3.12 if you do not have it (about two minutes here).

### One server in WSL, Claude Code on Windows too

With WSL's mirrored networking (`networkingMode=mirrored` in `%USERPROFILE%\.wslconfig`, the setup tested here), Windows and WSL share 127.0.0.1, so one server in WSL can serve both sides and both share one memory.

1. In WSL, from your project: `node ~/src/spookling-memory/cli/bin/spookling-memory.js init` (or `--scope user`). This installs and starts the server.
2. In PowerShell, point at the same clone through the WSL share:
   `node \\wsl.localhost\Ubuntu-24.04\home\<you>\src\spookling-memory\cli\bin\spookling-memory.js init --scope user`
   The wizard sees the clone is inside WSL. It skips `uv sync` (a Windows `.venv` would replace the one WSL runs), wires the hooks to the scripts on the share, and writes the MCP entry as `wsl.exe -d <distro> -- bash -lc "exec uv run --project '<path>' spookling-memory mcp"`, so the MCP tools run inside WSL too.

Windows sessions then log into the WSL project's `.memory/`; the server reads their `C:\...` transcripts through `/mnt/c`. Starting a hook from the share took 1 to 3 seconds per event here. NAT networking (`localhostForwarding`) was not tested.

## Flags

- `--scope project` (default) or `--scope user`, for `init` and `uninstall`. See the table below.
- `--no-anim` skips the animation: the wordmark prints as a still and the ghost's lines print as plain text.
- `--fast` (demo only) drops the pauses. Without it the demo takes about a minute; any key cuts the current pause short.
- `--frames N` (demo only) prints N frames from across the intro, one after another, and stops. Useful for checking the drawing without a live terminal.

The intro animates at 40 x 16 or larger. During `init` the ghost then stays at the top of the screen: it drifts and bobs on a purple band and says one line per step in a speech bubble, while the steps scroll below it. That stage needs 80 x 24 and takes the top 9 rows; from 30 rows it shows a larger ghost and takes 11. It redraws at 12 fps while a step runs and holds still while a prompt waits, so typing is never disturbed. It uses the alternate screen; when it closes, the whole log of the steps is printed again in the normal screen, so it stays in your scrollback (also on Ctrl-C). With `NO_COLOR`, output that is not a terminal, `--no-anim`, a smaller terminal, or a Windows console older than Windows 10 (no escape sequences), there is no animation and the ghost's lines print as `spookling: ...` among the steps. Colour is 24-bit when `COLORTERM` is `truecolor` or `24bit`, or in Windows Terminal (`WT_SESSION`), 256 colours otherwise. `FORCE_COLOR=1` keeps colour in piped output. Any key skips the intro; Ctrl-C restores the terminal and exits.

At a terminal, the harness and the tagger are picked from menus (arrows, then Enter) and a key shows as bullets while you type or paste it; the key itself is never printed.

## The tagger

Step 3 asks how the tagger runs: the small model that reads each chat once, after it ends, and tags the facts in it. Retrieval never calls it.

| Choice | Server started with | Needs | Checked by |
| --- | --- | --- | --- |
| 1 Jev through an OpenRouter key (the default) | `--model typesafe/jev-router` | `OPENROUTER_API_KEY` | the first tag. About $0.0015 a chat: OpenRouter bills the router per call, and 199 chats cost $0.30 on 19 Oct 2026 |
| 2 Anthropic API key | `--model anthropic/claude-haiku-4.5` | `ANTHROPIC_API_KEY` (starts `sk-ant-`) | the first tag. Haiku 4.5 at Anthropic's API price |
| 3 Your Codex subscription | `--model codex` | the `codex` CLI on the server's PATH, logged in with ChatGPT (Plus or higher) | `codex login status`, run by the wizard, its line shown |

`codex login status` only checks that a login is saved: on 23 Oct 2026 it said `Logged in using ChatGPT` here while every call failed with an expired refresh token. The first tag is the real check; a failed one leaves its reason in `.memory/server.log` and the chat untagged until the next try. Fix it with `codex logout`, then `codex login`.

A subscription tagger is slower: several seconds per chat (it starts the CLI each time), against about a second over an API. The Stop hook does not wait for it, but the server tags one chat at a time, so a prompt sent while a chat is being tagged waits, and recall for that prompt is dropped if the wait passes 15 s. Each tagged chat uses a little of your plan's allowance, and your own subscription terms apply. How the server runs `codex exec`, and the docs behind each flag: [docs/subscription-taggers.md](../docs/subscription-taggers.md).

To switch the tagger, stop the server (`uninstall`, or kill the pid in `.memory/server.pid`) and run `init` again; `init` reports a running server that tags with something else. `status` shows which tagger the running server uses.

## demo

`demo` runs the same steps and prompts as `init`, with the answers typed on screen for you: the harness menu moves to Claude Code, the tagger menu moves down to Codex and back to Jev, the OpenRouter key is typed as bullets, each write is answered y. The checks run for real (they only read). Nothing is written, started or run, and nothing in the folder is read: each write shows exactly what `init` would write (the JSON merged into `settings.json` and `.mcp.json` included) and then `demo: not written`; the server install shows its progress line and `demo: skipped`; the smoke test shows the request and a sample reply. A quiet `demo, nothing is written` sits in the top right corner throughout. It is `init` with a dry-run flag, not a copy, so it cannot drift from the real wizard.

Without a terminal (an assistant's shell, a pipe), answers are read from stdin. When stdin ends, or stays open and says nothing for 20 seconds, every prompt takes its default. Every write is still printed before it happens, so the log shows exactly what a default wrote.

## What `init` writes

Every write is shown before it happens; answer `n` to skip it. Every command it runs is shown the same way.

| Where | What |
| --- | --- |
| `.memory/keys.env` | the tagger's key: `OPENROUTER_API_KEY` for Jev or `ANTHROPIC_API_KEY` for Haiku; nothing for Codex. Taken from your environment, or typed at a hidden prompt; never printed. Mode 600 on macOS and Linux; on Windows it has the folder's own access rules. |
| `.memory/.gitignore` | `keys.env`, `server.pid`, `server.log`, and the server's caches `cache.sqlite*` (model replies) and `vectors.sqlite*` (embeddings); missing lines are added to a file you already have |
| `.memory/server.pid`, `.memory/server.log` | the background server: `uv run --project <server> spookling-memory serve --folder .memory --port 7447 --model <tagger>` |
| `.claude/settings.json` (project) or `~/.claude/settings.json` (user) | three hooks merged in, `PreCompact`, `UserPromptSubmit` and `Stop`, each `{"type": "command", "command": "node", "args": ["<hooks>/<event>.js"]}` |
| `.mcp.json` (project) | `mcpServers.spookling-memory`: stdio, `uv run --project <server> spookling-memory mcp`, with `SPOOKLING_MEMORY_URL` in its `env` |
| user MCP list (user scope) | the same entry, added with `claude mcp add-json spookling-memory '<entry>' --scope user`. Claude Code keeps it in `~/.claude.json`, a file it writes itself, so the wizard only reads that file. |
| `.memory/CODEX-TODO.md` | Codex only: a note that the Codex wiring is not built yet |

It also runs `uv sync` in the server folder. `<server>` and `<hooks>` are the `server/` and `hooks/` folders next to `cli/` in the repo, or inside the package once they are bundled. `CLAUDE_CONFIG_DIR` moves the user files, as it does for Claude Code. On Windows, `~` is `%USERPROFILE%`. Every other key in each file is kept. A file that is not valid JSON is left untouched and reported, never overwritten.

## What Claude Code does with it

Checked against the current Claude Code docs on 21 Oct 2026; [docs/claude-code-facts.md](../docs/claude-code-facts.md) has each fact and its source.

- The hooks use exec form (`command` plus `args`), which Claude Code spawns with no shell on every OS, so no path needs quoting. It needs Claude Code v2.1.139 or later.
- In an interactive session, hooks wait until you accept the folder's trust dialog, and Claude Code asks you once to approve the project's `.mcp.json` server.
- Recalled memory arrives as `additionalContext`, at most 10,000 characters. The server cuts there, because Claude Code would otherwise save a longer value to a file and show Claude only a 2,000-character preview.
- With the server down, the hooks print nothing and exit 0, within about a second, so a prompt is never blocked.
- To see them: `/hooks` in Claude Code lists them; `claude --debug` writes each run to `~/.claude/debug/<session-id>.txt`.

## Undo

`npx @spookling/memory uninstall` (with `--scope user` for the user files) removes only the Spookling hooks and the `spookling-memory` MCP entry; everything else in those files stays exactly as it was. It also offers to stop the server named in `.memory/server.pid`: a SIGTERM to its process group on macOS and Linux, `taskkill /T /F` on Windows. Your memory in `.memory/` stays, keys included. To remove that too, delete the folder yourself.

On Windows the stop is forced, so the server cannot remove its temporary copy of the log: delete `%TEMP%\spookling-log-*` if you want it gone. On macOS and Linux the server removes it on the way out.

## Tested

On 21 Oct 2026, on one PC: WSL2 Ubuntu 24.04 (Node 24.19, uv 0.12.5) and native Windows 11 (Node 24.7, uv 0.9.29, Python 3.12.12 fetched by uv).

- WSL: `npm test` and the server suite; `init`, `status` and `uninstall` in both scopes, in a sandbox home.
- Windows: `node --test test/hooks.test.js` (18 of 18) and the server suite (38 passed, 8 skipped: the `.sh` hooks and a WSL-only path test); `init`, `status` and `uninstall` (project scope) in a temp project, with the hooks and MCP entry it wrote driven the way Claude Code drives them, and the server tree gone after uninstall; `init` against the WSL clone through `\\wsl.localhost`.
- Not run: macOS (no Mac to hand), Claude Code itself firing these hooks (that needs a model call), Windows without mirrored networking.

## Develop

```bash
npm test                                   # node:test, no network
node bin/spookling-memory.js demo --fast   # the whole install, dry
node bin/spookling-memory.js demo --frames 3
```

The ghost is generated from `brand/logos/ghost-mark.png`, not drawn by hand: `python3 tools/make-ghost.py` (Pillow) writes `src/ghost-sprite.js` (38 x 40 pixels, 20 text rows, downsampled from the logo with hand-placed eyes and smile; and two small ghosts drawn by hand in the script, 13 x 14 and 19 x 20, since a downsample that small turns the face into a smudge; 4 tail frames each, only the lilac streaks move) and renders them back to PNG in `renders/` beside the logo, to check by eye. `python3 tools/render-frames.py` (POSIX) replays the CLI's own output through a small terminal interpreter and saves `renders/cutscene-frame.png`, `renders/cutscene-end.png` and `renders/wizard-frame.png` (80 x 24, 800 x 480 px), and `renders/wizard-frame-30.png` (80 x 30, 800 x 600 px). Neither tool ships in the package.
