import linecache
import os
import subprocess
import sys
import threading
import time
import tracemalloc
from collections import deque
from datetime import datetime, timezone
from typing import Any


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
        if value < 0:
            return default
        return value
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value < minimum:
            return default
        return value
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return default


class MemoryTelemetry:
    """Lightweight process memory telemetry with ring-buffer history."""

    def __init__(
        self,
        *,
        sample_interval_s: float | None = None,
        log_interval_s: float | None = None,
        max_samples: int | None = None,
        trace_frames: int | None = None,
        enable_tracemalloc: bool | None = None,
    ):
        self.sample_interval_s = (
            sample_interval_s
            if sample_interval_s is not None
            else _env_float("DASHSCRIBE_MEM_SAMPLE_INTERVAL", 30.0)
        )
        self.log_interval_s = (
            log_interval_s
            if log_interval_s is not None
            else _env_float("DASHSCRIBE_MEM_LOG_INTERVAL", 300.0)
        )
        self.max_samples = (
            max_samples
            if max_samples is not None
            else _env_int("DASHSCRIBE_MEM_MAX_SAMPLES", 720, minimum=16)
        )
        self.trace_frames = (
            trace_frames
            if trace_frames is not None
            else _env_int("DASHSCRIBE_MEM_TRACE_FRAMES", 8, minimum=1)
        )
        self.enable_tracemalloc = (
            enable_tracemalloc
            if enable_tracemalloc is not None
            else _env_bool("DASHSCRIBE_MEM_TRACE", False)
        )

        self.sample_interval_s = max(1.0, float(self.sample_interval_s))
        self.log_interval_s = max(0.0, float(self.log_interval_s))
        self.max_samples = max(16, int(self.max_samples))
        self.trace_frames = max(1, int(self.trace_frames))

        self._pid = os.getpid()
        self._samples: deque[dict[str, Any]] = deque(maxlen=self.max_samples)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            if self.enable_tracemalloc:
                self._ensure_tracemalloc()

            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

        self.capture_now()

    def stop(self):
        self._stop_event.set()
        thread = None
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def capture_now(self) -> dict[str, Any]:
        sample = self._collect_sample()
        with self._lock:
            self._samples.append(sample)
        return sample

    def get_report(
        self,
        *,
        include_top: bool = False,
        top_limit: int = 20,
        history: int = 120,
        refresh: bool = True,
    ) -> dict[str, Any]:
        if include_top:
            self._ensure_tracemalloc()

        if refresh:
            self.capture_now()

        with self._lock:
            samples = list(self._samples)

        if not samples:
            samples = [self.capture_now()]

        history = max(0, int(history))
        if history > 0:
            samples_view = samples[-history:]
        else:
            samples_view = []

        latest = samples[-1]
        first = samples[0]
        report: dict[str, Any] = {
            "process_id": self._pid,
            "sample_interval_seconds": self.sample_interval_s,
            "sample_count": len(samples),
            "tracemalloc_enabled": tracemalloc.is_tracing(),
            "latest": latest,
            "growth": {
                "rss_bytes_since_start": self._delta(first, latest, "rss_bytes"),
                "python_allocated_bytes_since_start": self._delta(
                    first, latest, "python_allocated_bytes"
                ),
                "rss_bytes_5m": self._delta_for_window(samples, "rss_bytes", 300.0),
                "python_allocated_bytes_5m": self._delta_for_window(
                    samples,
                    "python_allocated_bytes",
                    300.0,
                ),
            },
            "samples": samples_view,
        }

        if include_top:
            report["top_allocations"] = self.top_allocations(limit=top_limit)

        return report

    def top_allocations(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not tracemalloc.is_tracing():
            return []

        limit = max(1, min(int(limit), 100))
        snapshot = tracemalloc.take_snapshot()
        stats = snapshot.statistics("lineno")
        top: list[dict[str, Any]] = []
        for stat in stats[:limit]:
            frame = stat.traceback[0]
            source = linecache.getline(frame.filename, frame.lineno).strip()
            top.append(
                {
                    "file": frame.filename,
                    "line": frame.lineno,
                    "size_bytes": int(stat.size),
                    "count": int(stat.count),
                    "source": source,
                }
            )
        return top

    def _run_loop(self):
        next_log_ts = (
            time.time() + self.log_interval_s if self.log_interval_s > 0 else None
        )
        while not self._stop_event.wait(self.sample_interval_s):
            sample = self.capture_now()
            if next_log_ts is None:
                continue
            now = time.time()
            if now >= next_log_ts:
                self._print_sample(sample)
                next_log_ts = now + self.log_interval_s

    def _print_sample(self, sample: dict[str, Any]):
        rss = sample.get("rss_bytes")
        py_cur = sample.get("python_allocated_bytes")
        py_peak = sample.get("python_peak_bytes")
        rss_mb = self._to_mb(rss)
        py_cur_mb = self._to_mb(py_cur)
        py_peak_mb = self._to_mb(py_peak)
        print(
            "[memory] "
            f"rss={rss_mb}MB "
            f"py_cur={py_cur_mb}MB "
            f"py_peak={py_peak_mb}MB "
            f"samples={self.sample_count}"
        )

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def _collect_sample(self) -> dict[str, Any]:
        now = time.time()
        current_py, peak_py = (
            tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (None, None)
        )
        return {
            "timestamp": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "timestamp_unix": now,
            "rss_bytes": self._get_rss_bytes(),
            "python_allocated_bytes": (
                int(current_py) if current_py is not None else None
            ),
            "python_peak_bytes": (
                int(peak_py) if peak_py is not None else None
            ),
            "maxrss_bytes": self._get_maxrss_bytes(),
        }

    def _ensure_tracemalloc(self):
        if not tracemalloc.is_tracing():
            tracemalloc.start(self.trace_frames)

    def _get_rss_bytes(self) -> int | None:
        # `ps -o rss` returns KiB on macOS/Linux.
        try:
            out = subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(self._pid)],
                text=True,
            ).strip()
            if not out:
                return None
            return int(out.splitlines()[-1].strip()) * 1024
        except Exception:
            return None

    def _get_maxrss_bytes(self) -> int | None:
        try:
            import resource

            maxrss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            if sys.platform == "darwin":
                return maxrss
            return maxrss * 1024
        except Exception:
            return None

    def _delta_for_window(
        self,
        samples: list[dict[str, Any]],
        key: str,
        window_s: float,
    ) -> int | None:
        if not samples:
            return None
        latest = samples[-1]
        latest_ts = latest.get("timestamp_unix")
        if latest_ts is None:
            return None

        cutoff = float(latest_ts) - float(window_s)
        baseline = samples[0]
        for sample in samples:
            ts = sample.get("timestamp_unix")
            if ts is None:
                continue
            if ts >= cutoff:
                baseline = sample
                break

        return self._delta(baseline, latest, key)

    @staticmethod
    def _delta(
        old_sample: dict[str, Any],
        new_sample: dict[str, Any],
        key: str,
    ) -> int | None:
        old_val = old_sample.get(key)
        new_val = new_sample.get(key)
        if old_val is None or new_val is None:
            return None
        try:
            return int(new_val) - int(old_val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_mb(value: int | None) -> str:
        if value is None:
            return "n/a"
        return f"{(float(value) / (1024.0 * 1024.0)):.1f}"
