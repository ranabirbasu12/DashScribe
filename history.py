# history.py
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone


DEFAULT_DB_PATH = os.path.expanduser("~/.dashscribe/history.db")
SOURCE_DICTATION = "dictation"
SOURCE_FILE = "file"
VALID_SOURCES = frozenset({SOURCE_DICTATION, SOURCE_FILE})
WORD_RE = re.compile(r"[A-Za-z0-9']+")


class TranscriptionHistory:
    """SQLite-backed transcription history."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        if not self._path_writable(self.db_path):
            self.db_path = self._fallback_path()
        self._ensure_parent_dir()
        try:
            self._init_db()
        except sqlite3.OperationalError:
            # Fall back to a writable temp location in constrained environments.
            self.db_path = self._fallback_path()
            self._ensure_parent_dir()
            self._init_db()

    def _fallback_path(self):
        return os.path.join(tempfile.gettempdir(), "dashscribe-history.db")

    def _path_writable(self, path: str) -> bool:
        parent = os.path.dirname(path)
        if not parent:
            return False
        if not os.path.isdir(parent):
            # Writable if we can create the parent chain.
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                return False
        if not os.access(parent, os.W_OK):
            return False
        if os.path.exists(path) and not os.access(path, os.W_OK):
            return False
        return True

    def _ensure_parent_dir(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def _init_db(self):
        with self._connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    raw_text TEXT,
                    timestamp TEXT NOT NULL,
                    duration_seconds REAL,
                    latency_seconds REAL,
                    source TEXT NOT NULL DEFAULT 'dictation',
                    word_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            self._migrate_schema(conn)

    def _migrate_schema(self, conn):
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(transcriptions)").fetchall()
        }

        if "source" not in columns:
            conn.execute(
                "ALTER TABLE transcriptions ADD COLUMN source TEXT NOT NULL DEFAULT 'dictation'"
            )
        if "word_count" not in columns:
            conn.execute(
                "ALTER TABLE transcriptions ADD COLUMN word_count INTEGER NOT NULL DEFAULT 0"
            )
        if "raw_text" not in columns:
            conn.execute(
                "ALTER TABLE transcriptions ADD COLUMN raw_text TEXT"
            )

        conn.execute(
            "UPDATE transcriptions SET source = ? "
            "WHERE source IS NULL OR TRIM(source) = '' OR source NOT IN (?, ?)",
            (SOURCE_DICTATION, SOURCE_DICTATION, SOURCE_FILE),
        )
        self._backfill_word_counts(conn)

    def _backfill_word_counts(self, conn):
        rows = conn.execute(
            "SELECT id, text FROM transcriptions "
            "WHERE word_count IS NULL OR (word_count = 0 AND LENGTH(TRIM(text)) > 0)"
        ).fetchall()
        if not rows:
            return

        updates = []
        for row_id, text in rows:
            updates.append((self._count_words(text), row_id))
        conn.executemany(
            "UPDATE transcriptions SET word_count = ? WHERE id = ?",
            updates,
        )

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _normalize_source(self, source: str) -> str:
        source_key = str(source or "").strip().lower()
        if source_key in VALID_SOURCES:
            return source_key
        return SOURCE_DICTATION

    def _count_words(self, text: str) -> int:
        if not text:
            return 0
        return len(WORD_RE.findall(text))

    def add(
        self,
        text: str,
        duration: float = 0.0,
        latency: float = 0.0,
        source: str = SOURCE_DICTATION,
        raw_text: str | None = None,
    ):
        ts = datetime.now(timezone.utc).isoformat()
        source_val = self._normalize_source(source)
        word_count = self._count_words(text)
        sql = (
            "INSERT INTO transcriptions "
            "(text, raw_text, timestamp, duration_seconds, latency_seconds, source, word_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        params = (text, raw_text, ts, duration, latency, source_val, word_count)
        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "readonly" not in str(e).lower():
                raise
            self.db_path = self._fallback_path()
            self._ensure_parent_dir()
            self._init_db()
            with self._connect() as conn:
                conn.execute(sql, params)

    def get_recent(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM transcriptions ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM transcriptions WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM transcriptions").fetchone()[0]

    def get_usage_stats(self) -> dict:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT timestamp, duration_seconds, source, word_count, text "
                "FROM transcriptions"
            ).fetchall()

        if not rows:
            return {
                "streak_days": 0,
                "total_words": 0,
                "words_per_minute": 0.0,
            }

        local_days = set()
        total_words = 0
        total_duration_seconds = 0.0

        for row in rows:
            ts_raw = row["timestamp"]
            try:
                ts = datetime.fromisoformat(ts_raw)
            except (TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            local_days.add(ts.astimezone().date())

            if self._normalize_source(row["source"]) != SOURCE_DICTATION:
                continue

            wc = int(row["word_count"] or 0)
            if wc <= 0:
                wc = self._count_words(row["text"] or "")
            total_words += wc

            duration = row["duration_seconds"]
            if duration is not None:
                try:
                    duration_f = float(duration)
                except (TypeError, ValueError):
                    duration_f = 0.0
                if duration_f > 0:
                    total_duration_seconds += duration_f

        today = datetime.now().astimezone().date()
        streak = 0
        cursor = today
        while cursor in local_days:
            streak += 1
            cursor -= timedelta(days=1)

        wpm = 0.0
        if total_duration_seconds > 0:
            wpm = round(total_words / (total_duration_seconds / 60.0), 1)

        return {
            "streak_days": streak,
            "total_words": total_words,
            "words_per_minute": wpm,
        }
