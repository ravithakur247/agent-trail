"""SQLite persistence layer for AgentTrail.

All reads and writes go through the `Database` class, which serializes
access with a lock since the daemon touches it from both the main
detection loop and the filesystem-watcher thread.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

DB_DIR = Path.home() / ".agenttrail"
DB_PATH = DB_DIR / "agenttrail.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    pid INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    cwd TEXT
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    ppid INTEGER,
    cmdline TEXT,
    exe TEXT,
    cwd TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    exit_code INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    ts REAL NOT NULL,
    cpu_pct REAL,
    rss_mb REAL,
    disk_read_bytes INTEGER,
    disk_write_bytes INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS fs_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts REAL NOT NULL,
    path TEXT NOT NULL,
    event_type TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_commands_session_ts ON commands(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_metrics_session_ts ON metrics(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
CREATE INDEX IF NOT EXISTS idx_fs_events_session_ts ON fs_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_fs_events_ts ON fs_events(ts);
"""


class Database:
    """Thread-safe wrapper around the AgentTrail SQLite database."""

    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- sessions ---------------------------------------------------------
    def create_session(self, agent_name: str, pid: int, started_at: float, cwd: str) -> str:
        session_id = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, agent_name, pid, started_at, cwd) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, agent_name, pid, started_at, cwd),
            )
            self._conn.commit()
        return session_id

    def end_session(self, session_id: str, ended_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (ended_at, session_id)
            )
            self._conn.commit()

    def get_session(self, session_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            return cur.fetchone()

    def get_active_sessions(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC"
            )
            return cur.fetchall()

    def get_past_sessions(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM sessions WHERE ended_at IS NOT NULL ORDER BY started_at DESC"
            )
            return cur.fetchall()

    def get_all_sessions(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM sessions ORDER BY started_at DESC")
            return cur.fetchall()

    # -- commands -----------------------------------------------------------
    def insert_command(
        self,
        session_id: str,
        pid: int,
        ppid: int | None,
        cmdline: str,
        exe: str,
        cwd: str,
        started_at: float,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO commands
                   (session_id, pid, ppid, cmdline, exe, cwd, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, pid, ppid, cmdline, exe, cwd, started_at),
            )
            self._conn.commit()
            return cur.lastrowid

    def end_command(self, command_id: int, ended_at: float, exit_code: int | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE commands SET ended_at = ?, exit_code = ? WHERE id = ?",
                (ended_at, exit_code, command_id),
            )
            self._conn.commit()

    def get_commands_for_session(self, session_id: str) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM commands WHERE session_id = ? ORDER BY started_at",
                (session_id,),
            )
            return cur.fetchall()

    def count_commands_for_session(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) as c FROM commands WHERE session_id = ?", (session_id,)
            )
            return cur.fetchone()["c"]

    # -- metrics --------------------------------------------------------
    def insert_metric(
        self,
        session_id: str,
        pid: int,
        ts: float,
        cpu_pct: float | None,
        rss_mb: float | None,
        disk_read_bytes: int | None,
        disk_write_bytes: int | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO metrics
                   (session_id, pid, ts, cpu_pct, rss_mb, disk_read_bytes, disk_write_bytes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, pid, ts, cpu_pct, rss_mb, disk_read_bytes, disk_write_bytes),
            )
            self._conn.commit()

    def get_metrics_for_session(self, session_id: str) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM metrics WHERE session_id = ? ORDER BY ts", (session_id,)
            )
            return cur.fetchall()

    def get_latest_metrics_for_session(self, session_id: str) -> list[sqlite3.Row]:
        """Latest metric row per pid for a session (used by the live view)."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT m.* FROM metrics m
                   WHERE m.session_id = ?
                   AND m.id = (
                       SELECT id FROM metrics m2
                       WHERE m2.session_id = m.session_id AND m2.pid = m.pid
                       ORDER BY ts DESC LIMIT 1
                   )""",
                (session_id,),
            )
            return cur.fetchall()

    def get_peak_rss_for_session(self, session_id: str) -> float | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT MAX(rss_mb) as peak FROM metrics WHERE session_id = ?", (session_id,)
            )
            row = cur.fetchone()
            return row["peak"] if row else None

    # -- filesystem events ------------------------------------------------
    def insert_fs_event(self, session_id: str, ts: float, path: str, event_type: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO fs_events (session_id, ts, path, event_type) VALUES (?, ?, ?, ?)",
                (session_id, ts, path, event_type),
            )
            self._conn.commit()

    def get_fs_events_for_session(self, session_id: str) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM fs_events WHERE session_id = ? ORDER BY ts", (session_id,)
            )
            return cur.fetchall()


def merge_timeline(commands: Iterable[Any], fs_events: Iterable[Any]) -> list[dict]:
    """Merge a session's commands and filesystem events into one chronological
    list of plain dicts, sorted by timestamp.

    Accepts sqlite3.Row objects or plain dicts/mappings for either input.
    """
    entries: list[dict] = []

    for c in commands:
        entries.append(
            {
                "type": "command",
                "ts": c["started_at"],
                "pid": c["pid"],
                "cmdline": c["cmdline"],
                "cwd": c["cwd"],
                "ended_at": c["ended_at"],
                "exit_code": c["exit_code"],
            }
        )

    for e in fs_events:
        entries.append(
            {
                "type": "fs_event",
                "ts": e["ts"],
                "path": e["path"],
                "event_type": e["event_type"],
            }
        )

    entries.sort(key=lambda entry: entry["ts"])
    return entries
