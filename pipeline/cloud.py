from __future__ import annotations

import base64
import json
import math
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from pipeline.illustration import LOCAL_PROVIDER
from pipeline.project import atomic_json, data_hash, file_hash


CAPS = {"text": 5, "image": 2, "music": 1}
XERO_PORTKEY_BASE_URL = "https://llm-gateway.xgw.xero-test.com/v1"
LOCAL_MAX_TOKENS = 12000  # Reasoning tokens count against the limit; scene data alone is small.
_LOCK = threading.RLock()


def cloud_quote() -> dict:
    path = os.environ.get("CLOUD_PRICING_FILE", "")
    if not path:
        raise ValueError("Set CLOUD_PRICING_FILE to a reviewed cloud model/pricing JSON file before cloud generation.")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    text_provider = config.get("text_provider", "google")
    if text_provider not in {"google", "portkey"}:
        raise ValueError("Configure text_provider as google or portkey.")
    media_provider = config.get("media_provider", "google")
    if media_provider not in {"google", "local-illustration"}:
        raise ValueError("Configure media_provider as google or local-illustration.")
    if media_provider == "local-illustration" and text_provider != "portkey":
        raise ValueError("local-illustration media requires text_provider portkey.")
    music_source = config.get("music_source", "library")
    if music_source not in {"library", "generated"}:
        raise ValueError("Configure music_source as library or generated.")
    if music_source == "library":
        from pipeline.music import load_library
        load_library()
    if config.get("currency") != "USD" or not config.get("price_source") or not config.get("checked_at"):
        raise ValueError("Cloud pricing requires currency USD, price_source and checked_at.")
    items = {}
    for kind, limit in CAPS.items():
        if kind == "music" and music_source == "library":
            continue
        model = config.get("models", {}).get(kind)
        amount = config.get("usd_per_request", {}).get(kind)
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"Configure the {kind} model for the selected cloud provider.")
        local = media_provider == "local-illustration" and kind != "text"
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"Configure a positive conservative USD estimate per {kind} request.")
        items[kind] = {"model": model, "limit": limit, "usd_per_request": amount}
        if (kind == "text" and text_provider == "portkey") or local:
            if not model.startswith("@") or "/" not in model or not all(model[1:].split("/", 1)):
                raise ValueError(f"Portkey {kind} model must use @provider/model from your Model Catalog.")
            items[kind]["base_url"] = XERO_PORTKEY_BASE_URL
            items[kind]["provider"] = LOCAL_PROVIDER if local else "portkey"
    quote = {"items": items, "currency": "USD", "price_source": config["price_source"], "music_source": music_source,
             "checked_at": config["checked_at"], "total_usd": sum(item["limit"] * item["usd_per_request"] for item in items.values())}
    quote["revision"] = data_hash(quote)
    return quote


def quote_markdown(quote: dict) -> str:
    rows = ["| API | Provider | Model | Request cap | Estimate (USD) |", "|---|---|---|---:|---:|"]
    rows.extend(f"| {kind} | {item.get('provider', 'google')} | {item['model']} | {item['limit']} | ${item['limit'] * item['usd_per_request']:.4f} |"
                for kind, item in quote["items"].items())
    if quote["items"]["image"].get("provider") == LOCAL_PROVIDER:
        music = ("Music comes from the bundled CC0 library; no request is made for it." if quote.get("music_source") == "library"
                 else "Music is rendered locally from model-written note data.")
        disclosure = ("Selected excerpts and creative briefs are sent to Portkey and its configured provider through the Xero Test gateway. "
                      f"Images are rendered locally from model-written scene data; no Google media requests are made. {music} "
                      "Each image request uses one Portkey text-model call.")
    elif quote["items"]["text"].get("provider") == "portkey":
        disclosure = "Selected excerpts are sent to Portkey and its configured provider through the Xero Test gateway for text; media requests go to Google."
    else:
        disclosure = "Selected excerpts are sent to Google."
    return "\n".join(rows) + (f"\n\nEstimated batch: **USD ${quote['total_usd']:.4f}**. "
                              f"Pricing checked {quote['checked_at']}. Source: {quote['price_source']}. "
                              f"Estimate, not a billing guarantee. {disclosure}")


