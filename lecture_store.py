# lecture_store.py
"""SQLite storage for ClassNote lectures, segments, and labels."""
import os
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_unique
    ON lecture_segments(lecture_id, segment_index);

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

    def update_segment_text(self, lecture_id: int, segment_index: int, text: str):
        """Update the text of a single segment (user edit)."""
        conn = self._connect()
        conn.execute(
            "UPDATE lecture_segments SET text = ? WHERE lecture_id = ? AND segment_index = ?",
            (text, lecture_id, segment_index),
        )
        conn.commit()
        conn.close()

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

    def replace_segments(self, lecture_id: int, segments: list[dict]):
        """Delete all segments for a lecture and insert new ones (for re-transcribe)."""
        conn = self._connect()
        conn.execute("DELETE FROM lecture_segments WHERE lecture_id = ?", (lecture_id,))
        for seg in segments:
            conn.execute(
                "INSERT INTO lecture_segments (lecture_id, segment_index, text, start_ms, end_ms)"
                " VALUES (?, ?, ?, ?, ?)",
                (lecture_id, seg["index"], seg["text"], seg["start_ms"], seg["end_ms"]),
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
        start_ms: int = 0,
        end_ms: int = 0,
    ):
        """Replace segments in range with single corrected segment."""
        conn = self._connect()
        # Delete old segments in range (may not exist yet if not flushed)
        conn.execute(
            """DELETE FROM lecture_segments
               WHERE lecture_id = ? AND segment_index >= ? AND segment_index <= ?""",
            (lecture_id, start_index, end_index),
        )
        # Insert single corrected segment
        conn.execute(
            """INSERT INTO lecture_segments
               (lecture_id, segment_index, text, start_ms, end_ms, is_corrected, correction_group_id)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (lecture_id, start_index, corrected_text, start_ms, end_ms, correction_group_id),
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

    def delete_label(self, label_id: int):
        """Permanently delete a label and all its assignments."""
        conn = self._connect()
        conn.execute("DELETE FROM lecture_label_map WHERE label_id = ?", (label_id,))
        conn.execute("DELETE FROM lecture_labels WHERE id = ?", (label_id,))
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
