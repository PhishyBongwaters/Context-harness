"""Projects: named scopes above sessions (minimal v1).

A project is just {name, workdir} in <config-dir>/projects.json, a
`project` marker file per session dir, and one ephemeral
`[project: NAME]` line in provider messages (never stored).
Modes, SPEC/facts/goal files, and soul layering come later; this is the
hook they will hang off.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import default_config_dir


def projects_path() -> Path:
    return default_config_dir() / "projects.json"


def load_registry(path: str | Path | None = None) -> dict:
    p = Path(path) if path else projects_path()
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def save_registry(reg: dict, path: str | Path | None = None) -> Path:
    p = Path(path) if path else projects_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
    return p


def resolve_project(name: str, workdir: str | None = None,
                    path: str | Path | None = None) -> dict:
    """Resume (stored workdir) or start (workdir or cwd) a project.

    An explicit workdir updates the registry. The workdir is created.
    """
    reg = load_registry(path)
    entry = reg.get(name) or {}
    if workdir:
        entry = {"workdir": os.path.abspath(os.path.expanduser(workdir))}
        reg[name] = entry
        save_registry(reg, path)
    elif name not in reg:
        entry = {"workdir": os.getcwd()}
        reg[name] = entry
        save_registry(reg, path)
    Path(entry["workdir"]).mkdir(parents=True, exist_ok=True)
    return {"name": name, "workdir": entry["workdir"]}


def session_project(session_dir: str | Path) -> str | None:
    try:
        p = Path(session_dir) / "project"
        if p.is_file():
            return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    return None


def set_session_project(session_dir: str | Path, name: str) -> None:
    try:
        Path(session_dir).mkdir(parents=True, exist_ok=True)
        (Path(session_dir) / "project").write_text(name + "\n",
                                                   encoding="utf-8")
    except OSError:
        pass
