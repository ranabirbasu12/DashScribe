# transcriber.py
import tempfile
import os
import threading
import importlib

import mlx.core as mx
import numpy as np
from scipy.io import wavfile

import re

MODEL_REPO = "mlx-community/whisper-large-v3-turbo"

# Default prompt that establishes punctuation style for Whisper.
# Whisper mimics the style of the prompt (not instructions in it).
# A well-punctuated multi-sentence prompt steers it toward consistent punct.
PUNCTUATION_STYLE_PROMPT = (
    "I need to finish the report by Friday. Can you send me the updated numbers? "
    "Thanks, I'll review them tonight. Also, the meeting has been moved to 3 PM."
)


def _clean_hallucination(text: str) -> str:
    """Detect and remove repetitive hallucination patterns from Whisper output.

    Whisper sometimes produces text like "inac inac inac..." or "Thank you. Thank you. Thank you."
    when processing silence or low-energy audio. This detects any short phrase (1-4 words)
    repeated 4+ times consecutively and strips the repetitions.
    """
    if not text:
        return text

    # Match any token/short phrase repeated 4+ times consecutively
    # Handles: "word word word word..." and "two words two words two words..."
    cleaned = re.sub(
        r'\b((?:\S+\s*){1,4}?)\s*(?:\1\s*){3,}',
        r'\1',
        text,
    )

    # If the "cleaned" result is just 1-2 words that were the repeated unit,
    # and the original was mostly repetition (>80% removed), treat as pure hallucination
    if len(cleaned.split()) <= 2 and len(cleaned) < len(text) * 0.2:
        return ""

    return cleaned.strip()


def _normalize_whisper_result(raw: dict) -> dict:
    """Convert mlx-whisper output to our unified shape."""
    segments_out = []
    for seg in raw.get("segments", []):
        words_out = []
        for w in seg.get("words", []) or []:
            words_out.append({
                "text": (w.get("word") or "").strip(),
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0)),
                "prob": float(w.get("probability", 1.0)),
            })
        segments_out.append({
            "id": int(seg.get("id", len(segments_out))),
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": (seg.get("text") or "").strip(),
            "no_speech_prob": float(seg.get("no_speech_prob", 0.0)),
            "avg_logprob": float(seg.get("avg_logprob", 0.0)),
            "words": words_out,
        })
    return {
        "language": raw.get("language", "en"),
        "segments": segments_out,
    }


def _model_is_cached(model_repo: str) -> bool:
    """Check if the model is already in the HuggingFace cache."""
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    safe_name = "models--" + model_repo.replace("/", "--")
    model_dir = os.path.join(cache_dir, safe_name, "snapshots")
    return os.path.isdir(model_dir) and len(os.listdir(model_dir)) > 0


class WhisperTranscriber:
    def __init__(self, model_repo: str = MODEL_REPO):
        self.model_repo = model_repo
        self.is_ready = False
        self.status = "not_started"  # not_started, downloading, loading, ready, error
        self.status_message = "Initializing..."
        # Serialize all inference calls; mlx_whisper is not guaranteed thread-safe.
        self._lock = threading.RLock()
        self._mlx_whisper = None

    def _backend(self):
        if self._mlx_whisper is None:
            self._mlx_whisper = importlib.import_module("mlx_whisper")
        return self._mlx_whisper

    def warmup(self):
        """Run a tiny transcription to pre-load the model into memory."""
        cached = _model_is_cached(self.model_repo)
        if cached:
            self.status = "loading"
            self.status_message = "Loading model into memory..."
        else:
            self.status = "downloading"
            self.status_message = "Downloading model (~1.5 GB)..."

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        silence = np.zeros(16000, dtype=np.int16)
        wavfile.write(tmp.name, 16000, silence)
        tmp.close()
        try:
            if not cached:
                # After download completes, status switches to loading
                # (mlx_whisper.transcribe handles download + load in one call)
                pass
            self.transcribe(tmp.name)
            # If we were downloading, the model is now also loaded
            self.status = "ready"
            self.status_message = "Ready"
            self.is_ready = True
        except Exception as e:
            self.status = "error"
            self.status_message = f"Error: {e}"
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _build_prompt(self, initial_prompt: str | None = None) -> str:
        """Combine punctuation style prompt with user-provided prompt (dictionary terms etc).

        The style prompt establishes consistent punctuation. User prompt adds
        domain-specific terms. Both are concatenated, truncated to Whisper's
        224-token prompt limit.
        """
        parts = []
        parts.append(PUNCTUATION_STYLE_PROMPT)
        if initial_prompt:
            parts.append(initial_prompt)
        return " ".join(parts)

    def transcribe_segments(
        self,
        audio_path: str,
        *,
        language: str | None = "en",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        temperature: float = 0.0,
        beam_size: int | None = None,
        condition_on_previous_text: bool = False,
    ) -> dict:
        """Transcribe and return the full structured payload (segments, words, language).

        Defaults to language="en"; pass "auto" or None to enable Whisper's language detection.
        Returns a dict with keys: language, segments (list of dicts with id, start, end,
        text, no_speech_prob, avg_logprob, words). Word entries use {text, start, end, prob}.
        """
        with self._lock:
            prompt = self._build_prompt(initial_prompt)
            lang = None if language in ("auto", None) else language
            kwargs = {
                "path_or_hf_repo": self.model_repo,
                "language": lang,
                "task": task,
                "condition_on_previous_text": condition_on_previous_text,
                "initial_prompt": prompt,
                "word_timestamps": word_timestamps,
                "temperature": temperature,
            }
            if beam_size is not None:
                kwargs["beam_size"] = beam_size
            result = self._backend().transcribe(audio_path, **kwargs)
            self.is_ready = True
            mx.clear_cache()
            return _normalize_whisper_result(result)

    def transcribe(self, audio_path: str, *, initial_prompt: str | None = None) -> str:
        result = self.transcribe_segments(
            audio_path,
            language="en",
            initial_prompt=initial_prompt,
        )
        text = " ".join(s["text"] for s in result["segments"] if s["text"]).strip()
        return _clean_hallucination(text)

    def transcribe_array(self, audio: np.ndarray, *, initial_prompt: str | None = None) -> str:
        """Transcribe a numpy float32 audio array directly (no WAV file).

        Uses anti-hallucination parameters tuned for segmented audio.
        """
        with self._lock:
            prompt = self._build_prompt(initial_prompt)
            result = self._backend().transcribe(
                audio,
                path_or_hf_repo=self.model_repo,
                language="en",
                condition_on_previous_text=False,
                hallucination_silence_threshold=2.0,
                compression_ratio_threshold=2.4,
                initial_prompt=prompt,
            )
            self.is_ready = True
            mx.clear_cache()
            return _clean_hallucination(result["text"].strip())
