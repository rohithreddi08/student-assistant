"""Model engines. Each backend turns (system prompt, chat messages) into the raw JSON text of a reply.

  GeminiBackend        cloud: google-genai SDK, structured output (`response_json_schema`, falls back to a
                       flatter Pydantic schema on SDKs/models that reject anyOf)
  OllamaBackend        local: Ollama /api/chat with `format=<JSON schema>` (grammar-constrained by llama.cpp)
  OpenAICompatBackend  local: vLLM / llama.cpp server / LM Studio via /v1/chat/completions with
                       `response_format=json_schema` (vLLM guided decoding) or a raw GBNF `grammar`

messages = [{"role": "user" | "assistant", "content": str}, ...]
"""
from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.request
from typing import List, Literal, Optional

from pydantic import BaseModel

from .schemas import RESPONSE_SCHEMA, TOOL_NAMES


class BackendError(RuntimeError):
    pass


def _engine_schema() -> dict:
    s = copy.deepcopy(RESPONSE_SCHEMA)
    s.pop("$schema", None)
    s.pop("title", None)
    return s


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise BackendError(f"HTTP {e.code}: {e.read().decode(errors='ignore')[:400]}") from e
    except (urllib.error.URLError, OSError) as e:
        raise BackendError(f"cannot reach {url}: {getattr(e, 'reason', e)}") from e


# ---------------------------------------------------------------------------------------------
class _FlatCall(BaseModel):
    name: Literal[tuple(TOOL_NAMES)]  # type: ignore[valid-type]
    arguments_json: str


class _FlatResponse(BaseModel):
    thinking: str
    spoken_response: str
    tool_calls: List[_FlatCall]


class GeminiBackend:
    """Google AI Studio via `pip install google-genai`."""

    name = "gemini"

    def __init__(self, model: str = "gemini-3.6-flash", api_key: str | None = None, temperature: float = 0.7,
                 thinking_budget: Optional[int] = None, timeout_s: float = 90,
                 fallbacks: tuple = ("gemini-3.5-flash", "gemini-3.5-flash-lite")):
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover
            raise BackendError("google-genai is not installed: pip install google-genai") from e
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise BackendError("Set GEMINI_API_KEY or pass api_key=")
        self.types = types
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self.model, self.temperature, self.thinking_budget = model, temperature, thinking_budget
        self.models = [model] + [m for m in fallbacks if m != model]   # tried in order on quota / overload
        self.mode = "json_schema"   # becomes "flat" if this SDK/model rejects the full schema
        self.last_model = model

    def _config(self, system: str):
        t = self.types
        kw = dict(system_instruction=system, temperature=self.temperature, response_mime_type="application/json")
        if self.thinking_budget is not None:
            kw["thinking_config"] = t.ThinkingConfig(thinking_budget=self.thinking_budget)
        if self.mode == "json_schema":
            kw["response_json_schema"] = _engine_schema()
        else:
            kw["response_schema"] = _FlatResponse
        return t.GenerateContentConfig(**kw)

    def generate(self, system: str, messages: list[dict]) -> str:
        t = self.types
        contents = [t.Content(role="model" if m["role"] == "assistant" else "user",
                              parts=[t.Part(text=m["content"])]) for m in messages]
        last = None
        for model in self.models:
            for _ in range(2):
                try:
                    r = self.client.models.generate_content(model=model, contents=contents,
                                                            config=self._config(system))
                    self.last_model = model
                    return r.text or ""
                except Exception as e:  # SDK raises TypeError/ValueError for unknown fields, ClientError(400) for schema
                    msg, code = str(e), getattr(e, "code", None)
                    if code in (404, 429, 500, 503, 504) or "RESOURCE_EXHAUSTED" in msg or "UNAVAILABLE" in msg:
                        last = e
                        break   # next model
                    if self.mode == "json_schema" and (isinstance(e, (TypeError, ValueError)) or code == 400
                                                       or "schema" in msg.lower()):
                        self.mode = "flat"
                        continue
                    raise BackendError(f"gemini: {msg[:400]}") from e
        raise BackendError(f"gemini: all models failed; last error: {str(last)[:300]}")

    def generate_free_json(self, prompt: str) -> str:
        """Any JSON (no Ultron schema). Used by the dataset builder's teacher."""
        r = self.client.models.generate_content(
            model=self.model, contents=prompt,
            config=self.types.GenerateContentConfig(temperature=self.temperature, response_mime_type="application/json"))
        return r.text or ""


# ---------------------------------------------------------------------------------------------
class OllamaBackend:
    """Local models through Ollama (>= 0.5 for JSON-schema structured outputs).

    Recommended: qwen2.5:14b-instruct (16 GB VRAM, Q4_K_M) or qwen2.5:32b-instruct / llama3.3:70b for quality.
    """

    name = "ollama"

    def __init__(self, model: str = "qwen2.5:14b-instruct", host: str = "http://localhost:11434",
                 temperature: float = 0.6, num_ctx: int = 8192, timeout_s: float = 180):
        self.model, self.host, self.temperature, self.num_ctx, self.timeout = model, host.rstrip("/"), \
            temperature, num_ctx, timeout_s

    def generate(self, system: str, messages: list[dict]) -> str:
        body = {
            "model": self.model, "stream": False, "format": _engine_schema(),
            "messages": [{"role": "system", "content": system}] + messages,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
            "keep_alive": "30m",
        }
        out = _post_json(self.host + "/api/chat", body, timeout=self.timeout)
        return (out.get("message") or {}).get("content", "")


# ---------------------------------------------------------------------------------------------
class OpenAICompatBackend:
    """vLLM (`vllm serve Qwen/Qwen2.5-32B-Instruct-AWQ`), llama.cpp `llama-server`, LM Studio, etc.

    grammar=None  -> response_format json_schema (vLLM guided decoding / llama.cpp json_schema)
    grammar=str   -> raw GBNF sent as `grammar` (llama.cpp server); see ultron_core/ultron.gbnf
    """

    name = "openai-compat"

    def __init__(self, model: str, base_url: str = "http://localhost:8000/v1", api_key: str = "none",
                 temperature: float = 0.6, grammar: str | None = None, timeout_s: float = 180):
        self.model, self.base, self.key, self.temperature, self.grammar, self.timeout = \
            model, base_url.rstrip("/"), api_key, temperature, grammar, timeout_s

    def generate(self, system: str, messages: list[dict]) -> str:
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "system", "content": system}] + messages}
        if self.grammar:
            body["grammar"] = self.grammar
        else:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "UltronResponse", "strict": True,
                                                       "schema": _engine_schema()}}
        out = _post_json(self.base + "/chat/completions", body,
                         headers={"Authorization": f"Bearer {self.key}"}, timeout=self.timeout)
        return out["choices"][0]["message"].get("content") or ""
