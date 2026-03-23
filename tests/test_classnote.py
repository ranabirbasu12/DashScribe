# tests/test_classnote.py
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from classnote import ClassNotePipeline


@pytest.fixture
def mock_deps():
    """Create mock dependencies for ClassNotePipeline."""
    transcriber = MagicMock()
    transcriber.transcribe_array = MagicMock(return_value={"text": "hello world"})
    transcriber._lock = threading.RLock()

    store = MagicMock()
    store.create_lecture = MagicMock(return_value=1)
    store.flush_segments = MagicMock()
    store.add_segment = MagicMock()
    store.apply_correction = MagicMock()
    store.update_lecture = MagicMock()
    store.touch_lecture = MagicMock()

    return transcriber, store


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def _configure_segmenter_mock(MockSegmenter):
    """Make the mock segmenter's queue behave like a real queue."""
    import queue as _q
    mock_instance = MockSegmenter.return_value
    mock_instance.segment_queue = _q.Queue()
    mock_instance.seal_final.return_value = None
    return mock_instance


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_start_creates_session(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    result = pipeline.start("Physics 201")
    assert result["lecture_id"] == 1
    assert pipeline.is_active
    store.create_lecture.assert_called_once()
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stop_finalizes_session(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")
    result = pipeline.stop()
    assert not pipeline.is_active
    store.update_lecture.assert_called()  # status -> stopped


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_pause_and_resume(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    pipeline.pause()
    assert pipeline.is_paused
    assert not pipeline.is_active

    pipeline.resume()
    assert pipeline.is_active
    assert not pipeline.is_paused
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_segment_callback_fires(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """When a segment is transcribed, the on_segment callback fires."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    segments_received = []
    pipeline.on_segment = lambda seg: segments_received.append(seg)

    pipeline.start("Test")
    # Simulate a sealed segment arriving
    from vad import SealedSegment
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    pipeline._process_stream_a(seg)
    pipeline.stop()

    assert len(segments_received) == 1
    assert segments_received[0]["ghost"] is True
    assert segments_received[0]["text"] == "hello world"


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_opportunistic(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B only runs when no Stream A work is pending."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    # Simulate 3 segments for correction
    for i in range(3):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        seg.start_sample = i * 16000
        seg.end_sample = (i + 1) * 16000
        pipeline._completed_segments.append(seg)

    # Stream B should merge and re-transcribe
    pipeline._try_stream_b_correction()

    # Transcriber should have been called for the correction
    assert transcriber.transcribe_array.called
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_discard_deletes_everything(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")
    pipeline.discard()
    assert not pipeline.is_active
    store.delete_lecture.assert_called_once()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_full_pipeline_lifecycle_with_real_store(MockVAD, MockSegmenter, MockRecorder, tmp_dir):
    """Start -> segments -> stop -> verify DB has data."""
    from lecture_store import LectureStore
    from vad import SealedSegment

    db_path = os.path.join(tmp_dir, "test_lifecycle.db")
    store = LectureStore(db_path)

    transcriber = MagicMock()
    transcriber.transcribe_array = MagicMock(return_value={"text": "lecture content"})
    transcriber._lock = MagicMock()

    _configure_segmenter_mock(MockSegmenter)
    MockRecorder.return_value.elapsed_seconds = 3.0
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    # Start session
    result = pipeline.start("Biology 101")
    lecture_id = result["lecture_id"]
    assert pipeline.is_active

    # Simulate segments being transcribed via Stream A
    for i in range(3):
        seg = SealedSegment(
            segment_index=i,
            mic_audio=np.random.randn(16000).astype(np.float32),
            start_sample=i * 16000,
            end_sample=(i + 1) * 16000,
        )
        pipeline._process_stream_a(seg)

    # Flush pending results to DB
    pipeline._flush_to_db()

    # Verify segments are in the database
    segments = store.get_segments(lecture_id)
    assert len(segments) == 3
    assert segments[0]["text"] == "lecture content"

    # Stop session
    pipeline.stop()
    assert not pipeline.is_active

    # Verify lecture status updated
    lecture = store.get_lecture(lecture_id)
    assert lecture["status"] == "stopped"


# --- Integration tests ---


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_periodic_flush_updates_duration_and_wordcount(MockVAD, MockSegmenter, MockRecorder, tmp_dir):
    """Periodic flush should update duration and word_count in DB, not just on stop."""
    from lecture_store import LectureStore
    from vad import SealedSegment

    db_path = os.path.join(tmp_dir, "test_flush.db")
    store = LectureStore(db_path)

    transcriber = MagicMock()
    transcriber.transcribe_array = MagicMock(return_value={"text": "one two three"})

    _configure_segmenter_mock(MockSegmenter)
    MockRecorder.return_value.elapsed_seconds = 10.5
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    result = pipeline.start("Flush Test")
    lid = result["lecture_id"]

    # Process a segment
    seg = SealedSegment(segment_index=0, mic_audio=np.random.randn(16000).astype(np.float32),
                        start_sample=0, end_sample=16000)
    pipeline._process_stream_a(seg)

    # Trigger flush (simulates periodic timer)
    pipeline._flush_to_db()

    lecture = store.get_lecture(lid)
    assert lecture["duration_seconds"] == 10.5
    assert lecture["word_count"] == 3
    pipeline.stop()


def test_crash_recovery_detects_stale_lectures(tmp_dir):
    """Lectures stuck in 'recording' with old updated_at are detected as crashed."""
    from lecture_store import LectureStore

    db_path = os.path.join(tmp_dir, "test_crash.db")
    store = LectureStore(db_path)
    lid = store.create_lecture("Crash Test", "/fake/audio.wav")

    # Manually set updated_at to 10 minutes ago
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE lectures SET updated_at = datetime('now', '-10 minutes') WHERE id = ?",
        (lid,),
    )
    conn.commit()
    conn.close()

    crashed = store.detect_crashed_lectures(stale_minutes=5)
    assert len(crashed) == 1
    assert crashed[0]["id"] == lid

    store.mark_recovered(lid)
    lecture = store.get_lecture(lid)
    assert lecture["status"] == "recovered"


def test_delete_label_removes_from_all_lectures(tmp_dir):
    """Deleting a label permanently removes it and all assignments."""
    from lecture_store import LectureStore

    db_path = os.path.join(tmp_dir, "test_labels.db")
    store = LectureStore(db_path)
    lid = store.create_lecture("Label Test", "/fake.wav")
    label_id = store.create_label("Physics", "#3b82f6")
    store.assign_label(lid, label_id)

    assert len(store.get_lecture_labels(lid)) == 1

    store.delete_label(label_id)
    assert len(store.get_lecture_labels(lid)) == 0
    assert len(store.list_labels()) == 0


def test_replace_segments(tmp_dir):
    """replace_segments clears old segments and writes new ones."""
    from lecture_store import LectureStore

    db_path = os.path.join(tmp_dir, "test_replace.db")
    store = LectureStore(db_path)
    lid = store.create_lecture("Replace Test", "/fake.wav")

    # Add original segments
    store.add_segment(lid, 0, "original text", 0, 1000)
    store.add_segment(lid, 1, "more text", 1000, 2000)
    assert len(store.get_segments(lid)) == 2

    # Replace with new segments
    store.replace_segments(lid, [
        {"index": 0, "text": "new text one", "start_ms": 0, "end_ms": 1500},
        {"index": 1, "text": "new text two", "start_ms": 1500, "end_ms": 3000},
        {"index": 2, "text": "new text three", "start_ms": 3000, "end_ms": 4500},
    ])
    segments = store.get_segments(lid)
    assert len(segments) == 3
    assert segments[0]["text"] == "new text one"
    assert segments[2]["text"] == "new text three"


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_disk_space_check_fires_error(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Disk space check should call on_error when space is low."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    errors = []
    pipeline.on_error = lambda msg, recoverable: errors.append((msg, recoverable))

    # Mock os.statvfs to return very low space
    fake_stat = MagicMock()
    fake_stat.f_bavail = 10  # very few blocks
    fake_stat.f_frsize = 4096  # 4KB blocks = 40KB free

    with patch("classnote.os.statvfs", return_value=fake_stat):
        pipeline._check_disk_space()

    assert len(errors) == 1
    assert "critically low" in errors[0][0].lower()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_error_callback_propagates_from_recorder(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Recorder write errors should reach the pipeline's on_error."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    errors = []
    pipeline.on_error = lambda msg, recoverable: errors.append(msg)

    pipeline.start("Error Test")
    # Simulate recorder error
    pipeline._on_recorder_error("Audio write failed: disk full")
    assert len(errors) == 1
    assert "disk full" in errors[0]
    pipeline.stop()


# --- Additional coverage tests ---


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_start_while_active_raises(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Starting a session while one is active should raise RuntimeError."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("First")
    with pytest.raises(RuntimeError, match="already active"):
        pipeline.start("Second")
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_start_fires_on_status_recording(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """on_status('recording') fires during start."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    statuses = []
    pipeline.on_status = lambda s: statuses.append(s)
    pipeline.start("Test")
    assert "recording" in statuses
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stop_fires_on_status_stopped(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """on_status('stopped') fires during stop."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    statuses = []
    pipeline.on_status = lambda s: statuses.append(s)
    pipeline.start("Test")
    pipeline.stop()
    assert "stopped" in statuses


def test_stop_when_not_active():
    """stop() when not active or paused returns empty dict."""
    transcriber = MagicMock()
    store = MagicMock()
    with patch("classnote.SileroVAD"):
        pipeline = ClassNotePipeline(transcriber, store)
    assert pipeline.stop() == {}


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stop_with_seal_final_segment(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """stop() should enqueue seal_final segment if not None."""
    import queue as _q
    mock_seg_instance = MockSegmenter.return_value
    mock_seg_instance.segment_queue = _q.Queue()

    from vad import SealedSegment
    final_seg = SealedSegment(
        segment_index=99,
        mic_audio=np.random.randn(8000).astype(np.float32),
        start_sample=0,
        end_sample=8000,
    )
    mock_seg_instance.seal_final.return_value = final_seg

    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")
    pipeline.stop()
    # seal_final was called and segment was put on queue
    mock_seg_instance.seal_final.assert_called_once()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_pause_when_not_active_noop(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """pause() when not active should be a no-op."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    # Don't start -- pause should be a no-op
    pipeline.pause()
    assert not pipeline.is_paused


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_pause_fires_on_status(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """pause() should fire on_status('paused')."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    statuses = []
    pipeline.on_status = lambda s: statuses.append(s)
    pipeline.start("Test")
    pipeline.pause()
    assert "paused" in statuses
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_resume_when_not_paused_noop(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """resume() when not paused should be a no-op."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")
    # Not paused -- resume should be no-op
    pipeline.resume()
    assert pipeline.is_active
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_resume_fires_on_status(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """resume() should fire on_status('recording')."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    statuses = []
    pipeline.on_status = lambda s: statuses.append(s)
    pipeline.start("Test")
    pipeline.pause()
    pipeline.resume()
    # Should have recording (start), paused, recording (resume)
    assert statuses.count("recording") == 2
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_start_rename_success(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """When os.rename succeeds, store and recorder path are updated."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    # os.rename will succeed because we're using a real tmp_dir
    # We need to create the initial file so rename works
    with patch("classnote.os.rename") as mock_rename:
        mock_rename.side_effect = None  # success
        result = pipeline.start("Test")
        # store.update_lecture should be called with audio_path
        store.update_lecture.assert_called()
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_a_empty_text_skipped(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream A should skip segments with empty transcription."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    transcriber.transcribe_array.return_value = {"text": "  "}  # whitespace only
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    segments_received = []
    pipeline.on_segment = lambda seg: segments_received.append(seg)

    pipeline.start("Test")
    from vad import SealedSegment
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    pipeline._process_stream_a(seg)
    pipeline.stop()
    assert len(segments_received) == 0
    assert len(pipeline._pending_results) == 0


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_paused_noop(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B should not run when paused."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    # Add 3 segments
    for i in range(3):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        seg.start_sample = i * 16000
        seg.end_sample = (i + 1) * 16000
        pipeline._completed_segments.append(seg)

    pipeline.pause()
    transcriber.transcribe_array.reset_mock()
    pipeline._try_stream_b_correction()
    # Should not call transcriber since paused
    transcriber.transcribe_array.assert_not_called()
    pipeline._paused = False
    pipeline._active = True
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_not_enough_segments(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B should not run with fewer than 3 segments."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    # Add only 2 segments
    for i in range(2):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        pipeline._completed_segments.append(seg)

    transcriber.transcribe_array.reset_mock()
    pipeline._try_stream_b_correction()
    transcriber.transcribe_array.assert_not_called()
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_not_enough_uncorrected(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B should not run when uncorrected segments < 3."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    for i in range(3):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        seg.start_sample = i * 16000
        seg.end_sample = (i + 1) * 16000
        pipeline._completed_segments.append(seg)

    # Mark all as corrected
    pipeline._last_corrected_idx = 3

    transcriber.transcribe_array.reset_mock()
    pipeline._try_stream_b_correction()
    transcriber.transcribe_array.assert_not_called()
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_transcription_failure(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B should silently handle transcription exceptions."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    for i in range(3):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        seg.start_sample = i * 16000
        seg.end_sample = (i + 1) * 16000
        pipeline._completed_segments.append(seg)

    transcriber.transcribe_array.side_effect = RuntimeError("model error")
    pipeline._try_stream_b_correction()
    # Should not raise, should silently return
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_empty_text(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B should skip correction if merged transcription is empty."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    for i in range(3):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        seg.start_sample = i * 16000
        seg.end_sample = (i + 1) * 16000
        pipeline._completed_segments.append(seg)

    transcriber.transcribe_array.return_value = {"text": "  "}
    initial_counter = pipeline._correction_counter
    pipeline._try_stream_b_correction()
    assert pipeline._correction_counter == initial_counter
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_on_correction_callback(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B should fire on_correction callback."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    transcriber.transcribe_array.return_value = {"text": "corrected text"}
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    corrections = []
    pipeline.on_correction = lambda c: corrections.append(c)

    for i in range(3):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        seg.start_sample = i * 16000
        seg.end_sample = (i + 1) * 16000
        pipeline._completed_segments.append(seg)

    pipeline._try_stream_b_correction()
    assert len(corrections) == 1
    assert corrections[0]["text"] == "corrected text"
    assert corrections[0]["start_index"] == 0
    assert corrections[0]["end_index"] == 2
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_worker_loop_processes_segments(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Worker loop should process segments from queue and trigger Stream B."""
    import queue as _q
    mock_seg_instance = MockSegmenter.return_value
    mock_seg_instance.segment_queue = _q.Queue()
    mock_seg_instance.seal_final.return_value = None

    transcriber, store = mock_deps
    transcriber.transcribe_array.return_value = {"text": "hello"}
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    # Put a segment and sentinel on queue
    from vad import SealedSegment
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    mock_seg_instance.segment_queue.put(seg)
    mock_seg_instance.segment_queue.put(None)  # sentinel

    # Give the worker time to process
    time.sleep(0.5)
    pipeline.stop()
    assert len(pipeline._completed_segments) >= 1


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_worker_loop_error_handling(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Worker loop should call on_error when segment processing fails."""
    import queue as _q
    mock_seg_instance = MockSegmenter.return_value
    mock_seg_instance.segment_queue = _q.Queue()
    mock_seg_instance.seal_final.return_value = None

    transcriber, store = mock_deps
    transcriber.transcribe_array.side_effect = RuntimeError("transcription failed")
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    errors = []
    pipeline.on_error = lambda msg, recoverable: errors.append(msg)

    pipeline.start("Test")

    from vad import SealedSegment
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    mock_seg_instance.segment_queue.put(seg)
    mock_seg_instance.segment_queue.put(None)  # sentinel

    time.sleep(0.5)
    pipeline.stop()
    assert len(errors) >= 1
    assert "transcription failed" in errors[0]


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_periodic_flush(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """_periodic_flush should flush to DB, check disk, and reschedule."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    with patch.object(pipeline, "_flush_to_db") as mock_flush, \
         patch.object(pipeline, "_check_disk_space") as mock_disk, \
         patch.object(pipeline, "_schedule_flush") as mock_sched:
        pipeline._periodic_flush()
        mock_flush.assert_called_once()
        mock_disk.assert_called_once()
        mock_sched.assert_called_once()
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_flush_to_db_no_lecture_id(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """_flush_to_db should return early if no lecture_id."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline._lecture_id = None
    pipeline._flush_to_db()
    store.update_lecture.assert_not_called()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_flush_to_db_store_exception(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """_flush_to_db should handle store exceptions gracefully."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    store.update_lecture.side_effect = RuntimeError("DB error")
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    from vad import SealedSegment
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    transcriber.transcribe_array.return_value = {"text": "hello"}
    pipeline._process_stream_a(seg)

    # Should not raise even though store throws
    pipeline._flush_to_db()
    # Reset side_effect so stop() can work
    store.update_lecture.side_effect = None
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_flush_to_db_flush_segments_exception(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """_flush_to_db should handle flush_segments exceptions gracefully."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    store.flush_segments.side_effect = RuntimeError("write error")
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    transcriber.transcribe_array.return_value = {"text": "hello"}
    from vad import SealedSegment
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    pipeline._process_stream_a(seg)

    # Should not raise
    pipeline._flush_to_db()
    store.flush_segments.side_effect = None
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_disk_space_warning_500mb(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Disk space between 100-500MB should fire a recoverable warning."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    errors = []
    pipeline.on_error = lambda msg, recoverable: errors.append((msg, recoverable))

    fake_stat = MagicMock()
    fake_stat.f_bavail = 60000  # ~234MB free with 4096 block size
    fake_stat.f_frsize = 4096

    with patch("classnote.os.statvfs", return_value=fake_stat):
        pipeline._check_disk_space()

    assert len(errors) == 1
    assert errors[0][1] is True  # recoverable
    assert "low disk space" in errors[0][0].lower()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_disk_space_check_os_error(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Disk space check should handle OSError gracefully."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    with patch("classnote.os.statvfs", side_effect=OSError("no fs")):
        pipeline._check_disk_space()  # should not raise


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_discard_deletes_audio_file(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """discard() should delete the audio file."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    # Create a fake audio file
    fake_wav = os.path.join(tmp_dir, "fake.wav")
    with open(fake_wav, "w") as f:
        f.write("fake")
    MockRecorder.return_value.wav_path = fake_wav

    pipeline.discard()
    assert not os.path.exists(fake_wav)
    assert pipeline._lecture_id is None


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_discard_handles_missing_audio_file(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """discard() should handle missing audio file gracefully (OSError)."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    MockRecorder.return_value.wav_path = "/nonexistent/path.wav"
    pipeline.discard()  # should not raise


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_retranscribe_lecture_not_found(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """retranscribe should raise ValueError when lecture not found."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    store.get_lecture.return_value = None
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    with pytest.raises(ValueError, match="Lecture not found"):
        pipeline.retranscribe(999)


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_retranscribe_audio_not_found(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """retranscribe should raise ValueError when audio file missing."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    store.get_lecture.return_value = {"audio_path": "/nonexistent/audio.wav"}
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    with pytest.raises(ValueError, match="Audio file not found"):
        pipeline.retranscribe(1)


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_retranscribe_no_audio_path(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """retranscribe should raise ValueError when audio_path is empty."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps
    store.get_lecture.return_value = {"audio_path": ""}
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    with pytest.raises(ValueError, match="Audio file not found"):
        pipeline.retranscribe(1)


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_retranscribe_success(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """retranscribe should re-transcribe audio and return results."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps

    # Create a real WAV file
    import wave
    wav_path = os.path.join(tmp_dir, "test.wav")
    audio_data = np.random.randn(32000).astype(np.float32)
    int16_data = (audio_data * 32767).astype(np.int16)
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(int16_data.tobytes())

    store.get_lecture.return_value = {"audio_path": wav_path}
    transcriber.transcribe_array.return_value = {"text": "re-transcribed text"}

    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    # We need to mock VADSegmenter for retranscribe since it creates a new one
    from vad import SealedSegment
    import queue as _q

    mock_new_segmenter = MagicMock()
    mock_new_segmenter.segment_queue = _q.Queue()
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    mock_new_segmenter.segment_queue.put(seg)
    mock_new_segmenter.segment_queue.put(None)  # sentinel
    mock_new_segmenter.seal_final.return_value = None
    MockSegmenter.return_value = mock_new_segmenter

    progress_calls = []
    result = pipeline.retranscribe(1, on_progress=lambda cur, total: progress_calls.append((cur, total)))

    assert result["segments"] == 1
    assert result["word_count"] == 2  # "re-transcribed text"
    store.replace_segments.assert_called_once()
    assert len(progress_calls) == 1


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_retranscribe_empty_text_skipped(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """retranscribe should skip segments with empty text."""
    _configure_segmenter_mock(MockSegmenter)
    transcriber, store = mock_deps

    import wave
    wav_path = os.path.join(tmp_dir, "test.wav")
    audio_data = np.random.randn(16000).astype(np.float32)
    int16_data = (audio_data * 32767).astype(np.int16)
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(int16_data.tobytes())

    store.get_lecture.return_value = {"audio_path": wav_path}
    transcriber.transcribe_array.return_value = {"text": "  "}  # empty

    from vad import SealedSegment
    import queue as _q
    mock_new_segmenter = MagicMock()
    mock_new_segmenter.segment_queue = _q.Queue()
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    mock_new_segmenter.segment_queue.put(seg)
    mock_new_segmenter.segment_queue.put(None)
    mock_new_segmenter.seal_final.return_value = None
    MockSegmenter.return_value = mock_new_segmenter

    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    result = pipeline.retranscribe(1)
    assert result["segments"] == 0
