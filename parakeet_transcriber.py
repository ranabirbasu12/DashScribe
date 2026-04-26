# parakeet_transcriber.py
"""Parakeet TDT 0.6B v3 transcriber via parakeet-mlx (no PyTorch)."""
import threading

MODEL_REPO = "mlx-community/parakeet-tdt-0.6b-v3"


class ParakeetTranscriber:
    def __init__(self, model_repo: str = MODEL_REPO):
        self.model_repo = model_repo
        self.is_ready = False
        self.status = "not_started"
        self.status_message = "Initializing..."
        self._lock = threading.RLock()
        self._model = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        from parakeet_mlx import from_pretrained
        self._model = from_pretrained(self.model_repo)
        self.is_ready = True

    def transcribe_segments(
        self,
        audio_path: str,
        *,
        language: str | None = "en",   # accepted for API compat; Parakeet auto-detects
        task: str = "transcribe",       # ignored — Parakeet does transcription only
        initial_prompt: str | None = None,
        word_timestamps: bool = True,   # always on in Parakeet TDT
        temperature: float = 0.0,
        beam_size: int | None = None,
        condition_on_previous_text: bool = False,
    ) -> dict:
        with self._lock:
            self._ensure_loaded()
            result = self._model.transcribe(audio_path)
            segments = []
            for i, sent in enumerate(getattr(result, "sentences", []) or []):
                words = []
                for tok in getattr(sent, "tokens", []) or []:
                    words.append({
                        "text": (getattr(tok, "text", "") or "").strip(),
                        "start": float(getattr(tok, "start", 0.0)),
                        "end": float(getattr(tok, "end", 0.0)),
                        "prob": 1.0,
                    })
                segments.append({
                    "id": i,
                    "start": float(getattr(sent, "start", 0.0)),
                    "end": float(getattr(sent, "end", 0.0)),
                    "text": (getattr(sent, "text", "") or "").strip(),
                    "no_speech_prob": 0.0,
                    "avg_logprob": 0.0,
                    "words": words,
                })
            return {"language": "en", "segments": segments}

    def transcribe(self, audio_path: str, *, initial_prompt: str | None = None) -> str:
        result = self.transcribe_segments(audio_path, initial_prompt=initial_prompt)
        return " ".join(s["text"] for s in result["segments"]).strip()
