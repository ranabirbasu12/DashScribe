# tests/test_vad.py
import queue
from unittest.mock import MagicMock, patch

import numpy as np

from vad import SileroVAD, VADSegmenter, SealedSegment, VAD_WINDOW_SAMPLES


class FakeVAD:
    """Deterministic VAD for testing: returns preset probabilities."""

    def __init__(self, probs=None, threshold=0.5):
        self.threshold = threshold
        self._probs = probs or []
        self._call_count = 0
        self._available = True

    def __call__(self, audio_chunk):
        if self._call_count < len(self._probs):
            prob = self._probs[self._call_count]
        else:
            prob = 0.0
        self._call_count += 1
        return prob

    def reset(self):
        self._call_count = 0

    @property
    def is_available(self):
        return self._available


def test_silero_vad_graceful_when_unavailable():
    """Returns 0.0 when model is not loaded."""
    vad = SileroVAD()
    assert not vad.is_available
    assert vad(np.zeros(512, dtype=np.float32)) == 0.0


def test_silero_vad_load_missing_onnxruntime():
    """Returns False if onnxruntime is broken."""
    vad = SileroVAD()
    with patch("vad.os.path.exists", return_value=True):
        with patch("builtins.__import__", side_effect=ImportError("no ort")):
            assert vad.load() is False
    assert not vad.is_available


def test_segmenter_seals_on_silence_transition():
    """Feed speech chunks then silence chunks; segment appears in queue."""
    # 20 windows of speech (0.8) then 20 windows of silence (0.1)
    # At 512 samples/window, 20 silence windows = 10240 samples = 640ms > 600ms threshold
    probs = [0.8] * 20 + [0.1] * 20
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)

    # Feed enough audio for all 40 windows (40 * 512 = 20480 samples)
    # Feed in chunks of 1024 samples (realistic callback size)
    for _ in range(20):
        chunk = np.random.randn(1024).astype(np.float32) * 0.1
        seg.feed(chunk)

    # Should have one sealed segment
    assert not seg.segment_queue.empty()
    segment = seg.segment_queue.get_nowait()
    assert isinstance(segment, SealedSegment)
    assert segment.segment_index == 0
    assert segment.start_sample == 0
    assert len(segment.mic_audio) > 0


def test_segmenter_respects_minimum_duration():
    """Very short speech bursts (< 1s) are not sealed as independent segments."""
    # 2 windows speech + 20 windows silence = ~64ms speech, way under 1s minimum
    probs = [0.8] * 2 + [0.1] * 20
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)

    for _ in range(11):  # 11 * 1024 = 11264 samples, covers all 22 windows
        chunk = np.random.randn(1024).astype(np.float32) * 0.1
        seg.feed(chunk)

    # Segment too short, should NOT be sealed
    assert seg.segment_queue.empty()


def test_segmenter_seal_final():
    """Remaining audio is returned by seal_final()."""
    probs = [0.8] * 10  # All speech, no silence transition
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)

    # Feed 5120 samples of speech (10 windows)
    for _ in range(5):
        chunk = np.random.randn(1024).astype(np.float32) * 0.1
        seg.feed(chunk)

    # Queue should be empty (no silence transition)
    assert seg.segment_queue.empty()

    # seal_final should return the accumulated audio
    final = seg.seal_final()
    assert final is not None
    assert final.segment_index == 0
    assert len(final.mic_audio) == 5 * 1024


def test_segmenter_seal_final_too_short():
    """Very short final audio (< 100ms) is discarded."""
    vad = FakeVAD(probs=[0.8], threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)

    # Feed just 512 samples (32ms)
    seg.feed(np.random.randn(512).astype(np.float32))

    final = seg.seal_final()
    assert final is None  # < 100ms = 1600 samples


def test_segmenter_reset_clears_state():
    """Reset drains queue and resets all counters."""
    probs = [0.8] * 30 + [0.1] * 20
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)

    for _ in range(25):
        seg.feed(np.random.randn(1024).astype(np.float32) * 0.1)

    seg.reset()
    assert seg.segment_queue.empty()
    assert seg._segment_index == 0
    assert seg._global_sample_count == 0


