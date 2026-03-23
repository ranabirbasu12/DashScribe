# ClassNote v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add live lecture transcription with ghost-text preview, rolling correction, audio-mapped review, and label organization to DashScribe.

**Architecture:** Three new backend modules (lecture_recorder.py, lecture_store.py, classnote.py) mirror existing recorder/history/pipeline patterns. New WebSocket endpoint /ws/classnote. Frontend adds ClassNote sidebar tab with recording, review, and lecture list views. Dictation preemption via callback injection into GlobalHotkey.

**Tech Stack:** Python 3.12, FastAPI WebSocket, SQLite WAL, sounddevice, wave module, vanilla HTML/CSS/JS, Web Audio API

**Design doc:** `docs/plans/2026-03-11-classnote-design.md`

---

## Task 1: Refactor VADSegmenter — Configurable Thresholds

**Files:**
- Modify: `vad.py:117-136` (VADSegmenter class constants → constructor params)
- Modify: `pipeline.py:87` (pass defaults explicitly)
- Modify: `tests/test_pipeline.py` (verify existing tests still pass)

**Step 1: Update VADSegmenter constructor to accept all thresholds**

In `vad.py`, change the class constants to constructor parameters with defaults matching current values:

```python
# vad.py — VADSegmenter.__init__
def __init__(
    self,
    vad: SileroVAD,
    sample_rate: int = 16000,
    max_segment_duration_s: float = 20.0,
    silence_threshold_ms: int = 600,
    min_segment_duration_s: float = 1.0,
):
    self._vad = vad
    self._sample_rate = sample_rate
    self._max_segment_samples = int(max_segment_duration_s * sample_rate)
    self._silence_threshold_samples = int(silence_threshold_ms * sample_rate / 1000)
    self._min_segment_samples = int(min_segment_duration_s * sample_rate)
    # ... rest unchanged
```

Remove the class constants `SILENCE_THRESHOLD_MS`, `MIN_SEGMENT_DURATION_S`, `MAX_SEGMENT_DURATION_S`. Update all references in `feed()` to use the instance variables instead.

**Step 2: Update pipeline.py to pass defaults explicitly**

In `pipeline.py:start()`, where VADSegmenter is created, pass defaults explicitly so behavior is unchanged:

```python
self._segmenter = VADSegmenter(
    self._vad, self._sample_rate,
    max_segment_duration_s=20.0,
    silence_threshold_ms=600,
    min_segment_duration_s=1.0,
)
```

**Step 3: Run existing tests**

Run: `python3 -m pytest tests/test_pipeline.py tests/test_vad.py -v`
Expected: All existing tests pass (behavior unchanged, just parameterized).

**Step 4: Commit**

```
refactor: make VADSegmenter thresholds configurable via constructor params
```

---

## Task 2: LectureStore — Database Layer

**Files:**
- Create: `lecture_store.py`
- Create: `tests/test_lecture_store.py`

**Step 1: Write failing tests for LectureStore**

```python
# tests/test_lecture_store.py
import os
import sqlite3
import tempfile
import pytest
from lecture_store import LectureStore


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = LectureStore(path)
    yield s
    os.unlink(path)


# --- Lecture CRUD ---

def test_create_lecture(store):
    lid = store.create_lecture("Physics 201", "/tmp/test.wav")
    assert lid == 1
    lecture = store.get_lecture(lid)
    assert lecture["title"] == "Physics 201"
    assert lecture["status"] == "recording"
    assert lecture["audio_path"] == "/tmp/test.wav"


def test_update_lecture_status(store):
    lid = store.create_lecture("Math 101", "/tmp/m.wav")
    store.update_lecture(lid, status="stopped", duration_seconds=3600.0, word_count=5000)
    lecture = store.get_lecture(lid)
    assert lecture["status"] == "stopped"
    assert lecture["duration_seconds"] == 3600.0
    assert lecture["word_count"] == 5000


def test_delete_lecture_cascades_segments(store):
    lid = store.create_lecture("Bio", "/tmp/b.wav")
    store.add_segment(lid, 0, "hello", 0, 1000)
    store.delete_lecture(lid)
    assert store.get_lecture(lid) is None
    assert store.get_segments(lid) == []


def test_list_lectures(store):
    store.create_lecture("A", "/tmp/a.wav")
    store.create_lecture("B", "/tmp/b.wav")
    lectures = store.list_lectures()
    assert len(lectures) == 2
    # Most recent first
    assert lectures[0]["title"] == "B"


# --- Segments ---

def test_add_and_get_segments(store):
    lid = store.create_lecture("Test", "/tmp/t.wav")
    store.add_segment(lid, 0, "hello world", 0, 2000)
    store.add_segment(lid, 1, "second segment", 2000, 4000)
    segs = store.get_segments(lid)
    assert len(segs) == 2
    assert segs[0]["text"] == "hello world"
    assert segs[1]["start_ms"] == 2000


def test_update_segment_correction(store):
    lid = store.create_lecture("Test", "/tmp/t.wav")
    store.add_segment(lid, 0, "hello", 0, 1000)
    store.add_segment(lid, 1, "world", 1000, 2000)
    store.add_segment(lid, 2, "test", 2000, 3000)
    store.apply_correction(lid, start_index=0, end_index=2,
                           corrected_text="hello world test",
                           correction_group_id=1)
    segs = store.get_segments(lid)
    for s in segs:
        assert s["is_corrected"] == 1
        assert s["correction_group_id"] == 1


def test_bulk_upsert_segments(store):
    """Periodic flush writes multiple segments at once."""
    lid = store.create_lecture("Test", "/tmp/t.wav")
    segments = [
        {"index": 0, "text": "seg one", "start_ms": 0, "end_ms": 1000},
        {"index": 1, "text": "seg two", "start_ms": 1000, "end_ms": 2000},
    ]
    store.flush_segments(lid, segments)
    assert len(store.get_segments(lid)) == 2
    # Flush again with updated text (upsert)
    segments[0]["text"] = "seg one updated"
    store.flush_segments(lid, segments)
    segs = store.get_segments(lid)
    assert segs[0]["text"] == "seg one updated"
    assert len(segs) == 2


# --- Labels ---

def test_create_and_assign_label(store):
    lid = store.create_lecture("Test", "/tmp/t.wav")
    label_id = store.create_label("CS101", "#8b5cf6")
    store.assign_label(lid, label_id)
    labels = store.get_lecture_labels(lid)
    assert len(labels) == 1
    assert labels[0]["name"] == "CS101"


def test_remove_label(store):
    lid = store.create_lecture("Test", "/tmp/t.wav")
    label_id = store.create_label("CS101", "#8b5cf6")
    store.assign_label(lid, label_id)
    store.remove_label(lid, label_id)
    assert store.get_lecture_labels(lid) == []


def test_list_all_labels(store):
    store.create_label("CS101", "#ff0000")
    store.create_label("MATH201", "#00ff00")
    labels = store.list_labels()
    assert len(labels) == 2


# --- Crash Recovery ---

def test_detect_crashed_lectures(store):
    """Lectures with status='recording' and old updated_at are crashed."""
    lid = store.create_lecture("Crashed", "/tmp/c.wav")
    # Manually set updated_at to 10 minutes ago
    conn = sqlite3.connect(store._db_path)
    conn.execute(
        "UPDATE lectures SET updated_at = datetime('now', '-10 minutes') WHERE id = ?",
        (lid,),
    )
    conn.commit()
    conn.close()
    crashed = store.detect_crashed_lectures(stale_minutes=5)
    assert len(crashed) == 1
    assert crashed[0]["id"] == lid


def test_mark_recovered(store):
    lid = store.create_lecture("Crashed", "/tmp/c.wav")
    store.mark_recovered(lid)
    lecture = store.get_lecture(lid)
    assert lecture["status"] == "recovered"


# --- Search ---

def test_search_lectures(store):
    lid = store.create_lecture("Physics 201", "/tmp/p.wav")
    store.add_segment(lid, 0, "quantum mechanics lecture", 0, 5000)
    store.update_lecture(lid, status="stopped")
    results = store.search_lectures("quantum")
    assert len(results) == 1


def test_search_by_title(store):
    store.create_lecture("Physics 201", "/tmp/p.wav")
    store.create_lecture("Math 101", "/tmp/m.wav")
    results = store.search_lectures("Physics")
    assert len(results) == 1
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lecture_store.py -v`
Expected: ImportError — `lecture_store` module not found

