# tests/test_recorder.py
import numpy as np
from unittest.mock import patch, MagicMock
from recorder import AudioRecorder

SAMPLE_RATE = 16000


def test_recorder_initializes_with_correct_settings():
    rec = AudioRecorder()
    assert rec.sample_rate == SAMPLE_RATE
    assert rec.channels == 1
    assert rec.is_recording is False


def test_recorder_start_sets_recording_flag():
    rec = AudioRecorder()
    with patch.object(rec, '_stream', create=True):
        with patch('recorder.sd.InputStream') as mock_stream:
            mock_instance = MagicMock()
            mock_stream.return_value = mock_instance
            rec.start()
            assert rec.is_recording is True
            mock_instance.start.assert_called_once()


def test_recorder_stop_returns_wav_path():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    with patch('recorder.sd.InputStream'):
        rec._stream = MagicMock()
        path = rec.stop()
        assert path.endswith('.wav')
        assert rec.is_recording is False


def test_recorder_callback_appends_chunks():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    fake_data = np.random.randn(1600, 1).astype(np.float32)
    rec._audio_callback(fake_data, 1600, None, None)
    assert len(rec._chunks) == 1
    np.testing.assert_array_equal(rec._chunks[0], fake_data)


def test_recorder_stop_empty_returns_empty_string():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    rec._stream = MagicMock()
    path = rec.stop()
    assert path == ""


def test_audio_callback_fires_amplitude_callback():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    received = []
    rec.on_amplitude = lambda val: received.append(val)
    # Create a chunk with known RMS
    fake_data = np.ones((1600, 1), dtype=np.float32) * 0.5
    rec._audio_callback(fake_data, 1600, None, None)
    assert len(received) == 1
    assert abs(received[0] - 0.5) < 0.01


def test_amplitude_callback_not_called_when_not_recording():
    rec = AudioRecorder()
    rec.is_recording = False
    received = []
    rec.on_amplitude = lambda val: received.append(val)
    fake_data = np.ones((1600, 1), dtype=np.float32) * 0.5
    rec._audio_callback(fake_data, 1600, None, None)
    assert received == []


def test_vad_chunk_callback_fires_during_recording():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    received = []
    rec.on_vad_chunk = lambda chunk: received.append(chunk)
    fake_data = np.ones((1600, 1), dtype=np.float32) * 0.5
    rec._audio_callback(fake_data, 1600, None, None)
    assert len(received) == 1
    np.testing.assert_array_equal(received[0], fake_data)


def test_stop_raw_returns_mic_and_sys_audio():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    rec._sys_capture = None  # No system audio
    mic, sys = rec.stop_raw()
    assert mic is not None
    assert len(mic) == 1600
    assert sys is None
    assert rec.is_recording is False


def test_stop_raw_empty_returns_none():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    rec._stream = MagicMock()
    mic, sys = rec.stop_raw()
    assert mic is None
    assert sys is None


def test_get_sys_audio_chunks_returns_none_without_capture():
    rec = AudioRecorder()
    assert rec.get_sys_audio_chunks() is None


def test_get_sys_audio_chunks_returns_list():
    rec = AudioRecorder()
    fake_capture = MagicMock()
    fake_capture._chunks = [np.zeros(1000, dtype=np.float32)]
    rec._sys_capture = fake_capture
    result = rec.get_sys_audio_chunks()
    assert result is fake_capture._chunks


def test_get_wav_duration():
    """get_wav_duration returns correct duration for a WAV file."""
    from recorder import get_wav_duration
    import tempfile
    from scipy.io import wavfile
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    audio = np.zeros(16000, dtype=np.int16)  # 1 second at 16kHz
    wavfile.write(tmp.name, 16000, audio)
    tmp.close()
    duration = get_wav_duration(tmp.name)
    assert abs(duration - 1.0) < 0.01
    import os
    os.unlink(tmp.name)


