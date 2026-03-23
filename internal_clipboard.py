import threading


class InternalClipboard:
    """Thread-safe in-app clipboard buffer for transcriptions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._text = ""

    def set_text(self, text: str):
        with self._lock:
            self._text = text or ""

    def get_text(self) -> str:
        with self._lock:
            return self._text

    def has_text(self) -> bool:
        with self._lock:
            return bool(self._text)