**Step 3: Implement LectureStore**

```python
# lecture_store.py
"""SQLite storage for ClassNote lectures, segments, and labels."""
import os
import re
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    duration_seconds REAL,
    word_count INTEGER DEFAULT 0,
    audio_path TEXT,
    status TEXT NOT NULL DEFAULT 'recording'
);

CREATE TABLE IF NOT EXISTS lecture_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    is_corrected BOOLEAN DEFAULT 0,
    correction_group_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_segments_lecture ON lecture_segments(lecture_id);

CREATE TABLE IF NOT EXISTS lecture_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lecture_label_map (
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES lecture_labels(id),
    PRIMARY KEY (lecture_id, label_id)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


class LectureStore:
    """SQLite CRUD for lectures, segments, and labels."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.path.expanduser("~/.dashscribe/history.db")
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    # --- Lectures ---

    def create_lecture(self, title: str, audio_path: str) -> int:
        now = _now_iso()
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO lectures (title, created_at, updated_at, audio_path, status)"
            " VALUES (?, ?, ?, ?, 'recording')",
            (title, now, now, audio_path),
        )
        lid = cur.lastrowid
        conn.commit()
        conn.close()
        return lid

    def get_lecture(self, lecture_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM lectures WHERE id = ?", (lecture_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_lecture(self, lecture_id: int, **kwargs):
        allowed = {"title", "status", "duration_seconds", "word_count", "audio_path"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        fields["updated_at"] = _now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [lecture_id]
        conn = self._connect()
        conn.execute(f"UPDATE lectures SET {sets} WHERE id = ?", vals)
        conn.commit()
        conn.close()

    def delete_lecture(self, lecture_id: int):
        conn = self._connect()
        conn.execute("DELETE FROM lectures WHERE id = ?", (lecture_id,))
        conn.commit()
        conn.close()

    def list_lectures(self, limit: int = 100, offset: int = 0) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM lectures ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def touch_lecture(self, lecture_id: int):
        """Update updated_at for crash detection."""
        conn = self._connect()
        conn.execute(
            "UPDATE lectures SET updated_at = ? WHERE id = ?",
            (_now_iso(), lecture_id),
        )
        conn.commit()
        conn.close()

    # --- Segments ---

    def add_segment(
        self, lecture_id: int, index: int, text: str, start_ms: int, end_ms: int
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO lecture_segments (lecture_id, segment_index, text, start_ms, end_ms)"
            " VALUES (?, ?, ?, ?, ?)",
            (lecture_id, index, text, start_ms, end_ms),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def get_segments(self, lecture_id: int) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM lecture_segments WHERE lecture_id = ? ORDER BY segment_index",
            (lecture_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def flush_segments(self, lecture_id: int, segments: list[dict]):
        """Upsert multiple segments (periodic flush during recording)."""
        conn = self._connect()
        for seg in segments:
            conn.execute(
                """INSERT INTO lecture_segments (lecture_id, segment_index, text, start_ms, end_ms)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (lecture_id, segment_index)
                   DO UPDATE SET text = excluded.text, start_ms = excluded.start_ms, end_ms = excluded.end_ms""",
                (lecture_id, seg["index"], seg["text"], seg["start_ms"], seg["end_ms"]),
            )
        conn.execute(
            "UPDATE lectures SET updated_at = ? WHERE id = ?",
            (_now_iso(), lecture_id),
        )
        conn.commit()
        conn.close()

    def apply_correction(
        self,
        lecture_id: int,
        start_index: int,
        end_index: int,
        corrected_text: str,
        correction_group_id: int,
    ):
        """Mark segments in range as corrected with group ID."""
        conn = self._connect()
        conn.execute(
            """UPDATE lecture_segments
               SET is_corrected = 1, correction_group_id = ?
               WHERE lecture_id = ? AND segment_index >= ? AND segment_index <= ?""",
            (correction_group_id, lecture_id, start_index, end_index),
        )
        conn.commit()
        conn.close()

    # --- Labels ---

    def create_label(self, name: str, color: str) -> int:
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO lecture_labels (name, color) VALUES (?, ?)", (name, color)
        )
        lid = cur.lastrowid
        conn.commit()
        conn.close()
        return lid

    def list_labels(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM lecture_labels ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def assign_label(self, lecture_id: int, label_id: int):
        conn = self._connect()
        conn.execute(
            "INSERT OR IGNORE INTO lecture_label_map (lecture_id, label_id) VALUES (?, ?)",
            (lecture_id, label_id),
        )
        conn.commit()
        conn.close()

    def remove_label(self, lecture_id: int, label_id: int):
        conn = self._connect()
        conn.execute(
            "DELETE FROM lecture_label_map WHERE lecture_id = ? AND label_id = ?",
            (lecture_id, label_id),
        )
        conn.commit()
        conn.close()

    def get_lecture_labels(self, lecture_id: int) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT ll.* FROM lecture_labels ll
               JOIN lecture_label_map lm ON ll.id = lm.label_id
               WHERE lm.lecture_id = ?
               ORDER BY ll.name""",
            (lecture_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # --- Crash Recovery ---

    def detect_crashed_lectures(self, stale_minutes: int = 5) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM lectures
               WHERE status = 'recording'
               AND updated_at < datetime('now', ? || ' minutes')""",
            (f"-{stale_minutes}",),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_recovered(self, lecture_id: int):
        self.update_lecture(lecture_id, status="recovered")

    # --- Search ---

    def search_lectures(self, query: str, limit: int = 50) -> list[dict]:
        pattern = f"%{query}%"
        conn = self._connect()
        rows = conn.execute(
            """SELECT DISTINCT l.* FROM lectures l
               LEFT JOIN lecture_segments ls ON l.id = ls.lecture_id
               WHERE l.title LIKE ? OR ls.text LIKE ?
               ORDER BY l.created_at DESC LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
```

