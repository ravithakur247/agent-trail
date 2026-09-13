"""The AgentTrail background daemon: detection, metrics, and fs watching.

Run directly as `python -m agenttrail.daemon`; `agenttrail start` launches
this as a detached background process.
"""

from __future__ import annotations

import signal
import time

from . import db as db_module
from .detector import DETECTION_INTERVAL_SECONDS, AgentDetector
from .fs_watcher import FsWatcherManager
from .metrics import METRICS_INTERVAL_SECONDS, MetricsCollector

_running = True


def _handle_stop(signum, frame):
    global _running
    _running = False


def run() -> None:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    database = db_module.Database()
    detector = AgentDetector()
    detector.adopt_existing_sessions(database)
    metrics = MetricsCollector()
    fs_watcher = FsWatcherManager()

    for root_pid, session_id in detector.session_by_root.items():
        session = database.get_session(session_id)
        if session is not None:
            fs_watcher.watch_session(session_id, session["cwd"])
    for pid in detector.tracked:
        metrics.prime(pid)

    last_metrics_at = 0.0

    try:
        while _running:
            events = detector.poll(database)

            for event in events:
                if event.kind == "session_start":
                    fs_watcher.watch_session(event.session_id, event.data.get("cwd", ""))
                elif event.kind == "session_end":
                    fs_watcher.unwatch_session(event.session_id)
                elif event.kind == "process_start":
                    metrics.prime(event.pid)
                elif event.kind == "process_end":
                    metrics.forget(event.pid)

            for fs_event in fs_watcher.drain_events():
                database.insert_fs_event(
                    fs_event["session_id"],
                    fs_event["ts"],
                    fs_event["path"],
                    fs_event["event_type"],
                )

            now = time.time()
            if now - last_metrics_at >= METRICS_INTERVAL_SECONDS:
                last_metrics_at = now
                for pid, tracked in list(detector.tracked.items()):
                    sample = metrics.sample(pid)
                    if sample is not None:
                        database.insert_metric(
                            tracked.session_id,
                            pid,
                            sample["ts"],
                            sample["cpu_pct"],
                            sample["rss_mb"],
                            sample["disk_read_bytes"],
                            sample["disk_write_bytes"],
                        )

            time.sleep(DETECTION_INTERVAL_SECONDS)
    finally:
        fs_watcher.stop()
        database.close()


if __name__ == "__main__":
    run()
