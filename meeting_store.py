# meeting_store.py
"""SQLite storage for meeting transcriptions, segments, and labels."""
import os
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    app_name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'listen',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    duration_seconds REAL,
    word_count INTEGER DEFAULT 0,
    system_audio_path TEXT,
    mic_audio_path TEXT,
    status TEXT NOT NULL DEFAULT 'recording'
);

CREATE TABLE IF NOT EXISTS meeting_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    speaker TEXT NOT NULL DEFAULT 'others'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_meeting_segments_unique
    ON meeting_segments(meeting_id, segment_index);

CREATE TABLE IF NOT EXISTS meeting_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_label_map (
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES meeting_labels(id),
    PRIMARY KEY (meeting_id, label_id)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeetingStore:
    """SQLite CRUD for meetings, segments, and labels."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.path.expanduser("~/.dashscribe/meetings.db")
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

    # --- Meetings ---

    def create_meeting(self, title: str, app_name: str, mode: str = "listen") -> int:
        now = _now_iso()
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO meetings (title, app_name, mode, created_at, updated_at, status)"
            " VALUES (?, ?, ?, ?, ?, 'recording')",
            (title, app_name, mode, now, now),
        )
        mid = cur.lastrowid
        conn.commit()
        conn.close()
        return mid

    def get_meeting(self, meeting_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_meeting(self, meeting_id: int, **kwargs):
        allowed = {
            "title", "status", "duration_seconds", "word_count",
            "system_audio_path", "mic_audio_path",
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        fields["updated_at"] = _now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [meeting_id]
        conn = self._connect()
        conn.execute(f"UPDATE meetings SET {sets} WHERE id = ?", vals)
        conn.commit()
        conn.close()

    def delete_meeting(self, meeting_id: int):
        conn = self._connect()
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.commit()
        conn.close()

    def list_meetings(self, limit: int = 100, offset: int = 0) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM meetings ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def touch_meeting(self, meeting_id: int):
        conn = self._connect()
        conn.execute(
            "UPDATE meetings SET updated_at = ? WHERE id = ?",
            (_now_iso(), meeting_id),
        )
        conn.commit()
        conn.close()

    # --- Segments ---

    def add_segment(
        self, meeting_id: int, index: int, text: str,
        start_ms: int, end_ms: int, speaker: str = "others",
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO meeting_segments (meeting_id, segment_index, text, start_ms, end_ms, speaker)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (meeting_id, index, text, start_ms, end_ms, speaker),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def update_segment_text(self, meeting_id: int, segment_index: int, text: str):
        conn = self._connect()
        conn.execute(
            "UPDATE meeting_segments SET text = ? WHERE meeting_id = ? AND segment_index = ?",
            (text, meeting_id, segment_index),
        )
        conn.commit()
        conn.close()

    def get_segments(self, meeting_id: int) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM meeting_segments WHERE meeting_id = ? ORDER BY segment_index",
            (meeting_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def flush_segments(self, meeting_id: int, segments: list[dict]):
        conn = self._connect()
        for seg in segments:
            conn.execute(
                """INSERT INTO meeting_segments (meeting_id, segment_index, text, start_ms, end_ms, speaker)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (meeting_id, segment_index)
                   DO UPDATE SET text = excluded.text, start_ms = excluded.start_ms,
                                 end_ms = excluded.end_ms, speaker = excluded.speaker""",
                (meeting_id, seg["index"], seg["text"], seg["start_ms"], seg["end_ms"],
                 seg.get("speaker", "others")),
            )
        conn.execute(
            "UPDATE meetings SET updated_at = ? WHERE id = ?",
            (_now_iso(), meeting_id),
        )
        conn.commit()
        conn.close()

    def replace_segments(self, meeting_id: int, segments: list[dict]):
        conn = self._connect()
        conn.execute("DELETE FROM meeting_segments WHERE meeting_id = ?", (meeting_id,))
        for seg in segments:
            conn.execute(
                "INSERT INTO meeting_segments (meeting_id, segment_index, text, start_ms, end_ms, speaker)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (meeting_id, seg["index"], seg["text"], seg["start_ms"], seg["end_ms"],
                 seg.get("speaker", "others")),
            )
        conn.commit()
        conn.close()

    # --- Labels ---

    def create_label(self, name: str, color: str) -> int:
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO meeting_labels (name, color) VALUES (?, ?)", (name, color)
        )
        lid = cur.lastrowid
        conn.commit()
        conn.close()
        return lid

    def list_labels(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM meeting_labels ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def assign_label(self, meeting_id: int, label_id: int):
        conn = self._connect()
        conn.execute(
            "INSERT OR IGNORE INTO meeting_label_map (meeting_id, label_id) VALUES (?, ?)",
            (meeting_id, label_id),
        )
        conn.commit()
        conn.close()

    def remove_label(self, meeting_id: int, label_id: int):
        conn = self._connect()
        conn.execute(
            "DELETE FROM meeting_label_map WHERE meeting_id = ? AND label_id = ?",
            (meeting_id, label_id),
        )
        conn.commit()
        conn.close()

    def delete_label(self, label_id: int):
        conn = self._connect()
        conn.execute("DELETE FROM meeting_label_map WHERE label_id = ?", (label_id,))
        conn.execute("DELETE FROM meeting_labels WHERE id = ?", (label_id,))
        conn.commit()
        conn.close()

    def get_meeting_labels(self, meeting_id: int) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT ml.* FROM meeting_labels ml
               JOIN meeting_label_map mm ON ml.id = mm.label_id
               WHERE mm.meeting_id = ?
               ORDER BY ml.name""",
            (meeting_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # --- Search ---

    def search_meetings(self, query: str, limit: int = 50) -> list[dict]:
        pattern = f"%{query}%"
        conn = self._connect()
        rows = conn.execute(
            """SELECT DISTINCT m.* FROM meetings m
               LEFT JOIN meeting_segments ms ON m.id = ms.meeting_id
               WHERE m.title LIKE ? OR ms.text LIKE ?
               ORDER BY m.created_at DESC LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # --- Crash Recovery ---

    def detect_crashed_meetings(self, stale_minutes: int = 5) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM meetings
               WHERE status = 'recording'
               AND updated_at < datetime('now', ? || ' minutes')""",
            (f"-{stale_minutes}",),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_recovered(self, meeting_id: int):
        self.update_meeting(meeting_id, status="recovered")
