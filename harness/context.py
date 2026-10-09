"""The context file: the model's live transcript, as a file.

CLM-style: the file IS the conversation. The harness reads it before every
model call and sends the parsed messages; it appends new turns (assistant
replies, tool results, user messages) to the end. The model restructures it
freely with its ordinary write/edit tools.

Section format (one header line per section):

    ## user
    ## assistant
    ## tool <tool_call_id>
    ## sat <name>          assembled satellite content (T3)

An assistant section may end with a ```tool-calls fenced JSON block listing
that reply's tool calls. Anything before the first ## header is a preamble:
kept in the file, never sent to the model.

Token counting prefers tiktoken (cl100k_base) when installed, else falls
back to a chars/4 heuristic. The counter reports which estimator is active
so the model knows how much to trust the meter.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ESTIMATOR = "tiktoken/cl100k_base"

_ENC = None


def _encoding():
    """cl100k_base encoder, loaded lazily so the BPE download happens on
    first use rather than at import."""
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
        except ImportError:
            raise RuntimeError(
                "tiktoken is required: pip install -r requirements.txt")
        _ENC = tiktoken.get_encoding("cl100k_base")
    return _ENC


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


@dataclass
class Budget:
    hard: int
    soft: int
    # Full model context window for DISPLAY (None when unknown).
    # Enforcement (status, prune trigger, tints) always uses hard/soft;
    # every readout shows window when known so the context is never
    # confused with the prune trigger.
    window: int | None = None

    def denom(self) -> int:
        """Display denominator: the window, else the hard budget."""
        return self.window or self.hard

    def status(self, tokens: int) -> str:
        if tokens >= self.hard:
            return "over"
        if tokens >= self.soft:
            return "warn"
        return "ok"

    def meter_line(self, tokens: int) -> str:
        pct = 100.0 * tokens / self.hard if self.hard else 0
        return (f"[context budget: {tokens:,}/{self.hard:,} tokens "
                f"({pct:.0f}%) | estimator: {ESTIMATOR}]")


TRANSCRIPT_TEMPLATE = """# Context

This file is your live context -- the transcript itself. The harness reads
this file before every model call and sends it as the conversation. New
turns (your replies, tool results, user messages) are appended to the end
automatically; restructure anything above with your write/edit tools.

