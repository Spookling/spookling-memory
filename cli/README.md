# spookling

The install wizard for Spookling Memory. Node 18 or newer, no dependencies. Works on macOS, Linux (WSL included) and native Windows; what was run on which is listed under [Tested](#tested).

```bash
npx spookling init                 # the intro, then the step-by-step install, for this project
npx spookling init --scope user    # the same, wired for every project you open in Claude Code
npx spookling demo                 # the whole install as you would see it, answered for you; writes nothing
npx spookling status               # is the server up, what is wired (project and user files), which tagger; exit 0 when all is well
npx spookling uninstall            # remove the hooks and the MCP entry (add --scope user for the user files), keep .memory/
```

Until the npm package is published, run the same commands from a clone: `node <clone>/bin/spookling-memory.js init` (below).

`init` copies the package's `server/` and `hooks/` to a folder per version, `~/.spookling-memory/<version>/` (`%LOCALAPPDATA%\spookling-memory\<version>\` on Windows), runs `uv sync` there and wires that folder, so nothing Claude Code runs lives in the npx cache, which npm may clear. A new version gets a new folder; `uninstall` leaves them.

## Install from a clone

One repo has everything: the wizard, the hooks and the server, the memory algorithm included (`server/spookling_memory/core/`; memory-bench is not needed). The repo is private for now, so the clone URL below is a placeholder. Run `init` from the project folder you want the memory in; `.memory/` is created there. `init` copies the clone's `server/` and `hooks/` as it does from the package, so after a `git pull`, run `init` again to wire the new code.

### macOS (zsh)

```bash
brew install node                                   # Node 18+, or the installer from nodejs.org
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv, which runs the Python server
mkdir -p ~/src && cd ~/src
git clone <spookling-memory repo URL> spookling-memory
cd ~/code/your-project
node ~/src/spookling-memory/bin/spookling-memory.js init                 # this project
node ~/src/spookling-memory/bin/spookling-memory.js init --scope user    # or every project
```

### Linux and WSL (bash)

The same commands as macOS, with Node from your package manager or nvm. In WSL, run everything in the WSL shell, with Claude Code installed in WSL, and keep the clone and the project on the Linux side (`~/...`), not under `/mnt/c`.

### Windows, native Claude Code (PowerShell)

```powershell
winget install OpenJS.NodeJS.LTS                                       # Node 18+, or the installer from nodejs.org
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# open a new PowerShell window so node and uv are on PATH
mkdir $HOME\src; cd $HOME\src
git clone <spookling-memory repo URL> spookling-memory
cd $HOME\code\your-project
node $HOME\src\spookling-memory\bin\spookling-memory.js init                # this project
node $HOME\src\spookling-memory\bin\spookling-memory.js init --scope user   # or every project
```

Git for Windows is not needed: the hooks run `node` directly, with no shell. The first `uv sync` downloads Python 3.12 if you do not have it (about two minutes here).

### One server in WSL, Claude Code on Windows too

With WSL's mirrored networking (`networkingMode=mirrored` in `%USERPROFILE%\.wslconfig`, the setup tested here), Windows and WSL share 127.0.0.1, so one server in WSL can serve both sides and both share one memory.

1. In WSL, from your project: `node ~/src/spookling-memory/bin/spookling-memory.js init` (or `--scope user`). This installs the server in WSL's `~/.spookling-memory/<version>/` and starts it.
2. In PowerShell, point at the same clone through the WSL share:
   `node \\wsl.localhost\Ubuntu-24.04\home\<you>\src\spookling-memory\bin\spookling-memory.js init --scope user`
   The wizard sees the clone is inside WSL. It copies nothing to Windows and skips `uv sync` (a Windows `.venv` would replace the one WSL runs), wires the hooks to the scripts on the share, and writes the MCP entry as `wsl.exe -d <distro> -- bash -lc "exec uv run --project '<path>' spookling-memory mcp"`, so the MCP tools run inside WSL too.

Windows sessions then log into the WSL project's `.memory/`; the server reads their `C:\...` transcripts through `/mnt/c`. Starting a hook from the share took 1 to 3 seconds per event here. NAT networking (`localhostForwarding`) was not tested.

## Flags

- `--scope project` (default) or `--scope user`, for `init` and `uninstall`. See the table below.
- `--no-ghost` (init) skips the question about the ghost in the Claude Code status line, and leaves the status line alone.
- `--no-anim` skips the animation: the wordmark prints as a still and the ghost's lines print as plain text.
- `--fast` (demo only) drops the pauses. Without it the demo takes about a minute; any key cuts the current pause short.
- `--frames N` (demo only) prints N frames from across the intro, one after another, and stops. Useful for checking the drawing without a live terminal.

The intro animates at 40 x 16 or larger. During `init` the ghost then stays at the top of the screen: it drifts and bobs on a purple band and says one line per step in a speech bubble, while the steps scroll below it. That stage needs 80 x 24 and takes the top 9 rows; from 30 rows it shows a larger ghost and takes 11. It redraws at 12 fps while a step runs and holds still while a prompt waits, so typing is never disturbed. It uses the alternate screen; when it closes, the whole log of the steps is printed again in the normal screen, so it stays in your scrollback (also on Ctrl-C). With `NO_COLOR`, output that is not a terminal, `--no-anim`, a smaller terminal, or a Windows console older than Windows 10 (no escape sequences), there is no animation and the ghost's lines print as `spookling: ...` among the steps. Colour is 24-bit when `COLORTERM` is `truecolor` or `24bit`, or in Windows Terminal (`WT_SESSION`), 256 colours otherwise. `FORCE_COLOR=1` keeps colour in piped output. Any key skips the intro; Ctrl-C restores the terminal and exits.

At a terminal, the harness and the tagger are picked from menus (arrows, then Enter) and a key shows as bullets while you type or paste it; the key itself is never printed.

## The tagger

Step 3 asks how the tagger runs: the small model that reads each chat once, after it ends, and tags the facts in it. Retrieval never calls it.

| Choice | Server started with | Needs | Checked by |
| --- | --- | --- | --- |
| 1 Jev through an OpenRouter key (the default) | `--model typesafe/jev-router` | `OPENROUTER_API_KEY` | the first tag. About $0.002 a chat: the router is billed per call (199 chats cost $0.30 on 19 Oct 2026) and Jev itself is $0.042 per million input tokens |
| 2 Anthropic API key | `--model anthropic/claude-haiku-4.5` | `ANTHROPIC_API_KEY` (starts `sk-ant-`) | the first tag. Haiku 4.5 at Anthropic's API price |
| 3 Your Codex subscription | `--model codex` | the `codex` CLI on the server's PATH, logged in with ChatGPT (Plus or higher) | `codex login status`, run by the wizard, its line shown |

`codex login status` only checks that a login is saved: on 23 Oct 2026 it said `Logged in using ChatGPT` here while every call failed with an expired refresh token. The first tag is the real check; a failed one leaves its reason in `.memory/server.log` and the chat untagged until the next try. Fix it with `codex logout`, then `codex login`.

A subscription tagger is slower: several seconds per chat (it starts the CLI each time), against about a second over an API. The Stop hook does not wait for it, but the server tags one chat at a time, so a prompt sent while a chat is being tagged waits, and recall for that prompt is dropped if the wait passes 15 s. Each tagged chat uses a little of your plan's allowance, and your own subscription terms apply. How the server runs `codex exec`, and the docs behind each flag: [docs/subscription-taggers.md](../docs/subscription-taggers.md).

To switch the tagger, stop the server (`uninstall`, or kill the pid in `.memory/server.pid`) and run `init` again; `init` reports a running server that tags with something else. `status` shows which tagger the running server uses.

## demo

`demo` runs the same steps and prompts as `init`, with the answers typed on screen for you: the harness menu moves to Claude Code, the tagger menu moves down to Codex and back to Jev, the OpenRouter key is typed as bullets, each write is answered y. The checks run for real (they only read). Nothing is written, started or run, and nothing in the folder is read: each write shows exactly what `init` would write (the JSON merged into `settings.json` and `.mcp.json` included) and then `demo: not written`; the server install shows its progress line and `demo: skipped`; the smoke test shows the request and a sample reply. A quiet `demo, nothing is written` sits in the top right corner throughout. It is `init` with a dry-run flag, not a copy, so it cannot drift from the real wizard.

Without a terminal (an assistant's shell, a pipe), answers are read from stdin. When stdin ends, or stays open and says nothing for 20 seconds, every prompt takes its default. Every write is still printed before it happens, so the log shows exactly what a default wrote.

## What `init` writes

Every write is shown before it happens; answer `n` to skip it. Every command it runs is shown the same way. `init --yes` takes every default: the harnesses it finds (Claude Code if `claude` or `~/.claude` is there, Codex if `codex` or `$CODEX_HOME` is), the tagger you can already pay for (your Codex plan when `codex login status` says ChatGPT, else `ANTHROPIC_API_KEY`, else `OPENROUTER_API_KEY`, else none), and the ghost.

| Where | What |
| --- | --- |
| `~/.spookling-memory/<version>/` (`%LOCALAPPDATA%\spookling-memory\<version>\` on Windows) | `server/` and `hooks/`, copied from the package (a clone's `.venv` and caches stay behind); `uv sync` then makes `server/.venv` there |
| `.memory/keys.env` | the tagger's key: `OPENROUTER_API_KEY` for Jev or `ANTHROPIC_API_KEY` for Haiku; nothing for Codex or no tagger. The server reads it at start, so a server a hook restarts has the key too. Taken from your environment, or typed at a hidden prompt; never printed. Mode 600 on macOS and Linux; on Windows it has the folder's own access rules. |
| `.memory/.gitignore` | `keys.env`, `server.pid`, `server.log`, `server.json`, `server.starting`, `spookling.json`, the server's caches `cache.sqlite*` (model replies) and `vectors.sqlite*` (embeddings), and the hooks' `pulse.json*`; missing lines are added to a file you already have |
| `.memory/spookling.json` | `{version, server, uv, model, decider}`: what a hook needs to start this folder's server the way `init` did |
| `.memory/server.json`, `.memory/server.pid`, `.memory/server.log` | the background server: `uv run --project <server> spookling-memory serve --folder <abs .memory>`. It reads the tagger from `spookling.json`, takes this folder's own port (7448 to 7947, from a hash of the folder's path, the next free one up if taken), writes `{port, pid, folder}` to `server.json` once listening and removes it on the way out. Every reply carries `x-spookling-folder`, so a hook never takes another folder's answer. |
| `.claude/settings.json` (project) or `~/.claude/settings.json` (user) | four hooks merged in, `SessionStart`, `PreCompact`, `UserPromptSubmit` and `Stop`, each `{"type": "command", "command": "node", "args": ["<hooks>/<event>.js", "--memory", "<abs .memory>"]}`, PreCompact and UserPromptSubmit with a `statusMessage` for the spinner; and, unless `--no-ghost`, `statusLine` (see [The ghost](#the-ghost)) |
| `.mcp.json` (project) | `mcpServers.spookling-memory`: stdio, `uv run --project <server> spookling-memory mcp`, with `SPOOKLING_MEMORY_FOLDER` in its `env` (the tools read the port from its `server.json`) |
| user MCP list (user scope) | the same entry, added with `claude mcp add-json spookling-memory '<entry>' --scope user`. Claude Code keeps it in `~/.claude.json`, a file it writes itself, so the wizard only reads that file. |
| `.codex/hooks.json` (Codex, project) or `$CODEX_HOME/hooks.json` (Codex, user; default `~/.codex/hooks.json`) | SessionStart, UserPromptSubmit, PreCompact and Stop hooks merged in; existing hooks stay |

It also runs `uv sync` in the server folder. `<server>` and `<hooks>` are the `server/` and `hooks/` folders in `~/.spookling-memory/<version>/`, never the package itself (from Windows through `\\wsl.localhost`, the clone's own, on the share). `CLAUDE_CONFIG_DIR` moves the user files, as it does for Claude Code. On Windows, `~` is `%USERPROFILE%`. Every other key in each file is kept. A file that is not valid JSON is left untouched and reported, never overwritten.

## What Claude Code does with it

Checked against the current Claude Code docs on 21 Oct 2026; [docs/claude-code-facts.md](../docs/claude-code-facts.md) has each fact and its source.

- The hooks use exec form (`command` plus `args`), which Claude Code spawns with no shell on every OS, so no path needs quoting. It needs Claude Code v2.1.139 or later.
- In an interactive session, hooks wait until you accept the folder's trust dialog, and Claude Code asks you once to approve the project's `.mcp.json` server.
- Recalled memory arrives as `additionalContext`, at most 10,000 characters. The server cuts there, because Claude Code would otherwise save a longer value to a file and show Claude only a 2,000-character preview.
- With the server down, a hook starts it (one start at a time, guarded by `.memory/server.starting`) and waits up to 10 s (SessionStart 15 s); if it is still not up, the hook prints nothing and exits 0, so a prompt is never blocked. `SPOOKLING_MEMORY_URL` points every hook at a server you run yourself instead, and then nothing is started.
- Turns are written to `log.jsonl` before the tagger is called. A failed tagging call leaves them there untagged: search finds them, `/v1/stats` reports `tagging: {waiting, error, retry_in}`, and the next save after five minutes tries again. A `{"step": "tagged", "upto": <seq>}` line in `judgements.jsonl` records how far tagging got, so a restart picks up where it stopped. Recall never calls a model.
- To see them: `/hooks` in Claude Code lists them; `claude --debug` writes each run to `~/.claude/debug/<session-id>.txt`.

## The ghost

What shows where, and when (facts 49 to 66 in [docs/claude-code-facts.md](../docs/claude-code-facts.md)):

| Where | When | What |
| --- | --- | --- |
| spinner | while UserPromptSubmit or PreCompact runs | `👻 remembering`, `👻 keeping your rules` (the handler's `statusMessage`) |
| transcript, under your prompt | the prompt recalled facts, timeline events or past chats | `👻 3 facts and 1 rule from memory` (UserPromptSubmit's `systemMessage`). Standing rules ride on every prompt, so they alone add no line. PreCompact's `systemMessage` is dropped by Claude Code, so it prints nothing; Stop stays silent so it can never keep Claude going. |
| status line | Claude Code refreshes it: at session start, on each assistant message, after `/compact`, and every 5 s when the line is ours alone | the ghost and the last thing memory did: `3 facts · 1 rule recalled`, `listening` (nothing recalled), `saved`, `kept your 2 rules and 38 lines`, `asleep, server off`, or `remembering you` (nothing in this session yet, or nothing for 10 minutes) |

How it works:

- The hooks write `.memory/pulse.json` after each call (SessionStart adds the server's own counts: `remembered`, `facts_kept`, `rules_kept`; a failing tagger adds `tagging`, its reason): `{at, session, event, up, ...counts}`. It goes in the project's `.memory/`, else `~/.memory/` (the folder `status` picks), and never creates either. `up` is false only when the server could not be reached.
- The counts come from the server, in an `x-spookling-pulse` response header, so the hook bodies Claude Code and Codex read are unchanged: for a prompt, `facts` (fact records), `rules`, `events` (timeline lines), `chats` (dated past-chat blocks), `chars` and `cap` (the recall's size and the 10,000-character hook cap); for PreCompact, `kept`, `rules` and `lines` in the survivor block. An older server sends no header; the hooks then print its reply untouched.
- `hooks/status-line.js` reads Claude Code's stdin JSON and `pulse.json`, prints the ghost and exits 0. No network, no server call: about 24 ms wall clock here, 19 of them node starting. A pulse from another session (`session_id`) is not shown.
- The ghost (`hooks/ghost.js`, shared with the Codex hook) is a 16 x 16 pixel ghost drawn with half blocks, 16 columns by 8 rows, built the way [pixel-profile](https://github.com/TheMysteriousGhost/pixel-profile) (MIT) builds its ghost: a dark outline, shading down one side, small eyes, blush, a scalloped hem. Its state comes from the pulse alone: `active` (green) when this prompt's recall completed, `ready` after a save, a compaction or once the news is old, `degraded` when the last hook could not reach the server, `waiting` (asleep) before any hook of this session. Beside it, only what the pulse says: what was recalled, how much of the 10,000-character room it took, when the last hook ran, and which `.memory` it was.
- Colour: 24-bit codes where the terminal says it has them (`COLORTERM=truecolor` or `24bit`, or Windows Terminal), the xterm 256 palette otherwise; each state's colours stay apart there. `NO_COLOR` drops every escape and prints one 👻 line.
- Some fonts have no half blocks (Monaco, the font of Terminal.app's Pro profile); the terminal borrows them from another font, which puts rows out of line and floats the top row off the head. On Terminal.app the ghost reads the profile's font from Terminal's settings (cached until they change) and then draws a 10 x 9 ghost drawn by hand for one pixel a cell, background-coloured spaces only: 20 columns by 9 rows, in line on any font. `SPOOKLING_GHOST=half` or `cells` chooses by hand.
- Settings: with no status line, init writes `{"type": "command", "command": "node '<hooks>/status-line.js'", "refreshInterval": 5}`. With one, it only changes `command` to `node '<hooks>/status-line.js' --then '<yours>'`: your command runs first through the same shell Claude Code uses (`sh`; on Windows Git Bash, else PowerShell), and the ghost takes its own rows below (one line after two spaces under `NO_COLOR`); your `padding` and `refreshInterval` stay yours. Uninstall restores `command` from `--then`, or removes `statusLine` when there was none. On Windows the path goes in double quotes with forward slashes; a path or command holding `"`, `$`, a backtick or `!` (which Git Bash and PowerShell read differently) is refused, with a pointer to `--no-ghost`.
- With any status line set, Claude Code hides most footer hints (`esc to interrupt`, `? for shortcuts`). The status line needs the folder's trust and is skipped under `disableAllHooks`, `allowManagedHooksOnly` and `--safe-mode`.

## Undo

`npx spookling uninstall` (with `--scope user` for the user files) removes only the Spookling hooks, the ghost's status line (yours comes back) and the `spookling-memory` MCP entry; everything else in those files stays exactly as it was. It also offers to stop the server named in `.memory/server.pid`: a SIGTERM to its process group on macOS and Linux, `taskkill /T /F` on Windows. Your memory in `.memory/` stays, keys included. To remove that too, delete the folder yourself. The server and hooks stay in `~/.spookling-memory/<version>/` (it says so); delete that folder, after uninstall in every project and scope that used it, to remove them.

On Windows the stop is forced, so the server cannot remove its temporary copy of the log: delete `%TEMP%\spookling-log-*` if you want it gone. On macOS and Linux the server removes it on the way out.

## Codex

Run `node <clone>/bin/spookling-memory.js init` and select **Codex** (add `--scope user` for user hooks). The wizard uses the same server, `.memory/` folder and tagger choices as Claude Code. It merges native hooks into `.codex/hooks.json` for this project, or `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`) for user scope. No Codex executable or source files are patched, and no MCP entry is needed for automatic hooks.

In Codex, open `/hooks` and review/trust the new commands; project hooks also require a trusted project. Installation does not grant trust. Changed hook definitions require another review. `status` reports configuration and server availability; it does not claim to verify Codex's trust or that hooks ran.

- **SessionStart:** check `/v1/stats`; show the `ready` ghost only when the memory server answers successfully, with its `n_events`, `n_facts`, `n_rules` and folder beside it. Codex renders a hook message's colours and keeps its lines, so the ghost (`hooks/ghost.js`, the status line's) is drawn under a first line of words.
- **UserPromptSubmit:** recall through the existing `/v1/hook/user-prompt-submit` endpoint, pass its bounded `additionalContext` to Codex, then show the green `active` ghost with the counts from the server's `x-spookling-pulse` header beside it. A successful search with no matches still counts as completed recall: one `active` line, no ghost, so the history is not a ghost per prompt.
- **Stop / PreCompact:** reuse the existing background capture and compaction endpoints, reading Codex's rollout transcript on the server. No Stop continuation decisions or extra model turns.
- **Unavailable:** show the `degraded` ghost (`memory unavailable`) on a failed health check or recall; continue Codex normally. `SPOOKLING_TAGGING=1` skips the hooks to avoid recursion when Codex itself is the tagger.

The ghost appears in hook activity and informational messages (Codex surfaces `systemMessage` as a warning), not as a permanent screen widget. It confirms the stated operation, not that the model used every recalled fact or that future tagging will succeed. The integration uses [Codex's documented hook contract](https://learn.chatgpt.com/docs/hooks), checked 24 Oct 2026. Transcript parsing follows the internal `response_item` format observed with codex-cli 0.164.0; that format can change. Native Windows hook execution has not been verified.

Remove only Codex's hooks with `node <clone>/bin/spookling-memory.js uninstall --harness codex` (add `--scope user` for user hooks). Memory and other hooks remain. The existing optional server-stop prompt still applies.

## Tested

On 21 Oct 2026, on one PC: WSL2 Ubuntu 24.04 (Node 24.19, uv 0.12.5) and native Windows 11 (Node 24.7, uv 0.9.29, Python 3.12.12 fetched by uv).

- WSL: `npm test` and the server suite; `init`, `status` and `uninstall` in both scopes, in a sandbox home.
- Windows: `node --test test/hooks.test.js` (18 of 18) and the server suite (38 passed, 8 skipped: the `.sh` hooks and a WSL-only path test); `init`, `status` and `uninstall` (project scope) in a temp project, with the hooks and MCP entry it wrote driven the way Claude Code drives them, and the server tree gone after uninstall; `init` against the WSL clone through `\\wsl.localhost`.
- Not run: macOS (no Mac to hand), Claude Code itself firing these hooks (that needs a model call), Windows without mirrored networking.

On 24 Oct 2026, WSL only, after the move to one package: `npm test` (27) and the server suite (443) from a fresh clone; `npm pack`, the tarball unpacked, its `server/` and `hooks/` copied out by the wizard's `install()`, `uv sync` there and `serve` answering. CI (`.github/workflows/ci.yml`) runs the same on Linux, macOS and Windows with Node 20 and 24; not run yet.

## Develop

From the repo root (`package.json` is there, so `npm pack` ships `bin/`, `cli/src/`, `hooks/` and `server/`):

```bash
npm test                                   # node:test, no network (also works from cli/)
node bin/spookling-memory.js demo --fast   # the whole install, dry
node bin/spookling-memory.js demo --frames 3
npm pack --dry-run                         # what the package ships
```

`hooks/package.json` says `"type": "commonjs"`: the root one says `module`, and the hooks use `require`.

The ghost is generated from `brand/logos/ghost-mark.png`, not drawn by hand: `python3 cli/tools/make-ghost.py` (Pillow) writes `src/ghost-sprite.js` (38 x 40 pixels, 20 text rows, downsampled from the logo with hand-placed eyes and smile; and two small ghosts drawn by hand in the script, 13 x 14 and 19 x 20, since a downsample that small turns the face into a smudge; 4 tail frames each, only the lilac streaks move) and renders them back to PNG in `renders/` beside the logo, to check by eye. `python3 cli/tools/render-frames.py` (POSIX) replays the CLI's own output through a small terminal interpreter and saves `renders/cutscene-frame.png`, `renders/cutscene-end.png` and `renders/wizard-frame.png` (80 x 24, 800 x 480 px), and `renders/wizard-frame-30.png` (80 x 30, 800 x 600 px); `raw FILE OUT 120x30` renders any byte stream at another size, with emoji in Noto Color Emoji. `node cli/tools/ghost-screen.mjs pixel 80 24` prints a Claude Code screen with the real status line and transcript wording, for `raw`; `renders/ghost-status-line.png` came from it. None of these tools ship in the package.
