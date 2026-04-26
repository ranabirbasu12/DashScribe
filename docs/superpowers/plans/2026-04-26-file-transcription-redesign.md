# File Transcription Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild DashScribe's File tab into a 2026-tier local file-transcription experience: drag-drop + URL ingestion, AI diarization, Parakeet engine option, structured segment data, two-pane result view with karaoke playback and multi-format export.

**Architecture:** Add a unified per-job JSON segment payload as the central contract. New backend modules (`Diarizer`, `TranscriptAssembler`, `Exporter`, `FileJob`, `ParakeetTranscriber`) compose into a pipeline orchestrated by `FileJobRunner`. The frontend `#file-mode` page is rebuilt with three states (empty → transcribing → result) sharing a collapsible right sidebar. Other modes (Dictation, ClassNote, Meeting) are untouched.

**Tech Stack:** Python (FastAPI, mlx-whisper, parakeet-mlx, sherpa-onnx, python-docx, yt-dlp); vanilla JS/HTML/CSS frontend; py2app bundling. TDD on all backend modules with `pytest`. UI changes verified via the manual UAT checklist in the design spec.

**Spec:** [docs/superpowers/specs/2026-04-26-file-transcription-redesign-design.md](../specs/2026-04-26-file-transcription-redesign-design.md)

---

## Phase 1 — Backend foundation (TDD)

### Task 1: Refactor WhisperTranscriber to emit structured segments

**Files:**
- Modify: `transcriber.py`
- Modify: `tests/test_transcriber.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcriber.py`:

```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_transcriber.py::test_transcribe_segments_returns_structured_payload -v`
Expected: FAIL with `AttributeError: 'WhisperTranscriber' object has no attribute 'transcribe_segments'`

- [ ] **Step 3: Implement `transcribe_segments`**

Edit `transcriber.py`. Add this method to `WhisperTranscriber` (place it after `_build_prompt`):

```python
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

        language="auto" or None lets Whisper detect.
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
```

Then refactor the existing `transcribe()` to use `transcribe_segments()`:

```python
    def transcribe(self, audio_path: str, *, initial_prompt: str | None = None) -> str:
        result = self.transcribe_segments(
            audio_path,
            language="en",
            initial_prompt=initial_prompt,
        )
        text = " ".join(s["text"] for s in result["segments"]).strip()
        return _clean_hallucination(text)
```

(`transcribe_array` stays unchanged — it operates on numpy arrays and uses different anti-hallucination params.)

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_transcriber.py -v`
Expected: PASS for all (existing + new)

- [ ] **Step 5: Commit**

```bash
git add transcriber.py tests/test_transcriber.py
git commit -m "feat(transcriber): add structured transcribe_segments() with word timestamps"
```

---

### Task 2: Diarizer (sherpa-onnx)

**Files:**
- Create: `diarizer.py`
- Create: `tests/test_diarizer.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependency**

Append to `requirements.txt`:

```
sherpa-onnx>=1.10.30
```

Then: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_diarizer.py`:

```python
# tests/test_diarizer.py
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


def test_diarize_with_speaker_count_hint():
    """When num_speakers is given, it's passed through to the session config."""
    d = Diarizer()
    fake_session = MagicMock()
    fake_session.process.return_value.sort_by_start_time.return_value = []
    d._session = fake_session
    d.is_loaded = True
    d.diarize("/tmp/test.wav", num_speakers=3)
    assert fake_session.config.clustering.num_clusters == 3


def test_diarize_returns_empty_for_silence():
    d = Diarizer()
    fake_session = MagicMock()
    fake_session.process.return_value.sort_by_start_time.return_value = []
    d._session = fake_session
    d.is_loaded = True
    assert d.diarize("/tmp/test.wav") == []
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/test_diarizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'diarizer'`

- [ ] **Step 4: Implement `diarizer.py`**

Create `diarizer.py`:

```python
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
            import soundfile as sf
            import numpy as np
            audio, sr = sf.read(audio_path, dtype="float32", always_2d=False)
            if audio.ndim == 2:
                audio = audio.mean(axis=1)
            if sr != self._session.sample_rate:
                ratio = self._session.sample_rate / sr
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
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/test_diarizer.py -v`
Expected: PASS for all 5 tests

- [ ] **Step 6: Commit**

```bash
git add diarizer.py tests/test_diarizer.py requirements.txt
git commit -m "feat(diarizer): add sherpa-onnx based speaker diarization"
```

---

### Task 3: Transcript assembler (merge ASR segments + speaker turns)

**Files:**
- Create: `transcript_assembler.py`
- Create: `tests/test_transcript_assembler.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcript_assembler.py`:

```python
# tests/test_transcript_assembler.py
from diarizer import SpeakerSegment
from transcript_assembler import assemble


WHISPER_RESULT = {
    "language": "en",
    "segments": [
        {
            "id": 0, "start": 0.0, "end": 2.0, "text": "Hello there.",
            "no_speech_prob": 0.01, "avg_logprob": -0.2,
            "words": [
                {"text": "Hello", "start": 0.0, "end": 0.7, "prob": 0.99},
                {"text": "there.", "start": 0.7, "end": 2.0, "prob": 0.97},
            ],
        },
        {
            "id": 1, "start": 2.5, "end": 4.5, "text": "Hi back.",
            "no_speech_prob": 0.02, "avg_logprob": -0.3,
            "words": [
                {"text": "Hi", "start": 2.5, "end": 3.0, "prob": 0.95},
                {"text": "back.", "start": 3.0, "end": 4.5, "prob": 0.94},
            ],
        },
    ],
}


def test_assemble_without_diarization_uses_single_speaker():
    payload = assemble(
        whisper_result=WHISPER_RESULT,
        speaker_turns=None,
        engine="whisper-turbo",
        audio_path="/tmp/x.mp3",
        duration=4.5,
    )
    assert payload["version"] == 1
    assert payload["engine"] == "whisper-turbo"
    assert payload["language"] == "en"
    assert payload["duration_seconds"] == 4.5
    assert payload["audio_path"] == "/tmp/x.mp3"
    assert len(payload["speakers"]) == 1
    assert payload["speakers"][0]["id"] == "S1"
    for seg in payload["segments"]:
        assert seg["speaker_id"] == "S1"


def test_assemble_with_diarization_assigns_speakers():
    """Each ASR segment is assigned the speaker whose turn covers most of it."""
    turns = [
        SpeakerSegment(start=0.0, end=2.2, speaker_id="S1"),
        SpeakerSegment(start=2.3, end=4.5, speaker_id="S2"),
    ]
    payload = assemble(
        whisper_result=WHISPER_RESULT,
        speaker_turns=turns,
        engine="parakeet",
        audio_path="/tmp/x.mp3",
        duration=4.5,
    )
    assert {s["id"] for s in payload["speakers"]} == {"S1", "S2"}
    assert payload["segments"][0]["speaker_id"] == "S1"
    assert payload["segments"][1]["speaker_id"] == "S2"


def test_assemble_handles_partial_overlap_majority_wins():
    """A segment that straddles two speaker turns is assigned to the majority."""
    turns = [
        SpeakerSegment(start=0.0, end=1.5, speaker_id="S1"),
        SpeakerSegment(start=1.5, end=4.5, speaker_id="S2"),
    ]
    payload = assemble(
        whisper_result=WHISPER_RESULT,
        speaker_turns=turns,
        engine="whisper-turbo",
        audio_path="/tmp/x.mp3",
        duration=4.5,
    )
    # Segment 0 (0.0-2.0): 1.5s S1 + 0.5s S2 → S1
    # Segment 1 (2.5-4.5): all S2
    assert payload["segments"][0]["speaker_id"] == "S1"
    assert payload["segments"][1]["speaker_id"] == "S2"


def test_assemble_speakers_get_palette_colors():
    """First speaker gets palette[0], second palette[1], etc."""
    turns = [
        SpeakerSegment(start=0.0, end=2.0, speaker_id="S1"),
        SpeakerSegment(start=2.0, end=4.5, speaker_id="S2"),
    ]
    payload = assemble(
        whisper_result=WHISPER_RESULT,
        speaker_turns=turns,
        engine="whisper-turbo",
        audio_path="/tmp/x.mp3",
        duration=4.5,
    )
    colors = [s["color"] for s in payload["speakers"]]
    assert len(colors) == 2
    assert colors[0] != colors[1]
    assert all(c.startswith("#") and len(c) == 7 for c in colors)


def test_assemble_speakers_get_default_labels():
    turns = [SpeakerSegment(start=0.0, end=4.5, speaker_id="S1")]
    payload = assemble(
        whisper_result=WHISPER_RESULT,
        speaker_turns=turns,
        engine="whisper-turbo",
        audio_path="/tmp/x.mp3",
        duration=4.5,
    )
    assert payload["speakers"][0]["label"] == "Speaker 1"


def test_assemble_includes_created_at_timestamp():
    payload = assemble(
        whisper_result=WHISPER_RESULT,
        speaker_turns=None,
        engine="whisper-turbo",
        audio_path="/tmp/x.mp3",
        duration=4.5,
    )
    assert "created_at" in payload
    assert payload["created_at"].endswith("Z")
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_transcript_assembler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'transcript_assembler'`

- [ ] **Step 3: Implement `transcript_assembler.py`**

Create `transcript_assembler.py`:

```python
# transcript_assembler.py
"""Merge ASR segments + diarizer speaker turns into the unified payload."""
from datetime import datetime, timezone
from typing import Optional

from diarizer import SpeakerSegment

PAYLOAD_VERSION = 1
PALETTE = [
    "#5B8DEF", "#F08C5B", "#7DB35E", "#C46FCE",
    "#E0B341", "#52B7C6", "#D4604E", "#8C8C8C",
]


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _assign_speaker(seg_start: float, seg_end: float, turns: list[SpeakerSegment]) -> str:
    """Return the speaker_id whose turn overlaps the segment most."""
    best_id = "S1"
    best_overlap = -1.0
    for t in turns:
        ov = _overlap(seg_start, seg_end, t.start, t.end)
        if ov > best_overlap:
            best_overlap = ov
            best_id = t.speaker_id
    return best_id


