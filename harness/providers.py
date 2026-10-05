"""Provider abstraction over /v1 chat endpoints (stdlib only).

Internal message format (OpenAI-style dicts):
  {"role": "system"|"user"|"assistant"|"tool",
   "content": str | None,
   "tool_calls": [{"id": str, "name": str, "arguments": dict}] | None,
   "tool_call_id": str | None}   # only on role == "tool"

Internal tool definition:
  {"name": str, "description": str,
   "parameters": {...JSON schema...}}

Provider.chat() returns:
  {"content": str | None,
   "tool_calls": [{"id": str, "name": str, "arguments": dict}],
   "usage": {"input": int, "output": int}}
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _post(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise ProviderError(f"HTTP {e.code} from {url}: {body[:500]}",
                            status=e.code, body=body) from e
    except urllib.error.URLError as e:
        raise ProviderError(f"Connection failed to {url}: {e}") from e


class Provider:
    name = "base"

    def chat(self, *, system: str, messages: list[dict],
             tools: list[dict]) -> dict:
        raise NotImplementedError


class OpenAIProvider(Provider):
    """OpenAI /v1/chat/completions and any compatible endpoint."""

    name = "openai"

    def __init__(self, *, api_key: str | None, model: str,
                 base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def build_payload(self, *, system: str, messages: list[dict],
                      tools: list[dict]) -> dict:
        full = [{"role": "system", "content": system}] + [
            {k: v for k, v in m.items() if v is not None} for m in messages
        ]
        return {
            "model": self.model,
            "messages": full,
            "tools": [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }} for t in tools
            ],
            "tool_choice": "auto",
        }

    def parse_response(self, data: dict) -> dict:
        msg = data["choices"][0]["message"]
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            args = tc["function"].get("arguments") or "{}"
            tool_calls.append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": json.loads(args) if isinstance(args, str) else args,
            })
        usage = data.get("usage") or {}
        return {
            "content": msg.get("content"),
            "tool_calls": tool_calls,
            "usage": {"input": usage.get("prompt_tokens", 0),
                      "output": usage.get("completion_tokens", 0)},
        }

    def chat(self, *, system: str, messages: list[dict],
             tools: list[dict]) -> dict:
        data = _post(f"{self.base_url}/chat/completions",
                     self._headers(),
                     self.build_payload(system=system, messages=messages,
                                        tools=tools))
        return self.parse_response(data)


class AnthropicProvider(Provider):
    """Anthropic /v1/messages."""

    name = "anthropic"

    def __init__(self, *, api_key: str | None, model: str,
                 base_url: str = "https://api.anthropic.com/v1",
                 max_tokens: int = 4096):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens

    def _headers(self) -> dict:
        h = {"anthropic-version": "2023-06-01"}
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    def translate_messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            role, content = m["role"], m.get("content")
            if role == "tool":
                out.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": content or "",
                }]})
            elif role == "assistant":
                blocks: list[dict] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"],
                                   "name": tc["name"], "input": tc["arguments"]})
                out.append({"role": "assistant", "content": blocks or ""})
            else:  # user
                out.append({"role": m["role"], "content": content or ""})
        return out

    def build_payload(self, *, system: str, messages: list[dict],
                      tools: list[dict]) -> dict:
        return {
            "model": self.model,
            "system": system,
            "messages": self.translate_messages(messages),
            "tools": [{
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            } for t in tools],
            "max_tokens": self.max_tokens,
        }

    def parse_response(self, data: dict) -> dict:
        content, tool_calls = None, []
        for block in data.get("content") or []:
            if block["type"] == "text":
                content = (content or "") + block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append({"id": block["id"], "name": block["name"],
                                   "arguments": block.get("input") or {}})
        usage = data.get("usage") or {}
        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": {"input": usage.get("input_tokens", 0),
                      "output": usage.get("output_tokens", 0)},
        }

    def chat(self, *, system: str, messages: list[dict],
             tools: list[dict]) -> dict:
        data = _post(f"{self.base_url}/messages", self._headers(),
                     self.build_payload(system=system, messages=messages,
                                        tools=tools))
        return self.parse_response(data)


class MockProvider(Provider):
    """Scripted provider for tests. Each chat() pops the next response."""

    name = "mock"

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[dict] = []

    def chat(self, *, system: str, messages: list[dict],
             tools: list[dict]) -> dict:
        self.calls.append({"system": system, "messages": messages,
                           "tools": [t["name"] for t in tools]})
        if not self.script:
            raise ProviderError("MockProvider script exhausted")
        resp = self.script.pop(0)
        return {
            "content": resp.get("content"),
            "tool_calls": resp.get("tool_calls", []),
            "usage": resp.get("usage", {"input": 0, "output": 0}),
        }


_UNSET = object()


def make_provider(cfg, *, provider=None, model=None, base_url=None,
                  api_key=_UNSET) -> Provider:
    """Build a provider from cfg, with explicit overrides winning.

    api_key uses a sentinel so an explicit None (no key, e.g. local
    server) is distinguishable from "fall back to cfg".
    """
    name = provider or cfg.provider
    if name == "anthropic":
        return AnthropicProvider(
            api_key=cfg.api_key if api_key is _UNSET else api_key,
            model=model or cfg.model,
            base_url=base_url or cfg.base_url)
    if name == "openai":
        return OpenAIProvider(
            api_key=cfg.api_key if api_key is _UNSET else api_key,
            model=model or cfg.model,
            base_url=base_url or cfg.base_url)
    raise ProviderError(f"Unknown provider: {name}")
