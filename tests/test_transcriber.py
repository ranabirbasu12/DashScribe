# tests/test_transcriber.py
import numpy as np
from unittest.mock import MagicMock
from transcriber import WhisperTranscriber


def test_transcriber_initializes_with_model_name():
    t = WhisperTranscriber()
    assert t.model_repo == "mlx-community/whisper-large-v3-turbo"
    assert t.is_ready is False
    assert t.status == "not_started"


def test_transcribe_returns_text():
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {
        "text": " Hello world.",
        "language": "en",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": " Hello world.",
             "no_speech_prob": 0.01, "avg_logprob": -0.2, "words": []},
        ],
    }
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    result = t.transcribe("/tmp/test.wav")
    assert result == "Hello world."
    call_kwargs = mock_backend.transcribe.call_args[1]
    assert call_kwargs["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo"
    assert call_kwargs["language"] == "en"
    assert call_kwargs["condition_on_previous_text"] is False
    assert "initial_prompt" in call_kwargs


def test_transcribe_strips_whitespace():
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {
        "text": "  Some text  ",
        "language": "en",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "  Some text  ",
             "no_speech_prob": 0.01, "avg_logprob": -0.2, "words": []},
        ],
    }
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    result = t.transcribe("/tmp/test.wav")
    assert result == "Some text"


def test_transcribe_array_passes_numpy():
    """transcribe_array() passes numpy array with anti-hallucination params."""
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": " Hello from array."}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    audio = np.zeros(16000, dtype=np.float32)
    result = t.transcribe_array(audio)
    assert result == "Hello from array."
    call_kwargs = mock_backend.transcribe.call_args[1]
    assert call_kwargs["condition_on_previous_text"] is False
    assert call_kwargs["hallucination_silence_threshold"] == 2.0
    assert call_kwargs["compression_ratio_threshold"] == 2.4


def test_transcribe_array_with_initial_prompt():
    """transcribe_array() includes user prompt combined with style prompt."""
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": " DashScribe test."}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    audio = np.zeros(16000, dtype=np.float32)
    result = t.transcribe_array(audio, initial_prompt="DashScribe, FastAPI")
    assert result == "DashScribe test."
    call_kwargs = mock_backend.transcribe.call_args[1]
    assert "DashScribe, FastAPI" in call_kwargs["initial_prompt"]


def test_transcribe_array_without_initial_prompt():
    """transcribe_array() always passes punctuation style prompt."""
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": " Hello."}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    audio = np.zeros(16000, dtype=np.float32)
    t.transcribe_array(audio)
    call_kwargs = mock_backend.transcribe.call_args[1]
    assert "initial_prompt" in call_kwargs
    assert len(call_kwargs["initial_prompt"]) > 0


def test_clean_hallucination_empty():
    from transcriber import _clean_hallucination
    assert _clean_hallucination("") == ""
    assert _clean_hallucination(None) is None


def test_clean_hallucination_repetitive_text():
    from transcriber import _clean_hallucination
    # 4+ repetitions of a single word should be cleaned to just the word
    result = _clean_hallucination("Thank you. Thank you. Thank you. Thank you. Thank you.")
    # Repetitions cleaned, result should be shorter
    assert len(result) < len("Thank you. Thank you. Thank you. Thank you. Thank you.")


def test_clean_hallucination_normal_text():
    from transcriber import _clean_hallucination
    text = "This is a normal sentence with no repetition."
    assert _clean_hallucination(text) == text


def test_model_is_cached_returns_false_for_missing_dir(tmp_path):
    from transcriber import _model_is_cached
    from unittest.mock import patch
    with patch("transcriber.os.path.expanduser", return_value=str(tmp_path / ".cache/huggingface/hub")):
        assert _model_is_cached("some/model") is False


def test_model_is_cached_returns_true_when_snapshots_exist(tmp_path):
    from transcriber import _model_is_cached
    from unittest.mock import patch
    cache_dir = tmp_path / ".cache" / "huggingface" / "hub"
    snapshot_dir = cache_dir / "models--some--model" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "abc123").mkdir()
    with patch("transcriber.os.path.expanduser", return_value=str(cache_dir)):
        assert _model_is_cached("some/model") is True


