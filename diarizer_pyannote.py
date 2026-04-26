# diarizer_pyannote.py
"""Optional pyannote community-1 diarization (heavyweight: PyTorch + ~700 MB weights)."""
import importlib.util
import threading
from pathlib import Path

from diarizer import SpeakerSegment

CACHE_DIR = Path("~/.cache/dashscribe/pyannote").expanduser()
# NOTE: replace with the DashScribe-hosted CC-BY-4.0 mirror URL once published.
# The current placeholder URL is intentional — the install endpoint detects it
# and fails fast with a user-readable message.
WEIGHTS_URL = (
    "https://github.com/dashscribe/dashscribe/releases/download/"
    "models-pyannote-community-1/weights.tar.bz2"
)


def is_pyannote_installed() -> bool:
    return importlib.util.find_spec("pyannote.audio") is not None


class PyannoteDiarizer:
    def __init__(self):
        self.is_loaded = False
        self.status = "idle"
        self.status_message = ""
        self._lock = threading.RLock()
        self._pipeline = None

    def _ensure_loaded(self):
        if self._pipeline is not None:
            return
        from pyannote.audio import Pipeline
        weights_dir = CACHE_DIR / "speaker-diarization-community-1"
        if not weights_dir.exists():
            raise RuntimeError("Pyannote weights not downloaded — run the install flow first.")
        self._pipeline = Pipeline.from_pretrained(str(weights_dir))
        self.is_loaded = True

    def diarize(self, audio_path: str, *, num_speakers: int | str = "auto") -> list[SpeakerSegment]:
        with self._lock:
            self._ensure_loaded()
            kwargs = {}
            if isinstance(num_speakers, int) and num_speakers > 0:
                kwargs["num_speakers"] = num_speakers
            annotation = self._pipeline(audio_path, **kwargs)
            label_to_id: dict[str, str] = {}
            out: list[SpeakerSegment] = []
            for segment, _, label in annotation.itertracks(yield_label=True):
                if label not in label_to_id:
                    label_to_id[label] = f"S{len(label_to_id) + 1}"
                out.append(SpeakerSegment(
                    start=float(segment.start),
                    end=float(segment.end),
                    speaker_id=label_to_id[label],
                ))
            out.sort(key=lambda s: s.start)
            return out
