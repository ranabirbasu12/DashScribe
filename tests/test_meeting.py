# tests/test_meeting.py
"""Tests for MeetingPipeline — dual VAD + transcription orchestrator."""
import os
import sys
import tempfile
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest


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
        yield


def _import_meeting():
    for mod_name in ["meeting", "meeting_recorder", "system_audio"]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import meeting
    return meeting


@pytest.fixture
def store():
    from meeting_store import MeetingStore
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    s = MeetingStore(db_path=f.name)
    yield s
    os.unlink(f.name)


@pytest.fixture
def transcriber():
    mock = MagicMock()
    mock.transcribe_array.return_value = {"text": "hello world"}
    return mock


class TestMeetingPipeline:
    def test_init(self, mock_macos_modules, store, transcriber):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(transcriber=transcriber, store=store)
        assert not pipeline.is_active
        assert not pipeline.is_paused
        assert pipeline.meeting_id is None

    def test_start_creates_meeting(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        # Mock recorder to avoid real audio
        mock_recorder = MagicMock()
        pipeline._recorder = mock_recorder

        # Mock VAD
        mock_vad = MagicMock()
        pipeline._sys_vad = mock_vad
        pipeline._mic_vad = MagicMock()
        pipeline._vad_loaded = True

        result = pipeline.start(
            title="Standup",
            app_bundle_id="us.zoom.xos",
            mode="listen",
        )

        assert result["meeting_id"] > 0
        assert pipeline.is_active
        assert pipeline.meeting_id == result["meeting_id"]
        mock_recorder.start.assert_called_once()

    def test_stop_finalizes(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        mock_recorder = MagicMock()
        mock_recorder.stop.return_value = {
            "system_audio_path": str(tmp_path / "sys.wav"),
            "mic_audio_path": None,
        }
        pipeline._recorder = mock_recorder
        pipeline._sys_vad = MagicMock()
        pipeline._mic_vad = MagicMock()
        pipeline._vad_loaded = True

        pipeline.start(title="Test", app_bundle_id="us.zoom.xos", mode="listen")
        result = pipeline.stop()

        assert not pipeline.is_active
        assert result.get("meeting_id") is not None

        # Check DB record was updated
        meeting = store.get_meeting(result["meeting_id"])
        assert meeting["status"] == "stopped"

    def test_pause_resume(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        mock_recorder = MagicMock()
        pipeline._recorder = mock_recorder
        pipeline._sys_vad = MagicMock()
        pipeline._mic_vad = MagicMock()
        pipeline._vad_loaded = True

        pipeline.start(title="Test", app_bundle_id="us.zoom.xos", mode="listen")
        assert pipeline.is_active

        pipeline.pause()
        assert pipeline.is_paused
        assert not pipeline.is_active
        mock_recorder.pause.assert_called_once()

        pipeline.resume()
        assert pipeline.is_active
        assert not pipeline.is_paused
        mock_recorder.resume.assert_called_once()

    def test_discard(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        mock_recorder = MagicMock()
        mock_recorder.stop.return_value = {
            "system_audio_path": None,
            "mic_audio_path": None,
        }
        pipeline._recorder = mock_recorder
        pipeline._sys_vad = MagicMock()
        pipeline._mic_vad = MagicMock()
        pipeline._vad_loaded = True

        result = pipeline.start(title="Test", app_bundle_id="us.zoom.xos", mode="listen")
        mid = result["meeting_id"]

        pipeline.discard()
        assert not pipeline.is_active
        assert pipeline.meeting_id is None
        assert store.get_meeting(mid) is None

    def test_system_segment_tagged_others(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        segments_received = []
        pipeline.on_segment = lambda s: segments_received.append(s)

        # Simulate processing a system audio segment
        from vad import SealedSegment
        seg = SealedSegment(
            segment_index=0,
            mic_audio=np.zeros(16000, dtype=np.float32),
            start_sample=0,
            end_sample=16000,
        )
        pipeline._active = True
        pipeline._meeting_id = 1
        pipeline._pending_results = []
        pipeline._process_segment(seg, speaker="others")

        assert len(segments_received) == 1
        assert segments_received[0]["speaker"] == "others"

    def test_mic_segment_tagged_you(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        segments_received = []
        pipeline.on_segment = lambda s: segments_received.append(s)

        from vad import SealedSegment
        seg = SealedSegment(
            segment_index=0,
            mic_audio=np.zeros(16000, dtype=np.float32),
            start_sample=0,
            end_sample=16000,
        )
        pipeline._active = True
        pipeline._meeting_id = 1
        pipeline._pending_results = []
        pipeline._process_segment(seg, speaker="you")

        assert len(segments_received) == 1
        assert segments_received[0]["speaker"] == "you"

    def test_flush_to_db(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
        pipeline._meeting_id = mid
        pipeline._recorder = MagicMock()
        pipeline._pending_results = [
            {"index": 0, "text": "hello", "start_ms": 0, "end_ms": 1000, "speaker": "others"},
        ]

        pipeline._flush_to_db()
        segs = store.get_segments(mid)
        assert len(segs) == 1
        assert segs[0]["speaker"] == "others"

    def test_status_callback_on_start(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )

        statuses = []
        pipeline.on_status = lambda s: statuses.append(s)

        mock_recorder = MagicMock()
        pipeline._recorder = mock_recorder
        pipeline._sys_vad = MagicMock()
        pipeline._mic_vad = MagicMock()
        pipeline._vad_loaded = True

        pipeline.start(title="Test", app_bundle_id="us.zoom.xos", mode="listen")
        assert "recording" in statuses

        pipeline.stop()
        assert "stopped" in statuses

    def test_stop_when_not_active(self, mock_macos_modules, store, transcriber, tmp_path):
        mod = _import_meeting()
        pipeline = mod.MeetingPipeline(
            transcriber=transcriber, store=store,
            meetings_dir=str(tmp_path),
        )
        result = pipeline.stop()
        assert result == {}


class TestKnownMeetingApps:
    def test_known_apps_dict(self, mock_macos_modules):
        mod = _import_meeting()
        assert "us.zoom.xos" in mod.KNOWN_MEETING_APPS
        assert "com.microsoft.teams2" in mod.KNOWN_MEETING_APPS
        assert "com.apple.FaceTime" in mod.KNOWN_MEETING_APPS