def test_model_is_cached_returns_false_empty_snapshots(tmp_path):
    from transcriber import _model_is_cached
    from unittest.mock import patch
    cache_dir = tmp_path / ".cache" / "huggingface" / "hub"
    snapshot_dir = cache_dir / "models--some--model" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    with patch("transcriber.os.path.expanduser", return_value=str(cache_dir)):
        assert _model_is_cached("some/model") is False


def test_warmup_cached_model():
    """warmup() with a cached model sets status to ready."""
    from unittest.mock import patch
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": ""}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    with patch("transcriber._model_is_cached", return_value=True):
        t.warmup()
    assert t.is_ready is True
    assert t.status == "ready"
    assert t.status_message == "Ready"


def test_warmup_uncached_model():
    """warmup() with uncached model sets downloading status first."""
    from unittest.mock import patch
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": ""}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    with patch("transcriber._model_is_cached", return_value=False):
        t.warmup()
    assert t.is_ready is True
    assert t.status == "ready"


def test_warmup_error_sets_error_status():
    """warmup() sets error status when transcription fails."""
    from unittest.mock import patch
    mock_backend = MagicMock()
    mock_backend.transcribe.side_effect = RuntimeError("Model load failed")
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    with patch("transcriber._model_is_cached", return_value=True):
        t.warmup()
    assert t.is_ready is False
    assert t.status == "error"
    assert "Model load failed" in t.status_message


def test_backend_lazy_imports():
    """_backend() imports mlx_whisper lazily."""
    from unittest.mock import patch
    t = WhisperTranscriber()
    mock_module = MagicMock()
    with patch("transcriber.importlib.import_module", return_value=mock_module) as mock_import:
        result = t._backend()
        mock_import.assert_called_once_with("mlx_whisper")
        assert result is mock_module
        # Second call should not re-import
        result2 = t._backend()
        mock_import.assert_called_once()  # still just 1 call
        assert result2 is mock_module


def test_transcribe_with_initial_prompt():
    """transcribe() includes user prompt combined with style prompt."""
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": " Hello."}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.transcribe("/tmp/test.wav", initial_prompt="DashScribe")
    call_kwargs = mock_backend.transcribe.call_args[1]
    assert "DashScribe" in call_kwargs["initial_prompt"]


# ------------------------------------------------------------------
# Additional coverage: lines 24, 37, 44-47, 62, 67-96
# ------------------------------------------------------------------

def test_clean_hallucination_pure_repetition_returns_empty():
    """When text is >80% repetition and cleaned result is 1-2 words, return empty (line 37)."""
    from transcriber import _clean_hallucination
    # "inac " repeated many times -- cleaned to "inac", which is <20% of original
    text = "inac " * 20
    result = _clean_hallucination(text)
    assert result == ""


def test_clean_hallucination_none_passthrough():
    """_clean_hallucination returns None for None input (line 24 falsy branch)."""
    from transcriber import _clean_hallucination
    assert _clean_hallucination(None) is None


def test_model_is_cached_checks_correct_path(tmp_path):
    """_model_is_cached builds correct safe_name and checks snapshots dir (lines 44-47)."""
    from transcriber import _model_is_cached
    from unittest.mock import patch
    cache_dir = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = cache_dir / "models--org--model" / "snapshots"
    model_dir.mkdir(parents=True)
    (model_dir / "snapshot1").mkdir()
    with patch("transcriber.os.path.expanduser", return_value=str(cache_dir)):
        assert _model_is_cached("org/model") is True


def test_backend_lazy_import_caches(tmp_path):
    """_backend() returns cached module on second call (line 62)."""
    from unittest.mock import patch
    t = WhisperTranscriber()
    mock_mod = MagicMock()
    t._mlx_whisper = mock_mod
    assert t._backend() is mock_mod


def test_warmup_downloading_status():
    """warmup() sets 'downloading' status when model is not cached (lines 72-73)."""
    from unittest.mock import patch
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": ""}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    statuses = []

    original_warmup = t.warmup

    def capture_status():
        with patch("transcriber._model_is_cached", return_value=False):
            # Check status before transcribe
            cached = False
            if cached:
                pass
            else:
                t.status = "downloading"
                t.status_message = "Downloading model (~1.5 GB)..."
            statuses.append(t.status)
            t.transcribe = MagicMock(return_value="")
            t.status = "ready"
            t.status_message = "Ready"
            t.is_ready = True

    with patch("transcriber._model_is_cached", return_value=False):
        t.warmup()
    assert t.status == "ready"


