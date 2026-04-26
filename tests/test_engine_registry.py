# tests/test_engine_registry.py
from unittest.mock import MagicMock
import pytest
from engine_registry import EngineRegistry


def test_get_returns_whisper_turbo():
    whisper = MagicMock()
    reg = EngineRegistry(whisper_turbo=whisper)
    assert reg.get("whisper-turbo") is whisper


def test_get_returns_parakeet_lazy():
    parakeet_factory = MagicMock(return_value="PARAKEET")
    reg = EngineRegistry(whisper_turbo=MagicMock(), parakeet_factory=parakeet_factory)
    assert reg.get("parakeet") == "PARAKEET"
    assert reg.get("parakeet") == "PARAKEET"  # cached
    assert parakeet_factory.call_count == 1


def test_get_returns_whisper_large_lazy():
    large_factory = MagicMock(return_value="LARGE")
    reg = EngineRegistry(whisper_turbo=MagicMock(), whisper_large_factory=large_factory)
    assert reg.get("whisper-large") == "LARGE"
    assert large_factory.call_count == 1


def test_unknown_engine_raises():
    reg = EngineRegistry(whisper_turbo=MagicMock())
    with pytest.raises(ValueError):
        reg.get("xyz")
