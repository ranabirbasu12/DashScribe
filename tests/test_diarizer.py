# tests/test_diarizer.py
import pytest
from unittest.mock import MagicMock
from diarizer import Diarizer, SpeakerSegment


def test_speaker_segment_dataclass():
    s = SpeakerSegment(start=0.0, end=1.5, speaker_id="S1")
    assert s.start == 0.0
    assert s.end == 1.5
    assert s.speaker_id == "S1"


def test_diarizer_initial_state():
    d = Diarizer()
    assert d.is_loaded is False
    assert d.status == "idle"


@pytest.mark.diarizer
def test_diarize_returns_speaker_segments():
    """diarize() returns a list of SpeakerSegment with 1+ entries on speech audio."""
    d = Diarizer()
    fake_session = MagicMock()
    fake_session.process.return_value.sort_by_start_time.return_value = [
        MagicMock(start=0.0, end=1.5, speaker=0),
        MagicMock(start=1.5, end=3.0, speaker=1),
        MagicMock(start=3.0, end=4.2, speaker=0),
    ]
    d._session = fake_session
    d.is_loaded = True
    result = d.diarize("/tmp/test.wav")
    assert len(result) == 3
    assert result[0].speaker_id == "S1"
    assert result[1].speaker_id == "S2"
    assert result[2].speaker_id == "S1"
    assert result[0].start == 0.0
    assert result[0].end == 1.5


@pytest.mark.diarizer
def test_diarize_with_speaker_count_hint():
    """When num_speakers is given, it's passed through to the session config."""
    d = Diarizer()
    fake_session = MagicMock()
    fake_session.process.return_value.sort_by_start_time.return_value = []
    d._session = fake_session
    d.is_loaded = True
    d.diarize("/tmp/test.wav", num_speakers=3)
    assert fake_session.config.clustering.num_clusters == 3


@pytest.mark.diarizer
def test_diarize_returns_empty_for_silence():
    d = Diarizer()
    fake_session = MagicMock()
    fake_session.process.return_value.sort_by_start_time.return_value = []
    d._session = fake_session
    d.is_loaded = True
    assert d.diarize("/tmp/test.wav") == []


@pytest.mark.diarizer
def test_diarize_default_auto_resets_num_clusters_to_minus_one():
    """Default num_speakers='auto' sets num_clusters=-1 (threshold-based clustering)."""
    d = Diarizer()
    fake_session = MagicMock()
    fake_session.process.return_value.sort_by_start_time.return_value = []
    d._session = fake_session
    d.is_loaded = True
    d.diarize("/tmp/test.wav")  # default num_speakers="auto"
    assert fake_session.config.clustering.num_clusters == -1