def assemble(
    *,
    whisper_result: dict,
    speaker_turns: Optional[list[SpeakerSegment]],
    engine: str,
    audio_path: str,
    duration: float,
) -> dict:
    """Build the unified transcript payload."""
    if speaker_turns:
        unique_ids: list[str] = []
        for t in speaker_turns:
            if t.speaker_id not in unique_ids:
                unique_ids.append(t.speaker_id)
    else:
        unique_ids = ["S1"]

    speakers = []
    for i, sid in enumerate(unique_ids):
        speakers.append({
            "id": sid,
            "label": f"Speaker {i + 1}",
            "color": PALETTE[i % len(PALETTE)],
        })

    segments_out = []
    for seg in whisper_result.get("segments", []):
        if speaker_turns:
            spk = _assign_speaker(seg["start"], seg["end"], speaker_turns)
        else:
            spk = "S1"
        out = dict(seg)
        out["speaker_id"] = spk
        segments_out.append(out)

    return {
        "version": PAYLOAD_VERSION,
        "engine": engine,
        "language": whisper_result.get("language", "en"),
        "duration_seconds": duration,
        "audio_path": audio_path,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "speakers": speakers,
        "segments": segments_out,
    }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_transcript_assembler.py -v`
Expected: PASS for all 6 tests

- [ ] **Step 5: Commit**

```bash
git add transcript_assembler.py tests/test_transcript_assembler.py
git commit -m "feat(transcript): add assembler that merges ASR + speaker turns"
```

---

### Task 4: Exporter (TXT, Markdown, SRT, VTT, JSON, DOCX)

**Files:**
- Create: `exporter.py`
- Create: `tests/test_exporter.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependency**

Append to `requirements.txt`:

```
python-docx>=1.1.2
```

Then: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_exporter.py`:

```python
# tests/test_exporter.py
import json
import tempfile
from pathlib import Path

from exporter import (
    to_txt, to_markdown, to_srt, to_vtt, to_json, to_docx,
    write_export, FORMATS,
)


PAYLOAD = {
    "version": 1,
    "engine": "whisper-turbo",
    "language": "en",
    "duration_seconds": 6.0,
    "audio_path": "/tmp/x.mp3",
    "created_at": "2026-04-26T18:22:11Z",
    "speakers": [
        {"id": "S1", "label": "Alex", "color": "#5B8DEF"},
        {"id": "S2", "label": "Sam", "color": "#F08C5B"},
    ],
    "segments": [
        {"id": 0, "speaker_id": "S1", "start": 0.0, "end": 2.0,
         "text": "Hello there.", "no_speech_prob": 0.01, "avg_logprob": -0.2,
         "words": [{"text": "Hello", "start": 0.0, "end": 0.7, "prob": 0.99}]},
        {"id": 1, "speaker_id": "S2", "start": 2.5, "end": 4.5,
         "text": "Hi back.", "no_speech_prob": 0.02, "avg_logprob": -0.3,
         "words": []},
        {"id": 2, "speaker_id": "S1", "start": 5.0, "end": 6.0,
         "text": "Bye!", "no_speech_prob": 0.01, "avg_logprob": -0.2, "words": []},
    ],
}


def test_to_txt_joins_segment_texts():
    out = to_txt(PAYLOAD)
    assert out == "Hello there. Hi back. Bye!"


def test_to_markdown_groups_by_speaker_with_timestamps():
    out = to_markdown(PAYLOAD)
    assert "**Alex** _00:00:00_" in out
    assert "Hello there." in out
    assert "**Sam** _00:00:02_" in out
    assert "Hi back." in out
    # Two non-adjacent Alex turns
    assert out.count("**Alex**") == 2


def test_to_srt_format():
    out = to_srt(PAYLOAD)
    lines = out.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:02,000"
    assert lines[2] == "Alex: Hello there."
    assert lines[3] == ""
    assert lines[4] == "2"
    assert lines[5] == "00:00:02,500 --> 00:00:04,500"
    assert lines[6] == "Sam: Hi back."


def test_to_vtt_format():
    out = to_vtt(PAYLOAD)
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:02.000" in out
    assert "Alex: Hello there." in out


def test_to_json_returns_payload_string():
    out = to_json(PAYLOAD)
    parsed = json.loads(out)
    assert parsed["version"] == 1
    assert len(parsed["segments"]) == 3


def test_to_docx_writes_valid_file():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.docx"
        to_docx(PAYLOAD, str(path))
        assert path.exists()
        assert path.stat().st_size > 0
        from docx import Document
        doc = Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "Alex" in text
        assert "Hello there." in text


def test_formats_dict_has_all_six():
    assert set(FORMATS.keys()) == {"txt", "md", "srt", "vtt", "json", "docx"}


def test_write_export_dispatches_by_format(tmp_path):
    out = tmp_path / "x.srt"
    write_export(PAYLOAD, "srt", str(out))
    assert out.read_text(encoding="utf-8").startswith("1\n00:00:00,000")


def test_write_export_unknown_format_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="Unknown format"):
        write_export(PAYLOAD, "xyz", str(tmp_path / "x.xyz"))
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/test_exporter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'exporter'`

- [ ] **Step 4: Implement `exporter.py`**

Create `exporter.py`:

```python
# exporter.py
"""Render the unified transcript payload into multiple output formats."""
import json


def _fmt_ts(seconds: float, *, srt: bool = False) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    sep = "," if srt else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _fmt_clock(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _speaker_label(payload: dict, speaker_id: str) -> str:
    for sp in payload.get("speakers", []):
        if sp["id"] == speaker_id:
            return sp["label"]
    return speaker_id


def to_txt(payload: dict) -> str:
    return " ".join(s["text"] for s in payload["segments"]).strip()


def to_markdown(payload: dict) -> str:
    """Speaker-grouped paragraphs: consecutive same-speaker segments combine."""
    lines: list[str] = []
    current_spk: str | None = None
    current_chunks: list[str] = []
    current_start: float = 0.0
    for seg in payload["segments"]:
        if seg["speaker_id"] != current_spk:
            if current_chunks:
                lines.append(
                    f"**{_speaker_label(payload, current_spk)}** _"
                    f"{_fmt_clock(current_start)}_\n\n" + " ".join(current_chunks) + "\n"
                )
            current_spk = seg["speaker_id"]
            current_start = seg["start"]
            current_chunks = [seg["text"]]
        else:
            current_chunks.append(seg["text"])
    if current_chunks:
        lines.append(
            f"**{_speaker_label(payload, current_spk)}** _"
            f"{_fmt_clock(current_start)}_\n\n" + " ".join(current_chunks) + "\n"
        )
    return "\n".join(lines)


def to_srt(payload: dict) -> str:
    blocks: list[str] = []
    for i, seg in enumerate(payload["segments"], start=1):
        label = _speaker_label(payload, seg["speaker_id"])
        blocks.append(
            f"{i}\n"
            f"{_fmt_ts(seg['start'], srt=True)} --> {_fmt_ts(seg['end'], srt=True)}\n"
            f"{label}: {seg['text']}\n"
        )
    return "\n".join(blocks)


def to_vtt(payload: dict) -> str:
    blocks: list[str] = ["WEBVTT\n"]
    for seg in payload["segments"]:
        label = _speaker_label(payload, seg["speaker_id"])
        blocks.append(
            f"{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}\n"
            f"{label}: {seg['text']}\n"
        )
    return "\n".join(blocks)


def to_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def to_docx(payload: dict, dest_path: str) -> None:
    """Writes a .docx with speaker-grouped paragraphs and timestamps."""
    from docx import Document
    doc = Document()
    current_spk: str | None = None
    current_para = None
    for seg in payload["segments"]:
        if seg["speaker_id"] != current_spk:
            label = _speaker_label(payload, seg["speaker_id"])
            ts = _fmt_clock(seg["start"])
            heading = doc.add_paragraph()
            run = heading.add_run(f"{label}  ")
            run.bold = True
            heading.add_run(ts).italic = True
            current_para = doc.add_paragraph(seg["text"])
            current_spk = seg["speaker_id"]
        else:
            current_para.add_run(" " + seg["text"])
    doc.save(dest_path)


FORMATS = {
    "txt": "text/plain",
    "md": "text/markdown",
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
    "json": "application/json",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def write_export(payload: dict, fmt: str, dest_path: str) -> None:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format: {fmt}")
    if fmt == "docx":
        to_docx(payload, dest_path)
        return
    renderer = {
        "txt": to_txt, "md": to_markdown, "srt": to_srt,
        "vtt": to_vtt, "json": to_json,
    }[fmt]
    content = renderer(payload)
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(content)
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/test_exporter.py -v`
Expected: PASS for all 9 tests

- [ ] **Step 6: Commit**

```bash
git add exporter.py tests/test_exporter.py requirements.txt
git commit -m "feat(exporter): render unified payload to TXT/MD/SRT/VTT/JSON/DOCX"
```

---

### Task 5: FileJob runner

**Files:**
- Create: `file_job.py`
- Create: `tests/test_file_job.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_file_job.py`:

```python
# tests/test_file_job.py
import json
from unittest.mock import MagicMock

from file_job import FileJob, FileJobOptions, FileJobRunner


def test_options_defaults():
    o = FileJobOptions()
    assert o.engine == "auto"
    assert o.language == "auto"
    assert o.task == "transcribe"
    assert o.diarization_enabled is True
    assert o.diarization_engine == "sherpa-onnx"
    assert o.speaker_count == "auto"
    assert o.quality_preset == "balanced"
    assert o.timestamp_granularity == "sentence"


def test_options_resolve_engine_from_preset():
    o = FileJobOptions(engine="auto", quality_preset="fast")
    assert o.resolved_engine() == "parakeet"
    o2 = FileJobOptions(engine="auto", quality_preset="balanced")
    assert o2.resolved_engine() == "whisper-turbo"
    o3 = FileJobOptions(engine="auto", quality_preset="best")
    assert o3.resolved_engine() == "whisper-large"
    o4 = FileJobOptions(engine="parakeet", quality_preset="best")
    assert o4.resolved_engine() == "parakeet"  # explicit overrides preset


def test_filejob_dataclass():
    j = FileJob(job_id="abc", source_path="/tmp/x.mp3", options=FileJobOptions())
    assert j.job_id == "abc"
    assert j.source_path == "/tmp/x.mp3"
    assert j.status == "queued"


def test_runner_runs_pipeline_and_emits_progress(tmp_path):
    """The runner orchestrates extract → ASR → diarize → assemble → write JSON."""
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")  # presence check only; transcriber is mocked

    txr = MagicMock()
    txr.transcribe_segments.return_value = {
        "language": "en",
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "Hi.",
             "no_speech_prob": 0.01, "avg_logprob": -0.2, "words": []},
        ],
    }
    diarizer = MagicMock()
    from diarizer import SpeakerSegment
    diarizer.diarize.return_value = [
        SpeakerSegment(start=0.0, end=2.0, speaker_id="S1"),
    ]

    progress_events: list[dict] = []

    runner = FileJobRunner(
        transcriber_factory=lambda engine: txr,
        diarizer=diarizer,
        ffprobe_duration=lambda p: 2.0,
        on_progress=lambda job_id, **kw: progress_events.append({"job_id": job_id, **kw}),
    )
    job = FileJob(
        job_id="job1",
        source_path=str(audio),
        options=FileJobOptions(engine="whisper-turbo", diarization_enabled=True),
    )
    payload = runner.run(job)

    assert payload["version"] == 1
    assert payload["language"] == "en"
    assert payload["audio_path"] == str(audio)
    assert payload["segments"][0]["speaker_id"] == "S1"
    sidecar = audio.with_suffix(".json")
    assert sidecar.exists()
    saved = json.loads(sidecar.read_text(encoding="utf-8"))
    assert saved["version"] == 1
    stages = {e["stage"] for e in progress_events}
    assert "transcribing" in stages


