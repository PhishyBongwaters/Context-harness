"""Approval policy for tool calls (opencode-style).

Reads inside the allowed roots run free. Everything mutating goes
through allow / ask / deny:

  - allow: read inside roots, read-only exec probes, the model's own
    context.md curation, or a remembered approval.
  - deny:  denylist matches (destructive shell, sensitive paths).
    Never prompts, never runs.
  - ask:   human decides per call: approve-this-turn, approve-for-session,
    or deny. Session approvals persist in <session-dir>/approvals.json so
    a future notifier (or a second session later) can build on them;
    turn approvals live only for the current run_turn.

Timeouts are tunable: approval wait (default 120s, enough for a human
to respond to a notification) and exec runtime default/max. CLI flags
override config-file values.

The approver emits `approval-wait` before blocking and `approval-result`
after — the future notification system hooks those events.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
from pathlib import Path

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

APPROVAL_TIMEOUT = 120  # seconds to wait for a human decision
EXEC_TIMEOUT_DEFAULT = 60  # model-omitted exec timeout
EXEC_TIMEOUT_MAX = 300  # hard ceiling even if the model asks for more

# First-token allowlist for probing commands that don't mutate state.
READONLY_EXEC = {
    "ls", "dir", "cat", "type", "echo", "pwd", "whoami", "hostname",
    "python", "node", "git",
}
GIT_READONLY_SUBCOMMANDS = {"status", "diff", "log", "show", "branch", "rev-parse"}

# (pattern, reason) — always denied, never prompted.
DENY_EXEC = [
    (re.compile(r"\brm\s+[^|;&]*-[a-z]*r[a-z]*f\b[^|;&]*(?:^|[\s'\"])(?:/|~|\$HOME|\$USERPROFILE)", re.IGNORECASE),
     "recursive delete of a filesystem root or home directory"),
    (re.compile(r"\b(mkfs|fdisk|diskpart|format\s+[a-z]:)", re.IGNORECASE),
     "disk formatting / partitioning"),
    (re.compile(r"\bdd\b.*\bof=/dev/", re.IGNORECASE),
     "raw disk write via dd"),
    (re.compile(r":\(\)\s*\{\s*:\|\:&\s*\};:", re.IGNORECASE),
     "fork bomb"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b", re.IGNORECASE),
     "system power control"),
    (re.compile(r"(curl|wget|iwr|invoke-webrequest)\b[^|;&]*\|\s*(sh|bash|powershell|pwsh)\b", re.IGNORECASE),
     "piping a download straight into a shell"),
    (re.compile(r"\bchmod\s+-R\s+777\s+/", re.IGNORECASE),
     "recursive world-writable permissions on /"),
    (re.compile(r"\bchown\s+-R\b[^|;&]*/", re.IGNORECASE),
     "recursive ownership change"),
]

# Path components that are never written, even with approval.
DENY_PATH_PARTS = {".ssh", ".gnupg", ".aws"}


def _split_words(command: str) -> list[str]:
    return command.strip().split()


def is_readonly_exec(command: str) -> bool:
    words = _split_words(command)
    if not words:
        return True
    first = Path(words[0]).name.lower().rstrip(".exe")
    if first not in READONLY_EXEC:
        return False
    if first == "git":
        sub = words[1].lower() if len(words) > 1 else ""
        return sub in GIT_READONLY_SUBCOMMANDS
    if first in ("python", "node"):
        # `python --version`-style probes only; scripts may mutate.
        return len(words) == 2 and words[1].strip("-").lower() in ("version", "v")
    return True


def deny_exec_reason(command: str) -> str | None:
    for pat, reason in DENY_EXEC:
        if pat.search(command):
            return reason
    return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_deny_part(path: Path) -> str | None:
    for part in path.parts:
        if part in DENY_PATH_PARTS:
            return part
    return None


class Policy:
    """Pure policy: no I/O, no prompting. Decides allow/ask/deny."""

    def __init__(self, workdir: str, session_dir: str | Path,
                 context_path: str | Path):
        self.workdir = Path(os.path.expanduser(workdir)).resolve()
        self.session_dir = Path(session_dir).resolve()
        self.context_path = Path(context_path).resolve()

    def roots(self) -> list[Path]:
        return [self.workdir, self.session_dir]

    def resolve(self, path: str) -> Path:
        p = Path(os.path.expanduser(path or ""))
        if not p.is_absolute():
            p = self.workdir / p
        return p.resolve()

    def in_roots(self, path: Path) -> bool:
        return any(_is_within(path, r) for r in self.roots())

    def check(self, name: str, args: dict) -> tuple[str, str, str]:
        """Return (decision, reason, session_key)."""
        args = args or {}
        if name == "read":
            target = self.resolve(args.get("path") or "")
            key = f"read:{target}"
            if self.in_roots(target):
                return ALLOW, "read inside allowed roots", key
            return ASK, f"read outside allowed roots: {target}", key
        if name == "list_dir":
            target = self.resolve(args.get("path") or ".")
            key = f"list_dir:{target}"
            if self.in_roots(target):
                return ALLOW, "list inside allowed roots", key
            return ASK, f"list outside allowed roots: {target}", key
        if name == "tokens":
            if not args.get("path"):
                return ALLOW, "counting literal text", "tokens:<literal>"
            target = self.resolve(args.get("path") or "")
            key = f"tokens:{target}"
            if self.in_roots(target):
                return ALLOW, "counting inside allowed roots", key
            return ASK, f"counting outside allowed roots: {target}", key
        if name in ("write", "edit"):
            target = self.resolve(args.get("path") or "")
            key = f"{name}:{target}"
            if target == self.context_path:
                return ALLOW, "model curating its own transcript", key
            bad = _has_deny_part(target)
            if bad:
                return DENY, f"never writes under *.{bad} directories", key
            if self.in_roots(target):
                return ASK, f"{name} {target} (approvable for session)", key
            return ASK, f"{name} outside allowed roots: {target}", key
        if name == "exec":
            cmd = (args.get("command") or "").strip()
            cwd = self.resolve(args.get("workdir") or ".")
            key = f"exec:{cmd}"
            reason = deny_exec_reason(cmd)
            if reason:
                return DENY, reason, key
            if not self.in_roots(cwd):
                return ASK, f"exec outside allowed roots: {cwd}", key
            if is_readonly_exec(cmd):
                return ALLOW, "read-only probe", key
            return ASK, f"exec: {cmd[:120]} (approvable for session)", key
        return ASK, f"unknown tool '{name}'", f"{name}:?"


class StdinPump:
    """Single owner of stdin for the whole process.

    One daemon thread blocks in input(); every reader (REPL, approval
    prompts) takes lines from the same queue with its own timeout. This
    replaces one-thread-per-prompt: a timed-out prompt used to leave a
    zombie input() behind that stole the REPL's next line, making the
    session look dead after you walked away.
    """

    TIMEOUT = object()  # readline() timed out (distinct from EOF=None)

    def __init__(self, start_thread: bool = True):
        self._q: queue.Queue = queue.Queue()
        self._eof = False
        self._started = False
        self._start_thread = start_thread
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if not self._start_thread:
            return
        with self._lock:
            if not self._started:
                self._started = True
                threading.Thread(target=self._run, daemon=True,
                                 name="stdin-pump").start()

    def _run(self) -> None:
        while True:
            try:
                line = input()
            except (EOFError, OSError):
                self._q.put(None)
                return
            self._q.put(line)

    def readline(self, timeout: float | None = None):
        """A line, None on EOF, TIMEOUT on timeout. Blocks when None."""
        if self._eof:
            return None
        self._ensure()
        try:
            line = self._q.get(timeout=timeout)
        except queue.Empty:
            return StdinPump.TIMEOUT
        if line is None:
            self._eof = True
        return line


_pump = StdinPump()


def stdin_line(timeout: float | None = None):
    """Process-shared stdin read (see StdinPump)."""
    return _pump.readline(timeout)


class Approver:
    """Interactive approver with turn + session memory.

    on_event receives `approval-wait` / `approval-result` events for the
    future notification system. input_fn (tests) or pump (default shared
    stdin) supplies prompt answers.
    """

    def __init__(self, session_dir: str | Path, on_event=None,
                 input_fn=None, pump: StdinPump | None = None,
                 approval_timeout: int = APPROVAL_TIMEOUT,
                 auto_approve: bool = False):
        self.dir = Path(session_dir)
        self.file = self.dir / "approvals.json"
        self.on_event = on_event or (lambda kind, data: None)
        self.input_fn = input_fn
        self.pump = pump  # None -> process-shared pump
        self.approval_timeout = max(1, int(approval_timeout))
        self.auto_approve = auto_approve
        self.session_keys: set[str] = self._load()
        self.turn_keys: set[str] = set()

    def _load(self) -> set[str]:
        try:
            if self.file.is_file():
                data = json.loads(self.file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return {str(k) for k in data}
        except (OSError, ValueError):
            pass
        return set()

    def _save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(sorted(self.session_keys),
                                            indent=2) + "\n",
                                 encoding="utf-8")
        except OSError:
            pass

    def new_turn(self) -> None:
        self.turn_keys.clear()

    def remember_session(self, key: str) -> None:
        self.session_keys.add(key)
        self._save()

    def _read_answer(self) -> str | None:
        """One answer line, None on timeout/EOF. Never strands a reader."""
        if self.input_fn is not None:  # tests: legacy threaded read
            box: list[str] = []

            def _read():
                try:
                    box.append(self.input_fn())
                except (EOFError, KeyboardInterrupt, OSError):
                    pass

            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(self.approval_timeout)
            return box[0] if box else None
        pump = self.pump or _pump
        line = pump.readline(self.approval_timeout)
        if line is None or line is StdinPump.TIMEOUT:
            return None
        return line

    def _prompt(self, name: str, args: dict, reason: str) -> str:
        """Blocking prompt with timeout. Returns 'turn'/'session'/'deny'."""
        brief = (args.get("command") or args.get("path") or "").strip()
        if len(brief) > 200:
            brief = brief[:200] + "..."
        print(f"\n[approval needed] {name} {brief}", flush=True)
        print(f"  reason: {reason}", flush=True)
        print(f"  (a)pprove turn / (s)ession / (d)eny "
              f"[{self.approval_timeout}s, default deny]: ", end="",
              flush=True)
        ans = self._read_answer()
        if ans is None:
            return "deny"
        ans = ans.strip().lower()
        if ans in ("a", "approve", "turn", "y", "yes"):
            return "turn"
        if ans in ("s", "session", "always"):
            return "session"
        return "deny"

    def resolve(self, policy: Policy, name: str, args: dict,
                subagent_id: str | None = None
                ) -> tuple[bool, str | None]:
        """Return (approved, denial_message_or_None)."""
        decision, reason, key = policy.check(name, args or {})
        sub = {"subagent_id": subagent_id} if subagent_id else {}
        if decision == ALLOW:
            return True, None
        if decision == DENY:
            self.on_event("approval-result",
                          {"tool": name, "args": args, "decision": "deny",
                           "scope": "never", "reason": reason})
            return False, f"DENIED (never allowed): {reason}"
        if key in self.turn_keys:
            return True, None
        if key in self.session_keys:
            return True, None
        if self.auto_approve:
            self.on_event("approval-result",
                          {"tool": name, "args": args, "decision": "approve",
                           "scope": "auto (--yes)", "reason": reason})
            return True, None
        self.on_event("approval-wait",
                      {"tool": name, "args": args, "reason": reason,
                       "timeout": self.approval_timeout, **sub})
        scope = self._prompt(name, args or {}, reason)
        self.on_event("approval-result",
                      {"tool": name, "args": args,
                       "decision": "approve" if scope != "deny" else "deny",
                       "scope": scope, "reason": reason})
        if scope == "session":
            self.remember_session(key)
            return True, None
        if scope == "turn":
            self.turn_keys.add(key)
            return True, None
        return False, (f"DENIED by user ({reason}). "
                       "Do not retry; work another way or ask the user.")


def clamp_exec_timeout(args: dict, default: int = EXEC_TIMEOUT_DEFAULT,
                       cap: int = EXEC_TIMEOUT_MAX) -> dict:
    """Copy args with the exec timeout defaulted and capped."""
    out = dict(args or {})
    try:
        want = int(out.get("timeout") or default)
    except (TypeError, ValueError):
        want = default
    out["timeout"] = max(1, min(want, max(1, cap)))
    return out
