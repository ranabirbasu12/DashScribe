# tests/test_pipeline.py
import time
import numpy as np
from unittest.mock import MagicMock, patch

from pipeline import StreamingPipeline, SegmentResult
from vad import SealedSegment


class FakePipeline(StreamingPipeline):
    """Pipeline with mocked VAD for testing."""

    def __init__(self, transcriber, sample_rate=16000):
        super().__init__(transcriber, sample_rate)
        # Override VAD to always be available
        self._vad_loaded = True
        self._vad._available = True


def _make_transcriber(text="Hello."):
    txr = MagicMock()
    txr.transcribe_array = MagicMock(return_value=text)
    return txr


def test_pipeline_vad_not_available_fallback():
    """Pipeline returns empty results when VAD is not loaded."""
    txr = _make_transcriber()
    pipe = StreamingPipeline(txr)
    assert not pipe.vad_available
    pipe.start()
    assert not pipe._active  # Should not activate
    results = pipe.stop(None)
    assert results == []


def test_pipeline_process_segment_transcribes():
    """_process_segment returns a SegmentResult on success."""
    txr = _make_transcriber("Hello world.")
    pipe = FakePipeline(txr)
    segment = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    result = pipe._process_segment(segment, None)
    assert result is not None
    assert result.text == "Hello world."
    assert result.segment_index == 0
    assert abs(result.audio_duration - 1.0) < 0.01


def test_pipeline_process_segment_with_aec():
    """_process_segment applies AEC when system audio is provided."""
    txr = _make_transcriber("Clean audio.")
    pipe = FakePipeline(txr)
    segment = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    sys_audio = np.random.randn(16000).astype(np.float32) * 0.1
    with patch("aec.nlms_echo_cancel", return_value=segment.mic_audio), \
         patch("aec.noise_gate", return_value=segment.mic_audio):
        result = pipe._process_segment(segment, sys_audio)
    assert result is not None
    assert result.text == "Clean audio."


def test_pipeline_process_segment_empty_text():
    """_process_segment returns None when transcription is empty."""
    txr = _make_transcriber("")
    pipe = FakePipeline(txr)
    segment = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    result = pipe._process_segment(segment, None)
    assert result is None


def test_pipeline_align_sys_audio():
    """System audio alignment extracts correct slice."""
    pipe = FakePipeline(_make_transcriber())
    sys_audio = np.arange(32000, dtype=np.float32)

    # Extract samples 8000-16000
    ref = pipe._align_sys_audio(sys_audio, 8000, 16000)
    assert ref is not None
    assert len(ref) == 8000
    np.testing.assert_array_equal(ref, np.arange(8000, 16000, dtype=np.float32))


def test_pipeline_align_sys_audio_pads_when_short():
    """When system audio is shorter than needed, pad with zeros."""
    pipe = FakePipeline(_make_transcriber())
    sys_audio = np.ones(10000, dtype=np.float32)

    ref = pipe._align_sys_audio(sys_audio, 8000, 16000)
    assert ref is not None
    assert len(ref) == 8000
    # First 2000 from sys_audio[8000:10000], rest zeros
    np.testing.assert_array_equal(ref[:2000], np.ones(2000, dtype=np.float32))
    np.testing.assert_array_equal(ref[2000:], np.zeros(6000, dtype=np.float32))


def test_pipeline_align_sys_audio_beyond_range():
    """When segment starts beyond system audio, returns None."""
    pipe = FakePipeline(_make_transcriber())
    sys_audio = np.ones(5000, dtype=np.float32)
    ref = pipe._align_sys_audio(sys_audio, 8000, 16000)
    assert ref is None


