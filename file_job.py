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
