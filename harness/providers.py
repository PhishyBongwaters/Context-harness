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
import time
import urllib.error
import urllib.request


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


# 429 (rate-limited) retries: attempts beyond the first, with
# exponential backoff. Honors Retry-After when the server sends one.
_POST_RETRIES = 3
_POST_BACKOFF_BASE = 1.0  # seconds; attempt n waits base * 2**n
_POST_RETRY_AFTER_CAP = 60.0


def _retry_delay(headers, attempt: int) -> float:
    """Seconds to wait before retrying a 429 (0-indexed attempt)."""
    try:
        retry_after = float(headers.get("Retry-After", ""))
        if retry_after >= 0:
            return min(retry_after, _POST_RETRY_AFTER_CAP)
    except (TypeError, ValueError):
        pass
    return _POST_BACKOFF_BASE * (2 ** attempt)


def _post(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    for attempt in range(_POST_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < _POST_RETRIES:
                time.sleep(_retry_delay(e.headers, attempt))
                continue
            raise ProviderError(
                f"HTTP {e.code} from {url}: {body[:500]}",
                status=e.code, body=body) from e
        except urllib.error.URLError as e:
            raise ProviderError(f"Connection failed to {url}: {e}") from e
        except TimeoutError as e:
            # Raw socket timeouts (e.g. mid-response read stalls on a
            # loaded local server) escape urlopen unwrapped — convert,
            # never crash.
            raise ProviderError(
                f"Request to {url} timed out: {e}. The server may still "
                f"be evaluating (long prompt, slow model); retry the "
                f"turn.") from e
    # Unreachable: the loop either returns or raises.
    raise ProviderError(f"HTTP 429 from {url}: retries exhausted",
                        status=429)


class Provider:
    name = "base"

    def chat(self, *, system: str, messages: list[dict],
             tools: list[dict]) -> dict:
        raise NotImplementedError


class OpenAIProvider(Provider):
    """OpenAI /v1/chat/completions and any compatible endpoint."""

    name = "openai"

    def __init__(self, *, api_key: str | None, model: str,
                 base_url: str = "https://api.openai.com/v1",
                 timeout: int = 120):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def translate_messages(self, messages: list[dict]) -> list[dict]:
        """Internal format -> OpenAI wire format.

        Assistant tool calls become {"id", "type": "function",
        "function": {"name", "arguments": "<json>"}}; strict servers
        (e.g. llama.cpp) reject anything else.
        """
        out: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "tool":
                out.append({"role": "tool",
                            "tool_call_id": m["tool_call_id"],
                            "content": m.get("content") or ""})
            elif role == "assistant":
                msg: dict = {"role": "assistant",
                             "content": m.get("content")}
                calls = [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("arguments") or {},
                                               ensure_ascii=False),
                    },
                } for tc in m.get("tool_calls") or []]
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:  # user (system is prepended separately)
                out.append({"role": m["role"], "content": m.get("content") or ""})
        return out

    def build_payload(self, *, system: str, messages: list[dict],
                      tools: list[dict]) -> dict:
        full = [{"role": "system", "content": system}] + \
            self.translate_messages(messages)
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
                                        tools=tools),
                     timeout=self.timeout)
        return self.parse_response(data)


class NvidiaProvider(OpenAIProvider):
    """NVIDIA NIM /v1/chat/completions – OpenAI compatible."""

    name = "nvidia"

    def __init__(self, *, api_key: str | None, model: str,
                 base_url: str = "https://integrate.api.nvidia.com/v1",
                 timeout: int = 120):
        super().__init__(api_key=api_key, model=model,
                         base_url=base_url, timeout=timeout)


class AnthropicProvider(Provider):
    """Anthropic /v1/messages."""

    name = "anthropic"

    def __init__(self, *, api_key: str | None, model: str,
                 base_url: str = "https://api.anthropic.com/v1",
                 max_tokens: int = 4096, timeout: int = 120):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout

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
                                        tools=tools),
                     timeout=self.timeout)
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
                  api_key=_UNSET, timeout: int | None = None) -> Provider:
    """Build a provider from cfg, with explicit overrides winning.

    api_key uses a sentinel so an explicit None (no key, e.g. local
    server) is distinguishable from "fall back to cfg". timeout None
    falls back to cfg.request_timeout.
    """
    name = provider or cfg.provider
    timeout = (timeout if timeout is not None
               else getattr(cfg, "request_timeout", 120))
    if name == "anthropic":
        return AnthropicProvider(
            api_key=cfg.api_key if api_key is _UNSET else api_key,
            model=model or cfg.model,
            base_url=base_url or cfg.base_url,
            timeout=timeout)
    if name in ("openai", "nvidia"):
        # Both use OpenAI-compatible chat completions.
        ProviderCls = NvidiaProvider if name == "nvidia" else OpenAIProvider
        return ProviderCls(
            api_key=cfg.api_key if api_key is _UNSET else api_key,
            model=model or cfg.model,
            base_url=base_url or cfg.base_url,
            timeout=timeout)
    raise ProviderError(f"Unknown provider: {name}")