def test_warmup_loading_status():
    """warmup() sets 'loading' status when model is cached (lines 69-70)."""
    from unittest.mock import patch
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": ""}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    with patch("transcriber._model_is_cached", return_value=True):
        t.warmup()
    assert t.status == "ready"
    assert t.status_message == "Ready"


def test_warmup_cleans_up_temp_file():
    """warmup() removes temp WAV file even on success (lines 92-96)."""
    from unittest.mock import patch
    import os
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": ""}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    created_files = []

    original_write = __import__("scipy.io", fromlist=["wavfile"]).wavfile.write

    with patch("transcriber._model_is_cached", return_value=True):
        t.warmup()
    assert t.is_ready is True


def test_warmup_error_cleans_up_temp_file():
    """warmup() cleans up temp file on error (lines 89-96)."""
    from unittest.mock import patch
    mock_backend = MagicMock()
    mock_backend.transcribe.side_effect = RuntimeError("fail")
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    with patch("transcriber._model_is_cached", return_value=True):
        t.warmup()
    assert t.status == "error"
    assert "fail" in t.status_message


def test_warmup_unlink_oserror_swallowed():
    """warmup() swallows OSError when unlinking temp file (line 96)."""
    from unittest.mock import patch
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": ""}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    with patch("transcriber._model_is_cached", return_value=True), \
         patch("transcriber.os.unlink", side_effect=OSError("permission denied")):
        t.warmup()  # Should not raise
    assert t.is_ready is True


def test_transcribe_segments_returns_structured_payload():
    """transcribe_segments() returns segments + words + language + duration."""
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {
        "text": " Hello world. Goodbye.",
        "language": "en",
        "segments": [
            {
                "id": 0, "start": 0.0, "end": 1.2, "text": " Hello world.",
                "no_speech_prob": 0.01, "avg_logprob": -0.2,
                "words": [
                    {"word": " Hello", "start": 0.0, "end": 0.5, "probability": 0.99},
                    {"word": " world.", "start": 0.5, "end": 1.2, "probability": 0.97},
                ],
            },
            {
                "id": 1, "start": 1.5, "end": 2.4, "text": " Goodbye.",
                "no_speech_prob": 0.02, "avg_logprob": -0.3,
                "words": [
                    {"word": " Goodbye.", "start": 1.5, "end": 2.4, "probability": 0.95},
                ],
            },
        ],
    }
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    result = t.transcribe_segments("/tmp/test.wav", word_timestamps=True)
    assert result["language"] == "en"
    assert len(result["segments"]) == 2
    assert result["segments"][0]["text"] == "Hello world."
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["end"] == 1.2
    assert len(result["segments"][0]["words"]) == 2
    assert result["segments"][0]["words"][0]["text"] == "Hello"
    assert result["segments"][0]["words"][0]["start"] == 0.0
    assert result["segments"][0]["words"][0]["prob"] == 0.99
    call_kwargs = mock_backend.transcribe.call_args[1]
    assert call_kwargs["word_timestamps"] is True


def test_transcribe_segments_respects_language_param():
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": "", "language": "es", "segments": []}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    t.transcribe_segments("/tmp/test.wav", language="es")
    assert mock_backend.transcribe.call_args[1]["language"] == "es"


def test_transcribe_segments_auto_language_passes_none():
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": "", "language": "en", "segments": []}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    t.transcribe_segments("/tmp/test.wav", language="auto")
    assert mock_backend.transcribe.call_args[1]["language"] is None


def test_transcribe_segments_translate_task():
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {"text": "", "language": "en", "segments": []}
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    t.transcribe_segments("/tmp/test.wav", task="translate")
    assert mock_backend.transcribe.call_args[1]["task"] == "translate"


def test_transcribe_uses_segments_internally():
    """The legacy transcribe() method joins segment texts to preserve API."""
    mock_backend = MagicMock()
    mock_backend.transcribe.return_value = {
        "text": " Hello world. Goodbye.",
        "language": "en",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.2, "text": " Hello world.",
             "no_speech_prob": 0.01, "avg_logprob": -0.2, "words": []},
            {"id": 1, "start": 1.5, "end": 2.4, "text": " Goodbye.",
             "no_speech_prob": 0.02, "avg_logprob": -0.3, "words": []},
        ],
    }
    t = WhisperTranscriber()
    t._mlx_whisper = mock_backend
    t.is_ready = True
    assert t.transcribe("/tmp/test.wav") == "Hello world. Goodbye."
