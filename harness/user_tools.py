"""User-defined tools: scripts as tools.

A user tool is a script + a JSON manifest sidecar:

    discord_tts.py
    discord_tts.json

Manifest format:
    {
        "name": "discord_tts",
        "description": "Generate speech audio via the Discord bot's voice mode",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to speak"},
                "voice": {"type": "string", "description": "Voice name (optional)"}
            },
            "required": ["text"]
        },
        "timeout": 60,
        "interpreter": "python3"
    }

Script protocol:
- stdin: JSON object of the tool arguments, e.g. {"text": "hello"}
- stdout: JSON object {"result": "...", "files": ["path/to/audio.mp3"]}
  ("files" is optional; "result" is the text shown to the model)
- stderr: captured to the session log, not shown to the model
- exit 0 = success; non-zero = error (stderr tail returned as ERROR)

The script runs as a subprocess with the session workdir as cwd.
A crash or timeout returns an ERROR string — it never takes down the loop.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .tools import MAX_OUTPUT_CHARS

DEFAULT_TIMEOUT = 120


def load_user_tool(script_path: str | Path) -> tuple[dict, callable] | None:
    """Load a user tool from a script path.

    Returns (tool_definition, executor) or None if the manifest is
    missing/invalid. The tool_definition goes into the model's tool
    list; the executor is called as executor(args, workdir) -> str.
    """
    script = Path(script_path).expanduser().resolve()
    if not script.is_file():
        return None
    manifest_path = script.with_suffix(".json")
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    name = manifest.get("name") or script.stem
    # Names must be valid identifiers for the model; sanitize.
    name = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
    if not name or name[0].isdigit():
        name = "tool_" + name

    definition = {
        "name": name,
        "description": manifest.get("description", f"User tool: {name}"),
        "parameters": manifest.get("parameters", {
            "type": "object", "properties": {}, "required": []}),
    }
    timeout = int(manifest.get("timeout", DEFAULT_TIMEOUT))
    interpreter = manifest.get("interpreter", sys.executable)

    def executor(args: dict, workdir: str) -> str:
        try:
            proc = subprocess.run(
                [interpreter, str(script)],
                input=json.dumps(args or {}),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: user tool '{name}' timed out after {timeout}s"
        except OSError as e:
            return f"ERROR: user tool '{name}' failed to start: {e}"
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()[-2000:]
            return f"ERROR: user tool '{name}' exited {proc.returncode}: {err}"
        try:
            out = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            # Non-JSON stdout: treat raw output as the result.
            raw = (proc.stdout or "").strip()[:MAX_OUTPUT_CHARS]
            return raw or f"ERROR: user tool '{name}' returned empty output"
        result = out.get("result", "")
        files = out.get("files") or []
        parts = [str(result)] if result else []
        for f in files:
            parts.append(f"[file: {f}]")
        text = "\n".join(parts).strip()[:MAX_OUTPUT_CHARS]
        return text or f"ERROR: user tool '{name}' returned empty result"

    return definition, executor


def load_user_tools(paths: list[str | Path]) -> tuple[list[dict], dict]:
    """Load multiple user tools.

    Returns (definitions, executors_dict). Invalid entries are skipped.
    """
    definitions: list[dict] = []
    executors: dict[str, callable] = {}
    seen: set[str] = set()
    for p in paths or []:
        loaded = load_user_tool(p)
        if loaded is None:
            continue
        definition, executor = loaded
        if definition["name"] in seen:
            continue  # first wins on name collision
        seen.add(definition["name"])
        definitions.append(definition)
        executors[definition["name"]] = executor
    return definitions, executors


def discover_user_tools(tools_dir: str | Path) -> list[str]:
    """Find candidate tool scripts in a directory.

    Returns script paths that have a JSON manifest sidecar.
    """
    d = Path(tools_dir).expanduser()
    if not d.is_dir():
        return []
    found = []
    for script in sorted(d.iterdir()):
        if script.is_file() and script.suffix in (".py", ".sh"):
            if script.with_suffix(".json").is_file():
                found.append(str(script))
    return found
