# device_monitor.py
"""Monitors the default input audio device and fires callbacks on changes."""
from typing import Callable, Optional


class DeviceMonitor:
    """Watches the system default input device via CoreAudio.

    Fires on_device_changed(name) when the default input device changes.
    Fires on_device_changed(None) when no input device is available.
    """

    def __init__(self):
        self.on_device_changed: Optional[Callable[[Optional[str]], None]] = None
        self._started = False
        self._last_device_id: Optional[int] = None
        self._last_device_name: Optional[str] = None

    def start(self) -> None:
        """Register CoreAudio property listener. Idempotent."""
        if self._started:
            return
        self._started = True
        # CoreAudio wiring added in a later task.

    def stop(self) -> None:
        """Unregister listener. Idempotent."""
        if not self._started:
            return
        self._started = False

    def current_device_name(self) -> Optional[str]:
        """Return cached name of current default input device, or None."""
        return self._last_device_name

    def _fire_change(self, name: Optional[str]) -> None:
        """Internal: invoke the on_device_changed callback safely."""
        self._last_device_name = name
        cb = self.on_device_changed
        if cb is not None:
            try:
                cb(name)
            except Exception as e:
                print(f"DeviceMonitor callback error: {e}")