Sections start with a `## ` header: `## user`, `## assistant`,
`## tool <id>`. Keep those headers parseable and the transcript stays yours.
"""

_HEADER_RE = re.compile(
    r"^##[ \t]+(user|assistant|tool|sat|episode)(?:[ \t]+(\S+))?.*$")
_FENCE_OPEN = "```tool-calls"
_FENCE_CLOSE = "```"


def stamp(turn: int) -> str:
    """Header stamp for harness-owned turn numbers, e.g. 't0042'.

    Stamps ride in `## ` headers (`## user t0042`); the parser strips
    them on the wire (spec decision 4).
    """
    return f"t{int(turn):04d}"


def render_user(content: str) -> str:
    return f"## user\n{content.rstrip()}\n"


def render_history_user(content: str, turn: int) -> str:
    return f"## user {stamp(turn)}\n{content.rstrip()}\n"


def render_history_assistant(content: str, turn: int) -> str:
    return f"## assistant {stamp(turn)}\n{content.rstrip()}\n"


def render_assistant(content: str | None,
                     tool_calls: list[dict] | None) -> str:
    sec = f"## assistant\n{(content or '').rstrip()}"
    if tool_calls:
        fence = json.dumps([{"id": tc["id"], "name": tc["name"],
                             "arguments": tc.get("arguments") or {}}
                            for tc in tool_calls])
        sec += f"\n{_FENCE_OPEN}\n{fence}\n{_FENCE_CLOSE}"
    return sec + "\n"


def render_tool(tool_call_id: str, content: str) -> str:
    return f"## tool {tool_call_id}\n{content.rstrip()}\n"


def _split_tool_calls(body: str) -> tuple[str, list[dict]]:
    """Pull a trailing ```tool-calls JSON block out of an assistant section.

    Falls back to parsing <atem:> XML fragments (some models emit these
    instead of the fenced JSON despite prompt instructions).
    """
    lines = body.splitlines()
    try:
        open_idx = lines.index(_FENCE_OPEN)
    except ValueError:
        # No fenced block -- try XML fallback on the whole body
        return _split_atem_xml(body)
    try:
        close_idx = lines.index(_FENCE_CLOSE, open_idx + 1)
    except ValueError:
        return body, []
    raw = "\n".join(lines[open_idx + 1:close_idx])
    try:
        calls = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return body, []
    if not isinstance(calls, list):
        return body, []
    text = "\n".join(lines[:open_idx]).rstrip()
    return text, calls


def _split_atem_xml(body: str) -> tuple[str, list[dict]]:
    """Fallback: extract atem-style tool calls from malformed model output."""
    tag_open = chr(60) + 'atem:'  # '<atem:' without triggering XML parsing
    if tag_open + 'parameter' not in body:
        return body, []
    # Extract invoke name if present
    invoke_m = re.search(tag_open + r'invoke\s+name="([^"]+)"', body)
    tool_name = invoke_m.group(1) if invoke_m else None
    # Extract parameters: name="X">value (value runs until next < or end)
    params = {}
    ptag = tag_open + 'parameter'
    for m in re.finditer(ptag + r'\s+name="([^"]+)"' + r'>([^<]*)', body):
        params[m.group(1)] = m.group(2).strip()
    if not params:
        return body, []
    if not tool_name:
        tool_name = 'read' if 'path' in params else 'exec'
    call_id = 'atem-' + str(abs(hash(body)) % 1000000)
    calls = [{'id': call_id, 'name': tool_name, 'arguments': params}]
    # Strip the XML cruft from displayed text
    text = re.sub(r'\s*' + tag_open + r'[^>]*>.*?(?=' + tag_open + r'|\Z)',
                  '', body, flags=re.DOTALL)
    text = re.sub(r'</atem:(invoke|function_calls)>', '', text)
    text = text.replace('">', '').strip()
    return text, calls


def sanitize_assistant_content(content: str | None) -> str | None:
    """Drop echoed transcript structure from a reply before storing it.

    Chat-templated local models often mimic the file format, emitting
    `## user` / `## assistant` headers or ```tool-calls fences in their
    text. Stored verbatim, those lines would parse back as phantom
    sections (or phantom tool calls). The harness owns structure, so
    echoed structure lines are removed; body text is kept.
    """
    if not content:
        return content
    kept: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if line.strip() == _FENCE_OPEN:
            in_fence = True
            continue
        if in_fence:
            if line.strip() == _FENCE_CLOSE:
                in_fence = False
            continue
        if _HEADER_RE.match(line):
            continue
        kept.append(line)
    text = "\n".join(kept).strip()
    return text or None


def _split_sections(text: str) -> list[tuple[str, str | None, str, str]]:
    """Split the file into (role, label, header_line, body) sections.

    Anything before the first ## header is preamble and ignored.
    """
    sections: list[tuple[str, str | None, str, str]] = []
    cur: list[str] | None = None
    cur_role: str | None = None
    cur_label: str | None = None
    cur_header: str = ""

    def flush():
        if cur_role is not None:
            sections.append((cur_role, cur_label, cur_header,
                             "\n".join(cur or [])))

    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            flush()
            cur_role, cur_label = m.group(1), m.group(2)
            cur_header, cur = line.strip(), []
        elif cur_role is not None:
            cur.append(line)
        # else: preamble line, ignored
    flush()
    return sections


def parse_transcript(text: str) -> list[dict]:
    """Parse the context file back into internal (OpenAI-style) messages.

    Rules: preamble before the first ## header is ignored; a ## tool
    section whose id matches no earlier assistant tool call is dropped
    (orphan results are never sent to the provider).
    """
    messages: list[dict] = []
    seen_ids: set[str] = set()
    for role, label, _header, body in _split_sections(text):
        body = body.strip()
        if role == "user":
            messages.append({"role": "user", "content": body})
        elif role == "assistant":
            content, tool_calls = _split_tool_calls(body)
            messages.append({"role": "assistant",
                             "content": content or None,
                             "tool_calls": tool_calls or None})
            for tc in tool_calls:
                if isinstance(tc, dict) and tc.get("id"):
                    seen_ids.add(tc["id"])
        elif role == "tool":
            if label and label in seen_ids:
                messages.append({"role": "tool", "tool_call_id": label,
                                 "content": body})
            # orphan tool result: dropped, never sent
        elif role == "sat":
            # Assembled satellite content (T3): satellite files wrapped
            # by the harness as `## sat <name>`. Sent as user-role so
            # the model sees curated state as conversation context.
            messages.append({"role": "user", "content": body})
        elif role == "episode":
            # Episode pointer (T7): `## episode t<NNNN>` in history.md.
            # Cheap archive pointer, sent as a plain user-role message
            # (Rob's decision 2026-10-09).
            messages.append({"role": "user", "content": body})
    return messages


def diff_transcripts(old: str, new: str) -> dict:
    """Mechanical diff of two context-file states.

    Compares sections by (header, body); reordered-but-identical sections
    produce no diff. Returns removed/added section headers with token
    counts plus the net token delta (positive recovered = context freed).
    """
    from collections import Counter

    def keyed(text: str) -> list[tuple[str, str]]:
        return [(h, b.strip()) for _, _, h, b in _split_sections(text)]

    old_c, new_c = Counter(keyed(old)), Counter(keyed(new))
    removed = list((old_c - new_c).elements())
    added = list((new_c - old_c).elements())
    tb, ta = count_tokens(old), count_tokens(new)
    return {
        "removed": [{"header": h, "tokens": count_tokens(b)}
                    for h, b in removed],
        "added": [{"header": h, "tokens": count_tokens(b)}
                  for h, b in added],
        "tokens_before": tb,
        "tokens_after": ta,
        "recovered": tb - ta,
    }


class ContextFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> str:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(TRANSCRIPT_TEMPLATE, encoding="utf-8")
            return TRANSCRIPT_TEMPLATE
        return self.path.read_text(encoding="utf-8", errors="replace")

    def save(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def append(self, text: str) -> None:
        current = self.load()
        if current and not current.endswith("\n"):
            current += "\n"
        self.save(current + ("\n" if current else "") + text)

    def tokens(self) -> int:
        return count_tokens(self.load())