def test_pipeline_ordered_results():
    """Results are returned sorted by segment_index."""
    txr = MagicMock()
    call_count = [0]

    def mock_transcribe(audio):
        call_count[0] += 1
        return f"Segment {call_count[0]}."

    txr.transcribe_array = mock_transcribe
    pipe = FakePipeline(txr)

    # Manually set results out of order
    with pipe._results_lock:
        pipe._results = [
            SegmentResult(segment_index=2, text="Third.", audio_duration=1.0),
            SegmentResult(segment_index=0, text="First.", audio_duration=1.0),
            SegmentResult(segment_index=1, text="Second.", audio_duration=1.0),
        ]

    pipe._active = True
    pipe._segmenter = MagicMock()
    pipe._segmenter.seal_final.return_value = None
    pipe._segmenter.signal_done = MagicMock()
    pipe._segmenter.segment_queue = MagicMock()
    pipe._worker_thread = None

    results = pipe.stop(None)
    assert [r.text for r in results] == ["First.", "Second.", "Third."]


def test_pipeline_get_sys_audio_window_extracts_range():
    """Window extraction returns only the requested system-audio range."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [
        np.arange(1000, dtype=np.float32),
        np.arange(1000, 3000, dtype=np.float32),
    ]
    window = pipe._get_sys_audio_window(900, 1200)
    assert window is not None
    assert len(window) == 300
    np.testing.assert_array_equal(window, np.arange(900, 1200, dtype=np.float32))


def test_pipeline_get_sys_audio_window_pads_when_short():
    """Window extraction zero-pads when capture has not reached end_sample yet."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [np.ones(1000, dtype=np.float32)]
    window = pipe._get_sys_audio_window(900, 1200)
    assert window is not None
    assert len(window) == 300
    np.testing.assert_array_equal(window[:100], np.ones(100, dtype=np.float32))
    np.testing.assert_array_equal(window[100:], np.zeros(200, dtype=np.float32))


def test_pipeline_get_sys_audio_window_none():
    """Window extraction returns None when no system audio is available."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = None
    assert pipe._get_sys_audio_window(0, 100) is None


def test_pipeline_get_sys_audio_window_empty():
    """Window extraction returns None when chunk list is empty."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = []
    assert pipe._get_sys_audio_window(0, 100) is None


def test_pipeline_cancel_resets_session_state():
    """cancel() should stop active session and release references."""
    pipe = FakePipeline(_make_transcriber())
    pipe.start(sys_audio_chunks=[])
    assert pipe._active is True
    assert pipe._segmenter is not None

    pipe.cancel()

    assert pipe._active is False
    assert pipe._segmenter is None
    assert pipe._worker_thread is None
    assert pipe._sys_audio_chunks is None


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


def test_load_vad_success():
    """load_vad sets _vad_loaded=True when VAD loads (lines 54-57)."""
    txr = _make_transcriber()
    pipe = StreamingPipeline(txr)
    pipe._vad = MagicMock()
    pipe._vad.load.return_value = True
    pipe.load_vad()
    assert pipe._vad_loaded is True


def test_load_vad_failure():
    """load_vad prints warning when VAD fails to load (lines 58-59)."""
    txr = _make_transcriber()
    pipe = StreamingPipeline(txr)
    pipe._vad = MagicMock()
    pipe._vad.load.return_value = False
    pipe.load_vad()
    assert pipe._vad_loaded is False


def test_load_vad_already_loaded():
    """load_vad does nothing if already loaded."""
    txr = _make_transcriber()
    pipe = StreamingPipeline(txr)
    pipe._vad_loaded = True
    pipe._vad = MagicMock()
    pipe.load_vad()
    pipe._vad.load.assert_not_called()


def test_start_returns_false_when_already_active():
    """start() returns False when session is already active (line 77)."""
    pipe = FakePipeline(_make_transcriber())
    assert pipe.start() is True
    assert pipe.start() is False  # already active
    pipe.cancel()


def test_start_returns_false_when_worker_still_alive():
    """start() returns False when previous worker is still alive (lines 79-82)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._active = False
    mock_worker = MagicMock()
    mock_worker.is_alive.return_value = True
    pipe._worker_thread = mock_worker
    result = pipe.start()
    assert result is False


def test_start_clears_dead_worker():
    """start() clears dead worker thread and starts new session (line 82)."""
    pipe = FakePipeline(_make_transcriber())
    mock_worker = MagicMock()
    mock_worker.is_alive.return_value = False
    pipe._worker_thread = mock_worker
    result = pipe.start()
    assert result is True
    pipe.cancel()


def test_feed_does_nothing_when_inactive():
    """feed() is a no-op when pipeline is not active (lines 104-105)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._active = False
    pipe._segmenter = MagicMock()
    chunk = np.zeros(1024, dtype=np.float32)
    pipe.feed(chunk)  # should not call segmenter.feed
    pipe._segmenter.feed.assert_not_called()