def test_runner_without_diarization_uses_single_speaker(tmp_path):
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")
    txr = MagicMock()
    txr.transcribe_segments.return_value = {
        "language": "en",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "Hi.",
             "no_speech_prob": 0.01, "avg_logprob": -0.2, "words": []},
        ],
    }
    runner = FileJobRunner(
        transcriber_factory=lambda engine: txr,
        diarizer=MagicMock(),
        ffprobe_duration=lambda p: 1.0,
        on_progress=lambda *a, **kw: None,
    )
    job = FileJob(
        job_id="j",
        source_path=str(audio),
        options=FileJobOptions(diarization_enabled=False),
    )
    payload = runner.run(job)
    assert len(payload["speakers"]) == 1


def test_runner_diarization_failure_falls_back_to_single_speaker(tmp_path):
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")
    txr = MagicMock()
    txr.transcribe_segments.return_value = {
        "language": "en",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "Hi.",
             "no_speech_prob": 0.01, "avg_logprob": -0.2, "words": []},
        ],
    }
    failing_diarizer = MagicMock()
    failing_diarizer.diarize.side_effect = RuntimeError("boom")

    runner = FileJobRunner(
        transcriber_factory=lambda engine: txr,
        diarizer=failing_diarizer,
        ffprobe_duration=lambda p: 1.0,
        on_progress=lambda *a, **kw: None,
    )
    job = FileJob(
        job_id="j",
        source_path=str(audio),
        options=FileJobOptions(diarization_enabled=True),
    )
    payload = runner.run(job)
    assert len(payload["speakers"]) == 1
    assert payload.get("warnings") and any("Diarization" in w for w in payload["warnings"])
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_file_job.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'file_job'`

- [ ] **Step 3: Implement `file_job.py`**

Create `file_job.py`:

```python
# file_job.py
"""FileJob orchestrator: extract → ASR → diarize → assemble → write sidecar."""
import json
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from transcript_assembler import assemble


SUPPORTED_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg"}


@dataclass
class FileJobOptions:
    engine: str = "auto"             # auto | parakeet | whisper-turbo | whisper-large
    language: str = "auto"           # auto | ISO code
    task: str = "transcribe"         # transcribe | translate
    diarization_enabled: bool = True
    diarization_engine: str = "sherpa-onnx"  # sherpa-onnx | pyannote-community-1
    speaker_count: str | int = "auto"
    quality_preset: str = "balanced"  # fast | balanced | best
    custom_vocabulary: list[str] = field(default_factory=list)
    initial_prompt: str = ""
    timestamp_granularity: str = "sentence"  # none | sentence | word
    temperature: float = 0.0
    beam_size: int | None = None
    condition_on_previous_text: bool = False
    output_dir: str | None = None

    def resolved_engine(self) -> str:
        if self.engine != "auto":
            return self.engine
        return {
            "fast": "parakeet",
            "balanced": "whisper-turbo",
            "best": "whisper-large",
        }.get(self.quality_preset, "whisper-turbo")


@dataclass
class FileJob:
    job_id: str
    source_path: str
    options: FileJobOptions
    status: str = "queued"

    @staticmethod
    def new(source_path: str, options: FileJobOptions) -> "FileJob":
        return FileJob(job_id=uuid.uuid4().hex[:12], source_path=source_path, options=options)


def _ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _extract_audio(video_path: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", tmp.name],
        capture_output=True, check=True,
    )
    return tmp.name


