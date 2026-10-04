"""Configuration: JSON file + environment, no secrets on disk.

Config file: ~/.config/context-harness/config.json
API keys come from environment variables only, never from the config file.

Example config.json:
{
  "provider": "anthropic",
  "model": "claude-opus-4-1-20250822",
  "base_url": null,
  "api_key_env": "ANTHROPIC_API_KEY",
  "budget_hard": 100000,
  "budget_soft": 80000,
  "sessions_dir": "~/.local/share/context-harness/sessions"
}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    "provider": "openai",
    "model": "gpt-5",
    "base_url": None,
    "api_key_env": None,  # derived from provider when unset
    "budget_hard": 100_000,
    "budget_soft": 80_000,
    "sessions_dir": "~/.local/share/context-harness/sessions",
}

PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
}


@dataclass
class Config:
    provider: str = "openai"
    model: str = "gpt-5"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    budget_hard: int = 100_000
    budget_soft: int = 80_000
    sessions_dir: str = "~/.local/share/context-harness/sessions"
    api_key: str | None = field(default=None, repr=False)

    @property
    def sessions_path(self) -> Path:
        return Path(os.path.expanduser(self.sessions_dir))


def config_path() -> Path:
    return Path(os.path.expanduser("~/.config/context-harness/config.json"))


def load_config(path: str | Path | None = None) -> Config:
    raw: dict = {}
    cfg_file = Path(path) if path else config_path()
    if cfg_file.exists():
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))

    merged = dict(DEFAULTS)
    merged.update(raw)
    provider = merged.get("provider", "openai")
    pdefs = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
    if not merged.get("base_url"):
        merged["base_url"] = pdefs["base_url"]
    if not merged.get("api_key_env"):
        merged["api_key_env"] = pdefs["api_key_env"]

    key_env = merged["api_key_env"]
    api_key = os.environ.get(key_env)

    return Config(
        provider=provider,
        model=merged.get("model", "gpt-5"),
        base_url=merged["base_url"],
        api_key_env=key_env,
        budget_hard=int(merged.get("budget_hard", 100_000)),
        budget_soft=int(merged.get("budget_soft", 80_000)),
        sessions_dir=merged.get("sessions_dir", DEFAULTS["sessions_dir"]),
        api_key=api_key,
    )


def write_example_config(path: str | Path | None = None) -> Path:
    """Write an annotated example config (no secrets). Returns the path."""
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    example = {
        "provider": "openai",
        "model": "gpt-5",
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
        "budget_hard": 100000,
        "budget_soft": 80000,
        "sessions_dir": "~/.local/share/context-harness/sessions",
        "_notes": (
            "API key is read from the api_key_env environment variable; "
            "never put secrets in this file. base_url may point at any "
            "OpenAI-compatible /v1 endpoint (e.g. a local server)."
        ),
    }
    target.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    return target
