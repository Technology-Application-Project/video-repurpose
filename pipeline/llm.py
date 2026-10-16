"""One chat entry point for the pipeline: local Ollama models or the Claude API.

Models whose name starts with "claude-" go to the Anthropic API; everything else goes to Ollama.
Every call returns an Ollama-shaped dict, {"message": {"content": "..."}}, so callers stay unchanged.
"""
from __future__ import annotations

import copy
import os

import ollama

PROVIDER_OLLAMA = "Ollama (local)"
PROVIDER_CLAUDE = "Claude API"
PROVIDERS = [PROVIDER_OLLAMA, PROVIDER_CLAUDE]

CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
# These models accept the server-side refusal fallback ("default" picks the fallback model).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
# Constraints Claude structured outputs reject; the pipeline validates these itself after parsing.
_UNSUPPORTED_SCHEMA_KEYS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                            "minLength", "maxLength", "minItems", "maxItems", "uniqueItems"}

# Held in memory only: never written to disk, gone when the app restarts.
_api_key = ""


def set_api_key(key: str | None) -> None:
    global _api_key
    _api_key = (key or "").strip()


def has_api_key() -> bool:
    return bool(_api_key or os.environ.get("ANTHROPIC_API_KEY"))


def is_claude(model: str | None) -> bool:
    return str(model or "").startswith("claude-")


def provider_name(model: str | None) -> str:
    return "Claude" if is_claude(model) else "Ollama"


def _client():
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("The anthropic package is not installed. Run: pip install anthropic") from exc
    if not has_api_key():
        raise RuntimeError("Enter an Anthropic API key to use Claude models.")
    # With no key typed in the UI, the SDK falls back to ANTHROPIC_API_KEY.
    return anthropic.Anthropic(api_key=_api_key or None)


def list_claude_models() -> tuple[list[str], str]:
    if not has_api_key():
        return CLAUDE_MODELS, "Claude: enter an API key"
    try:
        found = [m.id for m in _client().models.list() if m.id.startswith("claude-")]
    except Exception as exc:
        return CLAUDE_MODELS, f"Claude: could not verify the API key ({exc})"
    preferred = [m for m in CLAUDE_MODELS if m in found] + [m for m in found if m not in CLAUDE_MODELS]
    return preferred or CLAUDE_MODELS, "Claude: API key accepted"


def _claude_schema(node):
    if isinstance(node, dict):
        clean = {key: _claude_schema(value) for key, value in node.items() if key not in _UNSUPPORTED_SCHEMA_KEYS}
        if clean.get("type") == "object":
            clean["additionalProperties"] = False
            clean.setdefault("properties", {})
        return clean
    if isinstance(node, list):
        return [_claude_schema(item) for item in node]
    return node


def _claude_chat(model: str, messages: list[dict], format=None) -> dict:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    params = {"model": model, "max_tokens": 16000, "messages": turns}
    if isinstance(format, dict):
        params["output_config"] = {"format": {"type": "json_schema", "schema": _claude_schema(copy.deepcopy(format))}}
    elif format == "json":
        system += "\n\nRespond with a single JSON object only: no prose and no code fences."
    if system.strip():
        params["system"] = system.strip()

    client = _client()
    if model in _FALLBACK_MODELS:
        response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    else:
        response = client.messages.create(**params)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's reply was cut off at the token limit.")
    text = "".join(block.text for block in response.content if block.type == "text")
    return {"message": {"role": "assistant", "content": text}}


def chat(model: str, messages: list[dict], format=None, options: dict | None = None) -> dict:
    """`format` is a JSON schema dict, "json", or None. `options` (temperature, num_ctx) apply to Ollama only."""
    if is_claude(model):
        return _claude_chat(model, messages, format)
    kwargs = {"model": model, "messages": messages}
    if format is not None:
        kwargs["format"] = format
    if options is not None:
        kwargs["options"] = options
    return ollama.chat(**kwargs)

