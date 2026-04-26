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