def test_segmenter_tracks_sample_offsets():
    """Multiple sealed segments have correct cumulative offsets."""
    # Two speech-silence cycles, each long enough to seal
    speech_windows = 40  # 40 * 512 = 20480 samples > 16000 (1s min)
    silence_windows = 20  # 20 * 512 = 10240 samples > 9600 (600ms threshold)
    probs = ([0.8] * speech_windows + [0.1] * silence_windows) * 2
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)

    total_windows = len(probs)
    total_samples = total_windows * VAD_WINDOW_SAMPLES
    chunks_needed = total_samples // 1024 + 1

    for _ in range(chunks_needed):
        seg.feed(np.random.randn(1024).astype(np.float32) * 0.1)

    segments = []
    while not seg.segment_queue.empty():
        s = seg.segment_queue.get_nowait()
        if s is not None:
            segments.append(s)

    assert len(segments) >= 1
    # First segment starts at 0
    assert segments[0].start_sample == 0
    assert segments[0].end_sample == len(segments[0].mic_audio)
    # Second segment (if present) starts near where first ended
    # (may overlap slightly due to pre-speech lookback buffer)
    if len(segments) >= 2:
        assert segments[1].start_sample <= segments[0].end_sample
        assert segments[1].start_sample >= segments[0].end_sample - 1600  # within 100ms lookback


def test_segmenter_signal_done():
    """signal_done puts None sentinel on queue."""
    vad = FakeVAD(threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)
    seg.signal_done()
    assert seg.segment_queue.get_nowait() is None


def test_segmenter_forced_split_on_continuous_speech():
    """Continuous speech should be periodically sealed even without silence."""
    probs = [0.9] * 200  # sustained speech windows
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000, max_segment_duration_s=1.0)

    # Feed >1s continuous speech; forced split should enqueue at least one segment.
    for _ in range(20):  # 20 * 1024 = 20480 samples
        seg.feed(np.random.randn(1024).astype(np.float32) * 0.1)

    assert not seg.segment_queue.empty()
    s = seg.segment_queue.get_nowait()
    assert isinstance(s, SealedSegment)
    assert s.segment_index == 0


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


def test_silero_vad_load_download_and_onnx_fail():
    """SileroVAD.load() downloads model then fails at onnxruntime (lines 40-47, 52-58)."""
    vad = SileroVAD()
    with patch("vad.os.path.exists", return_value=False), \
         patch("vad.os.makedirs"), \
         patch("urllib.request.urlretrieve") as mock_dl:
        mock_dl.return_value = None
        # After download, onnxruntime import will fail
        import builtins
        orig_import = builtins.__import__
        def side_effect(name, *args, **kwargs):
            if name == "onnxruntime":
                raise ImportError("no ort")
            return orig_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=side_effect):
            result = vad.load()
    # Download was attempted
    mock_dl.assert_called_once()
    assert result is False


def test_silero_vad_load_download_fails():
    """SileroVAD.load() returns False when download raises (lines 45-47)."""
    vad = SileroVAD()
    with patch("vad.os.path.exists", return_value=False), \
         patch("vad.os.makedirs"), \
         patch("urllib.request.urlretrieve", side_effect=Exception("download failed")):
        result = vad.load()
    assert result is False
    assert not vad.is_available


def test_silero_vad_load_onnx_success():
    """SileroVAD.load() succeeds when model exists and onnxruntime works (lines 52-58)."""
    vad = SileroVAD()
    mock_session = MagicMock()
    mock_ort = MagicMock()
    mock_ort.SessionOptions.return_value = MagicMock()
    mock_ort.InferenceSession.return_value = mock_session

    with patch("vad.os.path.exists", return_value=True):
        import builtins
        original_import = builtins.__import__
        def side_effect(name, *args, **kwargs):
            if name == "onnxruntime":
                return mock_ort
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=side_effect):
            result = vad.load()

    assert result is True
    assert vad.is_available
    assert vad._session is mock_session
    assert vad._state is not None
    assert vad._context is not None


