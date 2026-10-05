"""Configuration: JSON file + environment (.env supported), no secrets on disk.

Config file: platform default (see default_config_dir())
API keys come from environment variables only, never from the config file.
A `.env` file may supply those variables for convenience: the harness
loads (low to high precedence) `<config-dir>/.env`, then `./.env`.
Real environment variables always win over `.env` entries.

Example config.json:
{
  "provider": "anthropic",
  "model": "claude-opus-4-1-20250822",
  "base_url": null,
  "api_key_env": "ANTHROPIC_API_KEY",
  "budget_hard": 100000,
  "budget_soft": 80000,
  "sessions_dir": null
}

Example .env:
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

sessions_dir null -> platform default (LOCALAPPDATA on Windows,
~/.local/share on POSIX).
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
    "sessions_dir": None,  # platform default when unset
    "approval_timeout": 120,  # seconds to wait for a human approval
    "exec_timeout": 60,  # default exec runtime when the model omits it
    "exec_timeout_max": 300,  # hard ceiling even if the model asks for more
    "usage_note": True,  # ephemeral per-request usage line (never stored)
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


def _windows_base_dirs() -> tuple[str, str]:
    """(APPDATA, LOCALAPPDATA) with home-dir fallback. Split out so the
    env-var logic is testable without mocking os.name (which would
    confuse pathlib's Path dispatch)."""
    home = os.path.expanduser("~")
    return (os.environ.get("APPDATA") or home,
            os.environ.get("LOCALAPPDATA") or home)


def default_config_dir() -> Path:
    if os.name == "nt":
        appdata, _ = _windows_base_dirs()
        return Path(appdata) / "context-harness"
    return Path(os.path.expanduser("~/.config/context-harness"))


def default_sessions_dir() -> str:
    if os.name == "nt":
        _, local = _windows_base_dirs()
        return str(Path(local) / "context-harness" / "sessions")
    return "~/.local/share/context-harness/sessions"


@dataclass
class Config:
    provider: str = "openai"
    model: str = "gpt-5"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    budget_hard: int = 100_000
    budget_soft: int = 80_000
    sessions_dir: str | None = None
    api_key: str | None = field(default=None, repr=False)
    approval_timeout: int = 120
    exec_timeout: int = 60
    exec_timeout_max: int = 300
    usage_note: bool = True
    # Prune (janitor) model: cheaper/smaller model for prune-only turns.
    # Each falls back to the main setting when unset; resolution happens
    # in __main__ after CLI overrides so --provider/--model apply.
    prune_provider: str | None = None
    prune_model: str | None = None
    prune_base_url: str | None = None
    prune_api_key_env: str | None = None

    def __post_init__(self):
        if not self.sessions_dir:
            self.sessions_dir = default_sessions_dir()

    @property
    def sessions_path(self) -> Path:
        return Path(os.path.expanduser(self.sessions_dir))


def config_path() -> Path:
    return default_config_dir() / "config.json"


def dotenv_paths() -> list[Path]:
    """Candidate .env files, low to high precedence."""
    return [default_config_dir() / ".env", Path.cwd() / ".env"]


def parse_dotenv_text(text: str) -> dict[str, str]:
    """Minimal .env parser (stdlib only): KEY=VALUE, # comments,
    optional `export ` prefix, single/double quotes stripped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        # Expand escaped sequences inside double quotes only.
        if val and key:
            out[key] = val
    return out


def load_dotenv(path: str | Path | None = None,
                override: bool = False) -> dict[str, str]:
    """Load .env entries into os.environ (real env wins by default).

    path: load exactly this file; None: load dotenv_paths() in order.
    Returns the entries read from file(s).
    """
    candidates = [Path(path)] if path else dotenv_paths()
    loaded: dict[str, str] = {}
    for cand in candidates:
        try:
            if not cand.is_file():
                continue
            entries = parse_dotenv_text(
                cand.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        loaded.update(entries)
        for k, v in entries.items():
            if override or k not in os.environ:
                os.environ[k] = v
    return loaded


def load_config(path: str | Path | None = None,
                dotenv_path: str | Path | None = None) -> Config:
    load_dotenv(dotenv_path)
    raw: dict = {}
    cfg_file = Path(path) if path else config_path()
    if cfg_file.exists():
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))

    merged = dict(DEFAULTS)
    merged.update(raw)
    if not merged.get("sessions_dir"):
        merged["sessions_dir"] = default_sessions_dir()
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
        sessions_dir=merged.get("sessions_dir"),
        api_key=api_key,
        approval_timeout=int(merged.get("approval_timeout", 120)),
        exec_timeout=int(merged.get("exec_timeout", 60)),
        exec_timeout_max=int(merged.get("exec_timeout_max", 300)),
        usage_note=bool(merged.get("usage_note", True)),
        prune_provider=merged.get("prune_provider"),
        prune_model=merged.get("prune_model"),
        prune_base_url=merged.get("prune_base_url"),
        prune_api_key_env=merged.get("prune_api_key_env"),
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
        "sessions_dir": None,
        "prune_provider": None,
        "prune_model": None,
        "prune_base_url": None,
        "prune_api_key_env": None,
        "approval_timeout": 120,
        "exec_timeout": 60,
        "exec_timeout_max": 300,
        "usage_note": True,
        "_notes": (
            "API key is read from the api_key_env environment variable; "
            "never put secrets in this file. base_url may point at any "
            "OpenAI-compatible /v1 endpoint (e.g. a local server). "
            "sessions_dir null selects the platform default. "
            "prune_* selects the janitor model for prune-only turns "
            "(e.g. a small local model); each falls back to the main "
            "setting when null. approval_timeout is how long an approval "
            "prompt waits (seconds); exec_timeout/max bound tool runtime."
        ),
    }
    target.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    return target
