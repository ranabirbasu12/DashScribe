# diarizer.py
"""Speaker diarization via sherpa-onnx (no PyTorch).

Bundles pyannote-segmentation-3.0 + 3D-Speaker CAM++ embedding model.
Models download lazily on first use to ~/.cache/dashscribe/diarizer/.
"""
import os
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

CACHE_DIR = Path(os.path.expanduser("~/.cache/dashscribe/diarizer"))

SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
SEGMENTATION_FILE = "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"

EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
EMBEDDING_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker_id: str  # "S1", "S2", ...


class Diarizer:
    def __init__(self):
        self.is_loaded = False
        self.status = "idle"
        self.status_message = ""
        self._lock = threading.RLock()
        self._session = None

    def _model_paths(self) -> tuple[Path, Path]:
        return (CACHE_DIR / SEGMENTATION_FILE, CACHE_DIR / EMBEDDING_FILE)

    def is_cached(self) -> bool:
        seg, emb = self._model_paths()
        return seg.exists() and emb.exists()

    def _download_models(self) -> None:
        import tarfile
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        seg_path, emb_path = self._model_paths()
        if not seg_path.exists():
            tarball = CACHE_DIR / "segmentation.tar.bz2"
            urllib.request.urlretrieve(SEGMENTATION_URL, tarball)
            with tarfile.open(tarball, "r:bz2") as tf:
                tf.extractall(CACHE_DIR)
            tarball.unlink(missing_ok=True)
        if not emb_path.exists():
            urllib.request.urlretrieve(EMBEDDING_URL, emb_path)

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        if not self.is_cached():
            self.status = "downloading"
            self.status_message = "Downloading diarization models (~35 MB)..."
            self._download_models()
        self.status = "loading"
        self.status_message = "Loading diarization models..."
        import sherpa_onnx
        seg_path, emb_path = self._model_paths()
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(seg_path),
                ),
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_path)),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        self._session = sherpa_onnx.OfflineSpeakerDiarization(config)
        self.is_loaded = True
        self.status = "ready"
        self.status_message = "Ready"

    def diarize(
        self,
        audio_path: str,
        *,
        num_speakers: int | str = "auto",
    ) -> list[SpeakerSegment]:
        """Run diarization. num_speakers: 'auto' or an int 1..N."""
        with self._lock:
            self._ensure_loaded()
            if isinstance(num_speakers, int) and num_speakers > 0:
                self._session.config.clustering.num_clusters = num_speakers
            else:
                self._session.config.clustering.num_clusters = -1
            audio, sr = sf.read(audio_path, dtype="float32", always_2d=False)
            if audio.ndim == 2:
                audio = audio.mean(axis=1)
            target_sr = self._session.sample_rate
            if isinstance(target_sr, int) and sr != target_sr:
                ratio = target_sr / sr
                new_len = int(len(audio) * ratio)
                idx = (np.arange(new_len) / ratio).astype(np.int64)
                idx = np.clip(idx, 0, len(audio) - 1)
                audio = audio[idx]
            raw_result = self._session.process(audio).sort_by_start_time()
            return [
                SpeakerSegment(
                    start=float(s.start),
                    end=float(s.end),
                    speaker_id=f"S{int(s.speaker) + 1}",
                )
                for s in raw_result
            ]
