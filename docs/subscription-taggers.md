# Tagging through a Codex subscription

Checked 23 Oct 2026 against the official Codex docs, which now live on learn.chatgpt.com (`developers.openai.com/codex/<page>` redirects there; every page has a markdown twin at `<page>.md`). One line per fact, its source, and what our code does with it. **observed** marks what was seen on this machine (WSL2 Ubuntu 24.04, codex-cli 0.154.0, installed with npm under nvm) rather than read in the docs.

Sources: [NI] https://learn.chatgpt.com/docs/non-interactive-mode , [CR] https://learn.chatgpt.com/docs/developer-commands?surface=cli , [AU] https://learn.chatgpt.com/docs/auth , [CI] https://learn.chatgpt.com/docs/auth/ci-cd-auth , [PR] https://learn.chatgpt.com/docs/pricing , [MO] https://learn.chatgpt.com/docs/models , [CF] https://learn.chatgpt.com/docs/config-file/config-reference , [HK] https://learn.chatgpt.com/docs/hooks , [IN] https://learn.chatgpt.com/docs/codex/cli . All read 2026-10-23.

## The command

```bash
codex exec --ephemeral --skip-git-repo-check --sandbox read-only --ignore-user-config --ignore-rules \
  --disable hooks -c web_search=disabled -m gpt-6-luna --color never -
```

The prompt goes in on stdin, the reply comes back on stdout. The server runs it from a fresh temp folder, with `SPOOKLING_TAGGING=1` and without `OPENAI_API_KEY` or `CODEX_API_KEY` in its environment, and kills it (its whole process tree) after 90 s.

## Facts

1. `codex exec` (short `codex e`) is the non-interactive mode for scripts. [NI; CR] Used.
2. Progress goes to stderr; stdout carries only the final agent message. [NI] We read stdout as the reply; on a failure we report the last `ERROR:` line of stderr.
3. With no prompt argument, or with `-`, the prompt is read from stdin. [NI; CR] We pass `-` and write the prompt to stdin, so a long chat never meets a command-line length limit (32,767 characters on Windows). **observed**: `codex exec --help` (0.154.0) says the same.
4. There is no system-prompt flag for `exec`; `developer_instructions` exists only as a config value. [CF] We send one prompt: the tagger's system text, then each message under `[user]` or `[assistant]`.
5. `-m, --model` picks the model. [CR] We pin `gpt-6-luna`: the docs recommend Luna "for focused, repeatable tasks" [MO], and on Plus it allows about 350 to 3,000 local messages per five hours, against 15 to 160 for GPT-6.1 Sol, the default [PR]. A tagger on the default model would eat the user's coding allowance.
6. `--json` turns stdout into JSONL events (`thread.started`, `turn.completed`, `item.*`, `error`); `-o, --output-last-message` writes the final message to a file. [NI; CR] Not used: plain stdout already is the final message.
7. `--color always|never|auto` controls ANSI colour on stdout. [CR] `never`.
8. `codex exec` runs in a read-only sandbox by default; `--sandbox read-only|workspace-write|danger-full-access` sets it. [NI; CR] We pass `read-only` explicitly, because `--ignore-user-config` drops any default the user set.
9. There is no flag that removes the agent's shell tool. The read-only sandbox stops it writing files or reaching the network from commands; it can still read files. [NI; CR] The temp folder is empty and the prompt asks for JSON only.
10. `--ignore-user-config` skips `$CODEX_HOME/config.toml` (auth still comes from `CODEX_HOME`); `--ignore-rules` skips execpolicy `.rules` files. [CR] Both used, so the user's MCP servers, profiles and allow rules do not load for a tagging call.
11. Hooks load from `~/.codex/hooks.json` and `config.toml` (user and project), are on by default, and are turned off with `[features] hooks = false`; `--disable <feature>` is `-c features.<feature>=false`. [HK; CR] We pass `--disable hooks`, since `hooks.json` loads even with `--ignore-user-config`. **observed**: `codex features list` shows `hooks  stable  true`.
12. `web_search` is `disabled|cached|indexed|live`, default `cached`. [CF] `-c web_search=disabled` (a value that does not parse as TOML is taken as a literal string [CR], so no quotes need to survive a Windows command line).
13. Codex refuses to run outside a Git repository unless `--skip-git-repo-check` is given. [NI] Used: the temp folder is not a repository.
14. `--ephemeral` keeps session rollout files off disk. [NI; CR] Used: one tagging call per chat would otherwise leave one session file per chat.
15. `codex exec` reuses the saved CLI login by default. [NI] Sign in with ChatGPT gives subscription access; an API key gives usage-based access at API prices. [AU] So a ChatGPT login covers `codex exec`.
16. `CODEX_API_KEY=<key> codex exec ...` switches one run to an API key. [NI] We remove `CODEX_API_KEY` and `OPENAI_API_KEY` from the tagger's environment, so a key in the user's shell cannot quietly move the bill to the API.
17. `codex login status` prints the active authentication mode and exits 0 when logged in. [CR; AU] The wizard runs it and shows its line. **observed**: it printed `Logged in using ChatGPT` and exited 0 here, and the next `codex exec` still failed with 401 (`Your access token could not be refreshed. Please log out and sign in again.`). It checks that credentials are saved, not that they still work; the first tag is the real check (its error lands in `.memory/server.log`).
18. Plans: Free and Go get GPT-6 Luna in the desktop app only; Plus ($20 a month) and up include Codex in the CLI. [PR] The Codex choice needs Plus or higher.
19. Usage limits are per five hours, weekly limits may also apply, and local messages and cloud chats share the plan's allowance; Pro has no five-hour limit. [PR] Each tagged chat is one local message, plus one for each flagged item the slow path settles, plus a retry when a reply is not valid JSON.
20. For automation the docs call API keys the right default; running as a ChatGPT account on a runner is an "advanced" path for trusted, private machines, never public repositories. [NI; CI] Our case is the user's own machine and own login, run by the user's own server; the README says the user's own plan terms apply.
21. Sign in with ChatGPT tokens refresh automatically during use. [AU; CI] Nothing to do; a dead refresh token needs `codex logout` then `codex login` (fact 17).
22. Install: `curl -fsSL https://chatgpt.com/codex/install.sh | sh` (macOS, Linux), `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"` (Windows), or `npm install -g @openai/codex`. [IN] The wizard names npm, which works on all three.

## Speed

Not measured: no logged-in call succeeded here (fact 17). The one call that ran took 8.6 s to fail, most of it login retries. Expect several seconds per chat, against about a second for Jev or Haiku over an API. The tagging runs after the Stop hook has replied, so Claude is not held, but the server takes one call at a time: a prompt sent while a chat is being tagged waits for it, and the UserPromptSubmit hook gives up after 15 s and adds no memory to that one prompt.