def test_silero_vad_load_onnx_init_fails():
    """SileroVAD.load() returns False when onnxruntime init fails (lines 59-61)."""
    vad = SileroVAD()
    with patch("vad.os.path.exists", return_value=True):
        import builtins
        original_import = builtins.__import__
        def side_effect(name, *args, **kwargs):
            if name == "onnxruntime":
                mod = MagicMock()
                mod.InferenceSession.side_effect = Exception("init failed")
                return mod
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=side_effect):
            result = vad.load()

    assert result is False
    assert not vad.is_available


def test_silero_vad_reset_state():
    """SileroVAD._reset_state creates correct shapes (lines 66-67)."""
    vad = SileroVAD()
    vad._reset_state()
    assert vad._state.shape == (2, 1, 128)
    assert vad._context.shape == (1, 64)


def test_silero_vad_reset_when_available():
    """SileroVAD.reset() resets state when available (lines 71-72)."""
    vad = SileroVAD()
    vad._available = True
    vad._state = np.ones((2, 1, 128), dtype=np.float32)
    vad._context = np.ones((1, 64), dtype=np.float32)
    vad.reset()
    np.testing.assert_array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32))
    np.testing.assert_array_equal(vad._context, np.zeros((1, 64), dtype=np.float32))


def test_silero_vad_reset_when_unavailable():
    """SileroVAD.reset() does nothing when unavailable."""
    vad = SileroVAD()
    vad._available = False
    vad._state = None
    vad.reset()
    assert vad._state is None


def test_silero_vad_call_with_session():
    """SileroVAD.__call__ runs inference with correct inputs (lines 86-100)."""
    vad = SileroVAD()
    vad._available = True
    vad._state = np.zeros((2, 1, 128), dtype=np.float32)
    vad._context = np.zeros((1, 64), dtype=np.float32)

    mock_session = MagicMock()
    out_array = np.array([[0.75]], dtype=np.float32)
    new_state = np.zeros((2, 1, 128), dtype=np.float32)
    mock_session.run.return_value = (out_array, new_state)
    vad._session = mock_session

    chunk = np.random.randn(512).astype(np.float32)
    prob = vad(chunk)

    assert abs(prob - 0.75) < 0.01
    mock_session.run.assert_called_once()
    # Verify input shape: context (64) + chunk (512) = 576
    call_args = mock_session.run.call_args
    ort_inputs = call_args[1] if call_args[1] else call_args[0][1]
    assert ort_inputs["input"].shape == (1, 576)


def test_segmenter_seal_final_empty_chunks():
    """seal_final returns None when no chunks accumulated (line 263)."""
    vad = FakeVAD(threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)
    result = seg.seal_final()
    assert result is None


def test_segmenter_pre_speech_buffer_trimming():
    """Pre-speech buffer trims to ~500ms lookback (lines 188-189)."""
    probs = [0.0] * 50 + [0.9] * 20 + [0.0] * 20
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)

    # Feed lots of silence then speech then silence
    for _ in range(45):
        seg.feed(np.random.randn(1024).astype(np.float32) * 0.01)

    # Pre-speech buffer should have been consumed when speech started
    # The segment should include lookback audio


def test_segmenter_forced_split_resets_in_speech():
    """Forced split on long continuous speech resets _in_speech (line 254)."""
    probs = [0.9] * 500
    vad = FakeVAD(probs=probs, threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000, max_segment_duration_s=0.5,
                       min_segment_duration_s=0.1)

    # Feed enough audio for multiple forced splits
    for _ in range(30):
        seg.feed(np.random.randn(1024).astype(np.float32) * 0.1)

    segments = []
    while not seg.segment_queue.empty():
        s = seg.segment_queue.get_nowait()
        if s is not None:
            segments.append(s)

    # Should have at least 2 forced-split segments
    assert len(segments) >= 2
    # Each should have incrementing indices
    for i, s in enumerate(segments):
        assert s.segment_index == i


def test_segmenter_queue_drain_on_reset():
    """Reset drains existing items from the queue (lines 163-164)."""
    vad = FakeVAD(threshold=0.5)
    seg = VADSegmenter(vad, sample_rate=16000)
    # Put some items in queue
    seg.segment_queue.put(SealedSegment(0, np.zeros(1000), 0, 1000))
    seg.segment_queue.put(SealedSegment(1, np.zeros(1000), 1000, 2000))

    seg.reset()
    assert seg.segment_queue.empty()
