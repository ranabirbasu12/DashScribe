# tests/conftest.py
"""Shared pytest fixtures and patches for the DashScribe test suite."""
import numpy as np
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _patch_soundfile_for_diarizer(request):
    """Patch soundfile.read inside diarizer so tests that pre-set _session
    don't need a real audio file on disk."""
    # Only apply when the diarizer module is actually imported by the test.
    if "diarizer" not in request.node.nodeid:
        yield
        return

    dummy_audio = np.zeros(16000, dtype=np.float32)
    dummy_sr = 16000
    with patch("diarizer.sf") as mock_sf:
        mock_sf.read.return_value = (dummy_audio, dummy_sr)
        yield mock_sf
