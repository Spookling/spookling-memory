"""The tagger behind `serve --model`: the one place a model id is mapped to its backend, and the subscription backend.

    codex           your Codex subscription: the `codex` CLI, logged in with ChatGPT, run once per call (`codex exec`)
    anthropic/...   the Anthropic API when ANTHROPIC_API_KEY is set (core.models' "anthropic" backend)
    anything else   OpenRouter with OPENROUTER_API_KEY (core.models' default)

A subscription backend is a function (messages) -> reply text. Subscription is the adapter's ModelTriage with its one
model call swapped for that function, so judge, resolve, events and resolve_class all go through it with the same
prompts and parsing. The flags and the facts behind them: ../../docs/subscription-taggers.md.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile

from .core import models
from .core.triage import FIX, WRITER, ModelTriage, Spent

TIMEOUT = 90  # seconds per call; the whole process tree is killed after it
WINDOWS = os.name == "nt"
# Read-only, no user config, rules, hooks or web search, nothing kept on disk; the prompt on stdin, the reply on stdout.
# gpt-6-luna: the docs' model for focused, repeatable tasks, with the largest allowance per five hours.
CODEX = ["exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--ignore-user-config",
         "--ignore-rules", "--disable", "hooks", "-c", "web_search=disabled", "-m", "gpt-6-luna", "--color", "never", "-"]


def prompt(messages: list[dict]) -> str:
    """One text for a CLI that takes a single prompt: the system text first, then each message under its role."""
    return "\n\n".join(m["content"] if m["role"] == "system" else f"[{m['role']}]\n{m['content']}" for m in messages)


def env() -> dict:
    """The tagger's environment: no API key that would move the bill off the subscription, and SPOOKLING_TAGGING,
    which makes our own hooks exit at once if the CLI fires them."""
    drop = ("OPENAI_API_KEY", "CODEX_API_KEY")
    return {k: v for k, v in os.environ.items() if k not in drop} | {"SPOOKLING_TAGGING": "1"}


def why(stderr: str) -> str:
    """The line that says what went wrong: the CLI's last `ERROR:` line, else its last line."""
    lines = [x.strip() for x in stderr.splitlines() if x.strip()]
    errors = [x for x in lines if x.startswith("ERROR:")]
    return ((errors or lines or ["no error output"])[-1])[:300]


def kill(p: subprocess.Popen) -> None:
    """The process and everything it started (an npm `codex` is node running the real binary)."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(name: str, args: list[str], text: str) -> str:
    """Run a CLI from an empty temp folder with `text` on stdin; its stdout, stripped. RuntimeError (the server's 503)
    when it is missing, fails, says nothing, or runs past TIMEOUT."""
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(f"the {name} CLI is not on the server's PATH; install it, log in, and restart the server")
    with tempfile.TemporaryDirectory(prefix="spookling-tag-", ignore_cleanup_errors=True) as cwd:
        try:
            p = subprocess.Popen([exe, *args], cwd=cwd, env=env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                 start_new_session=not WINDOWS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise RuntimeError(f"{name} could not start: {e}") from None
        try:
            out, err = p.communicate(text, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            kill(p)
            p.communicate()
            raise RuntimeError(f"{name} gave no reply in {TIMEOUT} s") from None
    if p.returncode != 0:
        raise RuntimeError(f"{name} exited {p.returncode}: {why(err)}")
    if not out.strip():
        raise RuntimeError(f"{name} gave an empty reply: {why(err)}")
    return out.strip()


def codex(messages: list[dict]) -> str:
    return run("codex", CODEX, prompt(messages))


class Subscription(ModelTriage):
    """ModelTriage with core.models swapped for a subscription CLI. Replies are not cached, and the token counts are
    unknown (0)."""

    def __init__(self, model: str):
        super().__init__(model)
        self.name, self.ask = model, codex

    def _ask(self, messages, parse):
        # ModelTriage._ask with the call swapped: keep in step with core/triage.py
        spent = Spent()
        for _ in range(2):
            text = self.ask(messages)
            spent.calls += 1
            try:
                return parse(text), spent
            except (ValueError, KeyError, TypeError, AttributeError):
                messages = messages + [{"role": "assistant", "content": text}, {"role": "user", "content": FIX}]
        spent.failures += 1
        return None, spent


def backend(model: str) -> str:
    """Where a model id's tagging calls go: a subscription backend by name; "anthropic" for an anthropic/ id when
    ANTHROPIC_API_KEY is set; else "openrouter"."""
    if model == "codex":
        return model
    return "anthropic" if model.startswith("anthropic/") and os.environ.get("ANTHROPIC_API_KEY") else "openrouter"


def adapter(model: str | None = None, decider: str | None = None, decide_threshold: float = 0.8) -> dict:
    """SpooklingMemory kwargs for `serve --model [--decider]` (default: the adapter's writer, Haiku 4.5, no decider).
    For the API backends it sets core.models' routing, a process-wide switch: without it an anthropic/ id goes to
    OpenRouter. A decider (Jev) answers every gate as a typed question through the Decisions API on the OpenRouter key."""
    model = model or WRITER
    b = backend(model)
    if b == "codex":
        out = {"model": model, "triage": Subscription(model)}
    else:
        out = {"model": model}
        models.BACKEND = b
    if decider:
        out |= {"decider": decider, "decide_threshold": decide_threshold}
    return out
