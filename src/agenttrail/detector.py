"""Detects AI coding agent processes and tracks their process trees.

A "session" is rooted at the highest process in a tree whose name or
command line matches a known agent signature (e.g. the shell launches
`claude`, and everything `claude` spawns belongs to that session, no
matter how deep the descendant chain goes).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import psutil

AGENT_SIGNATURES = [
    "claude",
    "aider",
    "cursor-agent",
    "copilot",
    "windsurf",
    "codex",
    "codeium",
    "opencode",
    "amp",
    "gemini",
]

DETECTION_INTERVAL_SECONDS = 2

# Short signatures like "amp" or "codex" are substrings of unrelated tokens
# (e.g. Chromium's "ScreenCaptureKitStreamPickerSonoma" flag contains "amp").
# Requiring the match to not be sandwiched between two alphanumeric characters
# avoids flagging those while still matching real invocations regardless of
# surrounding punctuation/path separators (e.g. "/usr/bin/aider", "gemini-cli").
_SIGNATURE_PATTERNS = {
    sig: re.compile(r"(?<![a-z0-9])" + re.escape(sig) + r"(?![a-z0-9])") for sig in AGENT_SIGNATURES
}


def _matched_signature(text: str) -> str | None:
    text = text.lower()
    for sig, pattern in _SIGNATURE_PATTERNS.items():
        if pattern.search(text):
            return sig
    return None


def is_agent_process(proc: psutil.Process) -> bool:
    """True if this process's name or command line matches a known agent signature."""
    try:
        if _matched_signature(proc.name()) is not None:
            return True
        cmdline = " ".join(proc.cmdline())
        return _matched_signature(cmdline) is not None
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def resolve_agent_name(proc: psutil.Process) -> str:
    """Return the specific signature a process matched, or 'unknown'."""
    try:
        name = proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        name = ""
    matched = _matched_signature(name)
    if matched is not None:
        return matched

    try:
        cmdline = " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        cmdline = ""
    matched = _matched_signature(cmdline)
    if matched is not None:
        return matched

    return "unknown"


@dataclass
class TrackedProcess:
    pid: int
    ppid: int | None
    session_id: str
    root_pid: int
    started_at: float
    cwd: str
    exe: str
    cmdline: str
    command_id: int | None = None


@dataclass
class SessionEvent:
    kind: str  # session_start | session_end | process_start | process_end
    session_id: str
    pid: int
    data: dict = field(default_factory=dict)


class AgentDetector:
    """Polls the process table each cycle and tracks agent sessions."""

    def __init__(self):
        # pid -> TrackedProcess, for every currently tracked agent-related process
        self.tracked: dict[int, TrackedProcess] = {}
        # root_pid -> session_id, for currently active sessions
        self.session_by_root: dict[int, str] = {}

    def _find_agent_root(
        self, proc: psutil.Process, all_procs: dict[int, psutil.Process]
    ) -> psutil.Process | None:
        """Walk the parent chain and return the oldest ancestor (or the process
        itself) that matches a signature. Returns None if neither the process
        nor any ancestor matches.
        """
        chain = []
        current: psutil.Process | None = proc
        seen: set[int] = set()
        while current is not None and current.pid not in seen:
            seen.add(current.pid)
            chain.append(current)
            try:
                ppid = current.ppid()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                break
            current = all_procs.get(ppid)

        for candidate in reversed(chain):  # oldest ancestor first
            if is_agent_process(candidate):
                return candidate
        return None

    def adopt_existing_sessions(self, db) -> None:
        """Reconcile in-memory state with the database at startup.

        The daemon's in-memory tracking (`session_by_root`, `tracked`) is lost
        every time it restarts, but agent processes started by a previous run
        may still be alive. Without this step, the next poll() would treat
        them as brand-new sessions, creating a duplicate row while the
        original is left dangling forever with `ended_at` unset. Instead, we
        either resume tracking the same session (and its already-open
        commands) or close it out if the process has actually exited.
        """
        for row in db.get_active_sessions():
            root_pid = row["pid"]
            session_id = row["id"]

            proc = None
            if psutil.pid_exists(root_pid):
                try:
                    candidate = psutil.Process(root_pid)
                    if is_agent_process(candidate):
                        proc = candidate
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    proc = None

            if proc is None:
                db.end_session(session_id, time.time())
                continue

            self.session_by_root[root_pid] = session_id
            for command in db.get_commands_for_session(session_id):
                if command["ended_at"] is not None or not psutil.pid_exists(command["pid"]):
                    continue
                self.tracked[command["pid"]] = TrackedProcess(
                    pid=command["pid"],
                    ppid=command["ppid"],
                    session_id=session_id,
                    root_pid=root_pid,
                    started_at=command["started_at"],
                    cwd=command["cwd"],
                    exe=command["exe"],
                    cmdline=command["cmdline"],
                    command_id=command["id"],
                )

    def poll(self, db) -> list[SessionEvent]:
        """Run one detection cycle against the live process table.

        Returns a list of SessionEvent describing what changed, so callers
        (the daemon loop) can react — e.g. start/stop filesystem watches.
        """
        events: list[SessionEvent] = []
        now = time.time()

        all_procs: dict[int, psutil.Process] = {}
        for proc in psutil.process_iter(["pid"]):
            all_procs[proc.pid] = proc

        current_agent_pids: set[int] = set()

        for pid, proc in all_procs.items():
            try:
                root = self._find_agent_root(proc, all_procs)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            if root is None:
                continue

            current_agent_pids.add(pid)
            root_pid = root.pid

            if root_pid not in self.session_by_root:
                try:
                    cwd = root.cwd()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    cwd = ""
                agent_name = resolve_agent_name(root)
                session_id = db.create_session(agent_name, root_pid, now, cwd)
                self.session_by_root[root_pid] = session_id
                events.append(
                    SessionEvent(
                        "session_start",
                        session_id,
                        root_pid,
                        {"cwd": cwd, "agent_name": agent_name},
                    )
                )

            session_id = self.session_by_root[root_pid]

            if pid not in self.tracked:
                try:
                    cmdline = " ".join(proc.cmdline())
                    exe = proc.exe()
                    cwd = proc.cwd()
                    ppid = proc.ppid()
                    started_at = proc.create_time()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    cmdline, exe, cwd, ppid, started_at = "", "", "", None, now

                command_id = db.insert_command(session_id, pid, ppid, cmdline, exe, cwd, started_at)
                self.tracked[pid] = TrackedProcess(
                    pid=pid,
                    ppid=ppid,
                    session_id=session_id,
                    root_pid=root_pid,
                    started_at=started_at,
                    cwd=cwd,
                    exe=exe,
                    cmdline=cmdline,
                    command_id=command_id,
                )
                events.append(SessionEvent("process_start", session_id, pid))

        # Anything previously tracked that's no longer agent-related has ended.
        ended_pids = set(self.tracked) - current_agent_pids
        for pid in ended_pids:
            tracked = self.tracked.pop(pid)
            if tracked.command_id is not None:
                # The process is already gone by the time we notice, so psutil
                # can no longer report its exit code — record it as unknown.
                db.end_command(tracked.command_id, now, None)
            events.append(SessionEvent("process_end", tracked.session_id, pid))

        # A session ends once its root process disappears.
        ended_roots = [rp for rp in self.session_by_root if rp not in current_agent_pids]
        for root_pid in ended_roots:
            session_id = self.session_by_root.pop(root_pid)
            db.end_session(session_id, now)
            events.append(SessionEvent("session_end", session_id, root_pid))

        return events