def test_audio_callback_prints_status(capsys):
    """Audio callback prints status when status is not None."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    fake_data = np.zeros((1600, 1), dtype=np.float32)
    rec._audio_callback(fake_data, 1600, None, "input overflow")
    captured = capsys.readouterr()
    assert "input overflow" in captured.out


def test_start_already_recording():
    """start() is no-op when already recording."""
    rec = AudioRecorder()
    rec.is_recording = True
    # Should return without doing anything
    rec.start()
    assert rec.is_recording is True


def test_start_system_audio_failure():
    """start() continues even when system audio capture fails."""
    rec = AudioRecorder()
    with patch('recorder.sd.InputStream') as mock_stream:
        mock_instance = MagicMock()
        mock_stream.return_value = mock_instance
        # Mock system_audio import to raise
        with patch.dict('sys.modules', {'system_audio': MagicMock(side_effect=ImportError("no module"))}):
            rec.start()
        assert rec.is_recording is True


def test_start_stream_start_failure():
    """start() cleans up when stream.start() fails."""
    rec = AudioRecorder()
    with patch('recorder.sd.InputStream') as mock_stream:
        mock_instance = MagicMock()
        mock_instance.start.side_effect = RuntimeError("audio device error")
        mock_stream.return_value = mock_instance
        import pytest
        with pytest.raises(RuntimeError, match="audio device error"):
            rec.start()
        assert rec._stream is None
        assert rec._sys_capture is None


def test_start_stream_start_failure_with_sys_capture():
    """start() cleans up system capture when stream.start() fails."""
    import sys as _sys
    rec = AudioRecorder()
    with patch('recorder.sd.InputStream') as mock_stream:
        mock_instance = MagicMock()
        mock_instance.start.side_effect = RuntimeError("fail")
        mock_stream.return_value = mock_instance
        # Mock system_audio module so it's importable in recorder.start()
        mock_sys_capture = MagicMock()
        mock_sa_mod = MagicMock()
        mock_sa_mod.SystemAudioCapture.return_value = mock_sys_capture
        with patch.dict('sys.modules', {'system_audio': mock_sa_mod}):
            import pytest
            with pytest.raises(RuntimeError):
                rec.start()
            mock_sys_capture.stop.assert_called_once()


def test_stop_with_sys_capture():
    """stop() gets system audio from sys_capture and applies AEC."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    mock_sys = MagicMock()
    mock_sys.stop.return_value = np.zeros(1600, dtype=np.float32)
    rec._sys_capture = mock_sys
    mock_aec_mod = MagicMock()
    mock_aec_mod.nlms_echo_cancel.return_value = np.zeros(1600, dtype=np.float32)
    mock_aec_mod.noise_gate.return_value = np.zeros(1600, dtype=np.float32)
    with patch.dict('sys.modules', {'aec': mock_aec_mod}):
        path = rec.stop()
    assert path.endswith('.wav')


def test_stop_sys_capture_exception():
    """stop() handles sys_capture.stop() exceptions gracefully."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    mock_sys = MagicMock()
    mock_sys.stop.side_effect = Exception("capture error")
    rec._sys_capture = mock_sys
    path = rec.stop()
    assert path.endswith('.wav')


def test_stop_raw_with_sys_capture():
    """stop_raw() returns system audio when sys_capture is available."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    mock_sys = MagicMock()
    sys_audio = np.zeros(1600, dtype=np.float32)
    mock_sys.stop.return_value = sys_audio
    rec._sys_capture = mock_sys
    mic, sys_out = rec.stop_raw()
    assert mic is not None
    assert sys_out is sys_audio


def test_stop_raw_sys_capture_exception():
    """stop_raw() handles sys_capture.stop() exceptions gracefully."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    mock_sys = MagicMock()
    mock_sys.stop.side_effect = Exception("fail")
    rec._sys_capture = mock_sys
    mic, sys_out = rec.stop_raw()
    assert mic is not None
    assert sys_out is None


# ------------------------------------------------------------------
# Additional coverage: lines 15, 32, 48, 62-64, 68-81, 104-107, 117-122, 155-158
# ------------------------------------------------------------------

def test_get_wav_duration_returns_float():
    """get_wav_duration() returns correct float (line 15)."""
    from recorder import get_wav_duration
    import tempfile
    from scipy.io import wavfile
    import os
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    audio = np.zeros(32000, dtype=np.int16)  # 2 seconds at 16kHz
    wavfile.write(tmp.name, 16000, audio)
    tmp.close()
    duration = get_wav_duration(tmp.name)
    assert abs(duration - 2.0) < 0.01
    os.unlink(tmp.name)


def test_audio_callback_status_prints(capsys):
    """Audio callback prints status message (line 32)."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    fake_data = np.zeros((1600, 1), dtype=np.float32)
    rec._audio_callback(fake_data, 1600, None, "output underflow")
    captured = capsys.readouterr()
    assert "output underflow" in captured.out


def test_start_already_recording_noop():
    """start() returns immediately when already recording (line 48)."""
    rec = AudioRecorder()
    rec.is_recording = True
    with patch('recorder.sd.InputStream') as mock_stream:
        rec.start()
        mock_stream.assert_not_called()


def test_start_system_audio_import_error(capsys):
    """start() handles system_audio import error gracefully (lines 62-64)."""
    rec = AudioRecorder()
    with patch('recorder.sd.InputStream') as mock_stream:
        mock_instance = MagicMock()
        mock_stream.return_value = mock_instance
        with patch('builtins.__import__', side_effect=lambda name, *a, **kw:
                   (_ for _ in ()).throw(ImportError("no system_audio")) if name == "system_audio"
                   else __import__(name, *a, **kw)):
            rec.start()
        assert rec.is_recording is True
        assert rec._sys_capture is None


def test_start_stream_failure_closes_stream(capsys):
    """start() closes stream on failure (lines 68-81)."""
    rec = AudioRecorder()
    with patch('recorder.sd.InputStream') as mock_stream:
        mock_instance = MagicMock()
        mock_instance.start.side_effect = RuntimeError("device error")
        mock_stream.return_value = mock_instance
        import pytest
        with pytest.raises(RuntimeError):
            rec.start()
        mock_instance.close.assert_called_once()
        assert rec._stream is None