**Important:** The `flush_segments` method uses `ON CONFLICT` which requires a unique constraint. Add a unique constraint to the schema:

```sql
-- Add after the lecture_segments CREATE TABLE, replace the index:
CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_unique
    ON lecture_segments(lecture_id, segment_index);
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_lecture_store.py -v`
Expected: All pass

**Step 5: Commit**

```
feat(classnote): add LectureStore with lecture/segment/label CRUD and crash recovery
```

---

## Task 3: LectureRecorder — Audio Capture + WAV Streaming

**Files:**
- Create: `lecture_recorder.py`
- Create: `tests/test_lecture_recorder.py`

**Step 1: Write failing tests**

```python
# tests/test_lecture_recorder.py
import os
import struct
import tempfile
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from lecture_recorder import LectureRecorder


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@patch("lecture_recorder.sd")
def test_start_creates_wav_and_stream(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    assert rec.is_recording
    assert os.path.exists(wav_path)
    mock_sd.InputStream.assert_called_once()
    rec.stop()


@patch("lecture_recorder.sd")
def test_stop_closes_wav_and_stream(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    rec.stop()
    assert not rec.is_recording
    # WAV file should be valid
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000


@patch("lecture_recorder.sd")
def test_audio_callback_writes_to_wav(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Simulate audio callback
    chunk = np.random.randn(512).astype(np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    rec.stop()
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnframes() == 1024


@patch("lecture_recorder.sd")
def test_on_vad_chunk_callback(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    chunks = []
    rec.on_vad_chunk = lambda c: chunks.append(c)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    chunk = np.random.randn(512).astype(np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 512, None, None)
    rec.stop()
    assert len(chunks) == 1


@patch("lecture_recorder.sd")
def test_pause_and_resume(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    rec.pause()
    assert rec.is_paused
    assert not rec.is_recording
    rec.resume()
    assert rec.is_recording
    assert not rec.is_paused
    rec.stop()


@patch("lecture_recorder.sd")
def test_elapsed_seconds(mock_sd, tmp_dir):
    rec = LectureRecorder(sample_rate=16000)
    wav_path = os.path.join(tmp_dir, "test.wav")
    rec.start(wav_path)
    # Write 16000 samples = 1 second
    chunk = np.zeros(16000, dtype=np.float32)
    rec._audio_callback(chunk.reshape(-1, 1), 16000, None, None)
    assert abs(rec.elapsed_seconds - 1.0) < 0.01
    rec.stop()


def test_recover_wav_header(tmp_dir):
    """Write raw PCM data with broken header, verify recovery fixes it."""
    wav_path = os.path.join(tmp_dir, "broken.wav")
    # Write a minimal WAV header + raw data
    samples = np.random.randn(16000).astype(np.int16)  # 1 second
    raw_data = samples.tobytes()
    # Write broken WAV (header says 0 data bytes)
    with open(wav_path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36))  # Wrong size
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16))
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", 0))  # Wrong size
        f.write(raw_data)

    LectureRecorder.recover_wav(wav_path)

    with wave.open(wav_path, "rb") as wf:
        assert wf.getnframes() == 16000
        assert wf.getnchannels() == 1
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lecture_recorder.py -v`
Expected: ImportError

**Step 3: Implement LectureRecorder**

```python
# lecture_recorder.py
"""Long-running microphone capture with incremental WAV file writing."""
import os
import struct
import threading
import wave

import numpy as np
import sounddevice as sd


class LectureRecorder:
    """Captures mic audio for ClassNote lectures, streaming to WAV file."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.is_recording = False
        self.is_paused = False
        self.on_vad_chunk = None  # Callback for VAD: fn(np.ndarray float32)

        self._stream = None
        self._wav_file = None
        self._wav_path = None
        self._total_frames = 0
        self._lock = threading.Lock()

    def start(self, wav_path: str):
        """Start recording to the given WAV file path."""
        if self.is_recording:
            return
        os.makedirs(os.path.dirname(wav_path), exist_ok=True)
        self._wav_path = wav_path
        self._wav_file = wave.open(wav_path, "wb")
        self._wav_file.setnchannels(1)
        self._wav_file.setsampwidth(2)  # 16-bit
        self._wav_file.setframerate(self.sample_rate)
        self._total_frames = 0

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=512,
            callback=self._audio_callback,
        )
        self._stream.start()
        self.is_recording = True
        self.is_paused = False

    def stop(self):
        """Stop recording and finalize the WAV file."""
        if not self.is_recording and not self.is_paused:
            return
        self.is_recording = False
        self.is_paused = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if self._wav_file:
                self._wav_file.close()
                self._wav_file = None

    def pause(self):
        """Pause recording (stop mic stream, keep WAV open)."""
        if not self.is_recording:
            return
        self.is_recording = False
        self.is_paused = True
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def resume(self):
        """Resume recording after pause."""
        if not self.is_paused:
            return
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=512,
            callback=self._audio_callback,
        )
        self._stream.start()
        self.is_recording = True
        self.is_paused = False

    @property
    def elapsed_seconds(self) -> float:
        return self._total_frames / self.sample_rate

    @property
    def wav_path(self) -> str | None:
        return self._wav_path

    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice callback — write to WAV and forward to VAD."""
        audio = indata[:, 0].copy()  # mono float32

        # Write to WAV as int16
        int16_data = (audio * 32767).astype(np.int16)
        with self._lock:
            if self._wav_file:
                self._wav_file.writeframes(int16_data.tobytes())
                self._total_frames += len(int16_data)

        # Forward to VAD
        if self.on_vad_chunk:
            self.on_vad_chunk(audio)

    @staticmethod
    def recover_wav(wav_path: str):
        """Fix WAV header for a file with broken/incomplete header but valid PCM data."""
        with open(wav_path, "rb") as f:
            data = f.read()

        # Find the data chunk
        data_pos = data.find(b"data")
        if data_pos < 0:
            return
        pcm_start = data_pos + 8  # skip "data" + 4-byte size
        pcm_data = data[pcm_start:]
        data_size = len(pcm_data)
        file_size = 36 + data_size  # RIFF header(12) + fmt(24) + data header(8) + pcm

        with open(wav_path, "wb") as f:
            # RIFF header
            f.write(b"RIFF")
            f.write(struct.pack("<I", file_size - 8))
            f.write(b"WAVE")
            # fmt chunk (PCM, mono, 16kHz, 16-bit)
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16))
            # data chunk
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(pcm_data)
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_lecture_recorder.py -v`
Expected: All pass

