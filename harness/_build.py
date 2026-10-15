"""Build number for the harness.

Derived from git at runtime: commit count + short SHA.
Falls back to "dev" when git is unavailable.
"""
import subprocess


def get_build() -> str:
    try:
        count = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if count and sha:
            return f"b{count}.{sha}"
    except Exception:
        pass
    return "dev"