def test_feed_when_active():
    """feed() forwards chunk to segmenter when active."""
    pipe = FakePipeline(_make_transcriber())
    pipe._active = True
    pipe._segmenter = MagicMock()
    chunk = np.zeros(1024, dtype=np.float32)
    pipe.feed(chunk)
    pipe._segmenter.feed.assert_called_once_with(chunk)


def test_stop_when_not_active():
    """stop() returns empty list when not active (lines 134-137 area)."""
    pipe = FakePipeline(_make_transcriber())
    assert pipe.stop(None) == []


def test_cancel_when_not_active():
    """cancel() cleans up sys_audio_chunks when not active (lines 163-164)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [np.zeros(100)]
    pipe.cancel()
    assert pipe._sys_audio_chunks is None


def test_stop_processes_final_segment():
    """stop() processes the final segment from seal_final (lines 142-145)."""
    txr = _make_transcriber("Final text.")
    pipe = FakePipeline(txr)
    pipe._active = True
    final_seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    segmenter = MagicMock()
    segmenter.seal_final.return_value = final_seg
    segmenter.signal_done = MagicMock()
    pipe._segmenter = segmenter
    pipe._worker_thread = None

    results = pipe.stop(None)
    assert len(results) == 1
    assert results[0].text == "Final text."


def test_worker_loop_processes_segments():
    """Worker loop dequeues and processes segments (lines 191-209)."""
    txr = _make_transcriber("Segment text.")
    pipe = FakePipeline(txr)
    pipe._active = True

    from vad import VADSegmenter

    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )

    mock_segmenter = MagicMock()
    mock_segmenter.segment_queue = MagicMock()
    # Return the segment, then sentinel None to stop
    mock_segmenter.segment_queue.get.side_effect = [seg, None]

    with patch.object(pipe, "_get_sys_audio_window", return_value=None), \
         patch.object(pipe, "_trim_sys_audio_chunks"):
        pipe._worker_loop(mock_segmenter)

    assert len(pipe._results) == 1
    assert pipe._results[0].text == "Segment text."


def test_worker_loop_handles_empty_queue():
    """Worker loop handles Empty exception and breaks on inactive (lines 191-194)."""
    import queue as q
    txr = _make_transcriber()
    pipe = FakePipeline(txr)
    pipe._active = False  # will cause break on Empty

    mock_segmenter = MagicMock()
    mock_segmenter.segment_queue = MagicMock()
    mock_segmenter.segment_queue.get.side_effect = q.Empty()

    pipe._worker_loop(mock_segmenter)
    assert len(pipe._results) == 0


def test_process_segment_with_aligned_sys_audio():
    """_process_segment with sys_audio_aligned=True uses ref directly (line 226)."""
    txr = _make_transcriber("With AEC.")
    pipe = FakePipeline(txr)
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    sys_audio = np.random.randn(16000).astype(np.float32) * 0.1
    with patch("pipeline.nlms_echo_cancel", return_value=seg.mic_audio, create=True), \
         patch("pipeline.noise_gate", return_value=seg.mic_audio, create=True):
        # Use the actual import path
        with patch("aec.nlms_echo_cancel", return_value=seg.mic_audio), \
             patch("aec.noise_gate", return_value=seg.mic_audio):
            result = pipe._process_segment(seg, sys_audio, sys_audio_aligned=True)
    assert result is not None
    assert result.text == "With AEC."


def test_process_segment_aec_exception():
    """_process_segment falls back to raw audio when AEC fails (lines 234-235)."""
    txr = _make_transcriber("Raw audio.")
    pipe = FakePipeline(txr)
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    sys_audio = np.random.randn(16000).astype(np.float32)
    with patch("aec.nlms_echo_cancel", side_effect=Exception("AEC broken")):
        result = pipe._process_segment(seg, sys_audio)
    assert result is not None
    assert result.text == "Raw audio."


def test_process_segment_transcription_exception():
    """_process_segment returns None when transcription raises (lines 246-247)."""
    txr = MagicMock()
    txr.transcribe_array.side_effect = Exception("MLX crash")
    pipe = FakePipeline(txr)
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    result = pipe._process_segment(seg, None)
    assert result is None


def test_trim_sys_audio_chunks():
    """_trim_sys_audio_chunks removes fully consumed chunks (lines 277-293)."""
    pipe = FakePipeline(_make_transcriber())
    chunks = [
        np.zeros(1000, dtype=np.float32),
        np.zeros(1000, dtype=np.float32),
        np.zeros(1000, dtype=np.float32),
    ]
    pipe._sys_audio_chunks = chunks
    pipe._sys_audio_base_sample = 0

    pipe._trim_sys_audio_chunks(1500)
    # First chunk (0-1000) is fully consumed, second (1000-2000) is not
    assert len(pipe._sys_audio_chunks) == 2
    assert pipe._sys_audio_base_sample == 1000


def test_trim_sys_audio_chunks_none():
    """_trim_sys_audio_chunks does nothing when chunks is None (line 278-279)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = None
    pipe._trim_sys_audio_chunks(1000)  # should not raise