class CloudBatch:
    def __init__(self, directory: Path, quote: dict, *, scope: str):
        if quote != cloud_quote():
            raise ValueError("Cloud pricing or models changed. Generate again with the current configuration.")
        self.text_provider = quote["items"]["text"].get("provider", "google")
        if self.text_provider not in {"google", "portkey"}:
            raise ValueError("Unsupported text provider; no fallback was used.")
        credential = "XERO_PORTKEY_API_KEY" if self.text_provider == "portkey" else "GEMINI_API_KEY"
        if not os.environ.get(credential):
            raise ValueError(f"Set {credential} in the server environment; never enter it in the review form.")
        self.directory = Path(directory)
        self.path = self.directory / "requests.json"
        self.quote = quote
        self.scope = scope
        self.directory.mkdir(parents=True, exist_ok=True)
        with _LOCK, self._file_lock():
            if self.path.exists():
                saved = self._load()
                if saved["scope"] != scope or saved["quote"] != quote:
                    raise ValueError("Cloud batch belongs to another selection/configuration. Generate a new batch.")
            else:
                atomic_json(self.path, {"scope": scope, "quote": quote, "requests": {}})
        self._client = None
        self._portkey_client = None

    @property
    def client(self):
        if self._client is None:
            if not os.environ.get("GEMINI_API_KEY"):
                raise ValueError("Set GEMINI_API_KEY for Google media generation; Portkey is configured for text only.")
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                                        http_options=types.HttpOptions(timeout=120000,
                                            retry_options=types.HttpRetryOptions(attempts=1)))
        return self._client

    @property
    def portkey_client(self):
        if self._portkey_client is None:
            from portkey_ai import Portkey
            self._portkey_client = Portkey(api_key=os.environ["XERO_PORTKEY_API_KEY"],
                                          base_url=XERO_PORTKEY_BASE_URL, max_retries=0,
                                          timeout=120, config={"retry": {"attempts": 0}})
        return self._portkey_client

    def close(self):
        for client in (self._client, self._portkey_client):
            if client is not None:
                client.close()

    def _load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    @contextmanager
    def _file_lock(self):
        import fcntl
        with (self.directory / "requests.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _reserve(self, kind: str, payload: dict):
        key = data_hash({"kind": kind, "model": self.quote["items"][kind]["model"], "payload": payload})
        with _LOCK, self._file_lock():
            ledger = self._load()
            item = ledger["requests"].get(key)
            if item:
                if item["status"] == "complete":
                    path = self.directory / item["file"]
                    if not path.is_file() or file_hash(path) != item["sha256"]:
                        raise ValueError("Cached cloud asset changed. No paid replacement was requested.")
                    return key, item, path
                raise RuntimeError("A previous request failed or has an unknown outcome. Regenerate to start a new batch; no duplicate was submitted.")
            if sum(item["kind"] == kind for item in ledger["requests"].values()) >= CAPS[kind]:
                raise ValueError(f"Configured {kind} request cap reached. Regenerate to start a new batch.")
            item = {"kind": kind, "status": "reserved", "model": self.quote["items"][kind]["model"], "payload": payload}
            ledger["requests"][key] = item
            atomic_json(self.path, ledger)
            return key, item, None

    def _update(self, key, **values):
        with _LOCK, self._file_lock():
            ledger = self._load()
            ledger["requests"][key].update(values)
            atomic_json(self.path, ledger)

    def _complete(self, key, path, usage=None):
        self._update(key, status="complete", file=path.name, sha256=file_hash(path), usage=usage)

    def _portkey_json(self, model, messages, schema, name, max_tokens=4096):
        response = self.portkey_client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=0,
            response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}})
        content = response.choices[0].message.content if response.choices else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty or safety-blocked text response")
        if getattr(response.choices[0], "finish_reason", None) == "length":
            raise ValueError("Model output was truncated by the token limit")
        return content, response.usage.model_dump(mode="json") if response.usage else None

    def _failure(self, key, exc):
        code = getattr(exc, "code", None)
        if not isinstance(code, int):
            code = getattr(exc, "status_code", None)
        code = code if isinstance(code, int) else None
        self._update(key, status="failed", error_type=type(exc).__name__, http_status=code)
        return code, f", HTTP {code}" if code is not None else ""

    def text(self, *, model, format, options, messages):
        api = "portkey-chat-completions" if self.text_provider == "portkey" else "interactions"
        provider_label = "Portkey" if self.text_provider == "portkey" else "Google"
        key, item, cached = self._reserve("text", {"api": api, "messages": messages, "schema": format})
        if cached:
            return {"message": {"content": cached.read_text(encoding="utf-8")}, "provider": self.text_provider}
        try:
            if self.text_provider == "portkey":
                content, usage = self._portkey_json(item["model"], messages, format, "storyboard")
            else:
                response = self.client.interactions.create(
                    model=item["model"], input=json.dumps(messages[1:]),
                    system_instruction=messages[0]["content"] if messages else "Return source-grounded JSON.",
                    response_format={"type": "text", "mime_type": "application/json", "schema": format},
                    generation_config={"temperature": 0, "max_output_tokens": 4096}, store=False)
                content = response.output_text
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Empty or safety-blocked text response")
                usage = response.usage.model_dump(mode="json") if response.usage else None
            path = self.directory / f"{key}.json"
            path.write_text(content, encoding="utf-8")
            self._complete(key, path, usage)
            return {"message": {"content": content}, "provider": self.text_provider}
        except Exception as exc:
            code, status = self._failure(key, exc)
            hints = {
                    402: ("Check Portkey and its configured provider billing, then regenerate."
                      if self.text_provider == "portkey" else
                        "Google payment is required or prepaid credits are depleted. Add credits in Google AI Studio, then regenerate."),
                404: "The configured model is unavailable to this account; review model and pricing settings.",
            }
            hint = hints.get(code, "Check credentials, quota and model access.")
            raise RuntimeError(f"{provider_label} text request failed ({type(exc).__name__}{status}); {hint} No fallback was used.") from None

    def _local_asset(self, kind, key, item, prompt, aspect):
        from pipeline import illustration
        try:
            if kind == "image":
                content, usage = self._portkey_json(item["model"], illustration.scene_messages(prompt, aspect),
                                                    illustration.scene_schema(), "scene", LOCAL_MAX_TOKENS)
                path = self.directory / f"{key}.png"
                illustration.render_scene(content, aspect, path)
            else:
                content, usage = self._portkey_json(item["model"], illustration.music_messages(prompt),
                                                    illustration.music_schema(), "music", LOCAL_MAX_TOKENS)
                path = self.directory / f"{key}.wav"
                illustration.render_music(content, path)
            (self.directory / f"{key}.source.json").write_text(content, encoding="utf-8")
            self._complete(key, path, usage)
            return {"path": str(path), "sha256": file_hash(path), "kind": kind, "model": item["model"],
                    "provider": LOCAL_PROVIDER, "prompt": prompt}
        except Exception as exc:
            _, status = self._failure(key, exc)
            raise RuntimeError(f"Local {kind} request incomplete ({type(exc).__name__}{status}). "
                               "Failures require an explicit regeneration; no fallback was used.") from None

    def asset(self, kind: str, prompt: str, aspect: str = "16:9") -> dict:
        if kind not in {"image", "music"}:
            raise ValueError("Unsupported asset kind")
        local = self.quote["items"][kind].get("provider") == LOCAL_PROVIDER
        if not local:
            self.client  # Missing credentials must fail before a request slot is reserved.
        key, item, cached = self._reserve(kind, {"prompt": prompt, "aspect": aspect})
        if cached:
            return {"path": str(cached), "sha256": file_hash(cached), "kind": kind, "model": item["model"],
                    "prompt": prompt, **({"provider": LOCAL_PROVIDER} if local else {})}
        if local:
            return self._local_asset(kind, key, item, prompt, aspect)
        try:
            if kind == "image":
                response = self.client.models.generate_content(model=item["model"], contents=prompt,
                    config={"response_modalities": ["IMAGE"], "image_config": {"aspect_ratio": aspect}})
                parts = response.parts or []
                image = next((part.inline_data for part in parts if part.inline_data and part.inline_data.mime_type.startswith("image/")), None)
                if image is None:
                    raise ValueError("Empty or safety-blocked image response")
                path = self.directory / f"{key}.png"
                from PIL import Image
                import io
                with Image.open(io.BytesIO(image.data)) as bitmap:
                    bitmap.convert("RGB").save(path)
            else:
                response = self.client.interactions.create(model=item["model"], input=prompt)
                audio = response.output_audio
                if audio is None:
                    raise ValueError("Empty or safety-blocked music response")
                path = self.directory / f"{key}.mp3"
                path.write_bytes(base64.b64decode(audio.data))
            if not path.stat().st_size:
                raise ValueError("Empty generated asset")
            self._complete(key, path)
            return {"path": str(path), "sha256": file_hash(path), "kind": kind, "model": item["model"], "prompt": prompt}
        except Exception as exc:
            self._update(key, status="failed", error_type=type(exc).__name__)
            raise RuntimeError(f"Google {kind} request incomplete ({type(exc).__name__}). Failures require an explicit regeneration. No fallback was used.") from None


def request_text(**kwargs):
    raise ValueError("Storyboard cloud generation requires a configured CloudBatch.")