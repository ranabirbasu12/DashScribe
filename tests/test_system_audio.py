# tests/test_system_audio.py
"""Tests for SystemAudioCapture -- ScreenCaptureKit system audio."""
import sys
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest


# Mock all macOS-specific modules before importing system_audio
@pytest.fixture(autouse=True)
def mock_macos_modules():
    """Mock macOS-specific modules for testing."""
    mock_objc = MagicMock()
    mock_core_media = MagicMock()
    mock_screen_capture = MagicMock()
    mock_foundation = MagicMock()

    modules = {
        "objc": mock_objc,
        "CoreMedia": mock_core_media,
        "ScreenCaptureKit": mock_screen_capture,
        "Foundation": mock_foundation,
    }

    with patch.dict("sys.modules", modules):
        yield {
            "objc": mock_objc,
            "CoreMedia": mock_core_media,
            "ScreenCaptureKit": mock_screen_capture,
            "Foundation": mock_foundation,
        }


def _import_system_audio():
    """Import system_audio with mocked macOS modules."""
    if "system_audio" in sys.modules:
        del sys.modules["system_audio"]
    import system_audio
    return system_audio


class TestSystemAudioCapture:
    def test_init_defaults(self, mock_macos_modules):
        mod = _import_system_audio()
        cap = mod.SystemAudioCapture()
        assert cap.sample_rate == 16000
        assert cap._stream is None
        assert cap._available is True
        assert cap._chunks == []

    def test_init_custom_sample_rate(self, mock_macos_modules):
        mod = _import_system_audio()
        cap = mod.SystemAudioCapture(sample_rate=44100)
        assert cap.sample_rate == 44100

    def test_is_available_property(self, mock_macos_modules):
        mod = _import_system_audio()
        cap = mod.SystemAudioCapture()
        assert cap.is_available is True
        cap._available = False
        assert cap.is_available is False

    def test_stop_no_stream(self, mock_macos_modules):
        """stop() returns empty array when no stream is active."""
        mod = _import_system_audio()
        cap = mod.SystemAudioCapture()
        result = cap.stop()
        assert len(result) == 0
        assert result.dtype == np.float32

    def test_stop_with_chunks(self, mock_macos_modules):
        """stop() concatenates chunks and returns result."""
        mod = _import_system_audio()
        cap = mod.SystemAudioCapture()
        cap._chunks = [
            np.array([0.1, 0.2], dtype=np.float32),
            np.array([0.3, 0.4], dtype=np.float32),
        ]
        result = cap.stop()
        np.testing.assert_array_almost_equal(result, [0.1, 0.2, 0.3, 0.4])
        assert cap._chunks == []

    def test_stop_with_stream(self, mock_macos_modules):
        """stop() stops the stream before returning chunks."""
        mod = _import_system_audio()
        cap = mod.SystemAudioCapture()
        mock_stream = MagicMock()

        def fake_stop(handler):
            handler(None)

        mock_stream.stopCaptureWithCompletionHandler_ = fake_stop
        cap._stream = mock_stream
        cap._chunks = [np.array([0.5], dtype=np.float32)]
        result = cap.stop()
        assert cap._stream is None
        assert cap._handler is None
        np.testing.assert_array_almost_equal(result, [0.5])

    def test_start_timeout(self, mock_macos_modules, capsys):
        """start() handles timeout getting shareable content."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        def fake_get_content(*args, **kwargs):
            # Get the completion handler (last positional arg)
            # Don't call it -- simulating timeout
            pass

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        cap = mod.SystemAudioCapture()
        # Monkey-patch event.wait to return False (timeout)
        original_wait = threading.Event.wait
        with patch.object(threading.Event, 'wait', return_value=False):
            cap.start()
        assert cap._available is False

    def test_start_error_in_content(self, mock_macos_modules, capsys):
        """start() handles error in shareable content."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        def fake_get_content(self_arg, desktop_arg, handler):
            handler(None, "Permission denied")

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        cap = mod.SystemAudioCapture()
        cap.start()
        assert cap._available is False

    def test_start_no_displays(self, mock_macos_modules, capsys):
        """start() handles no displays found."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        def fake_get_content(self_arg, desktop_arg, handler):
            mock_content = MagicMock()
            mock_content.displays.return_value = []
            handler(mock_content, None)

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        cap = mod.SystemAudioCapture()
        cap.start()
        assert cap._available is False

    def test_start_on_start_error(self, mock_macos_modules, capsys):
        """start() handles error during startCapture."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        mock_display = MagicMock()
        mock_content = MagicMock()
        mock_content.displays.return_value = [mock_display]

        def fake_get_content(self_arg, desktop_arg, handler):
            handler(mock_content, None)

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        mock_stream_instance = MagicMock()

        def fake_start_capture(handler):
            handler("Start error occurred")

        mock_stream_instance.startCaptureWithCompletionHandler_ = fake_start_capture
        sck.SCStream.alloc.return_value.initWithFilter_configuration_delegate_.return_value = mock_stream_instance

        # Also mock _AudioHandler.alloc() chain
        mock_handler = MagicMock()
        mod._AudioHandler = MagicMock()
        mod._AudioHandler.alloc.return_value.initWithChunks_callback_.return_value = mock_handler

        cap = mod.SystemAudioCapture()
        cap.start()
        assert cap._available is False

    def test_start_success(self, mock_macos_modules):
        """start() sets up stream and starts capture successfully."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        mock_display = MagicMock()
        mock_content = MagicMock()
        mock_content.displays.return_value = [mock_display]

        def fake_get_content(self_arg, desktop_arg, handler):
            handler(mock_content, None)

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        mock_stream_instance = MagicMock()

        def fake_start_capture(handler):
            handler(None)  # No error

        mock_stream_instance.startCaptureWithCompletionHandler_ = fake_start_capture
        sck.SCStream.alloc.return_value.initWithFilter_configuration_delegate_.return_value = mock_stream_instance

        mock_handler = MagicMock()
        mod._AudioHandler = MagicMock()
        mod._AudioHandler.alloc.return_value.initWithChunks_callback_.return_value = mock_handler

        cap = mod.SystemAudioCapture()
        cap.start()
        assert cap._stream is mock_stream_instance
        assert cap._handler is mock_handler

    def test_start_no_content_no_error(self, mock_macos_modules, capsys):
        """start() handles None content with no error (line 71-73)."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        def fake_get_content(self_arg, desktop_arg, handler):
            handler(None, None)

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        cap = mod.SystemAudioCapture()
        cap.start()
        assert cap._available is False

    def test_start_with_app_filter(self, mock_macos_modules):
        """start(app_bundle_id=...) creates filter excluding other apps."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        mock_display = MagicMock()
        mock_content = MagicMock()
        mock_content.displays.return_value = [mock_display]

        # Two apps: Zoom (target) and Slack (should be excluded)
        mock_zoom = MagicMock()
        mock_zoom.bundleIdentifier.return_value = "us.zoom.xos"
        mock_zoom.applicationName.return_value = "Zoom"
        mock_slack = MagicMock()
        mock_slack.bundleIdentifier.return_value = "com.tinyspeck.slackmacgap"
        mock_slack.applicationName.return_value = "Slack"
        mock_content.applications.return_value = [mock_zoom, mock_slack]

        def fake_get_content(self_arg, desktop_arg, handler):
            handler(mock_content, None)

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        mock_stream_instance = MagicMock()
        mock_stream_instance.startCaptureWithCompletionHandler_ = lambda h: h(None)
        sck.SCStream.alloc.return_value.initWithFilter_configuration_delegate_.return_value = mock_stream_instance

        mock_handler = MagicMock()
        mod._AudioHandler = MagicMock()
        mod._AudioHandler.alloc.return_value.initWithChunks_callback_.return_value = mock_handler

        mock_filter = MagicMock()
        sck.SCContentFilter.alloc.return_value.initWithDisplay_excludingApplications_exceptingWindows_.return_value = mock_filter

        cap = mod.SystemAudioCapture()
        cap.start(app_bundle_id="us.zoom.xos")

        # The filter should have been created excluding Slack but not Zoom
        call_args = sck.SCContentFilter.alloc.return_value \
            .initWithDisplay_excludingApplications_exceptingWindows_.call_args
        exclude_list = call_args[0][1]
        assert mock_slack in exclude_list
        assert mock_zoom not in exclude_list

    def test_get_running_apps(self, mock_macos_modules):
        """get_running_apps() returns sorted app list."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        mock_content = MagicMock()
        mock_zoom = MagicMock()
        mock_zoom.applicationName.return_value = "Zoom"
        mock_zoom.bundleIdentifier.return_value = "us.zoom.xos"
        mock_slack = MagicMock()
        mock_slack.applicationName.return_value = "Slack"
        mock_slack.bundleIdentifier.return_value = "com.tinyspeck.slackmacgap"
        mock_content.applications.return_value = [mock_zoom, mock_slack]

        def fake_get_content(self_arg, desktop_arg, handler):
            handler(mock_content, None)

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        # Mock NSWorkspace to control app list
        mock_workspace = MagicMock()
        mock_ws_zoom = MagicMock()
        mock_ws_zoom.bundleIdentifier.return_value = "us.zoom.xos"
        mock_ws_zoom.localizedName.return_value = "Zoom"
        mock_ws_zoom.activationPolicy.return_value = 0
        mock_ws_slack = MagicMock()
        mock_ws_slack.bundleIdentifier.return_value = "com.tinyspeck.slackmacgap"
        mock_ws_slack.localizedName.return_value = "Slack"
        mock_ws_slack.activationPolicy.return_value = 0
        mock_workspace.sharedWorkspace.return_value.runningApplications.return_value = [mock_ws_zoom, mock_ws_slack]

        with patch.dict('sys.modules', {'AppKit': MagicMock(NSWorkspace=mock_workspace)}):
            apps = mod.SystemAudioCapture.get_running_apps()
        assert len(apps) == 2
        # Sorted alphabetically: Slack before Zoom
        assert apps[0]["name"] == "Slack"
        assert apps[1]["name"] == "Zoom"
        assert apps[1]["bundle_id"] == "us.zoom.xos"

    def test_get_running_apps_error(self, mock_macos_modules):
        """get_running_apps() falls back to SCK on ImportError, returns empty on error."""
        mod = _import_system_audio()
        sck = mock_macos_modules["ScreenCaptureKit"]

        def fake_get_content(self_arg, desktop_arg, handler):
            handler(None, "Permission denied")

        sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = fake_get_content

        # Make AppKit import fail so it falls back to SCK path
        with patch.dict('sys.modules', {'AppKit': None}):
            apps = mod.SystemAudioCapture.get_running_apps()
        assert apps == []

    def test_stop_stream_handler_cleanup(self, mock_macos_modules):
        """stop() clears stream and handler references (lines 121-133)."""
        mod = _import_system_audio()
        cap = mod.SystemAudioCapture()
        mock_stream = MagicMock()

        def fake_stop(handler):
            handler(None)

        mock_stream.stopCaptureWithCompletionHandler_ = fake_stop
        cap._stream = mock_stream
        cap._handler = MagicMock()
        cap._chunks = [np.array([1.0], dtype=np.float32)]

        result = cap.stop()
        assert cap._stream is None
        assert cap._handler is None
        assert len(result) == 1
        assert cap._chunks == []