class FileJobRunner:
    def __init__(
        self,
        *,
        transcriber_factory: Callable[[str], object],
        diarizer,
        ffprobe_duration: Callable[[str], float] = _ffprobe_duration,
        on_progress: Optional[Callable[..., None]] = None,
    ):
        self._transcriber_factory = transcriber_factory
        self._diarizer = diarizer
        self._ffprobe_duration = ffprobe_duration
        self._on_progress = on_progress or (lambda *a, **kw: None)
        self._cancelled: dict[str, bool] = {}
        self._lock = threading.Lock()

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancelled[job_id] = True

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return self._cancelled.get(job_id, False)

    def run(self, job: FileJob) -> dict:
        path = Path(job.source_path)
        is_video = path.suffix.lower() in SUPPORTED_VIDEO_EXT
        tmp_audio: str | None = None
        warnings: list[str] = []
        try:
            duration = self._ffprobe_duration(str(path))
            self._on_progress(job.job_id, stage="probed", percent=2, message=f"Loaded {path.name}")

            if is_video:
                self._on_progress(job.job_id, stage="extracting", percent=5, message="Extracting audio...")
                tmp_audio = _extract_audio(str(path))
                audio_for_asr = tmp_audio
            else:
                audio_for_asr = str(path)

            if self._is_cancelled(job.job_id):
                raise RuntimeError("cancelled")

            self._on_progress(job.job_id, stage="transcribing", percent=15,
                              message=f"Transcribing with {job.options.resolved_engine()}...")
            txr = self._transcriber_factory(job.options.resolved_engine())
            whisper_result = txr.transcribe_segments(
                audio_for_asr,
                language=job.options.language,
                task=job.options.task,
                initial_prompt=" ".join(job.options.custom_vocabulary)
                    if job.options.custom_vocabulary else (job.options.initial_prompt or None),
                word_timestamps=(job.options.timestamp_granularity == "word"),
                temperature=job.options.temperature,
                beam_size=job.options.beam_size,
                condition_on_previous_text=job.options.condition_on_previous_text,
            )

            if self._is_cancelled(job.job_id):
                raise RuntimeError("cancelled")

            speaker_turns = None
            if job.options.diarization_enabled:
                self._on_progress(job.job_id, stage="diarizing", percent=80, message="Identifying speakers...")
                try:
                    spk_count = job.options.speaker_count if isinstance(job.options.speaker_count, int) else "auto"
                    speaker_turns = self._diarizer.diarize(audio_for_asr, num_speakers=spk_count)
                except Exception as e:
                    warnings.append(f"Diarization failed: {e}")
                    speaker_turns = None

            payload = assemble(
                whisper_result=whisper_result,
                speaker_turns=speaker_turns,
                engine=job.options.resolved_engine(),
                audio_path=str(path),
                duration=duration,
            )
            if warnings:
                payload["warnings"] = warnings

            sidecar = path.with_suffix(".json")
            sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

            self._on_progress(job.job_id, stage="done", percent=100, message="Done")
            return payload
        finally:
            if tmp_audio:
                Path(tmp_audio).unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_file_job.py -v`
Expected: PASS for all 6 tests

- [ ] **Step 5: Commit**

```bash
git add file_job.py tests/test_file_job.py
git commit -m "feat(file_job): orchestrator running ASR + diarization pipeline"
```

---

### Task 6: WebSocket + REST API for FileJob

**Files:**
- Modify: `app.py` (remove old `transcribe_file` action; add new endpoints)
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py` (mirror whatever fixture pattern the file already uses; below assumes a `client_factory` helper — adapt to the file's actual conventions):

```python
def test_get_file_job_options_defaults_returns_dict(client_factory):
    """GET /api/file-job/options-defaults returns the default options."""
    client = client_factory()
    resp = client.get("/api/file-job/options-defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine"] == "auto"
    assert data["diarization_enabled"] is True
    assert data["quality_preset"] == "balanced"


def test_put_file_job_options_defaults_persists(client_factory, tmp_path, monkeypatch):
    """PUT /api/file-job/options-defaults persists to settings."""
    monkeypatch.setenv("DASHSCRIBE_CONFIG_DIR", str(tmp_path))
    client = client_factory()
    resp = client.put("/api/file-job/options-defaults", json={
        "engine": "parakeet",
        "diarization_enabled": False,
    })
    assert resp.status_code == 200
    resp2 = client.get("/api/file-job/options-defaults")
    data = resp2.json()
    assert data["engine"] == "parakeet"
    assert data["diarization_enabled"] is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_app.py::test_get_file_job_options_defaults_returns_dict -v`
Expected: FAIL with 404

- [ ] **Step 3: Add new endpoints to `app.py`**

In `app.py`, **delete** the `elif action == "transcribe_file":` block (lines 938–993) entirely.

Add to the top of `app.py` (with other imports):

```python
from file_job import FileJob, FileJobOptions, FileJobRunner
from diarizer import Diarizer
from exporter import write_export, FORMATS
```

After `create_app()` instantiates `txr` and `sm`, add:

```python
    # File-job state (Phase 1: in-memory only)
    file_jobs: dict[str, dict] = {}  # job_id -> {job, payload}
    diarizer = Diarizer()

    def _transcriber_for(engine: str):
        # Phase 1: only Whisper turbo is real; parakeet/whisper-large arrive in Task 12.
        return txr

    file_runner = FileJobRunner(
        transcriber_factory=_transcriber_for,
        diarizer=diarizer,
    )
```

Add REST endpoints (alongside `/api/browse-file`):

```python
    @app.get("/api/file-job/options-defaults")
    async def get_file_job_defaults():
        defaults = (settings.get("file_job_defaults", {}) if settings else {}) or {}
        merged = {**FileJobOptions().__dict__, **defaults}
        return JSONResponse(merged)

    @app.put("/api/file-job/options-defaults")
    async def put_file_job_defaults(payload: dict):
        if settings:
            settings.set("file_job_defaults", payload)
        return JSONResponse({"ok": True})

    @app.get("/api/file-job/{job_id}/payload")
    async def get_file_job_payload(job_id: str):
        entry = file_jobs.get(job_id)
        if not entry:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(entry["payload"])

    @app.post("/api/file-job/{job_id}/export")
    async def export_file_job(job_id: str, body: dict):
        entry = file_jobs.get(job_id)
        if not entry:
            return JSONResponse({"error": "not found"}, status_code=404)
        fmt = body.get("format", "txt")
        dest = body.get("dest_path")
        if not dest or fmt not in FORMATS:
            return JSONResponse({"error": "bad request"}, status_code=400)
        write_export(entry["payload"], fmt, dest)
        return JSONResponse({"path": dest})

    @app.get("/api/file-job/{job_id}/audio")
    async def get_file_job_audio(job_id: str):
        from fastapi.responses import FileResponse
        entry = file_jobs.get(job_id)
        if not entry:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(entry["payload"]["audio_path"])
```

In the WebSocket handler, replace the deleted `transcribe_file` block with the new actions:

```python
                    elif action == "start_file_job":
                        path = data.get("path", "")
                        opts = FileJobOptions(**(data.get("options") or {}))
                        job = FileJob.new(source_path=path, options=opts)
                        file_jobs[job.job_id] = {"job": job, "payload": None}
                        await ws.send_json({"type": "file_job_started", "job_id": job.job_id})

                        async def _emit(job_id, **kw):
                            try:
                                await ws.send_json({"type": "file_progress", "job_id": job_id, **kw})
                            except Exception:
                                pass
                        loop = asyncio.get_event_loop()
                        file_runner._on_progress = lambda jid, **kw: asyncio.run_coroutine_threadsafe(
                            _emit(jid, **kw), loop)
                        try:
                            payload = await asyncio.to_thread(file_runner.run, job)
                            file_jobs[job.job_id]["payload"] = payload
                            await ws.send_json({"type": "file_job_done", "job_id": job.job_id, "payload": payload})
                        except Exception as e:
                            await ws.send_json({"type": "file_job_error", "job_id": job.job_id, "message": str(e)})

                    elif action == "cancel_file_job":
                        jid = data.get("job_id", "")
                        file_runner.cancel(jid)
                        await ws.send_json({"type": "file_job_cancelled", "job_id": jid})

                    elif action == "update_speaker_label":
                        jid = data.get("job_id", "")
                        sid = data.get("speaker_id", "")
                        label = data.get("label", "")
                        entry = file_jobs.get(jid)
                        if entry and entry["payload"]:
                            for sp in entry["payload"]["speakers"]:
                                if sp["id"] == sid:
                                    sp["label"] = label
                            await ws.send_json({"type": "speaker_label_updated", "job_id": jid,
                                                "speaker_id": sid, "label": label})

                    elif action == "save_transcript_edits":
                        jid = data.get("job_id", "")
                        segments = data.get("segments", [])
                        entry = file_jobs.get(jid)
                        if entry and entry["payload"]:
                            entry["payload"]["segments"] = segments
                            from pathlib import Path as _P
                            import json as _json
                            sidecar = _P(entry["payload"]["audio_path"]).with_suffix(".json")
                            sidecar.write_text(_json.dumps(entry["payload"], indent=2, ensure_ascii=False),
                                               encoding="utf-8")
                            await ws.send_json({"type": "transcript_saved", "job_id": jid})
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: PASS (existing tests + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(api): file-job WebSocket + REST endpoints"
```

---

## Phase 2 — Frontend rebuild

### Task 7: New file-mode markup

**Files:**
- Modify: `static/index.html` (replace `#file-mode` page content)

- [ ] **Step 1: Replace the `#file-mode` block**

In `static/index.html`, find the `<!-- File page -->` block (around line 168) and replace the entire `<div id="file-mode" class="page">…</div>` element with the markup below. The markup is verbose because the engineer should not have to invent any classes — every element used by `file.js` and the CSS in Task 8 is named here:

```html
                <!-- File page -->
                <div id="file-mode" class="page">
                    <div class="page-header">
                        <h1 class="page-title">Transcribe File</h1>
                        <div class="page-header-actions">
                            <button id="file-sidebar-toggle" class="btn-ghost" title="Toggle options (Cmd+0)">⚙</button>
                        </div>
                    </div>
                    <div class="page-content file-layout">
                        <div class="file-main">
                            <!-- Empty state -->
                            <div id="file-empty" class="file-state file-empty">
                                <div id="file-dropzone" class="file-dropzone">
                                    <div class="file-dropzone-icon">⤓</div>
                                    <h3>Drop an audio or video file here</h3>
                                    <div class="file-dropzone-actions">
                                        <button id="file-browse-btn" class="btn-secondary">Browse…</button>
                                        <span class="file-dropzone-or">or paste a URL</span>
                                    </div>
                                    <input id="file-url" type="text" class="file-url-input"
                                           placeholder="https://www.youtube.com/watch?v=…" />
                                    <button id="file-sample-btn" class="link-button">Try a sample recording</button>
                                    <p class="file-dropzone-hint">
                                        Supports MP3, WAV, M4A, MP4, MOV, MKV, and more — transcribed
                                        locally on your Mac. Nothing leaves the device.
                                    </p>
                                </div>
                            </div>

                            <!-- Transcribing state -->
                            <div id="file-transcribing" class="file-state file-transcribing hidden">
                                <div class="file-progress-card">
                                    <div class="file-stage-list" id="file-stage-list"></div>
                                    <div class="progress-bar-inline">
                                        <div id="file-progress-fill" class="progress-fill-inline" style="width: 0%"></div>
                                    </div>
                                    <p id="file-progress-message" class="file-progress-msg"></p>
                                    <button id="file-cancel-btn" class="btn-secondary">Cancel</button>
                                </div>
                            </div>

                            <!-- Result state -->
                            <div id="file-result-view" class="file-state file-result-view hidden">
                                <div class="file-result-header">
                                    <span id="file-result-filename" class="file-result-filename"></span>
                                    <span id="file-result-meta" class="file-result-meta"></span>
                                    <button id="file-copy-all" class="btn-ghost" title="Copy all text">📋</button>
                                </div>
                                <div id="file-transcript" class="file-transcript"></div>
                                <div class="file-audio-player">
                                    <audio id="file-audio" controls preload="metadata"></audio>
                                </div>
                            </div>
                        </div>

                        <aside id="file-sidebar" class="file-sidebar">
                            <section class="file-sidebar-section">
                                <h4>Options</h4>
                                <label>Engine
                                    <select id="opt-engine">
                                        <option value="auto">Auto (preset)</option>
                                        <option value="parakeet">Parakeet (fast)</option>
                                        <option value="whisper-turbo">Whisper turbo</option>
                                        <option value="whisper-large">Whisper large (best)</option>
                                    </select>
                                </label>
                                <label>Quality preset
                                    <select id="opt-quality">
                                        <option value="fast">Fast</option>
                                        <option value="balanced" selected>Balanced</option>
                                        <option value="best">Best</option>
                                    </select>
                                </label>
                                <label>Language
                                    <select id="opt-language">
                                        <option value="auto" selected>Auto-detect</option>
                                        <option value="en">English</option>
                                        <option value="es">Spanish</option>
                                        <option value="fr">French</option>
                                        <option value="de">German</option>
                                        <option value="it">Italian</option>
                                        <option value="pt">Portuguese</option>
                                        <option value="hi">Hindi</option>
                                        <option value="ja">Japanese</option>
                                        <option value="zh">Chinese</option>
                                    </select>
                                </label>
                                <label>Task
                                    <select id="opt-task">
                                        <option value="transcribe" selected>Transcribe</option>
                                        <option value="translate">Translate to English</option>
                                    </select>
                                </label>
                                <label class="opt-checkbox">
                                    <input type="checkbox" id="opt-diarize" checked /> Identify speakers
                                </label>
                                <label>Speaker count
                                    <select id="opt-speakers">
                                        <option value="auto" selected>Auto</option>
                                        <option value="1">1</option><option value="2">2</option>
                                        <option value="3">3</option><option value="4">4</option>
                                        <option value="5">5</option><option value="6">6</option>
                                        <option value="7">7</option><option value="8">8</option>
                                    </select>
                                </label>
                                <details class="advanced-options">
                                    <summary>Advanced</summary>
                                    <label>Initial prompt
                                        <textarea id="opt-prompt" rows="2"></textarea>
                                    </label>
                                    <label>Timestamp granularity
                                        <select id="opt-ts">
                                            <option value="none">None</option>
                                            <option value="sentence" selected>Sentence</option>
                                            <option value="word">Word</option>
                                        </select>
                                    </label>
                                    <label>Temperature
                                        <input type="number" id="opt-temp" min="0" max="1" step="0.1" value="0" />
                                    </label>
                                    <label>Beam size
                                        <input type="number" id="opt-beam" min="1" max="10" step="1" />
                                    </label>
                                </details>
                                <button id="file-retranscribe" class="btn-primary hidden">Re-transcribe</button>
                            </section>
                            <section class="file-sidebar-section">
                                <h4>Export</h4>
                                <select id="export-format">
                                    <option value="txt">Plain text (.txt)</option>
                                    <option value="md">Markdown (.md)</option>
                                    <option value="srt">Subtitles (.srt)</option>
                                    <option value="vtt">WebVTT (.vtt)</option>
                                    <option value="docx">Word document (.docx)</option>
                                    <option value="json">JSON payload (.json)</option>
                                </select>
                                <button id="export-save" class="btn-secondary">Save as…</button>
                            </section>
                        </aside>
                    </div>
                </div>
```

- [ ] **Step 2: Add `<script src="file.js" defer></script>` to the document `<head>`**

After the existing `<script src="app.js" defer></script>` line, add:

```html
    <script src="file.js" defer></script>
```

- [ ] **Step 3: Verify markup loads**

Run the app: `python3 main.py` (with venv active). Navigate to the File tab. Expected: empty-state drop zone visible, sidebar visible, buttons inert (Task 9 wires them).

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat(file-ui): new three-state markup with sidebar and audio player"
```

---

### Task 8: File-mode CSS

**Files:**
- Modify: `static/style.css`

- [ ] **Step 1: Append file-mode styles**

Append to `static/style.css`:

```css
/* === File mode === */
.file-layout {
    display: grid;
    grid-template-columns: 1fr 280px;
    gap: 16px;
    height: 100%;
    min-height: 0;
}
.file-layout.sidebar-collapsed {
    grid-template-columns: 1fr 0;
}
.file-layout.sidebar-collapsed .file-sidebar { display: none; }

.file-main { min-width: 0; display: flex; flex-direction: column; }
.file-state { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.file-state.hidden { display: none; }

.file-dropzone {
    flex: 1;
    border: 2px dashed rgba(255,255,255,0.18);
    border-radius: 12px;
    padding: 48px 32px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 16px;
    transition: border-color 120ms ease, background-color 120ms ease;
}
.file-dropzone.drag-over { border-style: solid; border-color: #5B8DEF; background-color: rgba(91,141,239,0.06); }
.file-dropzone.drag-reject { border-style: solid; border-color: #D4604E; background-color: rgba(212,96,78,0.06); }
.file-dropzone-icon { font-size: 48px; opacity: 0.6; }
.file-dropzone-actions { display: flex; gap: 12px; align-items: center; }
.file-dropzone-or { color: rgba(255,255,255,0.6); font-size: 13px; }
.file-url-input {
    width: 360px; max-width: 80%;
    padding: 8px 12px; border-radius: 6px;
    border: 1px solid rgba(255,255,255,0.12);
    background: rgba(255,255,255,0.04); color: inherit;
}
.link-button {
    background: none; border: none; padding: 0;
    color: #5B8DEF; cursor: pointer; font-size: 13px;
}
.file-dropzone-hint { font-size: 12px; color: rgba(255,255,255,0.5); max-width: 480px; text-align: center; }

.file-progress-card {
    margin: auto; padding: 32px; max-width: 480px; width: 100%;
    border: 1px solid rgba(255,255,255,0.12); border-radius: 12px;
    display: flex; flex-direction: column; gap: 16px;
}
.file-stage-list { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.file-stage-list .stage { display: flex; gap: 8px; align-items: center; opacity: 0.5; }
.file-stage-list .stage.active { opacity: 1; font-weight: 500; }
.file-stage-list .stage.done { opacity: 0.85; }
.file-stage-list .stage::before { content: "○"; color: rgba(255,255,255,0.4); }
.file-stage-list .stage.active::before { content: "●"; color: #5B8DEF; }
.file-stage-list .stage.done::before { content: "✓"; color: #7DB35E; }

.file-result-view { gap: 12px; padding: 8px 0; }
.file-result-header {
    display: flex; align-items: center; gap: 12px;
    padding: 8px 12px; border-bottom: 1px solid rgba(255,255,255,0.08);
}
.file-result-filename { font-weight: 500; }
.file-result-meta { color: rgba(255,255,255,0.55); font-size: 13px; flex: 1; }

.file-transcript {
    flex: 1; overflow-y: auto; padding: 16px 12px;
    font-size: 14px; line-height: 1.6;
}
.transcript-turn { margin-bottom: 16px; }
.transcript-turn-header {
    display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px;
}
.speaker-chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 2px 8px; border-radius: 999px;
    font-size: 12px; font-weight: 500; cursor: pointer;
    background: rgba(255,255,255,0.06);
}
.speaker-chip::before {
    content: ""; width: 8px; height: 8px; border-radius: 50%;
    background-color: var(--speaker-color, #5B8DEF);
}
.speaker-chip-input {
    font: inherit; padding: 2px 6px; border-radius: 4px;
    border: 1px solid rgba(255,255,255,0.18);
    background: rgba(0,0,0,0.3); color: inherit; width: 120px;
}
.transcript-timestamp {
    font-size: 12px; color: rgba(255,255,255,0.5);
    cursor: pointer; font-variant-numeric: tabular-nums;
}
.transcript-timestamp:hover { color: #5B8DEF; }
.transcript-text { padding-left: 4px; }
.transcript-word { cursor: pointer; }
.transcript-word.low-confidence {
    text-decoration: underline dotted rgba(255,255,255,0.35);
}
.transcript-word.playing {
    background-color: rgba(91,141,239,0.25); border-radius: 3px;
}

.file-audio-player {
    border-top: 1px solid rgba(255,255,255,0.08);
    padding: 8px 12px;
}
.file-audio-player audio { width: 100%; }

.file-sidebar {
    border-left: 1px solid rgba(255,255,255,0.08);
    padding: 12px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 24px;
}
.file-sidebar-section h4 {
    margin: 0 0 8px 0; font-size: 11px; letter-spacing: 0.08em;
    color: rgba(255,255,255,0.5); text-transform: uppercase;
}
.file-sidebar label {
    display: flex; flex-direction: column; gap: 4px;
    margin-bottom: 10px; font-size: 12px; color: rgba(255,255,255,0.7);
}
.file-sidebar label.opt-checkbox { flex-direction: row; align-items: center; gap: 8px; }
.file-sidebar select, .file-sidebar input[type="text"], .file-sidebar input[type="number"], .file-sidebar textarea {
    padding: 6px 8px; border-radius: 6px;
    border: 1px solid rgba(255,255,255,0.12);
    background: rgba(255,255,255,0.04); color: inherit; font-size: 13px;
}
.advanced-options { margin-top: 8px; }
.advanced-options summary { cursor: pointer; font-size: 12px; color: rgba(255,255,255,0.6); }
```

- [ ] **Step 2: Verify visually**

Run `python3 main.py`, switch to File tab. Expected: drop zone styled with dashed border; sidebar laid out on the right with options.

- [ ] **Step 3: Commit**

```bash
git add static/style.css
git commit -m "feat(file-ui): styles for drop zone, transcript, sidebar"
```

---

### Task 9: file.js — empty state + drop + URL + browse + sidebar

**Files:**
- Create: `static/file.js`

**Important:** All DOM construction in this task uses `document.createElement` / `textContent` — never `innerHTML` with interpolated content. Stage list, transcript words, and rename inputs are built with explicit DOM nodes. This is a security requirement, not a style preference.

- [ ] **Step 1: Create the file (foundation only — rendering comes in Task 10)**

Create `static/file.js`:

```javascript
// static/file.js
// Owns the entire #file-mode page lifecycle. No innerHTML — all DOM via createElement.
(function () {
    let ws = null;
    let currentJob = null;            // {job_id}
    let currentPayload = null;        // unified transcript payload
    let optionsDefaults = null;
    let sidebarVisible = true;

    const el = (id) => document.getElementById(id);
    const dropzone = () => el("file-dropzone");
    const stateEmpty = () => el("file-empty");
    const stateWorking = () => el("file-transcribing");
    const stateResult = () => el("file-result-view");

    function setState(name) {
        for (const s of [stateEmpty(), stateWorking(), stateResult()]) {
            s.classList.add("hidden");
        }
        ({ empty: stateEmpty(), working: stateWorking(), result: stateResult() }[name]).classList.remove("hidden");
    }

    function clearChildren(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function getOptions() {
        return {
            engine: el("opt-engine").value,
            quality_preset: el("opt-quality").value,
            language: el("opt-language").value,
            task: el("opt-task").value,
            diarization_enabled: el("opt-diarize").checked,
            speaker_count: el("opt-speakers").value === "auto" ? "auto" : parseInt(el("opt-speakers").value, 10),
            initial_prompt: el("opt-prompt").value,
            timestamp_granularity: el("opt-ts").value,
            temperature: parseFloat(el("opt-temp").value || 0),
            beam_size: el("opt-beam").value ? parseInt(el("opt-beam").value, 10) : null,
        };
    }

    function applyOptions(o) {
        if (!o) return;
        if (o.engine) el("opt-engine").value = o.engine;
        if (o.quality_preset) el("opt-quality").value = o.quality_preset;
        if (o.language) el("opt-language").value = o.language;
        if (o.task) el("opt-task").value = o.task;
        el("opt-diarize").checked = !!o.diarization_enabled;
        el("opt-speakers").value = String(o.speaker_count ?? "auto");
        el("opt-prompt").value = o.initial_prompt || "";
        if (o.timestamp_granularity) el("opt-ts").value = o.timestamp_granularity;
        if (typeof o.temperature === "number") el("opt-temp").value = o.temperature;
        if (o.beam_size) el("opt-beam").value = o.beam_size;
    }

    async function loadDefaults() {
        try {
            const r = await fetch("/api/file-job/options-defaults");
            optionsDefaults = await r.json();
            applyOptions(optionsDefaults);
        } catch (_) { /* ignore */ }
    }

    function persistOptions() {
        const opts = getOptions();
        fetch("/api/file-job/options-defaults", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(opts),
        }).catch(() => {});
    }

    function startJobFromPath(path) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        setState("working");
        ws.send(JSON.stringify({ action: "start_file_job", path, options: getOptions() }));
    }

    async function startJobFromUrl(url) {
        const r = await fetch("/api/file-job/from-url", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });
        if (!r.ok) {
            window.alert("URL fetch failed");
            return;
        }
        const { path } = await r.json();
        startJobFromPath(path);
    }

    function bindEmptyState() {
        const dz = dropzone();
        ["dragenter", "dragover"].forEach(evt => {
            dz.addEventListener(evt, (e) => { e.preventDefault(); dz.classList.add("drag-over"); });
        });
        ["dragleave", "drop"].forEach(evt => {
            dz.addEventListener(evt, (e) => { e.preventDefault(); dz.classList.remove("drag-over"); });
        });
        dz.addEventListener("drop", (e) => {
            const file = e.dataTransfer.files[0];
            if (!file) return;
            if (file.path) {
                startJobFromPath(file.path);
            } else {
                window.alert("Please use the Browse button — drop only works in the desktop app.");
            }
        });

        el("file-browse-btn").addEventListener("click", async () => {
            const r = await fetch("/api/browse-file");
            const data = await r.json();
            if (data.path) startJobFromPath(data.path);
        });

        el("file-url").addEventListener("keydown", (e) => {
            if (e.key === "Enter" && e.target.value.trim()) {
                startJobFromUrl(e.target.value.trim());
            }
        });

        el("file-sample-btn").addEventListener("click", () => {
            startJobFromPath("__sample__");
        });
    }

    function bindSidebar() {
        ["opt-engine", "opt-quality", "opt-language", "opt-task", "opt-diarize",
         "opt-speakers", "opt-prompt", "opt-ts", "opt-temp", "opt-beam"].forEach(id => {
            el(id).addEventListener("change", persistOptions);
        });
        el("file-sidebar-toggle").addEventListener("click", () => {
            sidebarVisible = !sidebarVisible;
            document.querySelector(".file-layout").classList.toggle("sidebar-collapsed", !sidebarVisible);
        });
        document.addEventListener("keydown", (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "0") {
                e.preventDefault();
                el("file-sidebar-toggle").click();
            }
        });
    }

    function setWs(newWs) { ws = newWs; }

    function init() {
        ws = window.__appWebSocket || null;
        loadDefaults();
        bindEmptyState();
        bindSidebar();
        setState("empty");
    }

    document.addEventListener("DOMContentLoaded", init);

    // Exported here so app.js can route messages and so Task 10 can extend us.
    window.__fileMode = {
        startJobFromPath, setState, setWs,
        setJob: (j) => { currentJob = j; },
        clearChildren,
        // Result-rendering hooks added in Task 10:
        showResult: () => {},
        showWorking: () => {},
        updateProgress: () => {},
    };
})();
```

- [ ] **Step 2: Visual smoke test**

Run `python3 main.py`. Navigate to File tab. Expected: drop zone visible; clicking Browse opens native dialog; sidebar toggle works. Dropping or selecting won't fire transcription yet (that needs Task 11's WebSocket wiring).

- [ ] **Step 3: Commit**

```bash
git add static/file.js
git commit -m "feat(file-ui): file.js — empty state, drop, URL, browse, sidebar"
```

---

### Task 10: file.js — transcribing UI + result rendering + audio sync + export

**Files:**
- Modify: `static/file.js` (extend it)

**Important:** continue the no-`innerHTML` rule. All progress stages, transcript turns, speaker chips, words, and rename inputs are constructed with `document.createElement` and `textContent`.

- [ ] **Step 1: Add the rendering and audio-sync code**

Inside the IIFE in `static/file.js`, **before** the `function init()` definition, add:

```javascript
    function fmtClock(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return [h, m, s].map(n => String(n).padStart(2, "0")).join(":");
    }

    function speakerById(id) {
        return (currentPayload?.speakers || []).find(s => s.id === id);
    }

    function buildSpeakerChip(turn) {
        const sp = speakerById(turn.speaker_id) || { label: turn.speaker_id, color: "#888" };
        const chip = document.createElement("span");
        chip.className = "speaker-chip";
        chip.style.setProperty("--speaker-color", sp.color);
        chip.textContent = sp.label;
        chip.addEventListener("click", () => beginRenameSpeaker(turn.speaker_id, chip));
        return chip;
    }

    function buildWordSpan(text, start, end, isLow) {
        const span = document.createElement("span");
        span.className = "transcript-word" + (isLow ? " low-confidence" : "");
        span.dataset.start = String(start);
        span.dataset.end = String(end);
        span.textContent = text + " ";
        span.addEventListener("click", () => seekTo(start));
        return span;
    }

    function renderTranscript() {
        const root = el("file-transcript");
        clearChildren(root);
        if (!currentPayload) return;

        // Group consecutive same-speaker segments into turns
        const turns = [];
        for (const seg of currentPayload.segments) {
            const last = turns[turns.length - 1];
            if (last && last.speaker_id === seg.speaker_id) {
                last.segments.push(seg);
            } else {
                turns.push({ speaker_id: seg.speaker_id, start: seg.start, segments: [seg] });
            }
        }

        for (const turn of turns) {
            const turnEl = document.createElement("div");
            turnEl.className = "transcript-turn";
            turnEl.dataset.speakerId = turn.speaker_id;

            const header = document.createElement("div");
            header.className = "transcript-turn-header";
            header.appendChild(buildSpeakerChip(turn));

            const ts = document.createElement("span");
            ts.className = "transcript-timestamp";
            ts.textContent = fmtClock(turn.start);
            ts.addEventListener("click", () => seekTo(turn.start));
            header.appendChild(ts);
            turnEl.appendChild(header);

            const textEl = document.createElement("div");
            textEl.className = "transcript-text";
            textEl.contentEditable = "plaintext-only";
            textEl.addEventListener("blur", persistEdits);

            for (const seg of turn.segments) {
                if (seg.words && seg.words.length) {
                    for (const w of seg.words) {
                        textEl.appendChild(buildWordSpan(w.text, w.start, w.end, w.prob < 0.5));
                    }
                } else {
                    const isLow = (seg.no_speech_prob ?? 0) > 0.4;
                    textEl.appendChild(buildWordSpan(seg.text, seg.start, seg.end, isLow));
                }
            }
            turnEl.appendChild(textEl);
            root.appendChild(turnEl);
        }
    }

    function beginRenameSpeaker(speakerId, chipEl) {
        const sp = speakerById(speakerId);
        if (!sp) return;
        const input = document.createElement("input");
        input.className = "speaker-chip-input";
        input.value = sp.label;
        chipEl.replaceWith(input);
        input.focus();
        input.select();
        const finish = (commit) => {
            if (commit && input.value.trim() && input.value !== sp.label) {
                sp.label = input.value.trim();
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        action: "update_speaker_label",
                        job_id: currentJob.job_id,
                        speaker_id: speakerId,
                        label: sp.label,
                    }));
                }
            }
            renderTranscript();
        };
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") { e.preventDefault(); finish(true); }
            else if (e.key === "Escape") finish(false);
        });
        input.addEventListener("blur", () => finish(true));
    }

    function persistEdits() {
        if (!currentPayload || !ws || ws.readyState !== WebSocket.OPEN) return;
        // Replace each turn's first segment text with the edited concatenation; blank its tail segments.
        const turnEls = el("file-transcript").querySelectorAll(".transcript-turn");
        const segments = [];
        let segIdx = 0;
        for (const turnEl of turnEls) {
            const speakerId = turnEl.dataset.speakerId;
            const text = turnEl.querySelector(".transcript-text").innerText.trim();
            const turnSegments = [];
            while (segIdx < currentPayload.segments.length &&
                   currentPayload.segments[segIdx].speaker_id === speakerId) {
                turnSegments.push(currentPayload.segments[segIdx]);
                segIdx++;
            }
            if (turnSegments.length === 0) continue;
            turnSegments[0].text = text;
            for (let i = 1; i < turnSegments.length; i++) turnSegments[i].text = "";
            for (const s of turnSegments) segments.push(s);
        }
        currentPayload.segments = segments;
        ws.send(JSON.stringify({
            action: "save_transcript_edits",
            job_id: currentJob.job_id,
            segments,
        }));
    }

    function seekTo(seconds) {
        const a = el("file-audio");
        if (!a.src) return;
        a.currentTime = seconds;
        a.play().catch(() => {});
    }

    function bindAudioSync() {
        const audio = el("file-audio");
        let raf = 0;
        const tick = () => {
            const t = audio.currentTime;
            const words = el("file-transcript").querySelectorAll(".transcript-word");
            for (const w of words) {
                const start = parseFloat(w.dataset.start);
                const end = parseFloat(w.dataset.end);
                if (t >= start && t < end) w.classList.add("playing");
                else w.classList.remove("playing");
            }
            raf = requestAnimationFrame(tick);
        };
        audio.addEventListener("play", () => { cancelAnimationFrame(raf); tick(); });
        audio.addEventListener("pause", () => cancelAnimationFrame(raf));
    }

    function showResult(payload) {
        currentPayload = payload;
        const filename = (payload.audio_path || "").split("/").pop();
        el("file-result-filename").textContent = filename;
        el("file-result-meta").textContent =
            fmtClock(payload.duration_seconds) + " • " + payload.speakers.length + " speaker(s) • " + payload.engine;
        el("file-audio").src = "/api/file-job/" + currentJob.job_id + "/audio";
        renderTranscript();
        setState("result");
    }

    function showWorking() {
        clearChildren(el("file-stage-list"));
        el("file-progress-fill").style.width = "0%";
        el("file-progress-message").textContent = "Starting…";
        setState("working");
    }

    function updateProgress(msg) {
        const stages = ["probed", "extracting", "transcribing", "diarizing", "done"];
        const labels = {
            probed: "Loaded", extracting: "Extracting audio",
            transcribing: "Transcribing", diarizing: "Identifying speakers", done: "Done",
        };
        const list = el("file-stage-list");
        clearChildren(list);
        const cur = stages.indexOf(msg.stage);
        for (let i = 0; i < stages.length; i++) {
            const div = document.createElement("div");
            div.className = "stage" + (i < cur ? " done" : (i === cur ? " active" : ""));
            div.textContent = labels[stages[i]];
            list.appendChild(div);
        }
        el("file-progress-fill").style.width = (msg.percent || 0) + "%";
        el("file-progress-message").textContent = msg.message || "";
    }

    function bindCopyAndExport() {
        el("file-copy-all").addEventListener("click", async () => {
            if (!currentPayload) return;
            const text = currentPayload.segments.map(s => s.text).join(" ");
            await navigator.clipboard.writeText(text);
        });
        el("export-save").addEventListener("click", async () => {
            if (!currentJob) return;
            const fmt = el("export-format").value;
            const dest = pickSaveDest(fmt);
            if (!dest) return;
            const r = await fetch("/api/file-job/" + currentJob.job_id + "/export", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ format: fmt, dest_path: dest }),
            });
            if (!r.ok) window.alert("Export failed");
        });
        el("file-cancel-btn").addEventListener("click", () => {
            if (!currentJob || !ws || ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({ action: "cancel_file_job", job_id: currentJob.job_id }));
        });
    }

    function pickSaveDest(fmt) {
        // Phase 1: derive dest path from source path automatically (sibling file).
        if (!currentPayload?.audio_path) return null;
        const stem = currentPayload.audio_path.replace(/\.[^.]+$/, "");
        return stem + "." + fmt;
    }
