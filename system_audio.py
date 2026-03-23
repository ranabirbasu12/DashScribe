# system_audio.py
"""Capture system audio output via ScreenCaptureKit for echo cancellation."""
import threading

import objc
import numpy as np
import CoreMedia
import ScreenCaptureKit
from Foundation import NSObject


class _AudioHandler(NSObject):
    """Receives audio sample buffers from SCStream."""

    def initWithChunks_callback_(self, chunks_list, callback):
        self = objc.super(_AudioHandler, self).init()
        if self is not None:
            self._chunks = chunks_list
            self._callback = callback
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        if output_type != ScreenCaptureKit.SCStreamOutputTypeAudio:
            return
        try:
            block_buf = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
            if block_buf is None:
                return
            length = CoreMedia.CMBlockBufferGetDataLength(block_buf)
            result = CoreMedia.CMBlockBufferCopyDataBytes(block_buf, 0, length, None)
            if result is not None:
                _, raw_bytes = result
                audio = np.frombuffer(raw_bytes, dtype=np.float32).copy()
                self._chunks.append(audio)
                if self._callback:
                    self._callback(audio)
        except Exception:
            pass


class SystemAudioCapture:
    """Captures system audio output at 16kHz mono via ScreenCaptureKit."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._stream = None
        self._handler = None
        self._chunks: list[np.ndarray] = []
        self._available = True
        self.on_audio_chunk = None  # fn(np.ndarray float32) — real-time callback

    @staticmethod
    def _get_shareable_content(timeout: float = 3.0):
        """Get SCShareableContent synchronously. Returns (content, error)."""
        event = threading.Event()
        result = {}

        def on_content(content, error):
            result["content"] = content
            result["error"] = error
            event.set()

        ScreenCaptureKit.SCShareableContent \
            .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                True, True, on_content
            )

        if not event.wait(timeout=timeout):
            return None, "timeout"
        return result.get("content"), result.get("error")

    @staticmethod
    def get_running_apps() -> list[dict]:
        """Return list of running apps with name and bundle_id.

        Uses NSWorkspace instead of SCShareableContent so that ALL running
        apps are listed regardless of the current macOS Space / fullscreen context.
        """
        try:
            from AppKit import NSWorkspace
            workspace_apps = NSWorkspace.sharedWorkspace().runningApplications()
            apps = []
            seen = set()
            for app in workspace_apps:
                try:
                    bundle_id = str(app.bundleIdentifier()) if app.bundleIdentifier() else ""
                    name = str(app.localizedName()) if app.localizedName() else ""
                    # Skip background/system daemons (no UI) and duplicates
                    if not name or not bundle_id or bundle_id in seen:
                        continue
                    # activationPolicy 0 = regular app, 1 = accessory, 2 = prohibited
                    if app.activationPolicy() == 2:
                        continue
                    seen.add(bundle_id)
                    apps.append({"name": name, "bundle_id": bundle_id})
                except Exception:
                    continue
            return sorted(apps, key=lambda a: a["name"].lower())
        except ImportError:
            # Fallback to SCK if AppKit unavailable
            content, error = SystemAudioCapture._get_shareable_content()
            if error or not content:
                return []
            apps = []
            for app in content.applications():
                try:
                    name = str(app.applicationName()) if app.applicationName() else ""
                    bundle_id = str(app.bundleIdentifier()) if app.bundleIdentifier() else ""
                    if name and bundle_id:
                        apps.append({"name": name, "bundle_id": bundle_id})
                except Exception:
                    continue
            return sorted(apps, key=lambda a: a["name"].lower())

    def start(self, app_bundle_id: str | None = None):
        """Start capturing system audio. Non-blocking.

        Args:
            app_bundle_id: If provided, capture only audio from this app.
                           If None, capture all system audio (existing behavior).
        """
        self._chunks = []

        content, error = self._get_shareable_content()

        if error == "timeout":
            print("SystemAudio: timeout getting shareable content")
            self._available = False
            return

        if error or not content:
            print(f"SystemAudio: {error or 'no content'}")
            self._available = False
            return

        displays = content.displays()
        if not displays:
            print("SystemAudio: no displays found")
            self._available = False
            return

        # Configure for audio-only capture
        config = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setExcludesCurrentProcessAudio_(True)
        config.setSampleRate_(float(self.sample_rate))
        config.setChannelCount_(1)
        # Minimize video overhead
        config.setWidth_(2)
        config.setHeight_(2)
        config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))

        if app_bundle_id:
            # Per-app filtering: exclude all apps except the target
            exclude_apps = []
            for app in content.applications():
                try:
                    bid = str(app.bundleIdentifier()) if app.bundleIdentifier() else ""
                    if bid and bid != app_bundle_id:
                        exclude_apps.append(app)
                except Exception:
                    continue
            content_filter = ScreenCaptureKit.SCContentFilter.alloc() \
                .initWithDisplay_excludingApplications_exceptingWindows_(
                    displays[0], exclude_apps, []
                )
        else:
            content_filter = ScreenCaptureKit.SCContentFilter.alloc() \
                .initWithDisplay_excludingApplications_exceptingWindows_(
                    displays[0], [], []
                )

        self._handler = _AudioHandler.alloc().initWithChunks_callback_(self._chunks, self.on_audio_chunk)

        self._stream = ScreenCaptureKit.SCStream.alloc() \
            .initWithFilter_configuration_delegate_(content_filter, config, None)

        self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._handler, ScreenCaptureKit.SCStreamOutputTypeAudio, None, None
        )

        # Start capture (async → sync)
        start_event = threading.Event()

        def on_start(error):
            if error:
                print(f"SystemAudio: start error: {error}")
                self._available = False
            start_event.set()

        self._stream.startCaptureWithCompletionHandler_(on_start)
        start_event.wait(timeout=3)

    def stop(self) -> np.ndarray:
        """Stop capture and return the captured audio as a float32 numpy array."""
        if self._stream is not None:
            event = threading.Event()
            self._stream.stopCaptureWithCompletionHandler_(lambda err: event.set())
            event.wait(timeout=3)
            self._stream = None
            self._handler = None

        if not self._chunks:
            return np.array([], dtype=np.float32)

        result = np.concatenate(self._chunks, axis=0)
        self._chunks = []
        return result

    @property
    def is_available(self) -> bool:
        return self._available
