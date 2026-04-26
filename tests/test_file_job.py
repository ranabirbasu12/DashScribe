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