```

Then **replace** the `init()` function with this version that also wires the new bindings:

```javascript
    function init() {
        ws = window.__appWebSocket || null;
        loadDefaults();
        bindEmptyState();
        bindSidebar();
        bindAudioSync();
        bindCopyAndExport();
        setState("empty");
    }
```

And **replace** the `window.__fileMode = { … }` line at the bottom with:

```javascript
    window.__fileMode = {
        startJobFromPath, setState, setWs,
        setJob: (j) => { currentJob = j; },
        showResult, showWorking, updateProgress,
    };
```

- [ ] **Step 2: Commit**

```bash
git add static/file.js
git commit -m "feat(file-ui): result rendering, karaoke sync, speaker rename, export"
```

---

### Task 11: Wire file.js to the main app WebSocket

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Expose the WebSocket and route file_* messages**

Find the `ws.onmessage` handler in `static/app.js` (around line 412). Add the following near the top of the handler body, right after the `msg` is received/parsed:

```javascript
            // Route file-mode messages to file.js
            if (msg.type && msg.type.indexOf("file_") === 0) {
                const fm = window.__fileMode;
                if (!fm) return;
                if (msg.type === "file_job_started") {
                    fm.setJob({ job_id: msg.job_id });
                    fm.showWorking();
                    return;
                }
                if (msg.type === "file_progress") {
                    fm.updateProgress(msg);
                    return;
                }
                if (msg.type === "file_job_done") {
                    fm.showResult(msg.payload);
                    return;
                }
                if (msg.type === "file_job_error") {
                    window.alert(msg.message || "Transcription failed");
                    fm.setState("empty");
                    return;
                }
            }
