"""Filesystem watching for active agent session working directories.

Only the cwd of each currently-active agent session is watched — never
$HOME or any static, config-driven path. Only paths and event types are
recorded; file contents are never read or logged.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class SessionFsHandler(FileSystemEventHandler):
    """Forwards filesystem events for one session onto a shared queue."""

    def __init__(self, session_id: str, event_queue: "queue.Queue"):
        super().__init__()
        self.session_id = session_id
        self._queue = event_queue

    def _emit(self, event_type: str, path: str) -> None:
        self._queue.put(
            {
                "session_id": self.session_id,
                "ts": time.time(),
                "path": path,
                "event_type": event_type,
            }
        )

    def on_created(self, event):
        if not event.is_directory:
            self._emit("created", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._emit("modified", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._emit("deleted", event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._emit("deleted", event.src_path)
            self._emit("created", event.dest_path)


class FsWatcherManager:
    """Owns a single watchdog Observer and one recursive watch per active session cwd."""

    def __init__(self):
        self.observer = Observer()
        self.observer.start()
        self._watches: dict[str, object] = {}  # session_id -> watchdog watch handle
        self.events: "queue.Queue" = queue.Queue()

    def watch_session(self, session_id: str, cwd: str) -> None:
        if not cwd or session_id in self._watches or not Path(cwd).is_dir():
            return
        handler = SessionFsHandler(session_id, self.events)
        watch = self.observer.schedule(handler, cwd, recursive=True)
        self._watches[session_id] = watch

    def unwatch_session(self, session_id: str) -> None:
        watch = self._watches.pop(session_id, None)
        if watch is not None:
            self.observer.unschedule(watch)

    def drain_events(self) -> list[dict]:
        drained = []
        while True:
            try:
                drained.append(self.events.get_nowait())
            except queue.Empty:
                break
        return drained

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join(timeout=5)
