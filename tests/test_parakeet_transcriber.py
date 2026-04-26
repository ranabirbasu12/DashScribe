# tests/test_parakeet_transcriber.py
from unittest.mock import MagicMock
from parakeet_transcriber import ParakeetTranscriber


def test_initial_state():
    p = ParakeetTranscriber()
    assert p.is_ready is False


def test_transcribe_segments_returns_unified_shape():
    """Parakeet output is normalized to the same shape as Whisper segments."""
    p = ParakeetTranscriber()
    fake_model = MagicMock()
    fake_sentence = MagicMock()
    fake_sentence.text = "Hello there."
    fake_sentence.start = 0.0
    fake_sentence.end = 1.5
    tok1 = MagicMock(); tok1.text = "Hello"; tok1.start = 0.0; tok1.end = 0.7
    tok2 = MagicMock(); tok2.text = "there."; tok2.start = 0.7; tok2.end = 1.5
    fake_sentence.tokens = [tok1, tok2]
    fake_model.transcribe.return_value = MagicMock(text="Hello there.", sentences=[fake_sentence])
    p._model = fake_model
    p.is_ready = True
    result = p.transcribe_segments("/tmp/x.wav")
    assert result["language"] == "en"
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "Hello there."
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["end"] == 1.5
    assert len(result["segments"][0]["words"]) == 2
    assert result["segments"][0]["words"][0]["text"] == "Hello"