def test_start_stream_failure_with_close_exception():
    """start() handles stream.close() exception during cleanup (lines 70-73)."""
    rec = AudioRecorder()
    with patch('recorder.sd.InputStream') as mock_stream:
        mock_instance = MagicMock()
        mock_instance.start.side_effect = RuntimeError("device error")
        mock_instance.close.side_effect = Exception("close failed")
        mock_stream.return_value = mock_instance
        import pytest
        with pytest.raises(RuntimeError):
            rec.start()
        assert rec._stream is None


def test_start_stream_failure_with_sys_capture_stop_error():
    """start() handles sys_capture.stop() exception during cleanup (lines 76-80)."""
    import sys as _sys
    rec = AudioRecorder()
    with patch('recorder.sd.InputStream') as mock_stream:
        mock_instance = MagicMock()
        mock_instance.start.side_effect = RuntimeError("fail")
        mock_stream.return_value = mock_instance
        mock_sys_capture = MagicMock()
        mock_sys_capture.stop.side_effect = Exception("stop failed")
        mock_sa_mod = MagicMock()
        mock_sa_mod.SystemAudioCapture.return_value = mock_sys_capture
        with patch.dict('sys.modules', {'system_audio': mock_sa_mod}):
            import pytest
            with pytest.raises(RuntimeError):
                rec.start()
            assert rec._sys_capture is None


def test_stop_sys_capture_returns_audio():
    """stop() gets system audio from sys_capture (lines 104-107)."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    mock_sys = MagicMock()
    sys_audio = np.ones(1600, dtype=np.float32) * 0.5
    mock_sys.stop.return_value = sys_audio
    rec._sys_capture = mock_sys
    # Without AEC module, it should still produce a wav
    path = rec.stop()
    assert path.endswith('.wav')


def test_stop_aec_failure_uses_raw_audio(capsys):
    """stop() falls back to raw audio when AEC fails (lines 117-122)."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    mock_sys = MagicMock()
    mock_sys.stop.return_value = np.zeros(1600, dtype=np.float32)
    rec._sys_capture = mock_sys
    mock_aec = MagicMock()
    mock_aec.nlms_echo_cancel.side_effect = Exception("AEC failure")
    with patch.dict('sys.modules', {'aec': mock_aec}):
        path = rec.stop()
    assert path.endswith('.wav')
    captured = capsys.readouterr()
    assert "AEC failed" in captured.out


def test_stop_raw_with_sys_capture_exception():
    """stop_raw() handles sys_capture.stop() exception (lines 155-158)."""
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.ones((1600, 1), dtype=np.float32)]
    rec._stream = MagicMock()
    mock_sys = MagicMock()
    mock_sys.stop.side_effect = RuntimeError("stop error")
    rec._sys_capture = mock_sys
    mic, sys_out = rec.stop_raw()
    assert mic is not None
    assert sys_out is None


def test_reconnect_stream_swaps_input_stream():
    rec = AudioRecorder()
    old_stream = MagicMock()
    rec._stream = old_stream
    rec.is_recording = True

    with patch('recorder.sd.InputStream') as mock_stream_cls:
        new_stream = MagicMock()
        mock_stream_cls.return_value = new_stream
        rec.reconnect_stream()

    old_stream.stop.assert_called_once()
    old_stream.close.assert_called_once()
    new_stream.start.assert_called_once()
    assert rec._stream is new_stream
    assert rec.is_recording is True
    assert rec._device_lost is False


def test_reconnect_stream_when_not_recording_is_noop():
    rec = AudioRecorder()
    rec._stream = None
    rec.is_recording = False
    with patch('recorder.sd.InputStream') as mock_stream_cls:
        rec.reconnect_stream()
        mock_stream_cls.assert_not_called()


def test_reconnect_stream_handles_dead_old_stream():
    rec = AudioRecorder()
    dead_stream = MagicMock()
    dead_stream.stop.side_effect = Exception("PortAudio error")
    dead_stream.close.side_effect = Exception("PortAudio error")
    rec._stream = dead_stream
    rec.is_recording = True

    with patch('recorder.sd.InputStream') as mock_stream_cls:
        new_stream = MagicMock()
        mock_stream_cls.return_value = new_stream
        # Should NOT raise despite old stream errors
        rec.reconnect_stream()

    new_stream.start.assert_called_once()
    assert rec._stream is new_stream


def test_reconnect_stream_failure_sets_device_lost():
    rec = AudioRecorder()
    rec._stream = MagicMock()
    rec.is_recording = True

    with patch('recorder.sd.InputStream', side_effect=Exception("No device")):
        rec.reconnect_stream()

    assert rec._device_lost is True
    assert rec.is_recording is False
    assert rec._stream is None


def test_recorder_initializes_device_lost_false():
    rec = AudioRecorder()
    assert rec._device_lost is False