def test_get_sys_audio_window_negative_needed():
    """Window returns None when needed_len <= 0 (line 314)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [np.zeros(1000, dtype=np.float32)]
    result = pipe._get_sys_audio_window(500, 500)
    assert result is None


def test_get_sys_audio_window_skips_early_chunks():
    """Window skips chunks before start_sample (line 327)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [
        np.ones(1000, dtype=np.float32),   # 0-1000
        np.ones(1000, dtype=np.float32) * 2,  # 1000-2000
    ]
    pipe._sys_audio_base_sample = 0
    window = pipe._get_sys_audio_window(1200, 1500)
    assert window is not None
    assert len(window) == 300
    np.testing.assert_array_equal(window, np.ones(300, dtype=np.float32) * 2)


def test_get_sys_audio_window_breaks_past_end():
    """Window stops processing chunks past end_sample (line 329)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [
        np.ones(1000, dtype=np.float32),
        np.ones(1000, dtype=np.float32) * 2,
        np.ones(1000, dtype=np.float32) * 3,
    ]
    pipe._sys_audio_base_sample = 0
    window = pipe._get_sys_audio_window(500, 800)
    assert window is not None
    assert len(window) == 300


def test_get_sys_audio_window_no_pieces_returns_none():
    """Window returns None when requested range has no overlap (lines 338-339)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [np.ones(100, dtype=np.float32)]
    pipe._sys_audio_base_sample = 0
    result = pipe._get_sys_audio_window(500, 600)
    assert result is None


def test_get_sys_audio_window_exception_returns_none():
    """Window returns None on exception during iteration (lines 335-336)."""
    pipe = FakePipeline(_make_transcriber())
    # Create a chunk list that raises during iteration
    bad_chunk = MagicMock()
    bad_chunk.__len__ = MagicMock(side_effect=Exception("bad"))
    pipe._sys_audio_chunks = [bad_chunk]
    pipe._sys_audio_base_sample = 0
    result = pipe._get_sys_audio_window(0, 100)
    assert result is None


def test_get_sys_audio_window_concat_exception():
    """Window returns None if concatenation fails (lines 343-344)."""
    pipe = FakePipeline(_make_transcriber())
    pipe._sys_audio_chunks = [np.ones(100, dtype=np.float32)]
    pipe._sys_audio_base_sample = 0
    with patch("numpy.concatenate", side_effect=Exception("concat fail")):
        # This path is only hit when len(pieces) > 1
        # Use two chunks so concatenation is attempted
        pipe._sys_audio_chunks = [
            np.ones(50, dtype=np.float32),
            np.ones(50, dtype=np.float32),
        ]
        result = pipe._get_sys_audio_window(0, 100)
    assert result is None
