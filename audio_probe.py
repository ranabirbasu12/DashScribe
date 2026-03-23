# audio_probe.py
"""Lightweight per-app audio level monitoring via ScreenCaptureKit.

Sets up minimal SCStream captures per app to compute RMS audio levels
without recording. Used for the meeting setup app picker to show which
apps are currently producing sound.
"""
import math
import threading
import time

import numpy as np

try:
    import objc
    import CoreMedia
    import ScreenCaptureKit
    from Foundation import NSObject

    _HAS_SCK = True
except ImportError:
    _HAS_SCK = False


if _HAS_SCK:

    class _ProbeHandler(NSObject):
        """Receives audio buffers and computes RMS level."""

        def initWithCallback_(self, callback):
            self = objc.super(_ProbeHandler, self).init()
            if self is not None:
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
                    audio = np.frombuffer(raw_bytes, dtype=np.float32)
                    if len(audio) > 0:
                        rms = float(np.sqrt(np.mean(audio ** 2)))
                        self._callback(rms)
            except Exception:
                pass


class AppAudioProbe:
    """Monitors audio level from a single app via ScreenCaptureKit."""

    def __init__(self, bundle_id: str, display, content, sample_rate: int = 16000):
        self.bundle_id = bundle_id
        self._display = display
        self._content = content
        self._sample_rate = sample_rate
        self._stream = None
        self._handler = None
        self._level = 0.0
        self._peak = 0.0
        self._lock = threading.Lock()
        self._active = False
        self._decay = 0.85  # Smooth decay factor for visual

    def _on_audio(self, rms: float):
        with self._lock:
            # Convert to dB-like scale (0.0 to 1.0)
            if rms > 0:
                db = 20 * math.log10(max(rms, 1e-10))
                # Map -60dB..0dB to 0..1
                normalized = max(0.0, min(1.0, (db + 60) / 60))
            else:
                normalized = 0.0
            # Smoothed level: fast attack, slow decay
            if normalized > self._level:
                self._level = normalized
            else:
                self._level = self._level * self._decay + normalized * (1 - self._decay)
            self._peak = max(self._peak * 0.98, normalized)

    @property
    def level(self) -> float:
        with self._lock:
            return round(self._level, 3)

    @property
    def peak(self) -> float:
        with self._lock:
            return round(self._peak, 3)

    def start(self) -> bool:
        """Start monitoring. Returns True on success."""
        if not _HAS_SCK or self._active:
            return False

        try:
            # Build exclude list (all apps except target)
            exclude_apps = []
            for app in self._content.applications():
                try:
                    bid = str(app.bundleIdentifier()) if app.bundleIdentifier() else ""
                    if bid and bid != self.bundle_id:
                        exclude_apps.append(app)
                except Exception:
                    continue

            config = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
            config.setCapturesAudio_(True)
            config.setExcludesCurrentProcessAudio_(True)
            config.setSampleRate_(float(self._sample_rate))
            config.setChannelCount_(1)
            # Minimize video overhead
            config.setWidth_(2)
            config.setHeight_(2)
            config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))

            content_filter = ScreenCaptureKit.SCContentFilter.alloc() \
                .initWithDisplay_excludingApplications_exceptingWindows_(
                    self._display, exclude_apps, []
                )

            self._handler = _ProbeHandler.alloc().initWithCallback_(self._on_audio)

            self._stream = ScreenCaptureKit.SCStream.alloc() \
                .initWithFilter_configuration_delegate_(content_filter, config, None)

            self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self._handler, ScreenCaptureKit.SCStreamOutputTypeAudio, None, None
            )

            start_event = threading.Event()
            success = [True]

            def on_start(error):
                if error:
                    success[0] = False
                start_event.set()

            self._stream.startCaptureWithCompletionHandler_(on_start)
            start_event.wait(timeout=3)

            if success[0]:
                self._active = True
            return success[0]
        except Exception:
            return False

    def stop(self):
        """Stop monitoring."""
        if self._stream is not None:
            event = threading.Event()
            self._stream.stopCaptureWithCompletionHandler_(lambda err: event.set())
            event.wait(timeout=3)
            self._stream = None
            self._handler = None
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active


class AudioLevelMonitor:
    """Manages multiple AppAudioProbes to monitor audio levels across apps."""

    MAX_PROBES = 8  # Limit simultaneous captures

    def __init__(self, sample_rate: int = 16000):
        self._sample_rate = sample_rate
        self._probes: dict[str, AppAudioProbe] = {}
        self._lock = threading.Lock()
        self._active = False

    def start(self, bundle_ids: list[str]) -> bool:
        """Start monitoring the given apps. Returns True if at least one probe started."""
        if not _HAS_SCK:
            return False

        self.stop()

        try:
            from system_audio import SystemAudioCapture
            content, error = SystemAudioCapture._get_shareable_content()
            if error or not content:
                return False

            displays = content.displays()
            if not displays:
                return False

            started = 0
            for bid in bundle_ids[:self.MAX_PROBES]:
                probe = AppAudioProbe(bid, displays[0], content, self._sample_rate)
                if probe.start():
                    with self._lock:
                        self._probes[bid] = probe
                    started += 1

            self._active = started > 0
            return self._active
        except Exception:
            return False

    def get_levels(self) -> dict[str, dict]:
        """Return current audio levels for all monitored apps."""
        with self._lock:
            return {
                bid: {"level": probe.level, "peak": probe.peak}
                for bid, probe in self._probes.items()
            }

    def stop(self):
        """Stop all probes."""
        with self._lock:
            for probe in self._probes.values():
                try:
                    probe.stop()
                except Exception:
                    pass
            self._probes.clear()
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active
