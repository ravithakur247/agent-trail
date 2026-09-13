"""Tests for session/command detection logic in agenttrail.detector."""

from __future__ import annotations

import psutil
import pytest

from agenttrail.detector import AgentDetector, is_agent_process, resolve_agent_name


class FakeProcess:
    """Minimal stand-in for psutil.Process, exposing only what the detector uses."""

    def __init__(
        self, pid, ppid, name, cmdline=None, cwd="/tmp/proj", exe="/usr/bin/x", create_time=0.0
    ):
        self.pid = pid
        self._ppid = ppid
        self._name = name
        self._cmdline = cmdline if cmdline is not None else [name]
        self._cwd = cwd
        self._exe = exe
        self._create_time = create_time

    def ppid(self):
        return self._ppid

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmdline

    def cwd(self):
        return self._cwd

    def exe(self):
        return self._exe

    def create_time(self):
        return self._create_time


class FakeDB:
    """Records calls instead of touching SQLite, so detector logic can be
    tested in isolation from db.py."""

    def __init__(self):
        self.sessions = {}
        self.commands = {}
        self._next_command_id = 1

    def create_session(self, agent_name, pid, started_at, cwd):
        session_id = f"session-{len(self.sessions) + 1}"
        self.sessions[session_id] = {
            "id": session_id,
            "agent_name": agent_name,
            "pid": pid,
            "started_at": started_at,
            "cwd": cwd,
            "ended_at": None,
        }
        return session_id

    def end_session(self, session_id, ended_at):
        self.sessions[session_id]["ended_at"] = ended_at

    def get_active_sessions(self):
        return [s for s in self.sessions.values() if s["ended_at"] is None]

    def insert_command(self, session_id, pid, ppid, cmdline, exe, cwd, started_at):
        command_id = self._next_command_id
        self._next_command_id += 1
        self.commands[command_id] = {
            "id": command_id,
            "session_id": session_id,
            "pid": pid,
            "ppid": ppid,
            "cmdline": cmdline,
            "exe": exe,
            "cwd": cwd,
            "started_at": started_at,
            "ended_at": None,
            "exit_code": None,
        }
        return command_id

    def end_command(self, command_id, ended_at, exit_code):
        self.commands[command_id]["ended_at"] = ended_at
        self.commands[command_id]["exit_code"] = exit_code

    def get_commands_for_session(self, session_id):
        return [c for c in self.commands.values() if c["session_id"] == session_id]


@pytest.fixture
def fake_db():
    return FakeDB()


def test_is_agent_process_matches_name():
    assert is_agent_process(FakeProcess(1, 0, "claude-test"))
    assert is_agent_process(FakeProcess(1, 0, "aider"))


def test_is_agent_process_matches_cmdline():
    proc = FakeProcess(1, 0, "python3", cmdline=["python3", "-m", "codex"])
    assert is_agent_process(proc)


def test_is_agent_process_rejects_unrelated():
    assert not is_agent_process(FakeProcess(1, 0, "bash"))
    assert not is_agent_process(FakeProcess(1, 0, "node", cmdline=["node", "server.js"]))


def test_resolve_agent_name():
    assert resolve_agent_name(FakeProcess(1, 0, "claude-test")) == "claude"
    assert resolve_agent_name(FakeProcess(1, 0, "bash")) == "unknown"


def test_poll_detects_new_session_and_descendants(monkeypatch, fake_db):
    root = FakeProcess(100, 1, "claude-test", cwd="/tmp/proj")
    child = FakeProcess(101, 100, "node", cwd="/tmp/proj")
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [root, child])

    detector = AgentDetector()
    events = detector.poll(fake_db)

    assert len(fake_db.sessions) == 1
    session_id = next(iter(fake_db.sessions))
    assert fake_db.sessions[session_id]["agent_name"] == "claude"
    assert fake_db.sessions[session_id]["pid"] == 100

    assert len(fake_db.commands) == 2
    kinds = [e.kind for e in events]
    assert kinds.count("session_start") == 1
    assert kinds.count("process_start") == 2


def test_poll_is_idempotent_for_unchanged_processes(monkeypatch, fake_db):
    root = FakeProcess(100, 1, "claude-test")
    child = FakeProcess(101, 100, "node")
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [root, child])

    detector = AgentDetector()
    detector.poll(fake_db)
    events = detector.poll(fake_db)

    assert len(fake_db.sessions) == 1
    assert len(fake_db.commands) == 2
    assert events == []


def test_poll_ends_process_and_session_when_processes_exit(monkeypatch, fake_db):
    root = FakeProcess(100, 1, "claude-test")
    child = FakeProcess(101, 100, "node")

    detector = AgentDetector()
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [root, child])
    detector.poll(fake_db)

    # Child exits first.
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [root])
    events = detector.poll(fake_db)
    kinds = [e.kind for e in events]
    assert "process_end" in kinds
    assert "session_end" not in kinds
    command_101 = next(c for c in fake_db.commands.values() if c["pid"] == 101)
    assert command_101["ended_at"] is not None

    # Root exits next -> session should end.
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [])
    events = detector.poll(fake_db)
    kinds = [e.kind for e in events]
    assert "session_end" in kinds
    session_id = next(iter(fake_db.sessions))
    assert fake_db.sessions[session_id]["ended_at"] is not None


def test_poll_ignores_unrelated_processes(monkeypatch, fake_db):
    unrelated = FakeProcess(200, 1, "bash")
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [unrelated])

    detector = AgentDetector()
    events = detector.poll(fake_db)

    assert events == []
    assert fake_db.sessions == {}
    assert fake_db.commands == {}


def test_adopt_existing_sessions_resumes_a_still_running_process(monkeypatch, fake_db):
    session_id = fake_db.create_session("claude", 100, 900.0, "/tmp/proj")
    command_id = fake_db.insert_command(
        session_id, 100, 1, "claude", "/usr/bin/claude", "/tmp/proj", 900.0
    )

    root = FakeProcess(100, 1, "claude-test")
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid == 100)
    monkeypatch.setattr(psutil, "Process", lambda pid: root)

    detector = AgentDetector()
    detector.adopt_existing_sessions(fake_db)

    assert detector.session_by_root[100] == session_id
    assert 100 in detector.tracked
    assert detector.tracked[100].command_id == command_id
    assert fake_db.sessions[session_id]["ended_at"] is None


def test_adopt_existing_sessions_closes_out_a_dead_process(monkeypatch, fake_db):
    session_id = fake_db.create_session("claude", 100, 900.0, "/tmp/proj")

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)

    detector = AgentDetector()
    detector.adopt_existing_sessions(fake_db)

    assert 100 not in detector.session_by_root
    assert fake_db.sessions[session_id]["ended_at"] is not None


def test_adopt_existing_sessions_then_poll_does_not_duplicate(monkeypatch, fake_db):
    session_id = fake_db.create_session("claude", 100, 900.0, "/tmp/proj")
    fake_db.insert_command(session_id, 100, 1, "claude", "/usr/bin/claude", "/tmp/proj", 900.0)

    root = FakeProcess(100, 1, "claude-test")
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid == 100)
    monkeypatch.setattr(psutil, "Process", lambda pid: root)

    detector = AgentDetector()
    detector.adopt_existing_sessions(fake_db)

    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [root])
    events = detector.poll(fake_db)

    assert events == []
    assert len(fake_db.sessions) == 1
    assert len(fake_db.commands) == 1
