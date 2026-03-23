import threading
from collections import deque
from enum import Enum


class AppState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"


class AppStateManager:
    """Central state tracker with callback system for UI synchronization."""

    def __init__(self):
        self._state = AppState.IDLE
        self._state_callbacks: list = []
        self._amplitude_callbacks: list = []
        self._warning_callbacks: list = []
        self._amplitudes: deque[float] = deque(maxlen=200)
        self._lock = threading.Lock()

    @property
    def state(self) -> AppState:
        return self._state

    def set_state(self, new_state: AppState):
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        for cb in tuple(self._state_callbacks):
            try:
                cb(old, new_state)
            except Exception:
                pass

    def on_state_change(self, callback):
        if callback not in self._state_callbacks:
            self._state_callbacks.append(callback)

    def off_state_change(self, callback):
        try:
            self._state_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def push_amplitude(self, value: float):
        with self._lock:
            self._amplitudes.append(value)
        for cb in tuple(self._amplitude_callbacks):
            try:
                cb(value)
            except Exception:
                pass

    def get_amplitudes(self) -> list[float]:
        with self._lock:
            amps = list(self._amplitudes)
            self._amplitudes.clear()
            return amps

    def on_amplitude(self, callback):
        if callback not in self._amplitude_callbacks:
            self._amplitude_callbacks.append(callback)

    def off_amplitude(self, callback):
        try:
            self._amplitude_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def push_warning(self, message: str):
        for cb in tuple(self._warning_callbacks):
            try:
                cb(message)
            except Exception:
                pass

    def on_warning(self, callback):
        if callback not in self._warning_callbacks:
            self._warning_callbacks.append(callback)

    def off_warning(self, callback):
        try:
            self._warning_callbacks.remove(callback)
            return True
        except ValueError:
            return False
