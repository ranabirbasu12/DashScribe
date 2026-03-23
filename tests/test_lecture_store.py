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
