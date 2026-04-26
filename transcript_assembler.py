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
