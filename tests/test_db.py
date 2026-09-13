"""Tests for agenttrail.db: read/write functions and timeline merging."""

from __future__ import annotations

import pytest

from agenttrail import db as db_module


@pytest.fixture
def db(tmp_path):
    database = db_module.Database(tmp_path / "test.db")
    yield database
    database.close()


def test_create_and_get_session(db):
    session_id = db.create_session("claude", 100, 1000.0, "/tmp/proj")
    row = db.get_session(session_id)

    assert row["agent_name"] == "claude"
    assert row["pid"] == 100
    assert row["cwd"] == "/tmp/proj"
    assert row["ended_at"] is None


def test_end_session_moves_it_to_past_sessions(db):
    session_id = db.create_session("aider", 200, 1000.0, "/tmp/proj")

    assert len(db.get_active_sessions()) == 1
    assert len(db.get_past_sessions()) == 0

    db.end_session(session_id, 1050.0)

    assert len(db.get_active_sessions()) == 0
    past = db.get_past_sessions()
    assert len(past) == 1
    assert past[0]["ended_at"] == 1050.0


def test_insert_and_end_command(db):
    session_id = db.create_session("claude", 100, 1000.0, "/tmp/proj")
    command_id = db.insert_command(
        session_id, 101, 100, "claude --help", "/usr/bin/claude", "/tmp/proj", 1000.0
    )

    commands = db.get_commands_for_session(session_id)
    assert len(commands) == 1
    assert commands[0]["ended_at"] is None

    db.end_command(command_id, 1005.0, 0)

    commands = db.get_commands_for_session(session_id)
    assert commands[0]["ended_at"] == 1005.0
    assert commands[0]["exit_code"] == 0
    assert db.count_commands_for_session(session_id) == 1


def test_insert_metric_and_latest_lookup(db):
    session_id = db.create_session("claude", 100, 1000.0, "/tmp/proj")
    db.insert_metric(session_id, 100, 1000.0, 5.0, 50.0, 1024, 2048)
    db.insert_metric(session_id, 100, 1005.0, 8.0, 55.0, 2048, 4096)

    all_metrics = db.get_metrics_for_session(session_id)
    assert len(all_metrics) == 2

    latest = db.get_latest_metrics_for_session(session_id)
    assert len(latest) == 1
    assert latest[0]["cpu_pct"] == 8.0
    assert latest[0]["rss_mb"] == 55.0

    assert db.get_peak_rss_for_session(session_id) == 55.0


def test_insert_and_get_fs_events(db):
    session_id = db.create_session("claude", 100, 1000.0, "/tmp/proj")
    db.insert_fs_event(session_id, 1001.0, "/tmp/proj/a.py", "created")
    db.insert_fs_event(session_id, 1002.0, "/tmp/proj/a.py", "modified")
    db.insert_fs_event(session_id, 1003.0, "/tmp/proj/a.py", "deleted")

    events = db.get_fs_events_for_session(session_id)
    assert [e["event_type"] for e in events] == ["created", "modified", "deleted"]


def test_history_survives_reopening_the_same_file(tmp_path):
    path = tmp_path / "persist.db"
    db1 = db_module.Database(path)
    session_id = db1.create_session("claude", 100, 1000.0, "/tmp/proj")
    db1.end_session(session_id, 1050.0)
    db1.close()

    db2 = db_module.Database(path)
    past = db2.get_past_sessions()
    assert len(past) == 1
    assert past[0]["id"] == session_id
    db2.close()


def test_merge_timeline_sorts_commands_and_fs_events_chronologically():
    commands = [
        {
            "started_at": 1000.0,
            "pid": 100,
            "cmdline": "claude --help",
            "cwd": "/tmp/proj",
            "ended_at": 1002.0,
            "exit_code": 0,
        }
    ]
    fs_events = [
        {"ts": 999.0, "path": "/tmp/proj/before.py", "event_type": "created"},
        {"ts": 1001.0, "path": "/tmp/proj/after.py", "event_type": "created"},
    ]

    merged = db_module.merge_timeline(commands, fs_events)

    assert [entry["ts"] for entry in merged] == [999.0, 1000.0, 1001.0]
    assert merged[0]["type"] == "fs_event"
    assert merged[1]["type"] == "command"
    assert merged[2]["type"] == "fs_event"


def test_merge_timeline_handles_empty_inputs():
    assert db_module.merge_timeline([], []) == []
