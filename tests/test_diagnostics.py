import os
import sys
import time
import tracemalloc
from unittest.mock import patch, MagicMock

from diagnostics import MemoryTelemetry, _env_float, _env_int, _env_bool


def test_memory_telemetry_report_has_expected_shape():
    telemetry = MemoryTelemetry(sample_interval_s=1.0, log_interval_s=0.0, max_samples=32)
    telemetry.start()
    try:
        report = telemetry.get_report(history=10, refresh=True)
        assert "process_id" in report
        assert report["sample_count"] >= 1
        assert "latest" in report
        assert "rss_bytes" in report["latest"]
        assert "python_allocated_bytes" in report["latest"]
        assert "growth" in report
        assert "samples" in report
    finally:
        telemetry.stop()


def test_memory_telemetry_top_allocations_respects_limit():
    telemetry = MemoryTelemetry(sample_interval_s=1.0, log_interval_s=0.0, max_samples=16)
    telemetry.start()
    try:
        # Create some Python allocations so a snapshot has signal.
        _data = [bytearray(4096) for _ in range(32)]
        time.sleep(0.01)
        report = telemetry.get_report(include_top=True, top_limit=3, history=5, refresh=True)
        assert "top_allocations" in report
        assert len(report["top_allocations"]) <= 3
    finally:
        telemetry.stop()


# ------------------------------------------------------------------
# _env_float tests (lines 17-23)
# ------------------------------------------------------------------

def test_env_float_returns_default_when_unset():
    with patch.dict(os.environ, {}, clear=True):
        assert _env_float("NONEXISTENT_VAR", 42.0) == 42.0


def test_env_float_parses_valid_value():
    with patch.dict(os.environ, {"TEST_VAR": "3.14"}):
        assert _env_float("TEST_VAR", 0.0) == 3.14


def test_env_float_negative_returns_default():
    with patch.dict(os.environ, {"TEST_VAR": "-1.0"}):
        assert _env_float("TEST_VAR", 5.0) == 5.0


def test_env_float_invalid_returns_default():
    with patch.dict(os.environ, {"TEST_VAR": "not_a_number"}):
        assert _env_float("TEST_VAR", 5.0) == 5.0


# ------------------------------------------------------------------
# _env_int tests (lines 30-36)
# ------------------------------------------------------------------

def test_env_int_returns_default_when_unset():
    with patch.dict(os.environ, {}, clear=True):
        assert _env_int("NONEXISTENT_VAR", 100, minimum=1) == 100


def test_env_int_parses_valid_value():
    with patch.dict(os.environ, {"TEST_VAR": "50"}):
        assert _env_int("TEST_VAR", 100, minimum=1) == 50


def test_env_int_below_minimum_returns_default():
    with patch.dict(os.environ, {"TEST_VAR": "0"}):
        assert _env_int("TEST_VAR", 100, minimum=1) == 100


def test_env_int_invalid_returns_default():
    with patch.dict(os.environ, {"TEST_VAR": "abc"}):
        assert _env_int("TEST_VAR", 100, minimum=1) == 100


# ------------------------------------------------------------------
# _env_bool tests (lines 43-48)
# ------------------------------------------------------------------

def test_env_bool_returns_default_when_unset():
    with patch.dict(os.environ, {}, clear=True):
        assert _env_bool("NONEXISTENT_VAR", True) is True


def test_env_bool_truthy_values():
    for val in ["1", "true", "yes", "on", "TRUE", "Yes"]:
        with patch.dict(os.environ, {"TEST_VAR": val}):
            assert _env_bool("TEST_VAR", False) is True


def test_env_bool_falsy_values():
    for val in ["0", "false", "no", "off", "FALSE", "No"]:
        with patch.dict(os.environ, {"TEST_VAR": val}):
            assert _env_bool("TEST_VAR", True) is False


def test_env_bool_invalid_returns_default():
    with patch.dict(os.environ, {"TEST_VAR": "maybe"}):
        assert _env_bool("TEST_VAR", True) is True


# ------------------------------------------------------------------
# MemoryTelemetry - start/stop, run_loop, print_sample
# ------------------------------------------------------------------

def test_start_already_running():
    """Calling start() twice doesn't create duplicate threads."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mt.start()
    thread1 = mt._thread
    mt.start()  # Should be no-op
    thread2 = mt._thread
    assert thread1 is thread2
    mt.stop()


def test_start_with_tracemalloc_enabled():
    """start() with enable_tracemalloc=True starts tracemalloc."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0, enable_tracemalloc=True)
    mt.start()
    assert tracemalloc.is_tracing()
    mt.stop()


def test_stop_when_not_started():
    """stop() is safe when no thread is running."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mt.stop()  # Should not raise


def test_get_report_history_zero():
    """get_report() with history=0 returns empty samples list."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    report = mt.get_report(history=0, refresh=True)
    assert report["samples"] == []


