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
from types import SimpleNamespace
from urllib import request as _urlrequest

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
    "request_timeout": 120,  # HTTP seconds per model call (raise for huge
    # prompts on slow local servers; prefill of ~100k tokens can take
    # many minutes on big models)
    "prune_target": None,  # deterministic stages aim here (null -> soft)
    "prune_keep_tools": 5,  # newest tool sections exempt from eviction
    "prune_section_cap": 8000,  # per-section token cap (head+tail kept)
    "providers": {},  # named registry: name -> {kind, base_url, model,
    #   api_key_env}; "provider" names a registry entry when it matches,
    #   else a legacy kind (openai/anthropic/nvidia)
}

# Model context window registry (tokens). Used to auto-size budget_hard
# when the user has not explicitly set it. Keyed by model ID substring;
# first match wins. Extend as needed.
MODEL_CONTEXT_WINDOWS = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
    # Anthropic
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    # Google
    "gemini-2.0-flash": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-flash": 1_048_576,
    # Meta (via NVIDIA, etc.)
    "llama-3.1": 128_000,
    "llama-3.2": 128_000,
    "llama-3.3": 128_000,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    # Mistral
    "mistral-large": 128_000,
    "mistral-medium": 32_000,
    "mistral-small": 32_000,
    # xAI
    "grok-2": 131_072,
    "grok-beta": 131_072,
}


def get_model_context_window(model: str) -> int | None:
    """Look up a model context window from the registry.

    Matches by substring (case-insensitive); returns None if unknown.
    This is a fallback — live detection via detect_context_window()
    is preferred for OpenAI-compatible servers.
    """
    if not model:
        return None
    ml = model.lower()
    for key, window in MODEL_CONTEXT_WINDOWS.items():
        if key in ml:
            return window
    return None


