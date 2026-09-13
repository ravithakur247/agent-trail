"""FastAPI application serving the AgentTrail dashboard and JSON API."""

from __future__ import annotations

from pathlib import Path

import psutil
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db as db_module

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AgentTrail")

_db: db_module.Database | None = None


def get_db() -> db_module.Database:
    """FastAPI dependency returning the shared Database instance.

    Tests override this via `app.dependency_overrides[get_db]` to point at a
    temporary database instead of the real `~/.agenttrail/agenttrail.db`.
    """
    global _db
    if _db is None:
        _db = db_module.Database()
    return _db


def _kill_process_tree(pid: int) -> bool:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False

    children = parent.children(recursive=True)
    procs = children + [parent]
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass

    _, alive = psutil.wait_procs(procs, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    return True


def _child_count(pid: int) -> int:
    try:
        return len(psutil.Process(pid).children(recursive=True))
    except psutil.NoSuchProcess:
        return 0


@app.get("/api/live")
def live_sessions(database: db_module.Database = Depends(get_db)):
    sessions = []
    for row in database.get_active_sessions():
        metrics = database.get_latest_metrics_for_session(row["id"])
        cpu_total = sum(m["cpu_pct"] or 0 for m in metrics)
        rss_total = sum(m["rss_mb"] or 0 for m in metrics)
        sessions.append(
            {
                "id": row["id"],
                "agent_name": row["agent_name"],
                "pid": row["pid"],
                "started_at": row["started_at"],
                "cwd": row["cwd"],
                "cpu_pct": cpu_total,
                "rss_mb": rss_total,
                "child_count": _child_count(row["pid"]),
            }
        )
    return {"sessions": sessions}


@app.get("/api/sessions")
def list_sessions(database: db_module.Database = Depends(get_db)):
    sessions = []
    for row in database.get_past_sessions():
        duration = (row["ended_at"] or 0) - row["started_at"]
        sessions.append(
            {
                "id": row["id"],
                "agent_name": row["agent_name"],
                "pid": row["pid"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "duration_seconds": duration,
                "peak_rss_mb": database.get_peak_rss_for_session(row["id"]),
                "command_count": database.count_commands_for_session(row["id"]),
                "cwd": row["cwd"],
            }
        )
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}/timeline")
def session_timeline(session_id: str, database: db_module.Database = Depends(get_db)):
    session = database.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    commands = database.get_commands_for_session(session_id)
    fs_events = database.get_fs_events_for_session(session_id)
    metrics = database.get_metrics_for_session(session_id)

    return {
        "session": dict(session),
        "timeline": db_module.merge_timeline(commands, fs_events),
        "metrics": [dict(m) for m in metrics],
    }


@app.post("/api/sessions/{session_id}/kill")
def kill_session(session_id: str, database: db_module.Database = Depends(get_db)):
    session = database.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    killed = _kill_process_tree(session["pid"])
    return {"killed": killed}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
