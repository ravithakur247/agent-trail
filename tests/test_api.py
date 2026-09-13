"""Tests for the FastAPI dashboard API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agenttrail import api
from agenttrail import db as db_module


@pytest.fixture
def db(tmp_path):
    database = db_module.Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def client(db):
    api.app.dependency_overrides[api.get_db] = lambda: db
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_live_sessions_empty(client):
    res = client.get("/api/live")
    assert res.status_code == 200
    assert res.json() == {"sessions": []}


def test_list_sessions_reports_command_count_and_peak_rss(client, db):
    session_id = db.create_session("claude", 123, 1000.0, "/tmp/proj")
    db.insert_command(session_id, 123, 1, "claude --help", "/usr/bin/claude", "/tmp/proj", 1000.0)
    db.insert_metric(session_id, 123, 1001.0, 3.0, 42.0, 100, 200)
    db.end_session(session_id, 1010.0)

    res = client.get("/api/sessions")
    assert res.status_code == 200
    sessions = res.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["agent_name"] == "claude"
    assert sessions[0]["command_count"] == 1
    assert sessions[0]["peak_rss_mb"] == 42.0


def test_session_timeline_merges_commands_and_fs_events(client, db):
    session_id = db.create_session("claude", 123, 1000.0, "/tmp/proj")
    db.insert_command(session_id, 123, 1, "claude --help", "/usr/bin/claude", "/tmp/proj", 1000.0)
    db.insert_fs_event(session_id, 1001.0, "/tmp/proj/file.py", "created")
    db.end_session(session_id, 1010.0)

    res = client.get(f"/api/sessions/{session_id}/timeline")
    assert res.status_code == 200
    body = res.json()
    assert len(body["timeline"]) == 2
    assert body["timeline"][0]["type"] == "command"
    assert body["timeline"][1]["type"] == "fs_event"


def test_timeline_404_for_unknown_session(client):
    res = client.get("/api/sessions/does-not-exist/timeline")
    assert res.status_code == 404


def test_kill_404_for_unknown_session(client):
    res = client.post("/api/sessions/does-not-exist/kill")
    assert res.status_code == 404


def test_index_serves_dashboard_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "agenttrail" in res.text.lower()
