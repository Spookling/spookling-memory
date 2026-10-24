"""`serve --model`: maps a model id to its backend, and runs the codex subscription CLI as one."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile

from .core import models
from .core.triage import FIX, WRITER, ModelTriage, Spent

TIMEOUT = 90  # seconds per call; then the whole process tree is killed
WINDOWS = os.name == "nt"
# read-only, no user config, rules, hooks or web search; gpt-6-luna has the largest allowance per five hours
CODEX = ["exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--ignore-user-config",
         "--ignore-rules", "--disable", "hooks", "-c", "web_search=disabled", "-m", "gpt-6-luna", "--color", "never", "-"]


def prompt(messages: list[dict]) -> str:
    return "\n\n".join(m["content"] if m["role"] == "system" else f"[{m['role']}]\n{m['content']}" for m in messages)


def env() -> dict:
    """No API key (it would bill the API, not the plan); SPOOKLING_TAGGING makes our own hooks exit at once."""
    drop = ("OPENAI_API_KEY", "CODEX_API_KEY")
    return {k: v for k, v in os.environ.items() if k not in drop} | {"SPOOKLING_TAGGING": "1"}


def why(stderr: str) -> str:
    lines = [x.strip() for x in stderr.splitlines() if x.strip()]
    errors = [x for x in lines if x.startswith("ERROR:")]
    return ((errors or lines or ["no error output"])[-1])[:300]


def kill(p: subprocess.Popen) -> None:
    """The whole tree: an npm `codex` is node running the real binary."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(name: str, args: list[str], text: str) -> str:
    """stdout of a CLI run in an empty temp folder; RuntimeError (a 503) if it fails, says nothing or times out."""
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
    """ModelTriage through a subscription CLI: replies are not cached, token counts are 0."""

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
    if model == "codex":
        return model
    return "anthropic" if model.startswith("anthropic/") and os.environ.get("ANTHROPIC_API_KEY") else "openrouter"


def adapter(model: str | None = None, decider: str | None = None, decide_threshold: float = 0.8) -> dict:
    """Sets core.models.BACKEND, a process-wide switch: without it an anthropic/ id goes to OpenRouter."""
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