def detect_context_window(base_url: str | None, model: str | None,
                          api_key: str | None = None,
                          timeout: int = 10) -> int | None:
    """Detect a model's context window from an OpenAI-compatible server.

    Queries GET {base_url}/v1/models for meta.n_ctx, falling back to
    GET {base_url}/props for n_ctx (llama.cpp). Returns None if the
    server doesn't expose it. This is how tools detect the actual
    --ctx-size from a local llama.cpp server, not a registry guess.

    Detection chain (prior art: pi-llama-cpp extensions):
    1. /v1/models -> meta.n_ctx (loaded model, single or router mode)
    2. /v1/models -> max_model_len (legacy/ik_llama.cpp)
    3. /props -> n_ctx (llama.cpp; reflects --ctx-size even for unloaded
       models in router mode, per pi-llama-cpp docs)
    """
    if not base_url:
        return None
    import json as _json
    import urllib.request as _req
    base = base_url.rstrip("/")
    # Strip trailing /v1 to avoid /v1/v1/models (base_url may include it)
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # 1. Try /v1/models for meta.n_ctx
    try:
        r = _req.Request(f"{base}/v1/models", headers=headers)
        with _req.urlopen(r, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8", "replace"))
        models = data.get("data") or []
        # Find matching model, or use first if only one
        target = None
        if model:
            ml = model.lower()
            for m in models:
                mid = str(m.get("id", ""))
                if ml in mid.lower() or mid.lower() in ml:
                    target = m
                    break
        if target is None and len(models) == 1:
            target = models[0]
        if target:
            meta = target.get("meta") or {}
            n_ctx = meta.get("n_ctx")
            if n_ctx:
                return int(n_ctx)
            # Some servers use max_model_len (legacy/ik_llama.cpp)
            mml = target.get("max_model_len") or meta.get("max_model_len")
            if mml:
                return int(mml)
            # OpenAI-compatible servers (LM Studio, etc.) may expose
            # context_length / max_context_length at top level
            # (prior art: opencode feature request for dynamic detection)
            for key in ("context_length", "max_context_length",
                        "native_context_length"):
                cl = target.get(key)
                if cl:
                    return int(cl)
    except Exception:
        pass
    # 2. Fallback to /props for n_ctx (llama.cpp).
    # Prior art (maverobot/qwen36-mtp opencode workaround): /props may nest
    # it under default_generation_settings.n_ctx, not top-level n_ctx.
    try:
        r = _req.Request(f"{base}/props", headers=headers)
        with _req.urlopen(r, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8", "replace"))
        n_ctx = data.get("n_ctx")
        if not n_ctx:
            dgs = data.get("default_generation_settings") or {}
            n_ctx = dgs.get("n_ctx")
        if n_ctx:
            return int(n_ctx)
    except Exception:
        pass
    # 3. Fallback: for local servers, read --ctx-size from the
    # llama-server process command line (prior art: pi-llama-cpp reads
    # server args for unloaded models in router mode).
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        if host in ("localhost", "127.0.0.1", "::1"):
            import subprocess
            # Find llama-server processes and parse --ctx-size / -c
            out = subprocess.run(
                ["ps", "-eo", "args"], capture_output=True, text=True,
                timeout=5).stdout
            for line in out.splitlines():
                if "llama-server" not in line:
                    continue
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "--ctx-size" and i + 1 < len(parts):
                        return int(parts[i + 1])
                    if p == "-c" and i + 1 < len(parts):
                        # -c could be ambiguous; only take if numeric
                        try:
                            return int(parts[i + 1])
                        except ValueError:
                            pass
                    # --ctx-size=32768 form
                    if p.startswith("--ctx-size="):
                        return int(p.split("=", 1)[1])
    except Exception:
        pass
    return None


# Add-flow starting points (prefilled into the form, never auto-seeded
# into the user's config). api_key_env None = local server, no key.
PROVIDER_TEMPLATES = {
    "openai-cloud": {
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5",
        "api_key_env": "OPENAI_API_KEY",
    },
    "anthropic-cloud": {
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-opus-4-1-20250822",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "nvidia-cloud": {
        "kind": "nvidia",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "",
        "api_key_env": "NVIDIA_API_KEY",
    },
    "llama.cpp": {
        "kind": "openai",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "",
        "api_key_env": None,
    },
    "lmstudio": {
        "kind": "openai",
        "base_url": "http://localhost:1234/v1",
        "model": "",
        "api_key_env": None,
    },
    "ollama": {
        "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "model": "",
        "api_key_env": None,
    },
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
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
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
    # True if budget was auto-detected (not explicitly set by user).
    # Auto-detected budgets update on model switch; user-set ones don't.
    budget_hard_auto: bool = True
    budget_soft_auto: bool = True
    sessions_dir: str | None = None
    api_key: str | None = field(default=None, repr=False)
    approval_timeout: int = 120
    exec_timeout: int = 60
    exec_timeout_max: int = 300
    usage_note: bool = True
    request_timeout: int = 120
    prune_target: int | None = None
    prune_keep_tools: int = 5
    prune_section_cap: int = 8000
    # Prune (janitor) model: cheaper/smaller model for prune-only turns.
    # Each falls back to the main setting when unset; resolution happens
    # in __main__ after CLI overrides so --provider/--model apply.
    prune_provider: str | None = None
    prune_model: str | None = None
    prune_base_url: str | None = None
    prune_api_key_env: str | None = None
    providers: dict = field(default_factory=dict)

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
    providers = merged.get("providers") or {}
    if not isinstance(providers, dict):
        providers = {}
    # All connection decisions go through resolve_provider: a registry
    # hit resolves entry fields (falling back to kind defaults), else
    # the legacy path below is identical to the old post-processing.
    _tmp = SimpleNamespace(
        provider=merged.get("provider", "openai"),
        model=merged.get("model", "gpt-5"),
        base_url=merged.get("base_url"),
        api_key_env=merged.get("api_key_env"),
        api_key=None,
        providers=providers,
    )
    res = resolve_provider(_tmp)
    merged["base_url"] = res.base_url
    merged["api_key_env"] = res.api_key_env
    if res.registry_hit and res.model:
        merged["model"] = res.model

    key_env = merged["api_key_env"]
    api_key = os.environ.get(key_env) if key_env else None

    return Config(
        provider=merged.get("provider", "openai"),
        model=merged.get("model", "gpt-5"),
        base_url=merged["base_url"],
        api_key_env=key_env,
        budget_hard=int(merged.get("budget_hard", 100_000)),
        budget_soft=int(merged.get("budget_soft", 80_000)),
        # If user explicitly set budget, don't auto-update on model switch
        budget_hard_auto=("budget_hard" not in merged),
        budget_soft_auto=("budget_soft" not in merged),
        sessions_dir=merged.get("sessions_dir"),
        api_key=api_key,
        approval_timeout=int(merged.get("approval_timeout", 120)),
        exec_timeout=int(merged.get("exec_timeout", 60)),
        exec_timeout_max=int(merged.get("exec_timeout_max", 300)),
        usage_note=bool(merged.get("usage_note", True)),
        request_timeout=int(merged.get("request_timeout", 120)),
        prune_target=(int(merged["prune_target"])
                      if merged.get("prune_target") else None),
        prune_keep_tools=int(merged.get("prune_keep_tools", 5)),
        prune_section_cap=int(merged.get("prune_section_cap", 8000)),
        prune_provider=merged.get("prune_provider"),
        prune_model=merged.get("prune_model"),
        prune_base_url=merged.get("prune_base_url"),
        prune_api_key_env=merged.get("prune_api_key_env"),
        providers=providers,
    )


def resolve_provider(cfg) -> SimpleNamespace:
    """Resolve connection params for cfg.provider (registry name or kind).

    Registry hit (cfg.provider names an entry in cfg.providers): entry
    fields fall back per-field to that kind's PROVIDER_DEFAULTS, api_key
    from the environment (None key_env = local, no key). Else the legacy
    path: kind = cfg.provider, base_url/api_key_env default per kind.
    Returns namespace(kind, model, base_url, api_key_env, api_key,
    name, registry_hit). Display-only reachability lives in probe_provider.
    """
    providers = getattr(cfg, "providers", None) or {}
    name = getattr(cfg, "provider", "openai")
    entry = providers.get(name) if isinstance(providers, dict) else None
    if isinstance(entry, dict):
        kind = entry.get("kind") or "openai"
        pdefs = PROVIDER_DEFAULTS.get(kind, PROVIDER_DEFAULTS["openai"])
        base_url = entry.get("base_url") or pdefs["base_url"]
        model = entry.get("model") or getattr(cfg, "model", "") or ""
        if "api_key_env" in entry:
            key_env = entry["api_key_env"]  # explicit null = local/no key
        else:
            key_env = pdefs["api_key_env"]
        api_key = os.environ.get(key_env) if key_env else None
        return SimpleNamespace(kind=kind, model=model, base_url=base_url,
                               api_key_env=key_env, api_key=api_key,
                               name=name, registry_hit=True)
    pdefs = PROVIDER_DEFAULTS.get(name, PROVIDER_DEFAULTS["openai"])
    base_url = getattr(cfg, "base_url", None) or pdefs["base_url"]
    key_env = getattr(cfg, "api_key_env", None) or pdefs["api_key_env"]
    api_key = os.environ.get(key_env) if key_env else None
    if api_key is None and getattr(cfg, "api_key", None):
        # Keep an explicitly attached key (e.g. test fixtures) when the
        # environment has nothing for this key env.
        api_key = cfg.api_key
    return SimpleNamespace(kind=name, model=getattr(cfg, "model", "gpt-5"),
                           base_url=base_url, api_key_env=key_env,
                           api_key=api_key, name=name, registry_hit=False)


def probe_provider(base_url: str | None, kind: str | None,
                   timeout: int = 5) -> bool | None:
    """Display-only reachability: True/False, None when not probeable.

    None when there is no base_url or kind is anthropic (no /models
    endpoint). Otherwise GET {base_url}/models via stdlib; any failure
    (DNS, refused, timeout, HTTP error) is False. Never raises, never
    blocks connection decisions.
    """
    if not base_url or (kind or "") == "anthropic":
        return None
    url = base_url.rstrip("/") + "/models"
    try:
        req = _urlrequest.Request(url, headers={"Accept": "application/json"},
                                  method="GET")
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            resp.read(1)
        return True
    except Exception:
        return False


def validate_provider_entry(name: str, entry: dict,
                            existing: dict | None = None,
                            require_unique: bool = False) -> dict:
    """Check an add-flow entry; returns a cleaned copy. Raises ValueError.

    Name must be non-empty (unique among existing when require_unique),
    kind known, model non-empty.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("provider name must be non-empty")
    if require_unique and existing is not None and clean_name in existing:
        raise ValueError(f"provider '{clean_name}' already exists")
    if not isinstance(entry, dict):
        raise ValueError("provider entry must be an object")
    kind = (entry.get("kind") or "").strip()
    if kind not in PROVIDER_DEFAULTS:
        raise ValueError(
            f"unknown kind '{kind}' (expected one of "
            f"{sorted(PROVIDER_DEFAULTS)})")
    model = (entry.get("model") or "").strip()
    if not model:
        raise ValueError("model must be non-empty")
    base_url = (entry.get("base_url") or "").strip() or None
    key_env = entry.get("api_key_env")
    if isinstance(key_env, str):
        key_env = key_env.strip() or None
    elif key_env is not None:
        raise ValueError("api_key_env must be a string or null")
    return {"kind": kind, "base_url": base_url, "model": model,
            "api_key_env": key_env}


def save_provider_entry(path: str | Path, name: str, entry: dict,
                        activate: bool = True) -> Path:
    """Upsert providers[name] in config.json, preserving unknown keys.

    Reads the file raw ({} when missing), validates the entry, writes
    back; sets top-level provider=name when activate. Returns the path.
    """
    target = Path(path)
    raw: dict = {}
    if target.exists():
        raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raw = {}
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    clean = validate_provider_entry(name, entry, providers)
    clean_name = (name or "").strip()
    providers[clean_name] = clean
    raw["providers"] = providers
    if activate:
        raw["provider"] = clean_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return target


def set_active_provider(path: str | Path, name: str) -> Path:
    """Point top-level provider at name, preserving all other keys.

    Used after an explicit switch (e.g. TUI retarget) so the file always
    matches live state -- a stale name here resurrects the old endpoint
    on the next launch and looks "stuck". Best-effort companion to
    save_provider_entry (which handles entry upserts).
    """
    target = Path(path)
    raw: dict = {}
    if target.exists():
        raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raw = {}
    clean = (name or "").strip()
    if clean:
        raw["provider"] = clean
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return target


def snapshot_current(cfg) -> dict:
    """Registry entry snapshotting the live resolved connection."""
    res = resolve_provider(cfg)
    return {"kind": res.kind, "base_url": res.base_url,
            "model": getattr(cfg, "model", res.model),
            "api_key_env": res.api_key_env}


def save_current_provider(cfg, name: str,
                           path: str | Path | None = None) -> list[str]:
    """Snapshot the live connection into the registry under name."""
    target = Path(path) if path else config_path()
    providers = getattr(cfg, "providers", None) or {}
    clean_name = (name or "").strip()
    if not clean_name:
        return ["usage: /providers save-current <name>"]
    if isinstance(providers, dict) and clean_name in providers:
        return [f"[providers] '{clean_name}' already exists"]
    try:
        entry = snapshot_current(cfg)
        saved = validate_provider_entry(clean_name, entry, providers)
    except ValueError as e:
        return [f"[providers] cannot snapshot: {e}"]
    save_provider_entry(target, clean_name, saved, activate=False)
    if isinstance(getattr(cfg, "providers", None), dict):
        cfg.providers[clean_name] = saved
    return [f"[providers] saved '{clean_name}' "
            f"({saved['kind']}/{saved['model']})"]


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
        "request_timeout": 120,
        "prune_target": None,
        "prune_keep_tools": 5,
        "prune_section_cap": 8000,
        "providers": {},
        "_notes": (
            "API key is read from the api_key_env environment variable; "
            "never put secrets in this file. base_url may point at any "
            "OpenAI-compatible /v1 endpoint (e.g. a local server). "
            "sessions_dir null selects the platform default. "
            "prune_* selects the janitor model for prune-only turns "
            "(e.g. a small local model); each falls back to the main "
            "setting when null. approval_timeout is how long an approval "
            "prompt waits (seconds); exec_timeout/max bound tool runtime. "
            "providers maps a name to {kind, base_url, model, api_key_env} "
            "(api_key_env null = local, no key); provider names a registry "
            "entry when it matches, else a kind (openai/anthropic/nvidia)."
        ),
    }
    target.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    return target
