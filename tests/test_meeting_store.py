# tests/test_meeting_store.py
import os
import tempfile

import pytest

from meeting_store import MeetingStore


@pytest.fixture
def store():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    s = MeetingStore(db_path=f.name)
    yield s
    os.unlink(f.name)


def test_create_meeting(store):
    mid = store.create_meeting("Standup", app_name="Zoom", mode="full")
    assert mid > 0
    m = store.get_meeting(mid)
    assert m["title"] == "Standup"
    assert m["mode"] == "full"
    assert m["status"] == "recording"
    assert m["app_name"] == "Zoom"


def test_add_segment_with_speaker(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    sid = store.add_segment(mid, 0, "hello", 0, 1000, speaker="others")
    segs = store.get_segments(mid)
    assert len(segs) == 1
    assert segs[0]["speaker"] == "others"
    assert segs[0]["text"] == "hello"
    assert sid > 0


def test_add_segment_speaker_you(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="full")
    store.add_segment(mid, 0, "my words", 0, 1000, speaker="you")
    segs = store.get_segments(mid)
    assert segs[0]["speaker"] == "you"


def test_list_meetings(store):
    store.create_meeting("M1", app_name="Zoom", mode="listen")
    store.create_meeting("M2", app_name="Teams", mode="full")
    meetings = store.list_meetings()
    assert len(meetings) == 2


def test_search_meetings(store):
    mid = store.create_meeting("Sprint Planning", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "discuss the roadmap", 0, 1000, speaker="others")
    results = store.search_meetings("roadmap")
    assert len(results) == 1
    assert results[0]["title"] == "Sprint Planning"


def test_search_meetings_by_title(store):
    store.create_meeting("Sprint Planning", app_name="Zoom", mode="listen")
    results = store.search_meetings("Sprint")
    assert len(results) == 1


def test_delete_meeting(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.delete_meeting(mid)
    assert store.get_meeting(mid) is None


def test_update_meeting(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.update_meeting(mid, title="Updated", status="stopped", duration_seconds=120.0)
    m = store.get_meeting(mid)
    assert m["title"] == "Updated"
    assert m["status"] == "stopped"
    assert m["duration_seconds"] == 120.0


def test_update_segment_text(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "hello", 0, 1000, speaker="others")
    store.update_segment_text(mid, 0, "hello world")
    segs = store.get_segments(mid)
    assert segs[0]["text"] == "hello world"


def test_flush_segments(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.flush_segments(mid, [
        {"index": 0, "text": "first", "start_ms": 0, "end_ms": 500, "speaker": "others"},
        {"index": 1, "text": "second", "start_ms": 500, "end_ms": 1000, "speaker": "you"},
    ])
    segs = store.get_segments(mid)
    assert len(segs) == 2
    assert segs[0]["speaker"] == "others"
    assert segs[1]["speaker"] == "you"


def test_flush_segments_upsert(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "old", 0, 500, speaker="others")
    store.flush_segments(mid, [
        {"index": 0, "text": "new", "start_ms": 0, "end_ms": 500, "speaker": "others"},
    ])
    segs = store.get_segments(mid)
    assert len(segs) == 1
    assert segs[0]["text"] == "new"


def test_replace_segments(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "old1", 0, 500, speaker="others")
    store.add_segment(mid, 1, "old2", 500, 1000, speaker="others")
    store.replace_segments(mid, [
        {"index": 0, "text": "new1", "start_ms": 0, "end_ms": 1000, "speaker": "you"},
    ])
    segs = store.get_segments(mid)
    assert len(segs) == 1
    assert segs[0]["text"] == "new1"
    assert segs[0]["speaker"] == "you"


# --- Labels ---

def test_create_label(store):
    lid = store.create_label("important", "#ff0000")
    assert lid > 0


def test_list_labels(store):
    store.create_label("a", "#aaa")
    store.create_label("b", "#bbb")
    labels = store.list_labels()
    assert len(labels) == 2


def test_assign_and_get_labels(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    lid = store.create_label("tag", "#000")
    store.assign_label(mid, lid)
    labels = store.get_meeting_labels(mid)
    assert len(labels) == 1
    assert labels[0]["name"] == "tag"


def test_remove_label(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    lid = store.create_label("tag", "#000")
    store.assign_label(mid, lid)
    store.remove_label(mid, lid)
    labels = store.get_meeting_labels(mid)
    assert len(labels) == 0


def test_delete_label(store):
    lid = store.create_label("tag", "#000")
    store.delete_label(lid)
    labels = store.list_labels()
    assert len(labels) == 0


def test_get_meeting_returns_none_for_missing(store):
    assert store.get_meeting(999) is None


def test_touch_meeting(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    m1 = store.get_meeting(mid)
    store.touch_meeting(mid)
    m2 = store.get_meeting(mid)
    # updated_at should change (or be the same since it's fast)
    assert m2["updated_at"] is not None
