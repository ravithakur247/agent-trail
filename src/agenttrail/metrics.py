"""Resource metrics collection for tracked agent processes."""

from __future__ import annotations

import time

import psutil

METRICS_INTERVAL_SECONDS = 5


class MetricsCollector:
    """Samples CPU / RSS / disk I/O for individual processes.

    `psutil.Process.cpu_percent()` always returns 0.0 on the first call for a
    given process, because it needs a previous sample to compute a delta.
    We prime each newly-seen pid with a throwaway call and discard that
    first reading rather than recording a misleading 0%.
    """

    def __init__(self):
        self._primed: set[int] = set()

    def prime(self, pid: int) -> None:
        if pid in self._primed:
            return
        try:
            psutil.Process(pid).cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
        finally:
            self._primed.add(pid)

    def forget(self, pid: int) -> None:
        self._primed.discard(pid)

    def sample(self, pid: int) -> dict | None:
        """Return a metrics dict for `pid`, or None if it can't be sampled
        (not yet primed, or the process has since exited).
        """
        if pid not in self._primed:
            self.prime(pid)
            return None

        try:
            proc = psutil.Process(pid)
            cpu_pct = proc.cpu_percent(interval=None)
            rss_mb = proc.memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None

        disk_read = disk_write = None
        try:
            # io_counters() doesn't exist at all on some platforms (e.g. macOS
            # doesn't expose it on Process), rather than raising
            # NotImplementedError, so guard with getattr instead of a plain call.
            io_counters = getattr(proc, "io_counters", None)
            if io_counters is not None:
                io = io_counters()
                disk_read = io.read_bytes
                disk_write = io.write_bytes
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            NotImplementedError,
        ):
            pass  # not available on this platform, or insufficient permissions

        return {
            "pid": pid,
            "ts": time.time(),
            "cpu_pct": cpu_pct,
            "rss_mb": rss_mb,
            "disk_read_bytes": disk_read,
            "disk_write_bytes": disk_write,
        }

    def collect_for_pids(self, pids: list[int]) -> list[dict]:
        results = []
        for pid in pids:
            sample = self.sample(pid)
            if sample is not None:
                results.append(sample)
        return results
