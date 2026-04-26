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