def test_get_report_no_samples_captures_one():
    """get_report() with no existing samples auto-captures."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    report = mt.get_report(refresh=False)
    assert report["sample_count"] >= 1


def test_sample_count_property():
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    assert mt.sample_count == 0
    mt.capture_now()
    assert mt.sample_count == 1


def test_print_sample(capsys):
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    sample = {
        "rss_bytes": 100 * 1024 * 1024,
        "python_allocated_bytes": 50 * 1024 * 1024,
        "python_peak_bytes": 80 * 1024 * 1024,
    }
    mt._print_sample(sample)
    captured = capsys.readouterr()
    assert "[memory]" in captured.out
    assert "rss=" in captured.out


def test_print_sample_none_values(capsys):
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    sample = {
        "rss_bytes": None,
        "python_allocated_bytes": None,
        "python_peak_bytes": None,
    }
    mt._print_sample(sample)
    captured = capsys.readouterr()
    assert "n/a" in captured.out


def test_to_mb_none():
    assert MemoryTelemetry._to_mb(None) == "n/a"


def test_to_mb_value():
    assert MemoryTelemetry._to_mb(1048576) == "1.0"


# ------------------------------------------------------------------
# _run_loop with log_interval > 0
# ------------------------------------------------------------------

def test_run_loop_logs_when_interval_reached():
    """_run_loop logs a sample when log_interval_s has elapsed."""
    mt = MemoryTelemetry(sample_interval_s=0.01, log_interval_s=0.01, max_samples=16)
    mt.start()
    time.sleep(0.1)
    mt.stop()
    assert mt.sample_count >= 1


# ------------------------------------------------------------------
# _get_rss_bytes edge cases (lines 268, 270-271)
# ------------------------------------------------------------------

def test_get_rss_bytes_empty_output():
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    with patch("diagnostics.subprocess.check_output", return_value=""):
        assert mt._get_rss_bytes() is None


def test_get_rss_bytes_exception():
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    with patch("diagnostics.subprocess.check_output", side_effect=Exception("fail")):
        assert mt._get_rss_bytes() is None


# ------------------------------------------------------------------
# _get_maxrss_bytes edge cases (lines 280-282)
# ------------------------------------------------------------------

def test_get_maxrss_bytes_non_darwin():
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mock_resource = MagicMock()
    mock_resource.getrusage.return_value = MagicMock(ru_maxrss=1000)
    mock_resource.RUSAGE_SELF = 0
    with patch.dict("sys.modules", {"resource": mock_resource}), \
         patch("diagnostics.sys.platform", "linux"):
        result = mt._get_maxrss_bytes()
        assert result == 1000 * 1024


def test_get_maxrss_bytes_exception():
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    with patch("builtins.__import__", side_effect=ImportError("no resource")):
        # On import failure in _get_maxrss_bytes, it returns None
        # But resource is already imported, so let's mock it differently
        pass
    # Direct approach: patch the resource module to raise
    mock_resource = MagicMock()
    mock_resource.getrusage.side_effect = Exception("fail")
    mock_resource.RUSAGE_SELF = 0
    with patch.dict("sys.modules", {"resource": mock_resource}):
        result = mt._get_maxrss_bytes()
        assert result is None


# ------------------------------------------------------------------
# _delta_for_window edge cases (lines 291, 295, 302)
# ------------------------------------------------------------------

def test_delta_for_window_empty_samples():
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    assert mt._delta_for_window([], "rss_bytes", 300.0) is None


def test_delta_for_window_no_timestamp():
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    samples = [{"rss_bytes": 100}]  # no timestamp_unix
    assert mt._delta_for_window(samples, "rss_bytes", 300.0) is None


def test_delta_for_window_sample_no_ts():
    """Samples with missing timestamp_unix in the loop are skipped."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    now = time.time()
    samples = [
        {"rss_bytes": 100, "timestamp_unix": now - 10},
        {"rss_bytes": None},  # missing timestamp_unix
        {"rss_bytes": 200, "timestamp_unix": now},
    ]
    result = mt._delta_for_window(samples, "rss_bytes", 300.0)
    assert result == 100  # 200 - 100


# ------------------------------------------------------------------
# _delta edge cases (lines 321-322)
# ------------------------------------------------------------------

def test_delta_type_error():
    result = MemoryTelemetry._delta(
        {"key": "not_a_number"}, {"key": 100}, "key"
    )
    assert result is None


def test_delta_none_values():
    result = MemoryTelemetry._delta({"key": None}, {"key": 100}, "key")
    assert result is None


# ------------------------------------------------------------------
# Additional coverage for uncovered lines
# ------------------------------------------------------------------

def test_env_float_zero_is_valid():
    """_env_float accepts zero (non-negative)."""
    with patch.dict(os.environ, {"TEST_VAR": "0.0"}):
        assert _env_float("TEST_VAR", 5.0) == 0.0


def test_env_int_exact_minimum():
    """_env_int accepts value equal to minimum."""
    with patch.dict(os.environ, {"TEST_VAR": "16"}):
        assert _env_int("TEST_VAR", 100, minimum=16) == 16