**Step 5: Commit**

```
feat(classnote): add LectureRecorder with WAV streaming and crash recovery
```

---

## Task 4: ClassNotePipeline — Session Orchestrator

**Files:**
- Create: `classnote.py`
- Create: `tests/test_classnote.py`

This is the largest task. The pipeline coordinates LectureRecorder + VADSegmenter + WhisperTranscriber + LectureStore.

**Step 1: Write failing tests**

```python
# tests/test_classnote.py
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from classnote import ClassNotePipeline


@pytest.fixture
def mock_deps():
    """Create mock dependencies for ClassNotePipeline."""
    transcriber = MagicMock()
    transcriber.transcribe_array = MagicMock(return_value={"text": "hello world"})
    transcriber._lock = threading.RLock()

    store = MagicMock()
    store.create_lecture = MagicMock(return_value=1)
    store.flush_segments = MagicMock()
    store.add_segment = MagicMock()
    store.apply_correction = MagicMock()
    store.update_lecture = MagicMock()
    store.touch_lecture = MagicMock()

    return transcriber, store


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_start_creates_session(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    result = pipeline.start("Physics 201")
    assert result["lecture_id"] == 1
    assert pipeline.is_active
    store.create_lecture.assert_called_once()
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stop_finalizes_session(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")
    result = pipeline.stop()
    assert not pipeline.is_active
    store.update_lecture.assert_called()  # status -> stopped


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_pause_and_resume(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    pipeline.pause()
    assert pipeline.is_paused
    assert not pipeline.is_active

    pipeline.resume()
    assert pipeline.is_active
    assert not pipeline.is_paused
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_segment_callback_fires(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """When a segment is transcribed, the on_segment callback fires."""
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()

    segments_received = []
    pipeline.on_segment = lambda seg: segments_received.append(seg)

    pipeline.start("Test")
    # Simulate a sealed segment arriving
    from vad import SealedSegment
    seg = SealedSegment(
        segment_index=0,
        mic_audio=np.random.randn(16000).astype(np.float32),
        start_sample=0,
        end_sample=16000,
    )
    pipeline._process_stream_a(seg)
    pipeline.stop()

    assert len(segments_received) == 1
    assert segments_received[0]["ghost"] is True
    assert segments_received[0]["text"] == "hello world"


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_stream_b_opportunistic(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    """Stream B only runs when no Stream A work is pending."""
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")

    # Simulate 3 segments for correction
    for i in range(3):
        seg = MagicMock()
        seg.segment_index = i
        seg.mic_audio = np.random.randn(16000).astype(np.float32)
        seg.start_sample = i * 16000
        seg.end_sample = (i + 1) * 16000
        pipeline._completed_segments.append(seg)

    # Stream B should merge and re-transcribe
    pipeline._try_stream_b_correction()

    # Transcriber should have been called for the correction
    assert transcriber.transcribe_array.called
    pipeline.stop()


@patch("classnote.LectureRecorder")
@patch("classnote.VADSegmenter")
@patch("classnote.SileroVAD")
def test_discard_deletes_everything(MockVAD, MockSegmenter, MockRecorder, mock_deps, tmp_dir):
    transcriber, store = mock_deps
    pipeline = ClassNotePipeline(transcriber, store, lectures_dir=tmp_dir)
    pipeline.load_vad()
    pipeline.start("Test")
    pipeline.discard()
    assert not pipeline.is_active
    store.delete_lecture.assert_called_once()
```

**Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_classnote.py -v`
Expected: ImportError

**Step 3: Implement ClassNotePipeline**

```python
# classnote.py
"""ClassNote pipeline: live lecture transcription with two-stream architecture."""
import os
import queue
import threading
import time
from datetime import datetime

import numpy as np

from lecture_recorder import LectureRecorder
from lecture_store import LectureStore
from vad import SealedSegment, SileroVAD, VADSegmenter


