# tests/test_meeting_recorder.py
"""Tests for MeetingRecorder — dual-stream audio capture."""
import os
import sys
import tempfile
import wave
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest


# Mock macOS-specific modules before import
@pytest.fixture(autouse=True)
def mock_macos_modules():
    mock_objc = MagicMock()
    mock_core_media = MagicMock()
    mock_screen_capture = MagicMock()
    mock_foundation = MagicMock()
    mock_sd = MagicMock()

    modules = {
        "objc": mock_objc,
        "CoreMedia": mock_core_media,
        "ScreenCaptureKit": mock_screen_capture,
        "Foundation": mock_foundation,
        "sounddevice": mock_sd,
    }

    with patch.dict("sys.modules", modules):
        yield {"sounddevice": mock_sd, "ScreenCaptureKit": mock_screen_capture}


def _import_meeting_recorder():
    for mod_name in ["meeting_recorder", "system_audio"]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import meeting_recorder
    return meeting_recorder


class TestMeetingRecorder:
    def test_init_listen_mode(self, mock_macos_modules):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="listen")
        assert rec.mode == "listen"
        assert not rec.is_recording
        assert not rec.is_paused

    def test_init_full_mode(self, mock_macos_modules):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="full")
        assert rec.mode == "full"

    def test_start_listen_mode(self, mock_macos_modules, tmp_path):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="listen", app_bundle_id="us.zoom.xos")

        # Mock system audio capture
        mock_sys = MagicMock()
        rec._sys_capture = mock_sys

        sys_path = str(tmp_path / "system.wav")
        rec.start(system_wav_path=sys_path)

        assert rec.is_recording
        mock_sys.start.assert_called_once_with(app_bundle_id="us.zoom.xos")

    def test_start_full_mode(self, mock_macos_modules, tmp_path):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="full", app_bundle_id="us.zoom.xos")

        mock_sys = MagicMock()
        rec._sys_capture = mock_sys

        sys_path = str(tmp_path / "system.wav")
        mic_path = str(tmp_path / "mic.wav")
        rec.start(system_wav_path=sys_path, mic_wav_path=mic_path)

        assert rec.is_recording
        mock_sys.start.assert_called_once_with(app_bundle_id="us.zoom.xos")
        # Mic stream should be started in full mode
        assert rec._mic_recorder is not None

    def test_stop_returns_paths(self, mock_macos_modules, tmp_path):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="listen")

        mock_sys = MagicMock()
        mock_sys.stop.return_value = np.array([0.1, 0.2], dtype=np.float32)
        rec._sys_capture = mock_sys

        sys_path = str(tmp_path / "system.wav")
        rec.start(system_wav_path=sys_path)
        result = rec.stop()

        assert "system_audio_path" in result
        assert result["system_audio_path"] == sys_path
        assert not rec.is_recording

    def test_stop_full_mode_returns_both_paths(self, mock_macos_modules, tmp_path):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="full")

        mock_sys = MagicMock()
        mock_sys.stop.return_value = np.array([0.1], dtype=np.float32)
        rec._sys_capture = mock_sys

        mock_mic_stream = MagicMock()
        rec._mic_stream = mock_mic_stream
        rec._mic_recorder = mock_mic_stream

        sys_path = str(tmp_path / "system.wav")
        mic_path = str(tmp_path / "mic.wav")
        rec._system_wav_path = sys_path
        rec._mic_wav_path = mic_path
        rec.is_recording = True

        result = rec.stop()
        assert result["system_audio_path"] == sys_path
        assert result["mic_audio_path"] == mic_path
        mock_mic_stream.stop.assert_called_once()

    def test_pause_resume(self, mock_macos_modules, tmp_path):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="listen")

        mock_sys = MagicMock()
        rec._sys_capture = mock_sys

        sys_path = str(tmp_path / "system.wav")
        rec.start(system_wav_path=sys_path)
        assert rec.is_recording

        rec.pause()
        assert rec.is_paused
        assert not rec.is_recording

        rec.resume()
        assert rec.is_recording
        assert not rec.is_paused

    def test_on_system_audio_callback(self, mock_macos_modules):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="listen")
        chunks = []
        rec.on_system_audio = lambda data: chunks.append(data)
        audio = np.array([0.1, 0.2], dtype=np.float32)
        rec._on_system_chunk(audio)
        assert len(chunks) == 1

    def test_on_mic_audio_callback(self, mock_macos_modules):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="full")
        chunks = []
        rec.on_mic_audio = lambda data: chunks.append(data)
        audio = np.array([0.3, 0.4], dtype=np.float32)
        rec._on_mic_chunk(audio)
        assert len(chunks) == 1

    def test_stop_when_not_recording(self, mock_macos_modules):
        mod = _import_meeting_recorder()
        rec = mod.MeetingRecorder(mode="listen")
        result = rec.stop()
        assert result["system_audio_path"] is None