```

Inside `ws.onopen`, add:

```javascript
            window.__appWebSocket = ws;
            if (window.__fileMode && typeof window.__fileMode.setWs === "function") {
                window.__fileMode.setWs(ws);
            }
```

- [ ] **Step 2: Smoke test the round trip**

Run `python3 main.py`. On the File tab, click Browse and pick a short MP3. Expected:
1. UI flips to "transcribing" state with stage list animating through `probed → transcribing → diarizing → done`.
2. After completion, the result view shows a transcript with speaker chips and timestamps; the audio player loads.
3. Clicking a timestamp seeks the audio.
4. Clicking a speaker chip opens an inline rename input; Enter persists.

- [ ] **Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat(file-ui): wire file.js to the main WebSocket and route file_* messages"
```

---

## Phase 3 — Engines, URL ingestion, sample

### Task 12: Parakeet engine + engine registry

**Files:**
- Create: `parakeet_transcriber.py`
- Create: `tests/test_parakeet_transcriber.py`
- Create: `engine_registry.py`
- Create: `tests/test_engine_registry.py`
- Modify: `requirements.txt`
- Modify: `app.py` (replace `_transcriber_for` with the registry)

- [ ] **Step 1: Add dependency**

Append to `requirements.txt`:

```
parakeet-mlx>=0.5.1
```