class ClassNotePipeline:
    """Manages a ClassNote lecture recording session.

    Stream A: Fast per-segment transcription → ghost text
    Stream B: Opportunistic rolling correction → solidified text
    """

    FLUSH_INTERVAL_S = 30.0

    def __init__(
        self,
        transcriber,
        store: LectureStore,
        lectures_dir: str | None = None,
        sample_rate: int = 16000,
    ):
        self._transcriber = transcriber
        self._store = store
        self._lectures_dir = lectures_dir or os.path.expanduser(
            "~/.dashscribe/lectures"
        )
        self._sample_rate = sample_rate

        # State
        self._lecture_id = None
        self._active = False
        self._paused = False
        self._lock = threading.Lock()

        # VAD
        self._vad = SileroVAD()
        self._vad_loaded = False
        self._segmenter = None

        # Recorder
        self._recorder = None

        # Worker
        self._worker_thread = None
        self._stop_event = threading.Event()

        # Segments
        self._completed_segments = []  # SealedSegments for Stream B
        self._pending_results = []  # {index, text, ghost, start_ms, end_ms}
        self._correction_counter = 0

        # Flush timer
        self._flush_timer = None
        self._last_flush_segments = []

        # Callbacks
        self.on_segment = None  # fn({index, text, ghost, start_ms, end_ms})
        self.on_correction = None  # fn({start_index, end_index, text, start_ms, end_ms})
        self.on_status = None  # fn(status_string)
        self.on_error = None  # fn(message, recoverable)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def lecture_id(self) -> int | None:
        return self._lecture_id

    def load_vad(self):
        self._vad.load()
        self._vad_loaded = True

    def start(self, title: str) -> dict:
        """Start a new lecture recording session."""
        with self._lock:
            if self._active:
                raise RuntimeError("A lecture session is already active")

        os.makedirs(self._lectures_dir, mode=0o700, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = os.path.join(
            self._lectures_dir, f"lecture_{timestamp}.wav"
        )

        # Create DB record
        lecture_id = self._store.create_lecture(title, wav_path)
        self._lecture_id = lecture_id

        # Create recorder
        self._recorder = LectureRecorder(self._sample_rate)

        # Create VAD segmenter with lecture-tuned params
        self._segmenter = VADSegmenter(
            self._vad,
            self._sample_rate,
            max_segment_duration_s=30.0,
            silence_threshold_ms=1000,
            min_segment_duration_s=2.0,
        )

        # Wire recorder → segmenter
        self._recorder.on_vad_chunk = self._segmenter.feed

        # Start worker thread
        self._stop_event.clear()
        self._completed_segments.clear()
        self._pending_results.clear()
        self._correction_counter = 0
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True
        )
        self._worker_thread.start()

        # Start recording
        self._recorder.start(wav_path)
        self._active = True
        self._paused = False

        # Start periodic flush
        self._schedule_flush()

        if self.on_status:
            self.on_status("recording")

        # Update wav_path with actual lecture_id
        final_path = os.path.join(
            self._lectures_dir, f"lecture_{lecture_id}_{timestamp}.wav"
        )
        if wav_path != final_path:
            os.rename(wav_path, final_path)
            self._store.update_lecture(lecture_id, audio_path=final_path)
            self._recorder._wav_path = final_path

        return {"lecture_id": lecture_id, "wav_path": final_path}

    def stop(self) -> dict:
        """Stop recording, finalize session."""
        if not self._active and not self._paused:
            return {}

        self._active = False
        self._paused = False

        # Cancel flush timer
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None

        # Stop recorder
        if self._recorder:
            self._recorder.stop()

        # Seal final segment and signal worker
        if self._segmenter:
            self._segmenter.seal_final()
            self._segmenter.signal_done()

        # Wait for worker
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=60)

        # Final flush
        self._flush_to_db()

        # Update lecture record
        elapsed = self._recorder.elapsed_seconds if self._recorder else 0
        total_words = sum(
            len(r["text"].split()) for r in self._pending_results
        )
        self._store.update_lecture(
            self._lecture_id,
            status="stopped",
            duration_seconds=elapsed,
            word_count=total_words,
        )

        if self.on_status:
            self.on_status("stopped")

        return {
            "lecture_id": self._lecture_id,
            "duration": elapsed,
            "word_count": total_words,
        }

    def pause(self):
        """Pause recording (for dictation preemption or user pause)."""
        if not self._active:
            return
        self._active = False
        self._paused = True

        if self._recorder:
            self._recorder.pause()

        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None

        if self.on_status:
            self.on_status("paused")

    def resume(self):
        """Resume recording after pause."""
        if not self._paused:
            return
        self._paused = False
        self._active = True

        if self._recorder:
            self._recorder.resume()

        self._schedule_flush()

        if self.on_status:
            self.on_status("recording")

    def discard(self):
        """Discard the current session — delete audio + DB records."""
        was_active = self._active or self._paused
        self._active = False
        self._paused = False

        if self._flush_timer:
            self._flush_timer.cancel()
        if self._recorder:
            self._recorder.stop()
        if self._segmenter:
            self._segmenter.signal_done()
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)

        # Delete audio file
        if self._recorder and self._recorder.wav_path:
            try:
                os.remove(self._recorder.wav_path)
            except OSError:
                pass

        # Delete DB record
        if self._lecture_id:
            self._store.delete_lecture(self._lecture_id)

        self._lecture_id = None

    # --- Worker ---

    def _worker_loop(self):
        """Dequeue sealed segments from VAD and process via Stream A."""
        while not self._stop_event.is_set():
            try:
                segment = self._segmenter.segment_queue.get(timeout=1.0)
            except Exception:
                continue

            if segment is None:  # Sentinel
                break

            try:
                self._process_stream_a(segment)
                self._completed_segments.append(segment)
                # Try Stream B if enough segments and no pending work
                if (
                    len(self._completed_segments) >= 3
                    and self._segmenter.segment_queue.empty()
                    and not self._paused
                ):
                    self._try_stream_b_correction()
            except Exception as e:
                if self.on_error:
                    self.on_error(str(e), True)

    def _process_stream_a(self, segment: SealedSegment):
        """Transcribe a single segment → ghost text."""
        result = self._transcriber.transcribe_array(segment.mic_audio)
        text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()

        if not text:
            return

        start_ms = int(segment.start_sample / self._sample_rate * 1000)
        end_ms = int(segment.end_sample / self._sample_rate * 1000)

        seg_result = {
            "index": segment.segment_index,
            "text": text,
            "ghost": True,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        self._pending_results.append(seg_result)

        if self.on_segment:
            self.on_segment(seg_result)

    def _try_stream_b_correction(self):
        """Opportunistic rolling correction of last 3 segments."""
        if self._paused or len(self._completed_segments) < 3:
            return

        last_3 = self._completed_segments[-3:]
        # Merge audio
        merged = np.concatenate([s.mic_audio for s in last_3])

        try:
            result = self._transcriber.transcribe_array(merged)
            text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()
        except Exception:
            return  # Stream B failure is silent

        if not text:
            return

        self._correction_counter += 1
        start_idx = last_3[0].segment_index
        end_idx = last_3[-1].segment_index
        start_ms = int(last_3[0].start_sample / self._sample_rate * 1000)
        end_ms = int(last_3[-1].end_sample / self._sample_rate * 1000)

        # Persist correction
        self._store.apply_correction(
            self._lecture_id,
            start_idx,
            end_idx,
            text,
            self._correction_counter,
        )

        if self.on_correction:
            self.on_correction({
                "start_index": start_idx,
                "end_index": end_idx,
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "correction_group_id": self._correction_counter,
            })

    # --- Periodic flush ---

    def _schedule_flush(self):
        if self._active:
            self._flush_timer = threading.Timer(
                self.FLUSH_INTERVAL_S, self._periodic_flush
            )
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _periodic_flush(self):
        self._flush_to_db()
        self._schedule_flush()

    def _flush_to_db(self):
        """Write pending segments to SQLite."""
        if not self._lecture_id or not self._pending_results:
            return
        segments = [
            {
                "index": r["index"],
                "text": r["text"],
                "start_ms": r["start_ms"],
                "end_ms": r["end_ms"],
            }
            for r in self._pending_results
        ]
        try:
            self._store.flush_segments(self._lecture_id, segments)
            self._store.touch_lecture(self._lecture_id)
        except Exception:
            pass  # Non-fatal — will retry on next flush
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_classnote.py -v`
Expected: All pass

**Step 5: Commit**

```
feat(classnote): add ClassNotePipeline with two-stream architecture
```

---

## Task 5: WebSocket Endpoint /ws/classnote

**Files:**
- Modify: `app.py` (add /ws/classnote endpoint + REST endpoints)
- Modify: `tests/test_app.py` (add WebSocket tests)

**Step 1: Write failing tests**

Add to `tests/test_app.py`:

```python
# --- ClassNote WebSocket tests ---

@pytest.mark.asyncio
async def test_classnote_ws_start(client):
    """Start a lecture via WebSocket."""
    async with client.websocket_connect("/ws/classnote") as ws:
        await ws.send_json({"action": "start", "title": "Test Lecture"})
        msg = await ws.receive_json()
        assert msg["type"] == "status"
        assert msg["state"] == "recording"


@pytest.mark.asyncio
async def test_classnote_ws_invalid_action(client):
    """Unknown actions are rejected."""
    async with client.websocket_connect("/ws/classnote") as ws:
        await ws.send_json({"action": "invalid"})
        msg = await ws.receive_json()
        assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_classnote_ws_title_validation(client):
    """Title exceeding 255 chars is rejected."""
    async with client.websocket_connect("/ws/classnote") as ws:
        await ws.send_json({"action": "start", "title": "x" * 300})
        msg = await ws.receive_json()
        # Should still start, but title truncated
        assert msg["type"] == "status"
```

**Step 2: Implement /ws/classnote endpoint in app.py**

Add after the existing /ws/bar endpoint. Key points:
- Validate action against allowlist: `{"start", "pause", "resume", "stop"}`
- Sanitize title: strip HTML, max 255 chars
- Store `ClassNotePipeline` on `app.state.classnote_pipeline`
- Wire `on_segment`, `on_correction`, `on_status`, `on_error` callbacks to WebSocket sends
- Add `get_classnote_pipeline` callback for hotkey integration

Also add REST endpoints:
- `GET /api/classnote/lectures` — list lectures
- `GET /api/classnote/lectures/{id}` — get lecture with segments
- `GET /api/classnote/lectures/{id}/segments` — get segments (for WebSocket reconnect fallback)
- `DELETE /api/classnote/lectures/{id}` — delete lecture
- `POST /api/classnote/labels` — create label
- `GET /api/classnote/labels` — list labels
- `POST /api/classnote/lectures/{id}/labels/{label_id}` — assign label
- `DELETE /api/classnote/lectures/{id}/labels/{label_id}` — remove label

**Step 3: Run tests**

Run: `python3 -m pytest tests/test_app.py -v -k classnote`
Expected: All pass

**Step 4: Commit**

```
feat(classnote): add WebSocket /ws/classnote and REST API endpoints
```

---

## Task 6: Hotkey Integration — Dictation Preemption

**Files:**
- Modify: `hotkey.py:73-91` (add `get_classnote_pipeline` param)
- Modify: `hotkey.py:571-627` (_on_press — pause ClassNote before dictation)
- Modify: `hotkey.py:761-890` (_process_recording — resume ClassNote after dictation)
- Modify: `app.py` (pass callback to GlobalHotkey)

**Step 1: Add `get_classnote_pipeline` to GlobalHotkey.__init__**

```python
# In GlobalHotkey.__init__, add parameter:
def __init__(self, ..., get_classnote_pipeline=None):
    ...
    self._get_classnote_pipeline = get_classnote_pipeline or (lambda: None)
```

**Step 2: Modify _on_press to pause ClassNote**

Insert before `self.recorder.start()` (around line 611):

```python
# Pause ClassNote if active (yields mic)
cn_pipeline = self._get_classnote_pipeline()
if cn_pipeline and cn_pipeline.is_active:
    cn_pipeline.pause()
    time.sleep(0.05)  # Brief delay for mic release
```

**Step 3: Modify _process_recording to resume ClassNote (in finally block)**

Wrap the transcription logic in try/finally to ensure resume always happens:

```python
def _process_recording(self):
    cn_pipeline = self._get_classnote_pipeline()
    try:
        # ... existing transcription logic ...
    finally:
        # Resume ClassNote if it was paused
        if cn_pipeline and cn_pipeline.is_paused:
            cn_pipeline.resume()
```

**Step 4: Also handle _cancel_hotkey_recording**

Add resume call when dictation is cancelled:

```python
def _cancel_hotkey_recording(self):
    # ... existing cancel logic ...
    # Resume ClassNote if paused
    cn_pipeline = self._get_classnote_pipeline()
    if cn_pipeline and cn_pipeline.is_paused:
        cn_pipeline.resume()
```

**Step 5: Wire in app.py create_app()**

```python
def get_classnote_pipeline():
    return getattr(app.state, "classnote_pipeline", None)

hotkey = GlobalHotkey(..., get_classnote_pipeline=get_classnote_pipeline)
```

**Step 6: Run tests**

Run: `python3 -m pytest tests/ -v`
Expected: All pass (including existing hotkey tests)

**Step 7: Commit**

```
feat(classnote): integrate dictation preemption via hotkey callback
```

---

## Task 7: Frontend — HTML Structure + CSS

**Files:**
- Modify: `static/index.html` (add ClassNote sidebar item + page structure)
- Modify: `static/style.css` (add ClassNote-specific styles)

**Step 1: Add ClassNote sidebar nav item**

In `static/index.html`, after the "snippets" nav button (around line 85), add:

```html
<button class="nav-item" data-mode="classnote" title="ClassNote">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
        <circle cx="12" cy="10" r="3" fill="currentColor" opacity="0.5"/>
    </svg>
    <span>ClassNote</span>
    <span class="classnote-recording-dot" style="display:none;"></span>
</button>
```

**Step 2: Add ClassNote page structure**

Add a new page div after the existing pages:

```html
<div id="classnote-mode" class="page" style="display:none;">
    <!-- Lecture List View -->
    <div id="classnote-list" class="classnote-view">
        <div class="page-header">
            <h1>ClassNote</h1>
            <button id="classnote-start-btn" class="btn btn-primary">Start Lecture</button>
        </div>
        <div id="classnote-filters" class="classnote-filters">
            <input type="text" id="classnote-search" placeholder="Search lectures..." class="input-field">
            <div id="classnote-label-filter" class="label-chips"></div>
        </div>
        <div id="classnote-lectures" class="lecture-list">
            <!-- Populated by JS -->
        </div>
        <div id="classnote-empty" class="empty-state" style="display:none;">
            <p>No lectures yet.</p>
            <button class="btn btn-primary" onclick="document.getElementById('classnote-start-btn').click()">
                Start your first lecture
            </button>
        </div>
    </div>

    <!-- Recording View -->
    <div id="classnote-recording" class="classnote-view" style="display:none;">
        <div class="classnote-top-bar">
            <input type="text" id="classnote-title" class="classnote-title-input" placeholder="Lecture title...">
            <span class="recording-indicator"><span class="recording-dot"></span></span>
            <span id="classnote-elapsed">00:00</span>
            <div class="classnote-controls">
                <button id="classnote-pause-btn" class="btn btn-secondary" title="Pause">Pause</button>
                <button id="classnote-stop-btn" class="btn btn-primary" title="Stop">Stop</button>
                <button id="classnote-discard-btn" class="btn btn-danger" title="Discard">Discard</button>
            </div>
        </div>
        <div id="classnote-warning-banner" class="warning-banner" style="display:none;"></div>
        <div id="classnote-transcript" class="classnote-transcript">
            <!-- Segments appended by JS -->
        </div>
        <button id="classnote-jump-btn" class="jump-to-latest" style="display:none;">Jump to latest</button>
    </div>

    <!-- Review View -->
    <div id="classnote-review" class="classnote-view" style="display:none;">
        <div class="classnote-top-bar">
            <input type="text" id="review-title" class="classnote-title-input">
            <span id="review-meta" class="review-meta"></span>
            <div id="review-labels" class="label-chips"></div>
        </div>
        <div id="review-transcript" class="classnote-transcript review-mode">
            <!-- Populated by JS -->
        </div>
        <div class="classnote-bottom-bar">
            <button id="review-delete-btn" class="btn btn-danger">Delete Lecture</button>
        </div>
        <div id="review-player" class="audio-player" style="display:none;">
            <button id="player-play-btn" class="btn btn-icon">Play</button>
            <input type="range" id="player-scrub" min="0" max="100" value="0">
            <span id="player-time">0:00</span>
        </div>
    </div>

    <!-- Countdown Overlay -->
    <div id="classnote-countdown" class="countdown-overlay" style="display:none;">
        <span id="countdown-number" class="countdown-number">3</span>
        <button id="countdown-cancel" class="btn btn-secondary">Cancel</button>
    </div>
</div>
```

**Step 3: Add CSS styles**

Add ClassNote-specific styles to `static/style.css`. Key styles:

```css
/* ClassNote Recording Dot (sidebar) */
.classnote-recording-dot {
    width: 6px; height: 6px;
    background: var(--red);
    border-radius: 50%;
    animation: pulse 1.5s ease infinite;
    margin-left: auto;
}

/* ClassNote Top Bar */
.classnote-top-bar {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 20px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
}
.classnote-title-input {
    background: transparent; border: none; color: var(--text-primary);
    font-size: 16px; font-weight: 600; flex: 1;
    outline: none;
}

/* Recording indicator */
.recording-indicator { display: flex; align-items: center; gap: 6px; }
.recording-dot {
    width: 8px; height: 8px;
    background: var(--red);
    border-radius: 50%;
    animation: pulse 1.5s ease infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

/* Transcript area */
.classnote-transcript {
    flex: 1; overflow-y: auto;
    padding: 20px;
    font-size: 15px; line-height: 1.7;
}
.classnote-transcript .segment {
    margin-bottom: 8px;
    transition: color 0.3s var(--ease-apple), font-style 0.3s var(--ease-apple);
}
.classnote-transcript .segment.ghost {
    color: var(--text-secondary);
    font-style: italic;
}
.classnote-transcript .segment.solid {
    color: var(--text-primary);
    font-style: normal;
}
.classnote-transcript .separator {
    color: var(--text-tertiary);
    text-align: center;
    margin: 16px 0;
    font-size: 13px;
}

/* Review mode — hover to highlight */
.review-mode .segment:hover {
    background: var(--surface);
    border-radius: var(--radius-sm);
    cursor: pointer;
}
.review-mode .segment.playing {
    background: color-mix(in srgb, var(--accent) 15%, transparent);
    border-radius: var(--radius-sm);
}

/* Lecture cards */
.lecture-list { display: flex; flex-direction: column; gap: 8px; padding: 20px; }
.lecture-card {
    display: flex; flex-direction: column; gap: 4px;
    padding: 16px; border-radius: var(--radius-md);
    background: var(--surface); border: 1px solid var(--border);
    cursor: pointer; transition: border-color 0.2s;
}
.lecture-card:hover { border-color: var(--accent); }
.lecture-card .title { font-weight: 600; color: var(--text-primary); }
.lecture-card .meta { font-size: 13px; color: var(--text-secondary); }
.lecture-card .preview { font-size: 13px; color: var(--text-tertiary); }

/* Label chips */
.label-chip {
    display: inline-flex; align-items: center;
    padding: 2px 8px; border-radius: var(--radius-full);
    font-size: 12px; font-weight: 500;
}

/* Warning banner */
.warning-banner {
    padding: 8px 20px;
    background: color-mix(in srgb, var(--orange) 15%, transparent);
    color: var(--orange);
    font-size: 13px;
}

/* Jump to latest */
.jump-to-latest {
    position: sticky; bottom: 12px;
    align-self: center;
    padding: 6px 16px; border-radius: var(--radius-full);
    background: var(--accent); color: white;
    border: none; cursor: pointer;
    font-size: 13px;
}

/* Countdown overlay */
.countdown-overlay {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: rgba(0,0,0,0.7); z-index: 100;
}
.countdown-number { font-size: 72px; font-weight: 700; color: white; }

/* Audio player */
.audio-player {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 20px;
    border-top: 1px solid var(--border);
    background: var(--surface);
}

/* Empty state */
.empty-state {
    display: flex; flex-direction: column; align-items: center; gap: 16px;
    padding: 60px 20px;
    color: var(--text-tertiary);
}

/* Badges */
.badge { font-size: 11px; padding: 2px 6px; border-radius: var(--radius-sm); }
.badge-expired { background: color-mix(in srgb, var(--orange) 20%, transparent); color: var(--orange); }
.badge-recovered { background: color-mix(in srgb, var(--blue) 20%, transparent); color: var(--blue); }
```

**Step 4: Commit**

```
feat(classnote): add ClassNote HTML structure and CSS styles
```

---

## Task 8: Frontend — classnote.js

**Files:**
- Create: `static/classnote.js`
- Modify: `static/index.html` (add script tag)

**Step 1: Implement classnote.js**

This file handles:
1. WebSocket connection to /ws/classnote
2. Lecture list — fetch, render, search, filter
3. Recording UI — countdown, ghost text rendering, auto-scroll, pause/stop/discard
4. Review UI — segment click-to-play via Web Audio API, label management
5. Elapsed time client-side timer

Key sections:

```javascript
// static/classnote.js
(function() {
    'use strict';

    let ws = null;
    let currentLectureId = null;
    let currentView = 'list'; // 'list' | 'recording' | 'review'
    let elapsedTimer = null;
    let elapsedSeconds = 0;
    let autoScrollEnabled = true;
    let audioContext = null;
    let audioBuffer = null;
    let audioSource = null;

    // --- WebSocket ---

    function connectWS() { /* ... */ }
    function sendAction(action, data) { /* ... */ }
    function handleMessage(msg) { /* route by msg.type */ }

    // --- Lecture List ---

    async function loadLectures() { /* fetch /api/classnote/lectures */ }
    function renderLectureCard(lecture) { /* ... */ }
    function openLecture(id) { /* switch to review view */ }

    // --- Recording ---

    function startCountdown() { /* 3-2-1, then sendAction('start') */ }
    function addSegment(data) { /* append ghost/solid segment div */ }
    function applyCorrection(data) { /* replace segment range with solid block */ }
    function updateAutoScroll() { /* ... */ }

    // --- Review ---

    async function loadReview(lectureId) { /* fetch segments, render, load audio */ }
    function playSegment(startMs, endMs) { /* Web Audio API slice playback */ }

    // --- Labels ---

    async function loadLabels() { /* ... */ }
    function assignLabel(lectureId, labelId) { /* ... */ }

    // --- Timer ---

    function startElapsedTimer() { /* setInterval 1s */ }
    function stopElapsedTimer() { /* clearInterval */ }
    function formatElapsed(seconds) { /* MM:SS or H:MM:SS */ }

    // --- Init ---

    function init() {
        // Tab switching hooks
        // Button event listeners
        // Auto-scroll observer
    }

    document.addEventListener('DOMContentLoaded', init);
})();
```

**Step 2: Add script tag to index.html**

```html
<script src="/static/classnote.js"></script>
```

**Step 3: Commit**

```
feat(classnote): add classnote.js with recording, review, and lecture list UI
```

---

## Task 9: Crash Recovery + Auto-Cleanup

**Files:**
- Modify: `app.py` (add startup recovery check + cleanup daemon thread)

**Step 1: Add startup recovery**

In `create_app()`, after LectureStore initialization:

```python
# Detect and recover crashed lectures
crashed = lecture_store.detect_crashed_lectures(stale_minutes=5)
for lecture in crashed:
    lecture_store.mark_recovered(lecture["id"])
    # Fix WAV header if audio file exists
    audio_path = lecture.get("audio_path")
    if audio_path and os.path.exists(audio_path):
        try:
            LectureRecorder.recover_wav(audio_path)
        except Exception:
            pass
```

**Step 2: Add auto-cleanup daemon thread**

```python
def _cleanup_expired_audio(store, settings):
    """Background thread: delete audio files past retention period."""
    retention_days = settings.get("lecture_audio_retention_days", 30) if settings else 30
    lectures = store.list_lectures(limit=10000)
    for lecture in lectures:
        if lecture["status"] == "recording":
            continue
        created = datetime.fromisoformat(lecture["created_at"])
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days > retention_days and lecture.get("audio_path"):
            try:
                os.remove(lecture["audio_path"])
                store.update_lecture(lecture["id"], audio_path=None)
            except OSError:
                pass

# In create_app(), start cleanup thread:
threading.Thread(
    target=_cleanup_expired_audio,
    args=(lecture_store, settings),
    daemon=True,
).start()
```

**Step 3: Run all tests**

Run: `python3 -m pytest tests/ -v --cov=. --cov-report=term-missing`
Expected: All pass, coverage >= 80% on new modules

**Step 4: Commit**

```
feat(classnote): add crash recovery on startup and audio auto-cleanup
```

---

## Task 10: Integration Testing + Final Polish

**Files:**
- Modify: `tests/test_classnote.py` (add integration tests)
- Modify: `tests/test_app.py` (add full WebSocket lifecycle test)

**Step 1: Add integration test — full pipeline lifecycle**

```python
def test_full_pipeline_lifecycle(mock_deps, tmp_dir):
    """Start → segments → correction → stop → verify DB."""
    transcriber, store = mock_deps
    # Use a real LectureStore with temp DB
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    real_store = LectureStore(db_path)
    # ... test full lifecycle with mocked audio ...
    os.unlink(db_path)
```

**Step 2: Add WebSocket lifecycle test**

```python
async def test_classnote_full_lifecycle(client):
    """Start → pause → resume → stop via WebSocket."""
    async with client.websocket_connect("/ws/classnote") as ws:
        await ws.send_json({"action": "start", "title": "Integration Test"})
        msg = await ws.receive_json()
        assert msg["type"] == "status" and msg["state"] == "recording"

        await ws.send_json({"action": "pause"})
        msg = await ws.receive_json()
        assert msg["state"] == "paused"

        await ws.send_json({"action": "resume"})
        msg = await ws.receive_json()
        assert msg["state"] == "recording"

        await ws.send_json({"action": "stop"})
        msg = await ws.receive_json()
        assert msg["state"] == "stopped"
```

**Step 3: Run full test suite with coverage**

Run: `python3 -m pytest tests/ -v --cov=. --cov-report=term-missing --cov-fail-under=80`
Expected: All pass, 80%+ coverage

**Step 4: Final commit**

```
test(classnote): add integration tests for pipeline lifecycle and WebSocket
```

---

## Summary — Dependency Order

```
Task 1: VADSegmenter refactor (no deps)
    ↓
Task 2: LectureStore (no deps)
Task 3: LectureRecorder (no deps)
    ↓        ↓
Task 4: ClassNotePipeline (depends on 1, 2, 3)
    ↓
Task 5: WebSocket endpoint (depends on 4)
Task 6: Hotkey integration (depends on 4)
    ↓
Task 7: Frontend HTML + CSS (no code deps, but needs endpoints from 5)
Task 8: Frontend JS (depends on 5, 7)
    ↓
Task 9: Crash recovery + cleanup (depends on 2, 3)
Task 10: Integration tests (depends on all)
```

**Parallelizable:** Tasks 2 and 3 can run in parallel. Tasks 5 and 6 can run in parallel. Tasks 7 and 9 can run in parallel.
