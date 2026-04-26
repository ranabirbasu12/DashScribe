# engine_registry.py
"""Lazy engine selection so we don't load every model at startup."""
from typing import Callable, Optional


class EngineRegistry:
    def __init__(
        self,
        *,
        whisper_turbo,
        parakeet_factory: Optional[Callable[[], object]] = None,
        whisper_large_factory: Optional[Callable[[], object]] = None,
    ):
        self._whisper_turbo = whisper_turbo
        self._parakeet_factory = parakeet_factory
        self._whisper_large_factory = whisper_large_factory
        self._parakeet = None
        self._whisper_large = None

    def get(self, engine: str):
        if engine == "whisper-turbo":
            return self._whisper_turbo
        if engine == "parakeet":
            if self._parakeet is None:
                if self._parakeet_factory is None:
                    raise ValueError("Parakeet engine not configured")
                self._parakeet = self._parakeet_factory()
            return self._parakeet
        if engine == "whisper-large":
            if self._whisper_large is None:
                if self._whisper_large_factory is None:
                    raise ValueError("Whisper-large engine not configured")
                self._whisper_large = self._whisper_large_factory()
            return self._whisper_large
        raise ValueError(f"Unknown engine: {engine}")
