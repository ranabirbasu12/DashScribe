# tests/test_audio_probe.py
"""Tests for audio_probe.py — per-app audio level monitoring."""
import sys
import types
import math
import pytest
from unittest.mock import MagicMock, patch


# --- Mock macOS modules before import ---
def _setup_mock_modules():
    mods = {}
    for name in ("objc", "CoreMedia", "ScreenCaptureKit", "Foundation"):
        if name not in sys.modules:
            mods[name] = types.ModuleType(name)
            sys.modules[name] = mods[name]

    sys.modules["Foundation"].NSObject = type("NSObject", (), {"alloc": classmethod(lambda cls: cls())})
    sys.modules["objc"].super = lambda self, cls: type("_super", (), {"init": lambda s: self})()

    sck = sys.modules["ScreenCaptureKit"]
    sck.SCStreamOutputTypeAudio = 1
    sck.SCStreamConfiguration = MagicMock()
    sck.SCContentFilter = MagicMock()
    sck.SCStream = MagicMock()

    cm = sys.modules["CoreMedia"]
    cm.CMTimeMake = MagicMock(return_value=(1, 1))
    cm.CMSampleBufferGetDataBuffer = MagicMock()
    cm.CMBlockBufferGetDataLength = MagicMock()
    cm.CMBlockBufferCopyDataBytes = MagicMock()

    return mods


_setup_mock_modules()


class TestAppAudioProbe:
    """Tests for AppAudioProbe."""

    def test_init(self):
        from audio_probe import AppAudioProbe
        display = MagicMock()
        content = MagicMock()
        content.applications.return_value = []
        probe = AppAudioProbe("com.test.app", display, content)
        assert probe.bundle_id == "com.test.app"
        assert probe.level == 0.0
        assert probe.peak == 0.0
        assert not probe.is_active

    def test_on_audio_with_signal(self):
        from audio_probe import AppAudioProbe
        probe = AppAudioProbe("com.test", MagicMock(), MagicMock())
        # Simulate a moderate signal
        probe._on_audio(0.1)  # RMS = 0.1, ~-20dB -> normalized ~0.67
        assert probe.level > 0.0
        assert probe.peak > 0.0

    def test_on_audio_with_silence(self):
        from audio_probe import AppAudioProbe
        probe = AppAudioProbe("com.test", MagicMock(), MagicMock())
        probe._on_audio(0.0)
        assert probe.level == 0.0

    def test_on_audio_decay(self):
        from audio_probe import AppAudioProbe
        probe = AppAudioProbe("com.test", MagicMock(), MagicMock())
        probe._on_audio(0.5)  # loud signal
        level_after_loud = probe.level
        # Feed silence -> should decay
        for _ in range(10):
            probe._on_audio(0.0)
        assert probe.level < level_after_loud

    def test_on_audio_fast_attack(self):
        from audio_probe import AppAudioProbe
        probe = AppAudioProbe("com.test", MagicMock(), MagicMock())
        probe._on_audio(0.0)  # silence
        assert probe.level == 0.0
        probe._on_audio(0.5)  # sudden loud signal
        # Fast attack: level should jump immediately
        assert probe.level > 0.5

    def test_start_builds_stream(self):
        from audio_probe import AppAudioProbe

        mock_app = MagicMock()
        mock_app.bundleIdentifier.return_value = "com.other.app"
        content = MagicMock()
        content.applications.return_value = [mock_app]

        display = MagicMock()
        probe = AppAudioProbe("com.test.app", display, content)

        mock_stream = MagicMock()
        mock_stream.startCaptureWithCompletionHandler_ = lambda cb: cb(None)

        with patch("ScreenCaptureKit.SCStreamConfiguration") as mock_config_cls, \
             patch("ScreenCaptureKit.SCContentFilter") as mock_filter_cls, \
             patch("ScreenCaptureKit.SCStream") as mock_stream_cls:
            mock_config = MagicMock()
            mock_config_cls.alloc.return_value.init.return_value = mock_config
            mock_filter_cls.alloc.return_value.initWithDisplay_excludingApplications_exceptingWindows_.return_value = MagicMock()
            mock_stream_cls.alloc.return_value.initWithFilter_configuration_delegate_.return_value = mock_stream
            mock_stream.addStreamOutput_type_sampleHandlerQueue_error_ = MagicMock()

            result = probe.start()
            assert result is True
            assert probe.is_active

    def test_stop(self):
        from audio_probe import AppAudioProbe
        probe = AppAudioProbe("com.test", MagicMock(), MagicMock())
        probe._active = True
        mock_stream = MagicMock()
        mock_stream.stopCaptureWithCompletionHandler_ = lambda cb: cb(None)
        probe._stream = mock_stream
        probe.stop()
        assert not probe.is_active
        assert probe._stream is None


class TestAudioLevelMonitor:
    """Tests for AudioLevelMonitor."""

    def test_init(self):
        from audio_probe import AudioLevelMonitor
        m = AudioLevelMonitor()
        assert not m.is_active
        assert m.get_levels() == {}

    def test_get_levels_with_probes(self):
        from audio_probe import AudioLevelMonitor
        m = AudioLevelMonitor()
        mock_probe = MagicMock()
        mock_probe.level = 0.5
        mock_probe.peak = 0.7
        m._probes = {"com.test": mock_probe}
        levels = m.get_levels()
        assert "com.test" in levels
        assert levels["com.test"]["level"] == 0.5
        assert levels["com.test"]["peak"] == 0.7

    def test_stop_clears_probes(self):
        from audio_probe import AudioLevelMonitor
        m = AudioLevelMonitor()
        mock_probe = MagicMock()
        m._probes = {"com.test": mock_probe}
        m._active = True
        m.stop()
        assert m.get_levels() == {}
        assert not m.is_active
        mock_probe.stop.assert_called_once()

    def test_max_probes_limit(self):
        from audio_probe import AudioLevelMonitor
        m = AudioLevelMonitor()
        assert m.MAX_PROBES == 8

    @patch("audio_probe.AppAudioProbe")
    def test_start_creates_probes(self, MockProbe):
        from audio_probe import AudioLevelMonitor
        mock_probe_inst = MagicMock()
        mock_probe_inst.start.return_value = True
        MockProbe.return_value = mock_probe_inst

        with patch("system_audio.SystemAudioCapture._get_shareable_content") as mock_get:
            mock_content = MagicMock()
            mock_content.displays.return_value = [MagicMock()]
            mock_get.return_value = (mock_content, None)

            m = AudioLevelMonitor()
            result = m.start(["com.app1", "com.app2"])
            assert result is True
            assert m.is_active

    def test_start_no_content(self):
        from audio_probe import AudioLevelMonitor
        with patch("system_audio.SystemAudioCapture._get_shareable_content") as mock_get:
            mock_get.return_value = (None, "error")
            m = AudioLevelMonitor()
            result = m.start(["com.app1"])
            assert result is False