Then: `pip install -r requirements.txt`

- [ ] **Step 2: Tests for ParakeetTranscriber**

Create `tests/test_parakeet_transcriber.py`:

```python
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
```

- [ ] **Step 3: Run, verify failure, implement**

Run: `pytest tests/test_parakeet_transcriber.py -v` → FAIL with `ModuleNotFoundError`.

Create `parakeet_transcriber.py`:

```python
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
```

- [ ] **Step 4: Run Parakeet tests, verify they pass**

Run: `pytest tests/test_parakeet_transcriber.py -v` → PASS.

- [ ] **Step 5: Engine registry — failing tests**

Create `tests/test_engine_registry.py`:

```python
# tests/test_engine_registry.py
from unittest.mock import MagicMock
import pytest
from engine_registry import EngineRegistry


def test_get_returns_whisper_turbo():
    whisper = MagicMock()
    reg = EngineRegistry(whisper_turbo=whisper)
    assert reg.get("whisper-turbo") is whisper


def test_get_returns_parakeet_lazy():
    parakeet_factory = MagicMock(return_value="PARAKEET")
    reg = EngineRegistry(whisper_turbo=MagicMock(), parakeet_factory=parakeet_factory)
    assert reg.get("parakeet") == "PARAKEET"
    assert reg.get("parakeet") == "PARAKEET"  # cached
    assert parakeet_factory.call_count == 1


def test_get_returns_whisper_large_lazy():
    large_factory = MagicMock(return_value="LARGE")
    reg = EngineRegistry(whisper_turbo=MagicMock(), whisper_large_factory=large_factory)
    assert reg.get("whisper-large") == "LARGE"
    assert large_factory.call_count == 1


def test_unknown_engine_raises():
    reg = EngineRegistry(whisper_turbo=MagicMock())
    with pytest.raises(ValueError):
        reg.get("xyz")
```

- [ ] **Step 6: Implement registry**

Create `engine_registry.py`:

```python
# engine_registry.py
"""Lazy engine selection so we don't load every model at startup."""
from typing import Callable, Optional


class EngineRegistry:
    def __init__(
        self,
        *,
        whisper_turbo,
        parakeet_factory: Optional[Callable[[], object]] = None,
        whisper_large_factory: Optional[Callable[[], object]] = None,
    ):
        self._whisper_turbo = whisper_turbo
        self._parakeet_factory = parakeet_factory
        self._whisper_large_factory = whisper_large_factory
        self._parakeet = None
        self._whisper_large = None

    def get(self, engine: str):
        if engine == "whisper-turbo":
            return self._whisper_turbo
        if engine == "parakeet":
            if self._parakeet is None:
                if self._parakeet_factory is None:
                    raise ValueError("Parakeet engine not configured")
                self._parakeet = self._parakeet_factory()
            return self._parakeet
        if engine == "whisper-large":
            if self._whisper_large is None:
                if self._whisper_large_factory is None:
                    raise ValueError("Whisper-large engine not configured")
                self._whisper_large = self._whisper_large_factory()
            return self._whisper_large
        raise ValueError(f"Unknown engine: {engine}")
```

- [ ] **Step 7: Run tests, verify they pass**

Run: `pytest tests/test_engine_registry.py -v` → PASS.

- [ ] **Step 8: Wire registry into `app.py`**

In `app.py`, replace the file-job init block (the lines that create `diarizer` and `file_runner`) with:

```python
    diarizer = Diarizer()
    from engine_registry import EngineRegistry
    from parakeet_transcriber import ParakeetTranscriber
    from transcriber import WhisperTranscriber as _W
    engines = EngineRegistry(
        whisper_turbo=txr,
        parakeet_factory=lambda: ParakeetTranscriber(),
        whisper_large_factory=lambda: _W(model_repo="mlx-community/whisper-large-v3"),
    )

    def _transcriber_for(engine: str):
        return engines.get(engine)

    file_runner = FileJobRunner(
        transcriber_factory=_transcriber_for,
        diarizer=diarizer,
    )
```

- [ ] **Step 9: Commit**

```bash
git add parakeet_transcriber.py tests/test_parakeet_transcriber.py engine_registry.py tests/test_engine_registry.py app.py requirements.txt
git commit -m "feat(engines): Parakeet TDT v3 + lazy engine registry"
```

---

### Task 13: yt-dlp URL ingestion

**Files:**
- Modify: `app.py` (add `/api/file-job/from-url`)
- Modify: `requirements.txt`
- Create: `tests/test_url_ingest.py`

- [ ] **Step 1: Add dependency**

Append to `requirements.txt`:

```
yt-dlp>=2025.10.0
```

Then: `pip install -r requirements.txt`

- [ ] **Step 2: Failing test**

Create `tests/test_url_ingest.py`:

```python
# tests/test_url_ingest.py
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import create_app


def test_from_url_invokes_yt_dlp_and_returns_path(tmp_path):
    fake_path = tmp_path / "downloaded.m4a"
    fake_path.write_bytes(b"")
    with patch("app._download_url") as mock_dl:
        mock_dl.return_value = str(fake_path)
        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/file-job/from-url", json={"url": "https://example.com/x"})
        assert resp.status_code == 200
        assert resp.json()["path"] == str(fake_path)


def test_from_url_rejects_empty_url():
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/file-job/from-url", json={"url": ""})
    assert resp.status_code == 400
```

- [ ] **Step 3: Run, verify failure**

Run: `pytest tests/test_url_ingest.py -v` → FAIL.

- [ ] **Step 4: Implement endpoint**

In `app.py`, near `_extract_audio`, add:

```python
def _download_url(url: str) -> str:
    """Download bestaudio from a URL via yt-dlp; return local file path."""
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="dashscribe_url_")
    out_template = f"{tmp_dir}/%(id)s.%(ext)s"
    from yt_dlp import YoutubeDL
    with YoutubeDL({"format": "bestaudio/best", "outtmpl": out_template, "quiet": True}) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)
```

In `create_app()`, alongside the other file-job endpoints, add:

```python
    @app.post("/api/file-job/from-url")
    async def from_url(payload: dict):
        url = (payload or {}).get("url", "").strip()
        if not url:
            return JSONResponse({"error": "missing url"}, status_code=400)
        try:
            path = await asyncio.to_thread(_download_url, url)
            return JSONResponse({"path": path})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/test_url_ingest.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add app.py requirements.txt tests/test_url_ingest.py
git commit -m "feat(file): URL ingestion via yt-dlp"
```

---

### Task 14: Sample audio + sample-button backend route

**Files:**
- Create: `static/samples/sample-en.m4a` (placed manually — see Step 1)
- Modify: `app.py`

- [ ] **Step 1: Place a sample file**

```bash
mkdir -p static/samples
```

Record a ~10–15s spoken English clip yourself (use the existing Dictation feature) and save it at `static/samples/sample-en.m4a`. If no usable file is available, copy any short MP3/M4A from `tests/fixtures/` or anywhere on disk into that location and rename.

- [ ] **Step 2: Backend route to resolve `__sample__`**

In `app.py`, in the WebSocket `start_file_job` handler, **before** constructing the `FileJob.new(...)`, add:

```python
                        if path == "__sample__":
                            from pathlib import Path as _P
                            path = str(_P(STATIC_DIR) / "samples" / "sample-en.m4a")
```

- [ ] **Step 3: Smoke test**

Run `python3 main.py`, click "Try a sample recording" on the File tab. Expected: transcription completes on the bundled clip; result view appears.

- [ ] **Step 4: Commit**

```bash
git add static/samples/sample-en.m4a app.py
git commit -m "feat(file): bundled sample recording for first-launch demo"
```

---

## Phase 4 — Premium diarization (opt-in)

### Task 15: Pyannote community-1 opt-in installer

**Files:**
- Create: `diarizer_pyannote.py`
- Create: `tests/test_diarizer_pyannote.py`
- Modify: `app.py` (add install endpoint + per-job diarizer routing)
- Modify: `static/index.html` (add toggle in Settings)
- Modify: `static/app.js` (toggle handler)
- Modify: `static/file.js` (include `diarization_engine` in options)

- [ ] **Step 1: Failing tests**

Create `tests/test_diarizer_pyannote.py`:

```python
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
```

- [ ] **Step 2: Implement `diarizer_pyannote.py`**

Create `diarizer_pyannote.py`:

```python
# diarizer_pyannote.py
"""Optional pyannote community-1 diarization (heavyweight: PyTorch + ~700 MB weights)."""
import importlib.util
import threading
from pathlib import Path

from diarizer import SpeakerSegment

CACHE_DIR = Path("~/.cache/dashscribe/pyannote").expanduser()
WEIGHTS_URL = (
    # NOTE: replace with the DashScribe-hosted CC-BY-4.0 mirror URL once published.
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
```

- [ ] **Step 3: Run tests, verify they pass**

Run: `pytest tests/test_diarizer_pyannote.py -v` → PASS.

- [ ] **Step 4: Add install endpoint and per-job diarizer routing in `app.py`**

In `app.py`:

```python
    @app.get("/api/diarizer/enhanced/status")
    async def enhanced_status():
        from diarizer_pyannote import is_pyannote_installed, CACHE_DIR
        installed = is_pyannote_installed()
        weights_present = (CACHE_DIR / "speaker-diarization-community-1").exists()
        return JSONResponse({"installed": installed, "weights_present": weights_present})

    @app.post("/api/diarizer/enhanced/install")
    async def enhanced_install():
        import sys, subprocess
        from diarizer_pyannote import CACHE_DIR, WEIGHTS_URL
        target = str(Path("~/.dashscribe/pyannote_pkgs").expanduser())
        try:
            subprocess.run([
                sys.executable, "-m", "pip", "install", "--target", target,
                "pyannote.audio", "torch>=2.6,<3",
                "--index-url", "https://download.pytorch.org/whl/cpu",
            ], check=True, capture_output=True)
            sys.path.insert(0, target)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            import urllib.request, tarfile
            tarball = CACHE_DIR / "weights.tar.bz2"
            urllib.request.urlretrieve(WEIGHTS_URL, tarball)
            with tarfile.open(tarball, "r:bz2") as tf:
                tf.extractall(CACHE_DIR)
            tarball.unlink(missing_ok=True)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
```

Replace the `Diarizer()` line and `FileJobRunner(...)` block with:

```python
    sherpa_diarizer = Diarizer()
    pyannote_diarizer_holder = {"instance": None}

    def _diarizer_for(engine_name: str):
        if engine_name == "pyannote-community-1":
            inst = pyannote_diarizer_holder["instance"]
            if inst is None:
                from diarizer_pyannote import PyannoteDiarizer
                inst = PyannoteDiarizer()
                pyannote_diarizer_holder["instance"] = inst
            return inst
        return sherpa_diarizer

    file_runner = FileJobRunner(
        transcriber_factory=_transcriber_for,
        diarizer=sherpa_diarizer,  # default; overridden per-job below
    )
```

In the WS `start_file_job` handler, **before** `await asyncio.to_thread(file_runner.run, job)`, add:

```python
                        # Swap diarizer based on per-job preference (sherpa-onnx default vs pyannote)
                        file_runner._diarizer = _diarizer_for(opts.diarization_engine)
```

- [ ] **Step 5: Settings UI toggle**

In `static/index.html`, find the Settings modal General section and add:

```html
                                <div class="setting-row">
                                    <div class="setting-label">
                                        <span>Enhanced speaker recognition</span>
                                        <p class="setting-desc">Higher-accuracy diarization. Downloads ~700 MB on first use.</p>
                                    </div>
                                    <div class="setting-control">
                                        <label class="toggle">
                                            <input type="checkbox" id="enhanced-diarize-toggle" />
                                            <span class="toggle-slider"></span>
                                        </label>
                                    </div>
                                </div>
```

In `static/app.js` (alongside other settings handlers):

```javascript
            const enhancedToggle = document.getElementById("enhanced-diarize-toggle");
            if (enhancedToggle) {
                fetch("/api/diarizer/enhanced/status").then(r => r.json()).then(s => {
                    enhancedToggle.checked = s.installed && s.weights_present;
                });
                enhancedToggle.addEventListener("change", async () => {
                    if (!enhancedToggle.checked) return;
                    if (!window.confirm("Download ~700 MB of enhanced speaker models?")) {
                        enhancedToggle.checked = false;
                        return;
                    }
                    const r = await fetch("/api/diarizer/enhanced/install", { method: "POST" });
                    if (!r.ok) {
                        window.alert("Install failed");
                        enhancedToggle.checked = false;
                    }
                });
            }
```

In `static/file.js` `getOptions()`, add this property to the returned object:

```javascript
            diarization_engine: document.getElementById("enhanced-diarize-toggle")?.checked
                ? "pyannote-community-1" : "sherpa-onnx",
```

- [ ] **Step 6: Commit**

```bash
git add diarizer_pyannote.py tests/test_diarizer_pyannote.py app.py static/index.html static/app.js static/file.js
git commit -m "feat(diarizer): opt-in pyannote community-1 enhanced diarization"
```

---

## Phase 5 — Wrap-up

### Task 16: py2app build adjustments

**Files:**
- Modify: `setup.py` (add new packages to `packages` list)
- Modify: `build_app.sh` if any new namespace/native modules need post-build copies

- [ ] **Step 1: Add new package entries**

In `setup.py`, locate the `packages` list and add:

```python
        "sherpa_onnx",
        "parakeet_mlx",
        "yt_dlp",
        "docx",
```

- [ ] **Step 2: Build the app**

Run: `./build_app.sh`
Expected: `dist/DashScribe.app` builds without error. If sherpa-onnx native binary fails to load at runtime, follow the same pattern used for `_sounddevice_data` / `onnxruntime` — copy native libs out of the zipped Python archive in `build_app.sh`.

- [ ] **Step 3: Smoke test the bundled app**

Open `dist/DashScribe.app`. On the File tab, drop a small audio file. Verify it transcribes and shows the result identically to the dev-mode behavior.

- [ ] **Step 4: Commit**

```bash
git add setup.py build_app.sh
git commit -m "build(py2app): include sherpa-onnx, parakeet-mlx, yt-dlp, python-docx"
```

---

### Task 17: Manual UAT pass

**Files:** none — runtime verification only.

- [ ] **Step 1: Run the full UAT checklist from the spec (§9)**

Run `python3 main.py` (with venv active) and walk through every item. For each item, mark it pass/fail. Re-open as bug fixes if any fail.

  1. Drop a 2-min MP3 → result appears in <30s with diarized speakers.
  2. Drop an MP4 → audio is extracted, transcript is produced, no leftover temp file.
  3. Paste a YouTube URL → audio downloads, transcribes.
  4. Click "Try a sample" → result view appears with the bundled clip.
  5. Drop 3 files at once → queue chip appears, all complete sequentially. *(Note: batch queue is the deferred Task 18; this item is expected to fail until Task 18 ships.)*
  6. Rename "Speaker 1" to "Alex" → all turns update; reload page → name persists (sidecar JSON written by `save_transcript_edits`).
  7. Click a word → audio seeks; press Space → plays from there with karaoke highlight.
  8. Edit a word in the transcript → blur → reload → edit persists.
  9. Export to each of the 6 formats → each opens in its native app and looks right.
  10. Toggle "Enhanced diarization" → ~700 MB download → re-run a job → speaker labels improve qualitatively.
  11. Cancel mid-job → UI returns to empty state cleanly, no orphan temp files in `/tmp`.
  12. Drop unsupported file (`.zip`) → red border + tooltip, no crash.

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: All existing 1088 tests + the ~30 new tests added in this plan pass.

- [ ] **Step 3: Commit any UAT-driven fixes**

```bash
git add -A
git commit -m "fix(file-ui): UAT-driven fixes"
```

---

### Task 18: Deferred follow-ups

The following spec items are intentionally deferred. Each becomes its own small plan if pursued:

**A. Batch queue (spec §4.4)** — drop multiple files at once.
- Maintain `pendingJobs` array in `file.js`.
- Show a chip in the result-state header with count.
- On completion of one job, auto-start the next.
- Click chip → list popover with status per file.

**B. Keyboard shortcuts beyond `Cmd+0` (spec §4.5)**:
- `Cmd+E` — focus the Export Save-as button.
- `Cmd+C` with no selection — copy full transcript.
- `1`–`9` — reassign currently-selected turn(s) to speaker N.
- `Space`, `←`, `→`, audio-player shortcuts — these mostly work via the native `<audio>` element when it has focus; only audit / add custom handlers if Step-1 UAT shows them missing.

**C. Cmd+F search-in-transcript (spec §4.5)** — explicitly punted to Phase 2 in the spec.

**D. Range request support on `GET /api/file-job/:id/audio`** (spec §10) — only needed for files >2 GB.

---

## Final verification

- [ ] All 1088 existing + ~30 new tests pass: `pytest -v`
- [ ] App builds and runs from `dist/DashScribe.app`
- [ ] Spec UAT items 1–4 and 6–12 pass (item 5 is the deferred batch queue from Task 18)
- [ ] No leftover `_clean_hallucination` regex hits on Parakeet output (Parakeet doesn't produce that failure mode)
- [ ] No imports of `pyannote.audio` or `torch` happen at app startup (lazy-loaded only when enhanced diarization is enabled)
