"""Deterministic prune stages: no model calls, no judgment.

Ladder order (each runs only while still over target):
  1. dedupe_exact: collapse byte-identical assistant/tool sections,
     keeping the newest. Re-run dumps and repeated filler vanish.
  2. evict_oldest_tools: drop oldest ## tool sections beyond the newest
     K kept. Dump-bloat (95% of real overflow) dies here.
  3. cap_sections: truncate any single tool body over N tokens to
     head+tail with a marker. Bounds the worst case.

Never touched: ## user sections, the newest tool sections, assistant
prose (only exact dupes). When a ## tool section goes, its id is
scrubbed from assistant ```tool-calls fences too -- strict servers
reject tool_calls with no matching result message.

Returns (new_text, report). Pure functions; the caller measures.
"""
from __future__ import annotations

import json

from .context import (_FENCE_CLOSE, _FENCE_OPEN, _split_sections,
                      count_tokens)

DEFAULT_KEEP_RECENT_TOOLS = 5
DEFAULT_SECTION_CAP = 8000


def _render(sections: list[tuple[str, str | None, str, str]],
            preamble: str) -> str:
    out = [preamble] if preamble else []
    for _, _, header, body in sections:
        out.append(f"{header}\n{body.rstrip()}\n")
    return "\n".join(out)


def _split_preamble(text: str) -> tuple[str, str]:
    if "## " in text:
        pre, _, _ = text.partition("## ")
        return pre, "## " + _
    return text, ""


def dedupe_exact(text: str) -> tuple[str, int]:
    """Collapse identical (header, body) assistant/tool sections.

    Keeps the newest occurrence (latest state wins); user sections are
    never deduped. Returns (new_text, n_dropped).
    """
    pre, _ = _split_preamble(text)
    sections = _split_sections(text)
    # Key on (role, body): tool headers carry unique call ids, so
    # header-inclusive keys would never match re-run outputs.
    seen: set[tuple[str, str]] = set()
    keep_rev: list[bool] = []
    for role, _label, header, body in reversed(sections):
        key = (role, body.strip())
        if role in ("assistant", "tool") and key in seen:
            keep_rev.append(False)
        else:
            if role in ("assistant", "tool"):
                seen.add(key)
            keep_rev.append(True)
    kept = [s for s, k in zip(sections, reversed(keep_rev)) if k]
    dropped = len(sections) - len(kept)
    if not dropped:
        return text, 0
    dropped_ids = _tool_ids([s for s, k in zip(sections, reversed(keep_rev))
                             if not k])
    kept = _scrub_fences(kept, dropped_ids)
    return _render(kept, pre), dropped


def _tool_ids(sections) -> set[str]:
    return {label for role, label, _, _ in sections
            if role == "tool" and label}


def _scrub_fences(sections: list[tuple[str, str | None, str, str]],
                  drop_ids: set[str]
                  ) -> list[tuple[str, str | None, str, str]]:
    """Remove dropped tool ids from assistant tool-calls fences.

    A fence left empty is removed entirely (section becomes plain
    assistant text); malformed fences are left untouched.
    """
    if not drop_ids:
        return sections
    out = []
    for role, label, header, body in sections:
        if role != "assistant" or _FENCE_OPEN not in body:
            out.append((role, label, header, body))
            continue
        lines = body.splitlines()
        try:
            oi = lines.index(_FENCE_OPEN)
            ci = lines.index(_FENCE_CLOSE, oi + 1)
            calls = json.loads("\n".join(lines[oi + 1:ci]))
            assert isinstance(calls, list)
        except (ValueError, AssertionError):
            out.append((role, label, header, body))
            continue
        kept_calls = [c for c in calls
                      if not (isinstance(c, dict) and
                              c.get("id") in drop_ids)]
        if len(kept_calls) == len(calls):
            out.append((role, label, header, body))
            continue
        if kept_calls:
            fence = (f"{_FENCE_OPEN}\n{json.dumps(kept_calls)}\n"
                     f"{_FENCE_CLOSE}")
            new_body = "\n".join(lines[:oi]).rstrip() + "\n" + fence
        else:
            new_body = "\n".join(lines[:oi]).rstrip()
        out.append((role, label, header, new_body))
    return out


def evict_oldest_tools(text: str,
                       keep: int = DEFAULT_KEEP_RECENT_TOOLS
                       ) -> tuple[str, list[str]]:
    """Drop oldest ## tool sections beyond the newest `keep`.

    Returns (new_text, dropped_ids) -- ids need fence scrubbing.
    """
    pre, _ = _split_preamble(text)
    sections = _split_sections(text)
    idx = [i for i, s in enumerate(sections) if s[0] == "tool"]
    if len(idx) <= keep:
        return text, []
    drop = set(idx[:len(idx) - keep])
    dropped_ids = _tool_ids([sections[i] for i in sorted(drop)])
    kept = _scrub_fences([s for i, s in enumerate(sections)
                          if i not in drop], dropped_ids)
    return _render(kept, pre), sorted(dropped_ids)


def cap_sections(text: str, cap: int = DEFAULT_SECTION_CAP
                 ) -> tuple[str, int]:
    """Truncate tool bodies over `cap` tokens to head+tail + marker."""
    pre, _ = _split_preamble(text)
    sections = _split_sections(text)
    capped = 0
    out = []
    for role, label, header, body in sections:
        if role == "tool" and count_tokens(body) > cap:
            half = cap // 2
            lines = body.splitlines()
            # keep line-granular head/tail around the token halves
            head, tail = [], []
            n = 0
            for ln in lines:
                n += count_tokens(ln)
                head.append(ln)
                if n >= half:
                    break
            n = 0
            for ln in reversed(lines[len(head):] or lines):
                n += count_tokens(ln)
                tail.append(ln)
                if n >= half:
                    break
            tail.reverse()
            omitted = max(0, len(lines) - len(head) - len(tail))
            body = ("\n".join(head) +
                    f"\n...[{omitted} lines elided by deterministic cap]...\n"
                    + "\n".join(tail))
            capped += 1
        out.append((role, label, header, body))
    if not capped:
        return text, 0
    return _render(out, pre), capped


def prune_deterministic(text: str, *, target: int,
                        keep_recent_tools: int = DEFAULT_KEEP_RECENT_TOOLS,
                        section_cap: int = DEFAULT_SECTION_CAP) -> tuple[str, dict]:
    """Run the ladder until under target. Returns (new_text, report)."""
    report: dict = {"deduped": 0, "evicted": [], "capped": 0,
                    "tokens_before": count_tokens(text),
                    "tokens_after": 0}
    if report["tokens_before"] <= target:
        report["tokens_after"] = report["tokens_before"]
        return text, report
    text, report["deduped"] = dedupe_exact(text)
    if count_tokens(text) <= target:
        report["tokens_after"] = count_tokens(text)
        return text, report
    text, report["evicted"] = evict_oldest_tools(
        text, keep=keep_recent_tools)
    if count_tokens(text) <= target:
        report["tokens_after"] = count_tokens(text)
        return text, report
    text, report["capped"] = cap_sections(text, cap=section_cap)
    report["tokens_after"] = count_tokens(text)
    return text, report