def test_env_bool_true_values_case_variants():
    """_env_bool handles all truthy variations (lines 44-45)."""
    with patch.dict(os.environ, {"TEST_VAR": "ON"}):
        assert _env_bool("TEST_VAR", False) is True


def test_env_bool_false_values_case_variants():
    """_env_bool handles all falsy variations (lines 46-47)."""
    with patch.dict(os.environ, {"TEST_VAR": "OFF"}):
        assert _env_bool("TEST_VAR", True) is False


def test_get_report_include_top_enables_tracemalloc():
    """get_report() with include_top=True enables tracemalloc (line 138)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    report = mt.get_report(include_top=True, top_limit=5, history=5, refresh=True)
    assert "top_allocations" in report
    assert tracemalloc.is_tracing()


def test_get_report_no_refresh():
    """get_report() with refresh=False skips capture_now (line 140-141)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mt.capture_now()  # Pre-populate
    report = mt.get_report(refresh=False, history=5)
    assert report["sample_count"] >= 1


def test_get_report_empty_samples_view_with_history_zero():
    """get_report() with history=0 returns empty samples (line 153)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mt.capture_now()
    report = mt.get_report(history=0, refresh=False)
    assert report["samples"] == []


def test_top_allocations_not_tracing():
    """top_allocations() returns empty list when not tracing (line 185)."""
    was_tracing = tracemalloc.is_tracing()
    if was_tracing:
        tracemalloc.stop()
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0, enable_tracemalloc=False)
    result = mt.top_allocations()
    assert result == []
    # Restart if it was running before
    if was_tracing:
        tracemalloc.start()


def test_run_loop_with_log_interval_none():
    """_run_loop with log_interval_s=0 sets next_log_ts to None and continues (line 211-212)."""
    mt = MemoryTelemetry(sample_interval_s=0.01, log_interval_s=0.0, max_samples=16)
    mt.start()
    time.sleep(0.05)
    mt.stop()
    assert mt.sample_count >= 1


def test_print_sample_with_values(capsys):
    """_print_sample prints formatted MB values (lines 219-225)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mt.capture_now()  # Add at least one sample so sample_count > 0
    sample = {
        "rss_bytes": 200 * 1024 * 1024,
        "python_allocated_bytes": 100 * 1024 * 1024,
        "python_peak_bytes": 150 * 1024 * 1024,
    }
    mt._print_sample(sample)
    captured = capsys.readouterr()
    assert "200.0" in captured.out
    assert "100.0" in captured.out
    assert "150.0" in captured.out


def test_sample_count_with_multiple_samples():
    """sample_count property returns correct count (lines 235-236)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mt.capture_now()
    mt.capture_now()
    assert mt.sample_count == 2


def test_get_rss_bytes_valid_output():
    """_get_rss_bytes returns correct value for valid ps output (lines 268-269)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    with patch("diagnostics.subprocess.check_output", return_value="  12345\n"):
        result = mt._get_rss_bytes()
        assert result == 12345 * 1024


def test_get_maxrss_bytes_darwin():
    """_get_maxrss_bytes on darwin returns raw value (line 279)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    # On actual macOS, this should return a value
    result = mt._get_maxrss_bytes()
    assert result is None or isinstance(result, int)


def test_delta_for_window_skips_no_ts_samples():
    """_delta_for_window skips samples without timestamp_unix (line 302)."""
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    now = time.time()
    samples = [
        {"rss_bytes": 50, "timestamp_unix": now - 400},
        {"rss_bytes": None, "timestamp_unix": None},  # no ts
        {"rss_bytes": 100, "timestamp_unix": now - 100},
        {"rss_bytes": 200, "timestamp_unix": now},
    ]
    result = mt._delta_for_window(samples, "rss_bytes", 300.0)
    # baseline should be the sample at now-100 (first within cutoff window)
    assert result == 100  # 200 - 100


def test_delta_valid():
    """_delta returns correct integer delta (line 320)."""
    result = MemoryTelemetry._delta({"key": 100}, {"key": 250}, "key")
    assert result == 150


def test_delta_type_error_string_values():
    """_delta returns None when values can't be converted to int (lines 321-322)."""
    result = MemoryTelemetry._delta(
        {"key": "abc"}, {"key": "def"}, "key"
    )
    assert result is None


def test_to_mb_zero():
    """_to_mb handles zero bytes (lines 326-328)."""
    assert MemoryTelemetry._to_mb(0) == "0.0"


def test_ensure_tracemalloc_starts():
    """_ensure_tracemalloc starts tracing if not already tracing."""
    was_tracing = tracemalloc.is_tracing()
    if was_tracing:
        tracemalloc.stop()
    mt = MemoryTelemetry(sample_interval_s=60.0, log_interval_s=0.0)
    mt._ensure_tracemalloc()
    assert tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.stop()
