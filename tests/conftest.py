# tests/conftest.py
"""Shared pytest fixtures for the DashScribe test suite."""
import numpy as np
import pytest
from unittest.mock import patch


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "diarizer: patch diarizer.sf so the test can call diarize() without real audio on disk",
    )


@pytest.fixture(autouse=True)
def _patch_soundfile_for_diarizer(request):
    """Patch soundfile.read inside diarizer for tests marked @pytest.mark.diarizer."""
    if not request.node.get_closest_marker("diarizer"):
        yield
        return
    dummy_audio = np.zeros(16000, dtype=np.float32)
    dummy_sr = 16000
    with patch("diarizer.sf") as mock_sf:
        mock_sf.read.return_value = (dummy_audio, dummy_sr)
        yield mock_sf
