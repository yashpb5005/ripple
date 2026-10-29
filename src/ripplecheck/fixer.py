"""Optional AI fixes. The model only suggests; Ripple re-checks every suggestion against
the changed schema and only calls it "ready" if Postgres accepts it."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"

SYSTEM_PROMPT = """You repair PostgreSQL queries after a schema change.
Rules:
- Reply with ONLY the corrected SQL. No explanation, no code fences.
- Keep the same meaning, the same result columns and the same $1, $2 ... parameters.
- Use only tables and columns that exist in the new schema you are given.
- If fixing it would mean guessing business meaning, reply exactly: CANNOT_FIX"""

Complete = Callable[[str, str], str]


def _strip_fences(text: str) -> str:
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    return (match.group(1) if match else text).strip()


class Fixer:
    def __init__(self, complete: Complete):
        self._complete = complete

    def suggest(self, sql: str, error: str, changes: str, schema: str) -> str | None:
        prompt = (
            f"What changed:\n{changes}\n\n"
            f"New structure of the tables this query uses:\n{schema}\n\n"
            f"PostgreSQL error:\n{error}\n\n"
            f"Query to fix:\n{sql}"
        )
        answer = _strip_fences(self._complete(SYSTEM_PROMPT, prompt))
        if not answer or "CANNOT_FIX" in answer.upper():
            return None
        return answer.rstrip(";").strip()


def anthropic_complete(model: str | None = None) -> Complete:
    try:
        import anthropic
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise RuntimeError("AI fixes need the extra package: pip install 'ripplecheck[ai]'") from error
    client = anthropic.Anthropic()
    chosen = model or os.environ.get("RIPPLE_MODEL") or DEFAULT_ANTHROPIC_MODEL

    def complete(system: str, user: str) -> str:
        message = client.messages.create(
            model=chosen,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(block, "text", "") for block in message.content)

    return complete


def openai_compatible_complete(model: str | None = None) -> Complete:
    """Any OpenAI-compatible endpoint: OpenAI, Ollama, vLLM, LiteLLM and others."""
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("OPENAI_API_KEY", "")
    chosen = model or os.environ.get("RIPPLE_MODEL")
    if not chosen:
        raise RuntimeError("Set RIPPLE_MODEL (or --model) to choose a model for your OpenAI-compatible provider.")

    def complete(system: str, user: str) -> str:
        body = json.dumps(
            {"model": chosen, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        ).encode()
        request = urllib.request.Request(
            f"{base}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
        return data["choices"][0]["message"]["content"] or ""

    return complete


def make_fixer(provider: str | None = None, model: str | None = None) -> Fixer:
    provider = provider or os.environ.get("RIPPLE_LLM")
    if not provider:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"):
            provider = "openai"
    if provider == "anthropic":
        return Fixer(anthropic_complete(model))
    if provider == "openai":
        return Fixer(openai_compatible_complete(model))
    raise RuntimeError(
        "To suggest fixes, set ANTHROPIC_API_KEY, or OPENAI_API_KEY (or OPENAI_BASE_URL) plus RIPPLE_MODEL."
    )
