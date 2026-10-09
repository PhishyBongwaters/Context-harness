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
import re

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


def _strip_line_numbers(body: str) -> str:
    """Remove read-tool line-number prefixes for dedupe comparison.

    The read tool numbers lines as f"{n:6d}  {line}" (right-aligned in
    6 chars, two spaces). Stripping that exact shape lets dedupe catch
    overlapping reads of the same file at different offsets. Anything
    else -- including genuine "1: foo" content -- is left alone.
    Comparison-only; the kept section retains its original text.
    """
    lines = body.splitlines()
    stripped = [re.sub(r"^ {0,5}\d{1,6}  ", "", ln) for ln in lines]
    return "\n".join(stripped).strip()


def dedupe_exact(text: str) -> tuple[str, int]:
    """Collapse identical (header, body) assistant/tool sections.

    Keeps the newest occurrence (latest state wins); user sections are
    never deduped. Also collapses by tool ID: if the same ## tool <id>
    appears multiple times (stale/reused ID), keep only the newest
    regardless of body content. Returns (new_text, n_dropped).
    """
    pre, _ = _split_preamble(text)
    sections = _split_sections(text)
    # Key on (role, body): tool headers carry unique call ids, so
    # header-inclusive keys would never match re-run outputs.
    seen: set[tuple[str, str]] = set()
    seen_tool_ids: set[str] = set()
    keep_rev: list[bool] = []
    for role, label, header, body in reversed(sections):
        # Tool ID dedupe: same ID twice = stale, keep newest only.
        if role == "tool" and label:
            if label in seen_tool_ids:
                keep_rev.append(False)
                continue
            seen_tool_ids.add(label)
        # For tool sections, compare with line numbers stripped --
        # read outputs differ only in numbering.
        cmp_body = (_strip_line_numbers(body) if role == "tool"
                    else body.strip())
        key = (role, cmp_body)
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
                        section_cap: int = DEFAULT_SECTION_CAP,
                        archive_dir: str | None = None) -> tuple[str, dict]:
    """Run the ladder until under target. Returns (new_text, report).

    If archive_dir is given and text is >2x target, the oldest half of
    sections is moved to a dated archive .md file in archive_dir.
    The harness does this mechanically -- no model call needed.
    """
    report: dict = {"deduped": 0, "evicted": [], "capped": 0,
                    "tokens_before": count_tokens(text),
                    "tokens_after": 0, "archived": None}
    if report["tokens_before"] <= target:
        report["tokens_after"] = report["tokens_before"]
        return text, report
    # Archive-first: if way over target (>2x), split oldest half to a
    # dated file. Mechanical, no model.
    if archive_dir and report["tokens_before"] > target * 2:
        text, archived_text = _archive_oldest_half(text)
        if archived_text:
            import datetime as _dt
            import os as _os
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            apath = _os.path.join(archive_dir,
                                  f"context.archive-{stamp}.md")
            try:
                _os.makedirs(archive_dir, exist_ok=True)
                with open(apath, "w", encoding="utf-8") as f:
                    f.write(archived_text)
                report["archived"] = apath
            except OSError:
                pass
        if count_tokens(text) <= target:
            report["tokens_after"] = count_tokens(text)
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


def _archive_oldest_half(text: str) -> tuple[str, str | None]:
    """Split text, returning (newest_half, oldest_half_text).

    Pure function -- caller writes the archive file.
    """
    pre, _ = _split_preamble(text)
    sections = _split_sections(text)
    if len(sections) < 4:
        return text, None
    half = len(sections) // 2
    old = sections[:half]
    new = sections[half:]
    return _render(new, pre), _render(old, pre)


_TURN_LABEL_RE = re.compile(r"^t(\d+)$")
# Pointer size margin subtracted from the cap when deciding how many
# turns to move; the real pointer is ~30 tokens.
_POINTER_MARGIN = 100


def window_history(sdir, cap_tokens: int, min_turns: int = 1) -> dict:
    """Deterministically bound history.md (H2).

    Groups history sections by turn stamp. While the file exceeds
    cap_tokens, moves the oldest whole turns to
    archive/history-<date>-t<NNNN>-t<NNNN>.md and leaves a
    ## history-archive pointer in their place. The newest turn is never
    archived; at least min_turns stamped turns are kept. No model calls,
    no summarization -- just windowing with lookback.

    Returns {"windowed": bool, "turns": "t0001-t0002" (or ""),
             "archive": "archive/..." (or None)}.
    """
    import datetime as _dt
    from pathlib import Path
    sdir = Path(sdir)
    hp = sdir / "history.md"
    if not hp.exists():
        return {"windowed": False, "turns": "", "archive": None}
    text = hp.read_text(encoding="utf-8", errors="replace")
    if count_tokens(text) <= cap_tokens:
        return {"windowed": False, "turns": "", "archive": None}

    pre, _ = _split_preamble(text)
    # Group sections by turn stamp. Unstamped sections (shouldn't
    # happen -- the harness stamps everything) sort last and are never
    # archived.
    groups: dict = {}
    for sec in _split_sections(text):
        _role, label, _h, _b = sec
        m = _TURN_LABEL_RE.match(label or "")
        turn = int(m.group(1)) if m else float("inf")
        groups.setdefault(turn, []).append(sec)
    ordered = sorted(groups.items(), key=lambda kv: kv[0])

    def rendered(grps) -> str:
        secs = [s for _, ss in grps for s in ss]
        return _render(secs, pre)

    stamped = lambda grps: [g for g in grps if g[0] != float("inf")]
    kept = list(ordered)
    archived = []
    while (len(stamped(kept)) > min_turns
           and count_tokens(rendered(kept)) > cap_tokens - _POINTER_MARGIN):
        archived.append(kept.pop(0))
    if not archived:
        return {"windowed": False, "turns": "", "archive": None}

    first, last = archived[0][0], archived[-1][0]
    tag = f"t{first:04d}-t{last:04d}"
    stamp = _dt.datetime.now().strftime("%Y%m%d")
    adir = sdir / "archive"
    adir.mkdir(parents=True, exist_ok=True)
    dest = adir / f"history-{stamp}-{tag}.md"
    n = 2
    while dest.exists():
        dest = adir / f"history-{stamp}-{tag}-{n}.md"
        n += 1
    dest.write_text(
        _render([s for _, ss in archived for s in ss], ""),
        encoding="utf-8")

    ptr = ("history-archive", None, "## history-archive",
           f"archive: archive/{dest.name}\n"
           f"turns: t{first:04d}-t{last:04d}\n")
    kept_secs = [ptr] + [s for _, ss in kept for s in ss]
    hp.write_text(_render(kept_secs, pre), encoding="utf-8")
    return {"windowed": True,
            "turns": f"t{first:04d}-t{last:04d}",
            "archive": f"archive/{dest.name}"}
