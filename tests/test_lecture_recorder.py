# tests/test_lecture_recorder.py
import os
import struct
import tempfile
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from lecture_recorder import LectureRecorder


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@patch("lecture_recorder.sd")
def test_start_creates_wav_and_stream(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    assert rec.is_recording
    assert os.path.exists(wav_path)
    mock_sd.InputStream.assert_called_once()
    rec.stop()


@patch("lecture_recorder.sd")
def test_stop_closes_wav_and_stream(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    rec.stop()
    assert not rec.is_recording
    # WAV file should be valid
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000


@patch("lecture_recorder.sd")
def test_audio_callback_writes_to_wav(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Simulate audio callback
    chunk = np.random.randn(512).astype(np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    rec.stop()
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnframes() == 1024


@patch("lecture_recorder.sd")
def test_on_vad_chunk_callback(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    chunks = []
    rec.on_vad_chunk = lambda c: chunks.append(c)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    chunk = np.random.randn(512).astype(np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    rec.stop()
    assert len(chunks) == 1


@patch("lecture_recorder.sd")
def test_pause_and_resume(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    rec.pause()
    assert rec.is_paused
    assert not rec.is_recording
    rec.resume()
    assert rec.is_recording
    assert not rec.is_paused
    rec.stop()


@patch("lecture_recorder.sd")
def test_elapsed_seconds(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Write 16000 samples = 1 second
    chunk = np.zeros(16000, dtype=np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 16000, None, None)
    assert abs(rec.elapsed_seconds - 1.0) < 0.01
    rec.stop()


def test_recover_wav_header(tmp_dir):
    """Write raw PCM data with broken header, verify recovery fixes it."""
    wav_path = os.path.join(tmp_dir, "broken.wav")
    # Write a minimal WAV header + raw data
    samples = np.random.randn(16000).astype(np.int16)  # 1 second
    raw_data = samples.tobytes()
    # Write broken WAV (header says 0 data bytes)
    with open(wav_path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36))  # Wrong size
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16))
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", 0))  # Wrong size
        f.write(raw_data)

    LectureRecorder.recover_wav(wav_path)

    with wave.open(wav_path, "rb") as wf:
        assert wf.getnframes() == 16000
        assert wf.getnchannels() == 1


@patch("lecture_recorder.sd")
def test_start_already_recording(mock_sd, tmp_dir):
    """start() is no-op when already recording."""
    rec = LectureRecorder(sample_rate=16000)
    rec.is_recording = True
    rec.start(os.path.join(tmp_dir, "test.wav"))
    mock_sd.InputStream.assert_not_called()


@patch("lecture_recorder.sd")
def test_stop_when_not_recording(mock_sd, tmp_dir):
    """stop() is no-op when not recording and not paused."""
    rec = LectureRecorder(sample_rate=16000)
    rec.stop()  # Should not raise
    assert not rec.is_recording


@patch("lecture_recorder.sd")
def test_stop_flushes_and_fsyncs(mock_sd, tmp_dir):
    """stop() flushes and fsyncs the WAV file."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Write some data
    chunk = np.zeros(512, dtype=np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    rec.stop()
    assert rec._wav_file is None
    # File should be valid
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnframes() == 512


@patch("lecture_recorder.sd")
def test_stop_handles_fsync_error(mock_sd, tmp_dir):
    """stop() handles OSError during fsync gracefully (line 70-71)."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Replace _wav_file with a mock that raises OSError on flush/fsync
    # but allows close() to succeed
    mock_wav = MagicMock()
    mock_wav._file = MagicMock()
    mock_wav._file.flush.side_effect = OSError("disk full")
    rec._wav_file = mock_wav
    rec.stop()  # Should not raise -- OSError caught on lines 70-71
    assert rec._wav_file is None


@patch("lecture_recorder.sd")
def test_pause_when_not_recording(mock_sd, tmp_dir):
    """pause() is no-op when not recording."""
    rec = LectureRecorder(sample_rate=16000)
    rec.pause()
    assert not rec.is_paused


@patch("lecture_recorder.sd")
def test_resume_when_not_paused(mock_sd, tmp_dir):
    """resume() is no-op when not paused."""
    rec = LectureRecorder(sample_rate=16000)
    rec.resume()
    assert not rec.is_recording


def test_wav_path_property():
    """wav_path returns None before start."""
    rec = LectureRecorder(sample_rate=16000)
    assert rec.wav_path is None


@patch("lecture_recorder.sd")
def test_audio_callback_status_fires_error(mock_sd, tmp_dir):
    """Audio callback fires on_write_error for input status."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    errors = []
    rec.on_write_error = lambda msg: errors.append(msg)
    chunk = np.zeros(512, dtype=np.float32)
    # Status with "input" in message
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, "input overflow")
    rec.stop()
    assert len(errors) == 1
    assert "Microphone" in errors[0]


@patch("lecture_recorder.sd")
def test_audio_callback_fsync_interval(mock_sd, tmp_dir):
    """Audio callback fsyncs after FSYNC_INTERVAL_FRAMES."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Write enough data to trigger fsync (80000 frames)
    chunk = np.zeros(16000, dtype=np.float32)
    for _ in range(6):  # 6 * 16000 = 96000 > 80000
        rec._audio_callback(chunk.reshape(-1, 1), 16000, None, None)
    rec.stop()
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnframes() == 96000


@patch("lecture_recorder.sd")
def test_audio_callback_write_error(mock_sd, tmp_dir):
    """Audio callback fires on_write_error when WAV write fails."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    errors = []
    rec.on_write_error = lambda msg: errors.append(msg)
    # Close the wav file to force write error
    rec._wav_file.close()
    rec._wav_file = MagicMock()
    rec._wav_file.writeframes.side_effect = OSError("disk full")
    chunk = np.zeros(512, dtype=np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    assert len(errors) == 1
    assert "write failed" in errors[0].lower() or "Audio write failed" in errors[0]
    # Clean up
    rec._wav_file = None
    rec.is_recording = False


def test_recover_wav_no_data_chunk(tmp_dir):
    """recover_wav returns early if no 'data' chunk found."""
    wav_path = os.path.join(tmp_dir, "no_data.wav")
    with open(wav_path, "wb") as f:
        f.write(b"RIFF" + b"\x00" * 20)
    original_size = os.path.getsize(wav_path)
    LectureRecorder.recover_wav(wav_path)
    # File should be unchanged (no data chunk found)
    assert os.path.getsize(wav_path) == original_size


# ------------------------------------------------------------------
# Additional coverage for lecture_recorder.py
# ------------------------------------------------------------------

@patch("lecture_recorder.sd")
def test_start_already_recording_is_noop(mock_sd, tmp_dir):
    """start() returns immediately when already recording (line 35)."""
    rec = LectureRecorder(sample_rate=16000)
    rec.is_recording = True
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Should not create a new InputStream
    mock_sd.InputStream.assert_not_called()
    assert not os.path.exists(wav_path)


@patch("lecture_recorder.sd")
def test_stop_when_not_recording_or_paused(mock_sd):
    """stop() returns immediately when not recording and not paused (line 58)."""
    rec = LectureRecorder(sample_rate=16000)
    assert not rec.is_recording
    assert not rec.is_paused
    rec.stop()  # Should not raise


@patch("lecture_recorder.sd")
def test_stop_handles_fsync_oserror(mock_sd, tmp_dir):
    """stop() catches OSError during flush/fsync (lines 70-71)."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Replace wav_file with mock that raises on flush
    mock_wav = MagicMock()
    mock_wav._file = MagicMock()
    mock_wav._file.flush.side_effect = OSError("disk error")
    rec._wav_file = mock_wav
    rec.stop()  # Should not raise
    assert rec._wav_file is None


@patch("lecture_recorder.sd")
def test_pause_when_not_recording_noop(mock_sd):
    """pause() is no-op when not recording (line 78)."""
    rec = LectureRecorder(sample_rate=16000)
    rec.pause()
    assert not rec.is_paused


@patch("lecture_recorder.sd")
def test_resume_when_not_paused_noop(mock_sd):
    """resume() is no-op when not paused (line 89)."""
    rec = LectureRecorder(sample_rate=16000)
    rec.resume()
    assert not rec.is_recording


def test_wav_path_property_none():
    """wav_path property returns None before start (line 107)."""
    rec = LectureRecorder(sample_rate=16000)
    assert rec.wav_path is None


@patch("lecture_recorder.sd")
def test_audio_callback_status_with_input_keyword(mock_sd, tmp_dir):
    """Audio callback fires on_write_error when status has 'input' (lines 112-114)."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    errors = []
    rec.on_write_error = lambda msg: errors.append(msg)
    chunk = np.zeros(512, dtype=np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, "input overflow detected")
    rec.stop()
    assert len(errors) == 1
    assert "Microphone" in errors[0]


@patch("lecture_recorder.sd")
def test_audio_callback_status_without_input_no_error(mock_sd, tmp_dir):
    """Audio callback does not fire on_write_error when status lacks 'input' (line 113)."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    errors = []
    rec.on_write_error = lambda msg: errors.append(msg)
    chunk = np.zeros(512, dtype=np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, "output underflow")
    rec.stop()
    assert len(errors) == 0


@patch("lecture_recorder.sd")
def test_audio_callback_fsync_on_interval(mock_sd, tmp_dir):
    """Audio callback triggers fsync after FSYNC_INTERVAL_FRAMES (lines 128-133)."""
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Write exactly FSYNC_INTERVAL_FRAMES worth of data
    chunk_size = 16000
    chunks_needed = (LectureRecorder.FSYNC_INTERVAL_FRAMES // chunk_size) + 1
    chunk = np.zeros(chunk_size, dtype=np.float32)
    for _ in range(chunks_needed):
        rec._audio_callback(chunk.reshape(-1, 1), chunk_size, None, None)
    # After fsync, _frames_since_fsync should be reset or small
    assert rec._frames_since_fsync < LectureRecorder.FSYNC_INTERVAL_FRAMES
    rec.stop()


def test_recover_wav_valid_recovery(tmp_dir):
    """recover_wav correctly rebuilds WAV header (line 148+)."""
    wav_path = os.path.join(tmp_dir, "broken.wav")
    # Create a WAV with PCM data but broken header sizes
    samples = np.zeros(8000, dtype=np.int16)
    raw_data = samples.tobytes()
    with open(wav_path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 0))  # Wrong file size
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", 0))  # Wrong data size
        f.write(raw_data)

    LectureRecorder.recover_wav(wav_path)

    with wave.open(wav_path, "rb") as wf:
        assert wf.getnframes() == 8000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
