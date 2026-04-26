# tests/test_diarizer_pyannote.py
from unittest.mock import patch, MagicMock
from diarizer_pyannote import PyannoteDiarizer, is_pyannote_installed


def test_is_pyannote_installed_true_when_module_importable():
    with patch("importlib.util.find_spec") as fs:
        fs.return_value = object()
        assert is_pyannote_installed() is True


def test_is_pyannote_installed_false_when_missing():
    with patch("importlib.util.find_spec") as fs:
        fs.return_value = None
        assert is_pyannote_installed() is False


def test_diarize_returns_speaker_segments_format():
    """Pyannote output is normalized to SpeakerSegment objects."""
    pd = PyannoteDiarizer()
    fake_pipeline = MagicMock()
    seg1 = MagicMock(); seg1.start = 0.0; seg1.end = 1.5
    seg2 = MagicMock(); seg2.start = 1.5; seg2.end = 3.0
    fake_annotation = MagicMock()
    fake_annotation.itertracks.return_value = [
        (seg1, None, "SPEAKER_00"),
        (seg2, None, "SPEAKER_01"),
    ]
    fake_pipeline.return_value = fake_annotation
    pd._pipeline = fake_pipeline
    pd.is_loaded = True
    result = pd.diarize("/tmp/x.wav")
    assert len(result) == 2
    assert result[0].speaker_id == "S1"
    assert result[1].speaker_id == "S2"
