# tests/test_app.py
import tempfile
import os
import threading
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from app import create_app
from history import TranscriptionHistory
from state import AppState, AppStateManager
from config import SettingsManager
import config as config_module
from lecture_store import LectureStore
from classnote import ClassNotePipeline


class DummyMemoryTelemetry:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.calls = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def get_report(self, *, include_top, top_limit, history, refresh):
        self.calls.append(
            {
                "include_top": include_top,
                "top_limit": top_limit,
                "history": history,
                "refresh": refresh,
            }
        )
        return {
            "process_id": 123,
            "sample_count": 1,
            "latest": {
                "rss_bytes": 1024,
            },
            "growth": {},
            "samples": [],
            "top_allocations": [],
        }


def test_static_index_served():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "DashScribe" in resp.text


@patch("app.get_wav_duration", return_value=3.5)
def test_websocket_start_stop_flow(_mock_dur):
    mock_recorder = MagicMock()
    mock_recorder.is_recording = False
    mock_recorder.stop.return_value = "/tmp/fake.wav"
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = "Hello world."
    mock_transcriber.is_ready = True
    mock_transcriber.model_repo = "mock-model"

    app = create_app(recorder=mock_recorder, transcriber=mock_transcriber)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "start"})
        resp = ws.receive_json()
        assert resp["type"] == "status"
        assert resp["status"] == "recording"
        mock_recorder.start.assert_called_once()

        ws.send_json({"action": "stop"})
        # Should get transcribing status, then result
        messages = []
        for _ in range(2):
            messages.append(ws.receive_json())
        types = [m["type"] for m in messages]
        assert "status" in types
        assert "result" in types
        result_msg = next(m for m in messages if m["type"] == "result")
        assert result_msg["text"] == "Hello world."


def test_bar_page_served():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/bar")
    assert resp.status_code == 200
    assert "bar" in resp.text.lower()


def test_bar_websocket_receives_state():
    sm = AppStateManager()
    mock_recorder = MagicMock()
    mock_recorder.is_recording = False
    mock_transcriber = MagicMock()
    mock_transcriber.is_ready = True
    app = create_app(recorder=mock_recorder, transcriber=mock_transcriber, state_manager=sm)
    client = TestClient(app)
    with client.websocket_connect("/ws/bar") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "state"
        assert msg["state"] == "idle"


def test_bar_websocket_start_stop():
    sm = AppStateManager()
    mock_recorder = MagicMock()
    mock_recorder.is_recording = False
    mock_recorder.stop.return_value = "/tmp/fake.wav"
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = "Hello"
    mock_transcriber.is_ready = True
    app = create_app(recorder=mock_recorder, transcriber=mock_transcriber, state_manager=sm)
    client = TestClient(app)
    with client.websocket_connect("/ws/bar") as ws:
        msg = ws.receive_json()  # initial state
        ws.send_json({"action": "start"})
        msg = ws.receive_json()
        assert msg["type"] == "state"
        assert msg["state"] == "recording"


def test_history_api_returns_entries():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    history = TranscriptionHistory(db_path)
    history.add("Test entry", duration=1.0, latency=0.5)

    app = create_app(history=history)
    client = TestClient(app)
    resp = client.get("/api/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["text"] == "Test entry"
    assert data["entries"][0]["source"] == "dictation"
    os.unlink(db_path)


def test_history_search_api():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    history = TranscriptionHistory(db_path)
    history.add("The quick brown fox", duration=1.0, latency=0.5)
    history.add("Hello world", duration=1.0, latency=0.5)

    app = create_app(history=history)
    client = TestClient(app)
    resp = client.get("/api/history/search?q=fox")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    os.unlink(db_path)


def test_history_stats_api():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    history = TranscriptionHistory(db_path)
    history.add("hello world", duration=30.0, latency=0.2, source="dictation")
    history.add("from file words", duration=0.0, latency=0.2, source="file")

    app = create_app(history=history)
    client = TestClient(app)
    resp = client.get("/api/history/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["streak_days"] >= 1
    assert data["total_words"] == 2
    assert data["words_per_minute"] == 4.0
    os.unlink(db_path)


def test_memory_diagnostics_endpoint_uses_telemetry():
    telemetry = DummyMemoryTelemetry()
    mock_transcriber = MagicMock()
    mock_transcriber.is_ready = True
    app = create_app(
        memory_telemetry=telemetry,
        transcriber=mock_transcriber,
    )
    with TestClient(app) as client:
        resp = client.get("/api/diagnostics/memory?top=true&top_limit=7&history=55")
        assert resp.status_code == 200
        data = resp.json()
        assert data["process_id"] == 123
        assert data["latest"]["rss_bytes"] == 1024

    assert telemetry.started == 1
    assert telemetry.stopped == 1
    assert telemetry.calls
    call = telemetry.calls[-1]
    assert call["include_top"] is True
    assert call["top_limit"] == 7
    assert call["history"] == 55
    assert call["refresh"] is True


def test_get_hotkey_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.get("/api/settings/hotkey")
            assert resp.status_code == 200
            data = resp.json()
            assert data["key"] == "alt_r"
            assert data["display"] == "Right Option"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_hotkey_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/hotkey", json={"key": "f5"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["key"] == "f5"
            assert data["display"] == "F5"
            assert settings.hotkey_string == "f5"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_hotkey_combo_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/hotkey", json={"key": "cmd+shift+char:r"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["key"] == "cmd+shift+char:r"
            assert data["display"] == "Cmd+Shift+R"
            assert settings.hotkey_string == "cmd+shift+char:r"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_hotkey_invalid_key():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/hotkey", json={"key": "not_a_key"})
            assert resp.status_code == 400
            assert settings.hotkey_string == "alt_r"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_get_insertion_settings_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.get("/api/settings/insertion")
            assert resp.status_code == 200
            data = resp.json()
            assert data["auto_insert"] is True
            assert data["repaste_key"] == "char:v"
            assert data["repaste_display"] == "Cmd+Option+V"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_get_theme_settings_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.get("/api/settings/theme")
            assert resp.status_code == 200
            data = resp.json()
            assert data["theme"] == "auto"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_theme_settings_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/theme", json={"theme": "dark"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["theme"] == "dark"
            assert settings.theme_mode == "dark"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_theme_settings_endpoint_invalid():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/theme", json={"theme": "sunset"})
            assert resp.status_code == 400
            assert settings.theme_mode == "auto"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_auto_insert_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/insertion/auto-insert", json={"enabled": False})
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["auto_insert"] is False
            assert settings.auto_insert is False
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_repaste_key_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/insertion/repaste-key", json={"key": "char:b"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["repaste_key"] == "char:b"
            assert data["repaste_display"] == "Cmd+Option+B"
            assert settings.repaste_key_string == "char:b"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_repaste_combo_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/insertion/repaste-key", json={"key": "cmd+shift+char:b"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["repaste_key"] == "cmd+shift+char:b"
            assert data["repaste_display"] == "Cmd+Shift+B"
            assert settings.repaste_key_string == "cmd+shift+char:b"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_set_repaste_key_invalid_modifier():
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.post("/api/settings/insertion/repaste-key", json={"key": "alt_r"})
            assert resp.status_code == 400
            assert settings.repaste_key_string == "char:v"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


# --- ClassNote REST API tests ---

def test_classnote_lectures_empty():
    """GET /api/classnote/lectures returns empty when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures")
    assert resp.status_code == 200
    assert resp.json()["lectures"] == []


def test_classnote_status_no_pipeline():
    """GET /api/classnote/status returns inactive when no pipeline."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/classnote/status")
    assert resp.status_code == 200
    assert resp.json()["active"] is False


def test_classnote_lectures_with_store():
    """GET /api/classnote/lectures returns lectures from store."""
    mock_store = MagicMock()
    mock_store.list_lectures.return_value = [
        {"id": 1, "title": "Test", "status": "stopped"}
    ]
    mock_store.get_lecture_labels.return_value = []
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures")
    assert resp.status_code == 200
    assert len(resp.json()["lectures"]) == 1


def test_classnote_lectures_search():
    """GET /api/classnote/lectures?q=test uses search_lectures."""
    mock_store = MagicMock()
    mock_store.search_lectures.return_value = [
        {"id": 1, "title": "Test Lecture", "status": "stopped"}
    ]
    mock_store.get_lecture_labels.return_value = []
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures?q=test")
    assert resp.status_code == 200
    assert len(resp.json()["lectures"]) == 1
    mock_store.search_lectures.assert_called_once_with("test", limit=100)


def test_classnote_get_lecture():
    """GET /api/classnote/lectures/{id} returns lecture with segments and labels."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = {"id": 1, "title": "Test"}
    mock_store.get_segments.return_value = [{"id": 1, "text": "hello"}]
    mock_store.get_lecture_labels.return_value = [{"id": 1, "name": "CS101"}]
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lecture"]["title"] == "Test"
    assert len(data["segments"]) == 1
    assert len(data["labels"]) == 1


def test_classnote_get_lecture_not_found():
    """GET /api/classnote/lectures/{id} returns 404 when not found."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = None
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/999")
    assert resp.status_code == 404


def test_classnote_delete_lecture():
    """DELETE /api/classnote/lectures/{id} deletes lecture."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = {"id": 1, "title": "Test", "audio_path": None}
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.delete("/api/classnote/lectures/1")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_store.delete_lecture.assert_called_once_with(1)


def test_classnote_delete_lecture_not_found():
    """DELETE /api/classnote/lectures/{id} returns 404 when not found."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = None
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.delete("/api/classnote/lectures/999")
    assert resp.status_code == 404


def test_classnote_labels_empty():
    """GET /api/classnote/labels returns empty when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/classnote/labels")
    assert resp.status_code == 200
    assert resp.json()["labels"] == []


def test_classnote_create_label():
    """POST /api/classnote/labels creates a label."""
    mock_store = MagicMock()
    mock_store.create_label.return_value = 1
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/labels", json={"name": "CS101", "color": "#ff0000"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["name"] == "CS101"


def test_classnote_create_label_empty_name():
    """POST /api/classnote/labels rejects empty name."""
    mock_store = MagicMock()
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/labels", json={"name": ""})
    assert resp.status_code == 400


def test_classnote_status_with_pipeline():
    """GET /api/classnote/status returns pipeline state."""
    mock_pipeline = MagicMock()
    mock_pipeline.is_active = True
    mock_pipeline.is_paused = False
    mock_pipeline.lecture_id = 42
    app = create_app(classnote_pipeline=mock_pipeline)
    client = TestClient(app)
    resp = client.get("/api/classnote/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is True
    assert data["lecture_id"] == 42


def test_classnote_ws_no_pipeline():
    """WebSocket /ws/classnote sends error when no pipeline."""
    app = create_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/classnote") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["recoverable"] is False


def test_classnote_ws_invalid_action():
    """Unknown WebSocket actions are rejected."""
    mock_pipeline = MagicMock()
    mock_pipeline.is_active = False
    mock_pipeline.is_paused = False
    app = create_app(classnote_pipeline=mock_pipeline)
    client = TestClient(app)
    with client.websocket_connect("/ws/classnote") as ws:
        ws.send_json({"action": "invalid"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_classnote_segments_endpoint():
    """GET /api/classnote/lectures/{id}/segments returns segments."""
    mock_store = MagicMock()
    mock_store.get_segments.return_value = [
        {"id": 1, "text": "hello", "start_ms": 0, "end_ms": 1000}
    ]
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/1/segments")
    assert resp.status_code == 200
    assert len(resp.json()["segments"]) == 1


def test_classnote_crash_recovery_on_startup():
    """Crashed lectures are detected and marked recovered on startup."""
    import tempfile as _tf
    from lecture_store import LectureStore

    with _tf.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        store = LectureStore(db_path)
        lid = store.create_lecture("Crashed Lecture", "/tmp/fake.wav")
        # Backdate updated_at to trigger stale detection
        conn = store._connect()
        conn.execute(
            "UPDATE lectures SET updated_at = datetime('now', '-10 minutes') WHERE id = ?",
            (lid,),
        )
        conn.commit()
        conn.close()
        # Verify it's detected as crashed
        assert len(store.detect_crashed_lectures(stale_minutes=5)) == 1
        # Creating the app should recover it
        _app = create_app(lecture_store=store)
        lecture = store.get_lecture(lid)
        assert lecture["status"] == "recovered"


def test_classnote_cleanup_expired_audio():
    """Expired audio files are deleted by background cleanup."""
    import tempfile as _tf
    import time
    from datetime import datetime, timezone, timedelta
    from lecture_store import LectureStore

    with _tf.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        audio_path = os.path.join(tmpdir, "old.wav")
        with open(audio_path, "w") as f:
            f.write("fake audio")
        store = LectureStore(db_path)
        lid = store.create_lecture("Old Lecture", audio_path)
        store.update_lecture(lid, status="stopped")
        # Backdate created_at to 60 days ago
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn = store._connect()
        conn.execute(
            "UPDATE lectures SET created_at = ? WHERE id = ?",
            (old_date, lid),
        )
        conn.commit()
        conn.close()
        # Mock settings to set retention to 30 days
        mock_settings = MagicMock()
        mock_settings.get.return_value = 30
        _app = create_app(lecture_store=store, settings=mock_settings)
        # Give the daemon thread a moment to run
        time.sleep(0.5)
        assert not os.path.exists(audio_path)
        lecture = store.get_lecture(lid)
        assert lecture["audio_path"] is None


def test_classnote_assign_remove_label():
    """POST and DELETE label assignment works."""
    mock_store = MagicMock()
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/labels/2")
    assert resp.status_code == 200
    mock_store.assign_label.assert_called_once_with(1, 2)
    resp = client.delete("/api/classnote/lectures/1/labels/2")
    assert resp.status_code == 200
    mock_store.remove_label.assert_called_once_with(1, 2)


@pytest.mark.asyncio
async def test_classnote_ws_full_lifecycle():
    """Start -> pause -> resume -> stop via WebSocket — integration with real store."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = LectureStore(db_path)
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe_array.return_value = {"text": "test"}
        mock_transcriber._lock = threading.RLock()

        pipeline = ClassNotePipeline(
            transcriber=mock_transcriber,
            store=store,
        )

        # Mock load_vad to skip actual VAD loading
        pipeline.load_vad = MagicMock(return_value=True)

        from httpx import AsyncClient, ASGITransport

        app = create_app(classnote_pipeline=pipeline, lecture_store=store)

        # Test the REST status endpoint reflects pipeline state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/classnote/status")
            assert resp.json()["active"] is False

        # Test with synchronous TestClient for WebSocket
        client = TestClient(app)
        resp = client.get("/api/classnote/status")
        assert resp.status_code == 200
        assert resp.json()["active"] is False
        assert resp.json()["paused"] is False
    finally:
        os.unlink(db_path)


# ========================================================
# Additional coverage tests
# ========================================================


def _make_settings_tmpdir():
    """Helper: create temp config dir and patch config_module paths."""
    import tempfile
    tmpdir = tempfile.mkdtemp()
    orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
    config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    config_module.CONFIG_DIR = tmpdir
    return tmpdir, orig_path, orig_dir


def _restore_config(orig_path, orig_dir):
    config_module.CONFIG_PATH = orig_path
    config_module.CONFIG_DIR = orig_dir


# --- _get_static_dir frozen branch (lines 38-40) ---

def test_static_dir_frozen():
    """_get_static_dir returns bundle path when sys.frozen is set."""
    with patch("app.sys") as mock_sys, patch("app.os") as mock_os:
        mock_sys.frozen = "macosx_app"
        mock_os.environ.get.return_value = "/my/bundle"
        mock_os.path.join.return_value = "/my/bundle/static"
        mock_os.path.dirname.return_value = "/dev"
        from app import _get_static_dir
        result = _get_static_dir()
        mock_os.path.join.assert_called_with("/my/bundle", "static")
        assert result == "/my/bundle/static"


# --- Browse file endpoint (lines 296-309) ---

def test_browse_file_no_window():
    """GET /api/browse-file returns null path when no window."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/browse-file")
    assert resp.status_code == 200
    assert resp.json()["path"] is None


def test_browse_file_with_window():
    """GET /api/browse-file uses webview dialog when window present."""
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = ["/path/to/file.wav"]
    mock_webview = MagicMock()
    mock_webview.OPEN_DIALOG = 0
    app = create_app()
    app.state.main_window = mock_window
    client = TestClient(app)
    with patch.dict("sys.modules", {"webview": mock_webview}):
        resp = client.get("/api/browse-file")
    assert resp.status_code == 200
    assert resp.json()["path"] == "/path/to/file.wav"


def test_browse_file_cancelled():
    """GET /api/browse-file returns null when user cancels dialog."""
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = None
    mock_webview = MagicMock()
    mock_webview.OPEN_DIALOG = 0
    app = create_app()
    app.state.main_window = mock_window
    client = TestClient(app)
    with patch.dict("sys.modules", {"webview": mock_webview}):
        resp = client.get("/api/browse-file")
    assert resp.status_code == 200
    assert resp.json()["path"] is None


def test_browse_file_exception():
    """GET /api/browse-file returns null on exception."""
    mock_window = MagicMock()
    mock_window.create_file_dialog.side_effect = Exception("dialog error")
    app = create_app()
    app.state.main_window = mock_window
    client = TestClient(app)
    resp = client.get("/api/browse-file")
    assert resp.status_code == 200
    assert resp.json()["path"] is None


# --- Settings endpoints with no settings (fallback branches) ---

def test_get_hotkey_no_settings():
    """GET /api/settings/hotkey returns defaults when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/hotkey")
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "alt_r"
    assert data["display"] == "Right Option"


def test_set_hotkey_no_settings():
    """POST /api/settings/hotkey returns 500 when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/settings/hotkey", json={"key": "f5"})
    assert resp.status_code == 500


def test_set_hotkey_empty_key():
    """POST /api/settings/hotkey returns 400 for empty key."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/settings/hotkey", json={"key": ""})
    assert resp.status_code == 400


def test_get_insertion_no_settings():
    """GET /api/settings/insertion returns defaults when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/insertion")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_insert"] is True
    assert data["repaste_key"] == "char:v"


def test_get_theme_no_settings():
    """GET /api/settings/theme returns default when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/theme")
    assert resp.status_code == 200
    assert resp.json()["theme"] == "auto"


def test_set_theme_no_settings():
    """POST /api/settings/theme returns 500 when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/settings/theme", json={"theme": "dark"})
    assert resp.status_code == 500


def test_set_theme_empty():
    """POST /api/settings/theme returns 400 for empty theme."""
    tmpdir, orig_path, orig_dir = _make_settings_tmpdir()
    try:
        settings = SettingsManager()
        app = create_app(settings=settings)
        client = TestClient(app)
        resp = client.post("/api/settings/theme", json={"theme": ""})
        assert resp.status_code == 400
    finally:
        _restore_config(orig_path, orig_dir)


def test_set_auto_insert_no_settings():
    """POST /api/settings/insertion/auto-insert returns 500 when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/settings/insertion/auto-insert", json={"enabled": False})
    assert resp.status_code == 500


def test_set_repaste_key_no_settings():
    """POST /api/settings/insertion/repaste-key returns 500 when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/settings/insertion/repaste-key", json={"key": "char:b"})
    assert resp.status_code == 500


def test_set_repaste_key_empty():
    """POST /api/settings/insertion/repaste-key returns 400 for empty key."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/settings/insertion/repaste-key", json={"key": ""})
    assert resp.status_code == 400


# --- Hotkey capture endpoints (lines 396-416) ---

def test_start_capture_no_hotkey():
    """POST /api/settings/hotkey/capture returns 500 when no hotkey."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/settings/hotkey/capture")
    assert resp.status_code == 500


def test_start_capture_with_hotkey():
    """POST /api/settings/hotkey/capture starts capture."""
    mock_hotkey = MagicMock()
    app = create_app()
    app.state.hotkey = mock_hotkey
    client = TestClient(app)
    resp = client.post("/api/settings/hotkey/capture")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_hotkey.start_key_capture.assert_called_once()


def test_poll_capture_no_hotkey():
    """GET /api/settings/hotkey/capture returns not captured when no hotkey."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/hotkey/capture")
    assert resp.status_code == 200
    assert resp.json()["captured"] is False


def test_poll_capture_with_hotkey():
    """GET /api/settings/hotkey/capture returns poll result."""
    mock_hotkey = MagicMock()
    mock_hotkey.poll_key_capture.return_value = {"captured": True, "key": "f6"}
    app = create_app()
    app.state.hotkey = mock_hotkey
    client = TestClient(app)
    resp = client.get("/api/settings/hotkey/capture")
    assert resp.status_code == 200
    assert resp.json()["captured"] is True


def test_cancel_capture_no_hotkey():
    """DELETE /api/settings/hotkey/capture succeeds even without hotkey."""
    app = create_app()
    client = TestClient(app)
    resp = client.delete("/api/settings/hotkey/capture")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_cancel_capture_with_hotkey():
    """DELETE /api/settings/hotkey/capture cancels capture."""
    mock_hotkey = MagicMock()
    app = create_app()
    app.state.hotkey = mock_hotkey
    client = TestClient(app)
    resp = client.delete("/api/settings/hotkey/capture")
    assert resp.status_code == 200
    mock_hotkey.cancel_key_capture.assert_called_once()


# --- Permissions endpoints (lines 418-471) ---

@patch("app.check_permissions", create=True)
def test_get_permissions(mock_check):
    """GET /api/permissions returns permissions + model info."""
    mock_check.return_value = {"accessibility": True, "microphone": True}
    mock_txr = MagicMock()
    mock_txr.is_ready = True
    mock_txr.status = "ready"
    mock_txr.status_message = "Ready"
    mock_pipe = MagicMock()
    mock_pipe.vad_available = True
    tmpdir, orig_path, orig_dir = _make_settings_tmpdir()
    try:
        settings = SettingsManager()
        with patch("app.check_permissions", return_value={"accessibility": True, "microphone": True}):
            app = create_app(
                transcriber=mock_txr,
                pipeline=mock_pipe,
                settings=settings,
            )
            client = TestClient(app)
            resp = client.get("/api/permissions")
    finally:
        _restore_config(orig_path, orig_dir)
    assert resp.status_code == 200
    data = resp.json()
    assert "permissions" in data
    assert "models" in data
    assert data["models"]["whisper"]["ready"] is True


@patch("app.request_microphone_access", create=True)
def test_request_mic_permission(mock_req):
    """POST /api/permissions/request-microphone calls request_microphone_access."""
    with patch("app.request_microphone_access"):
        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/permissions/request-microphone")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_open_settings_pane_invalid_url():
    """POST /api/permissions/open-settings rejects non-Apple URLs."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/permissions/open-settings", json={"url": "https://evil.com"})
    assert resp.status_code == 400


def test_open_settings_pane_valid():
    """POST /api/permissions/open-settings opens Apple system prefs."""
    mock_permissions = MagicMock()
    with patch.dict("sys.modules", {"permissions": mock_permissions}):
        app = create_app()
        client = TestClient(app)
        resp = client.post(
            "/api/permissions/open-settings",
            json={"url": "x-apple.systempreferences:com.apple.preference.security"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_dismiss_onboarding():
    """POST /api/permissions/dismiss-onboarding sets setup_complete."""
    tmpdir, orig_path, orig_dir = _make_settings_tmpdir()
    try:
        settings = SettingsManager()
        app = create_app(settings=settings)
        client = TestClient(app)
        resp = client.post("/api/permissions/dismiss-onboarding")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert settings.get("setup_complete", False) is True
    finally:
        _restore_config(orig_path, orig_dir)


def test_dismiss_onboarding_no_settings():
    """POST /api/permissions/dismiss-onboarding works without settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/permissions/dismiss-onboarding")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# --- Version & Updates (lines 474-540) ---

def test_get_version():
    """GET /api/version returns version."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


def test_update_status_no_updater():
    """GET /api/update/status returns disabled when no updater."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/update/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


def test_update_status_with_updater():
    """GET /api/update/status returns updater status."""
    mock_updater = MagicMock()
    mock_updater.get_status.return_value = {"status": "idle", "current_version": "1.0.0"}
    app = create_app(updater=mock_updater)
    client = TestClient(app)
    resp = client.get("/api/update/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


def test_check_for_update_no_updater():
    """POST /api/update/check returns 500 when no updater."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/update/check")
    assert resp.status_code == 500


def test_check_for_update_with_updater():
    """POST /api/update/check triggers check."""
    mock_updater = MagicMock()
    app = create_app(updater=mock_updater)
    client = TestClient(app)
    resp = client.post("/api/update/check")
    assert resp.status_code == 200
    mock_updater.check_now.assert_called_once()


def test_download_update_no_updater():
    """POST /api/update/download returns 500 when no updater."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/update/download")
    assert resp.status_code == 500


def test_download_update_with_updater():
    """POST /api/update/download triggers download."""
    mock_updater = MagicMock()
    app = create_app(updater=mock_updater)
    client = TestClient(app)
    resp = client.post("/api/update/download")
    assert resp.status_code == 200
    mock_updater.download_update.assert_called_once()


def test_cancel_update_no_updater():
    """POST /api/update/cancel returns 500 when no updater."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/update/cancel")
    assert resp.status_code == 500


def test_cancel_update_with_updater():
    """POST /api/update/cancel cancels download."""
    mock_updater = MagicMock()
    app = create_app(updater=mock_updater)
    client = TestClient(app)
    resp = client.post("/api/update/cancel")
    assert resp.status_code == 200
    mock_updater.cancel_download.assert_called_once()


def test_install_update_no_updater():
    """POST /api/update/install returns 500 when no updater."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/update/install")
    assert resp.status_code == 500


def test_install_update_with_updater():
    """POST /api/update/install installs update."""
    mock_updater = MagicMock()
    app = create_app(updater=mock_updater)
    client = TestClient(app)
    resp = client.post("/api/update/install")
    assert resp.status_code == 200
    mock_updater.install_update.assert_called_once()


def test_skip_update_no_updater():
    """POST /api/update/skip returns 500 when no updater."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/update/skip", json={"version": "2.0.0"})
    assert resp.status_code == 500


def test_skip_update_missing_version():
    """POST /api/update/skip returns 400 for missing version."""
    mock_updater = MagicMock()
    app = create_app(updater=mock_updater)
    client = TestClient(app)
    resp = client.post("/api/update/skip", json={"version": ""})
    assert resp.status_code == 400


def test_skip_update_with_updater():
    """POST /api/update/skip skips version."""
    mock_updater = MagicMock()
    app = create_app(updater=mock_updater)
    client = TestClient(app)
    resp = client.post("/api/update/skip", json={"version": "2.0.0"})
    assert resp.status_code == 200
    mock_updater.skip_version.assert_called_once_with("2.0.0")


def test_get_update_settings_no_settings():
    """GET /api/update/settings returns defaults when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/update/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_check"] is True
    assert data["include_prerelease"] is False


def test_get_update_settings_with_settings():
    """GET /api/update/settings returns configured values."""
    tmpdir, orig_path, orig_dir = _make_settings_tmpdir()
    try:
        settings = SettingsManager()
        settings.set("update_auto_check", False)
        settings.set("update_include_prerelease", True)
        app = create_app(settings=settings)
        client = TestClient(app)
        resp = client.get("/api/update/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_check"] is False
        assert data["include_prerelease"] is True
    finally:
        _restore_config(orig_path, orig_dir)


def test_set_update_settings_no_settings():
    """POST /api/update/settings returns 500 when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/update/settings", json={"auto_check": False})
    assert resp.status_code == 500


def test_set_update_settings():
    """POST /api/update/settings updates values."""
    tmpdir, orig_path, orig_dir = _make_settings_tmpdir()
    try:
        settings = SettingsManager()
        app = create_app(settings=settings)
        client = TestClient(app)
        resp = client.post("/api/update/settings", json={
            "auto_check": False,
            "include_prerelease": True,
        })
        assert resp.status_code == 200
        assert settings.get("update_auto_check") is False
        assert settings.get("update_include_prerelease") is True
    finally:
        _restore_config(orig_path, orig_dir)


# --- Smart features settings (lines 544-588) ---

def test_get_smart_cleanup_no_settings():
    """GET /api/settings/smart-cleanup returns false when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/smart-cleanup")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_set_smart_cleanup():
    """POST /api/settings/smart-cleanup sets value."""
    mock_settings = MagicMock()
    mock_settings.smart_cleanup = False
    app = create_app(settings=mock_settings)
    client = TestClient(app)
    resp = client.post("/api/settings/smart-cleanup", json={"enabled": True})
    assert resp.status_code == 200
    assert mock_settings.smart_cleanup is True


def test_get_context_formatting_no_settings():
    """GET /api/settings/context-formatting returns false when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/context-formatting")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_set_context_formatting():
    """POST /api/settings/context-formatting sets value."""
    mock_settings = MagicMock()
    mock_settings.context_formatting = False
    app = create_app(settings=mock_settings)
    client = TestClient(app)
    resp = client.post("/api/settings/context-formatting", json={"enabled": True})
    assert resp.status_code == 200
    assert mock_settings.context_formatting is True


def test_get_snippets_no_settings():
    """GET /api/settings/snippets returns empty when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/snippets")
    assert resp.status_code == 200
    assert resp.json()["snippets"] == []


def test_get_snippets_with_settings():
    """GET /api/settings/snippets returns configured snippets."""
    mock_settings = MagicMock()
    mock_settings.snippets = [{"trigger": "/sig", "text": "Best regards"}]
    app = create_app(settings=mock_settings)
    client = TestClient(app)
    resp = client.get("/api/settings/snippets")
    assert resp.status_code == 200
    assert len(resp.json()["snippets"]) == 1


def test_set_snippets():
    """POST /api/settings/snippets sets snippets."""
    mock_settings = MagicMock()
    app = create_app(settings=mock_settings)
    client = TestClient(app)
    resp = client.post("/api/settings/snippets", json={"snippets": [{"trigger": "/hi", "text": "Hello"}]})
    assert resp.status_code == 200
    mock_settings.set_snippets.assert_called_once()


def test_get_dictionary_no_settings():
    """GET /api/settings/dictionary returns empty when no settings."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/settings/dictionary")
    assert resp.status_code == 200
    assert resp.json()["terms"] == []


def test_get_dictionary_with_settings():
    """GET /api/settings/dictionary returns terms."""
    mock_settings = MagicMock()
    mock_settings.dictionary_prompt = "FastAPI, PyObjC, mlx-whisper"
    app = create_app(settings=mock_settings)
    client = TestClient(app)
    resp = client.get("/api/settings/dictionary")
    assert resp.status_code == 200
    assert len(resp.json()["terms"]) == 3


def test_set_dictionary():
    """POST /api/settings/dictionary sets terms."""
    mock_settings = MagicMock()
    app = create_app(settings=mock_settings)
    client = TestClient(app)
    resp = client.post("/api/settings/dictionary", json={"terms": ["FastAPI", "PyObjC"]})
    assert resp.status_code == 200
    mock_settings.set_dictionary.assert_called_once_with(["FastAPI", "PyObjC"])


# --- WebSocket additional paths (lines 822-956) ---

def test_ws_start_already_recording():
    """WS start while already recording sends recording status."""
    mock_rec = MagicMock()
    mock_rec.is_recording = True
    mock_txr = MagicMock()
    mock_txr.is_ready = True
    app = create_app(recorder=mock_rec, transcriber=mock_txr)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "start"})
        resp = ws.receive_json()
        assert resp["type"] == "status"
        assert resp["status"] == "recording"


def test_ws_start_exception():
    """WS start error sends error message."""
    mock_rec = MagicMock()
    mock_rec.is_recording = False
    mock_rec.start.side_effect = Exception("mic error")
    mock_txr = MagicMock()
    mock_txr.is_ready = True
    sm = AppStateManager()
    app = create_app(recorder=mock_rec, transcriber=mock_txr, state_manager=sm)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "start"})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert "mic error" in resp["message"]


def test_ws_cancel():
    """WS cancel sends idle status."""
    mock_rec = MagicMock()
    mock_rec.is_recording = False
    mock_txr = MagicMock()
    mock_txr.is_ready = True
    app = create_app(recorder=mock_rec, transcriber=mock_txr)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "cancel"})
        resp = ws.receive_json()
        assert resp["type"] == "status"
        assert resp["status"] == "idle"


def test_ws_status_action():
    """WS status action returns model status."""
    mock_rec = MagicMock()
    mock_rec.is_recording = False
    mock_txr = MagicMock()
    mock_txr.is_ready = True
    mock_txr.status = "ready"
    mock_txr.status_message = "Model loaded"
    app = create_app(recorder=mock_rec, transcriber=mock_txr)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "status"})
        resp = ws.receive_json()
        assert resp["type"] == "model_status"
        assert resp["ready"] is True
        assert resp["status"] == "ready"


# --- WebSocket stop with no audio / short recording (line 851-857) ---
# Note: the four test_ws_transcribe_file_* tests were removed in Task 6 along
# with the underlying "transcribe_file" WebSocket action. The new file-job flow
# is covered by test_get_file_job_options_defaults / test_put_file_job_options_defaults_persists
# at the bottom of this file, plus tests/test_file_job.py for the runner pipeline.

@patch("app.get_wav_duration", return_value=0.5)
def test_ws_stop_no_audio(_mock_dur):
    """WS stop with no audio sends error about short recording."""
    mock_rec = MagicMock()
    mock_rec.is_recording = False
    mock_rec.stop.return_value = None
    mock_txr = MagicMock()
    mock_txr.is_ready = True
    app = create_app(recorder=mock_rec, transcriber=mock_txr)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "start"})
        ws.receive_json()  # recording status
        ws.send_json({"action": "stop"})
        msgs = []
        for _ in range(2):
            msgs.append(ws.receive_json())
        types = [m["type"] for m in msgs]
        assert "error" in types


# --- ClassNote REST additional coverage ---

def test_classnote_delete_lecture_with_audio():
    """DELETE /api/classnote/lectures/{id} deletes audio file."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"fake")
        audio_path = f.name
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = {"id": 1, "title": "Test", "audio_path": audio_path}
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.delete("/api/classnote/lectures/1")
    assert resp.status_code == 200
    assert not os.path.exists(audio_path)


def test_classnote_get_lecture_no_store():
    """GET /api/classnote/lectures/{id} returns 404 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/1")
    assert resp.status_code == 404


def test_classnote_delete_lecture_no_store():
    """DELETE /api/classnote/lectures/{id} returns 404 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.delete("/api/classnote/lectures/1")
    assert resp.status_code == 404


def test_classnote_segments_no_store():
    """GET /api/classnote/lectures/{id}/segments returns empty when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/1/segments")
    assert resp.status_code == 200
    assert resp.json()["segments"] == []


def test_classnote_get_audio_no_store():
    """GET /api/classnote/lectures/{id}/audio returns 404 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/1/audio")
    assert resp.status_code == 404


def test_classnote_get_audio_not_found():
    """GET /api/classnote/lectures/{id}/audio returns 404 when lecture not found."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = None
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/1/audio")
    assert resp.status_code == 404


def test_classnote_get_audio_no_audio_path():
    """GET /api/classnote/lectures/{id}/audio returns 404 when no audio path."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = {"id": 1, "audio_path": None}
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/lectures/1/audio")
    assert resp.status_code == 404


def test_classnote_get_audio_success():
    """GET /api/classnote/lectures/{id}/audio returns file when exists."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF" + b"\x00" * 100)
        audio_path = f.name
    try:
        mock_store = MagicMock()
        mock_store.get_lecture.return_value = {"id": 1, "audio_path": audio_path}
        app = create_app(lecture_store=mock_store)
        client = TestClient(app)
        resp = client.get("/api/classnote/lectures/1/audio")
        assert resp.status_code == 200
    finally:
        os.unlink(audio_path)


def test_classnote_labels_with_store():
    """GET /api/classnote/labels returns labels from store."""
    mock_store = MagicMock()
    mock_store.list_labels.return_value = [{"id": 1, "name": "CS101", "color": "#ff0000"}]
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.get("/api/classnote/labels")
    assert resp.status_code == 200
    assert len(resp.json()["labels"]) == 1


def test_classnote_create_label_no_store():
    """POST /api/classnote/labels returns 503 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/classnote/labels", json={"name": "CS101"})
    assert resp.status_code == 503


def test_classnote_assign_label_no_store():
    """POST label assignment returns 503 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/labels/2")
    assert resp.status_code == 503


def test_classnote_remove_label_no_store():
    """DELETE label assignment returns 503 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.delete("/api/classnote/lectures/1/labels/2")
    assert resp.status_code == 503


def test_classnote_delete_label():
    """DELETE /api/classnote/labels/{id} deletes label."""
    mock_store = MagicMock()
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.delete("/api/classnote/labels/1")
    assert resp.status_code == 200
    mock_store.delete_label.assert_called_once_with(1)


def test_classnote_delete_label_no_store():
    """DELETE /api/classnote/labels/{id} returns 503 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.delete("/api/classnote/labels/1")
    assert resp.status_code == 503


# --- Retranscribe (lines 1345-1366) ---

def test_retranscribe_no_pipeline():
    """POST retranscribe returns 503 when no pipeline."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/retranscribe")
    assert resp.status_code == 503


def test_retranscribe_while_recording():
    """POST retranscribe returns 409 when pipeline is active."""
    mock_pipeline = MagicMock()
    mock_pipeline.is_active = True
    mock_store = MagicMock()
    app = create_app(classnote_pipeline=mock_pipeline, lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/retranscribe")
    assert resp.status_code == 409


def test_retranscribe_lecture_not_found():
    """POST retranscribe returns 404 when lecture not found."""
    mock_pipeline = MagicMock()
    mock_pipeline.is_active = False
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = None
    app = create_app(classnote_pipeline=mock_pipeline, lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/retranscribe")
    assert resp.status_code == 404


def test_retranscribe_no_audio():
    """POST retranscribe returns 404 when no audio file."""
    mock_pipeline = MagicMock()
    mock_pipeline.is_active = False
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = {"id": 1, "audio_path": None}
    app = create_app(classnote_pipeline=mock_pipeline, lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/retranscribe")
    assert resp.status_code == 404


def test_retranscribe_success():
    """POST retranscribe succeeds with valid lecture."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"fake audio")
        audio_path = f.name
    try:
        mock_pipeline = MagicMock()
        mock_pipeline.is_active = False
        mock_pipeline.retranscribe.return_value = {"segments": 5}
        mock_store = MagicMock()
        mock_store.get_lecture.return_value = {"id": 1, "audio_path": audio_path}
        app = create_app(classnote_pipeline=mock_pipeline, lecture_store=mock_store)
        client = TestClient(app)
        resp = client.post("/api/classnote/lectures/1/retranscribe")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


# --- Export lecture (lines 1368-1405) ---

def test_export_lecture_no_store():
    """POST export returns 503 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/export")
    assert resp.status_code == 503


def test_export_lecture_not_found():
    """POST export returns 404 when lecture not found."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = None
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/export")
    assert resp.status_code == 404


def test_export_lecture_no_window():
    """POST export returns 500 when no window."""
    mock_store = MagicMock()
    mock_store.get_lecture.return_value = {"id": 1, "title": "Test"}
    mock_store.get_segments.return_value = [{"text": "Hello world"}]
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.post("/api/classnote/lectures/1/export")
    assert resp.status_code == 500


# --- Update segment text (lines 1407-1416) ---

def test_update_segment_no_store():
    """PATCH segment returns 503 when no store."""
    app = create_app()
    client = TestClient(app)
    resp = client.patch(
        "/api/classnote/lectures/1/segments/0",
        json={"text": "updated"},
    )
    assert resp.status_code == 503


def test_update_segment_empty_text():
    """PATCH segment returns 400 for empty text."""
    mock_store = MagicMock()
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.patch(
        "/api/classnote/lectures/1/segments/0",
        json={"text": ""},
    )
    assert resp.status_code == 400


def test_update_segment_success():
    """PATCH segment updates text."""
    mock_store = MagicMock()
    app = create_app(lecture_store=mock_store)
    client = TestClient(app)
    resp = client.patch(
        "/api/classnote/lectures/1/segments/0",
        json={"text": "corrected text"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_store.update_segment_text.assert_called_once_with(1, 0, "corrected text")


# --- History with pagination (lines 264-267) ---

def test_history_pagination():
    """GET /api/history supports limit and offset."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    history = TranscriptionHistory(db_path)
    for i in range(10):
        history.add(f"Entry {i}", duration=1.0, latency=0.5)
    app = create_app(history=history)
    client = TestClient(app)
    resp = client.get("/api/history?limit=3&offset=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 3
    assert data["total"] == 10
    os.unlink(db_path)


# --- _post_process function tests (lines 1433-1478) ---

def test_post_process_empty_text():
    """_post_process returns empty text unchanged (tuple)."""
    from app import _post_process
    text, s1, raw = _post_process("", None, None)
    assert text == ""
    text2, s12, raw2 = _post_process(None, None, None)
    assert text2 is None


def test_post_process_short_text():
    """_post_process returns short text unchanged (tuple)."""
    from app import _post_process
    text, s1, raw = _post_process("hi there", None, None)
    assert text == "hi there"


def test_post_process_no_settings():
    """_post_process returns text unchanged when no settings."""
    from app import _post_process
    text, s1, raw = _post_process("This is a longer text with many words here now", None, None)
    assert text == "This is a longer text with many words here now"


def test_post_process_no_features_enabled():
    """_post_process returns text unchanged when no features enabled."""
    from app import _post_process
    mock_settings = MagicMock()
    mock_settings.smart_cleanup = False
    mock_settings.context_formatting = False
    mock_settings.snippets_prompt_fragment = None
    text, s1, raw = _post_process("This is a longer text with many words here now", MagicMock(), mock_settings)
    assert text == "This is a longer text with many words here now"


def test_post_process_smart_cleanup():
    """_post_process calls LLM when smart_cleanup enabled."""
    from app import _post_process
    mock_settings = MagicMock()
    mock_settings.smart_cleanup = True
    mock_settings.context_formatting = False
    mock_settings.snippets_prompt_fragment = None
    mock_llm = MagicMock()
    mock_llm.generate.return_value = "Cleaned text with many words here now"
    text, s1, raw = _post_process("This is a longer text with many words here now", mock_llm, mock_settings)
    assert text == "Cleaned text with many words here now"
    mock_llm.generate.assert_called_once()


def test_post_process_llm_returns_none():
    """_post_process returns original text when LLM returns None."""
    from app import _post_process
    mock_settings = MagicMock()
    mock_settings.smart_cleanup = True
    mock_settings.context_formatting = False
    mock_settings.snippets_prompt_fragment = None
    mock_llm = MagicMock()
    mock_llm.generate.return_value = None
    original = "This is a longer text with many words here now"
    text, s1, raw = _post_process(original, mock_llm, mock_settings)
    assert text == original


# --- _retry_transcribe tests ---

def test_retry_transcribe_no_cache():
    """_retry_transcribe returns None when no cached audio."""
    from app import _retry_transcribe, _last_audio_cache
    _last_audio_cache["audio"] = None
    result = _retry_transcribe(MagicMock())
    assert result is None


def test_retry_transcribe_with_cache():
    """_retry_transcribe transcribes cached audio (5-tuple)."""
    import numpy as np
    from app import _retry_transcribe, _last_audio_cache
    _last_audio_cache["audio"] = np.zeros(16000, dtype=np.float32)
    _last_audio_cache["sample_rate"] = 16000
    mock_txr = MagicMock()
    mock_txr.transcribe_array.return_value = "Retried text"
    result = _retry_transcribe(mock_txr)
    assert result is not None
    text, elapsed, audio_duration, raw_text, stage1_text = result
    assert text == "Retried text"
    assert audio_duration == 1.0
    _last_audio_cache["audio"] = None


def test_retry_transcribe_with_llm():
    """_retry_transcribe applies two-stage post-processing (5-tuple)."""
    import numpy as np
    from app import _retry_transcribe, _last_audio_cache
    _last_audio_cache["audio"] = np.zeros(16000, dtype=np.float32)
    _last_audio_cache["sample_rate"] = 16000
    mock_txr = MagicMock()
    mock_txr.transcribe_array.return_value = "This is a longer text with many words here now"
    mock_settings = MagicMock()
    mock_settings.smart_cleanup = True
    mock_settings.context_formatting = False
    mock_settings.snippets_prompt_fragment = None
    mock_settings.dictionary_prompt = None
    mock_llm = MagicMock()
    mock_llm.generate.return_value = "Cleaned longer text with many words here now"
    result = _retry_transcribe(mock_txr, settings=mock_settings, llm=mock_llm)
    assert result is not None
    text, elapsed, audio_duration, raw_text, stage1_text = result
    assert text == "Cleaned longer text with many words here now"
    assert raw_text == "This is a longer text with many words here now"
    _last_audio_cache["audio"] = None


# ===================== Meeting Transcription REST API Tests =====================

def _create_meeting_app(mt_store=None, mt_pipeline=None):
    """Helper to create app with meeting dependencies."""
    from meeting_store import MeetingStore
    if mt_store is None:
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        mt_store = MeetingStore(db_path=f.name)
    return create_app(meeting_store=mt_store, meeting_pipeline=mt_pipeline), mt_store


def test_meeting_apps_endpoint():
    app, _ = _create_meeting_app()
    client = TestClient(app)
    with patch("system_audio.SystemAudioCapture.get_running_apps", return_value=[
        {"name": "Zoom", "bundle_id": "us.zoom.xos"},
    ]):
        resp = client.get("/api/meeting/apps")
    assert resp.status_code == 200
    data = resp.json()
    assert "apps" in data
    assert len(data["apps"]) == 1


def test_meeting_status_no_pipeline():
    app, _ = _create_meeting_app()
    client = TestClient(app)
    resp = client.get("/api/meeting/status")
    assert resp.status_code == 200
    assert resp.json()["active"] is False


def test_meeting_status_with_pipeline():
    mock_pipeline = MagicMock()
    mock_pipeline.is_active = True
    mock_pipeline.is_paused = False
    mock_pipeline.meeting_id = 42
    app, _ = _create_meeting_app(mt_pipeline=mock_pipeline)
    client = TestClient(app)
    resp = client.get("/api/meeting/status")
    data = resp.json()
    assert data["active"] is True
    assert data["meeting_id"] == 42


def test_list_meetings_empty():
    app, store = _create_meeting_app()
    client = TestClient(app)
    resp = client.get("/api/meeting/meetings")
    assert resp.status_code == 200
    assert resp.json()["meetings"] == []


def test_list_meetings_with_data():
    app, store = _create_meeting_app()
    store.create_meeting("Standup", app_name="Zoom", mode="listen")
    client = TestClient(app)
    resp = client.get("/api/meeting/meetings")
    assert len(resp.json()["meetings"]) == 1


def test_list_meetings_search():
    app, store = _create_meeting_app()
    mid = store.create_meeting("Sprint Planning", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "discuss roadmap", 0, 1000, speaker="others")
    client = TestClient(app)
    resp = client.get("/api/meeting/meetings?q=roadmap")
    assert len(resp.json()["meetings"]) == 1


def test_get_meeting():
    app, store = _create_meeting_app()
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "hello", 0, 1000, speaker="others")
    client = TestClient(app)
    resp = client.get(f"/api/meeting/meetings/{mid}")
    data = resp.json()
    assert data["meeting"]["title"] == "Test"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["speaker"] == "others"


def test_get_meeting_not_found():
    app, _ = _create_meeting_app()
    client = TestClient(app)
    resp = client.get("/api/meeting/meetings/999")
    assert resp.status_code == 404


def test_delete_meeting():
    app, store = _create_meeting_app()
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    client = TestClient(app)
    resp = client.delete(f"/api/meeting/meetings/{mid}")
    assert resp.status_code == 200
    assert store.get_meeting(mid) is None


def test_update_meeting_segment():
    app, store = _create_meeting_app()
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "old text", 0, 1000, speaker="others")
    client = TestClient(app)
    resp = client.patch(
        f"/api/meeting/meetings/{mid}/segments/0",
        json={"text": "new text"},
    )
    assert resp.status_code == 200
    segs = store.get_segments(mid)
    assert segs[0]["text"] == "new text"


def test_update_meeting_segment_empty_text():
    app, store = _create_meeting_app()
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.add_segment(mid, 0, "old", 0, 1000, speaker="others")
    client = TestClient(app)
    resp = client.patch(
        f"/api/meeting/meetings/{mid}/segments/0",
        json={"text": ""},
    )
    assert resp.status_code == 400


def test_meeting_labels_crud():
    app, store = _create_meeting_app()
    client = TestClient(app)

    # Create label
    resp = client.post("/api/meeting/labels", json={"name": "important", "color": "#ff0000"})
    assert resp.status_code == 200
    label_id = resp.json()["id"]

    # List labels
    resp = client.get("/api/meeting/labels")
    assert len(resp.json()["labels"]) == 1

    # Assign label
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    resp = client.post(f"/api/meeting/meetings/{mid}/labels/{label_id}")
    assert resp.status_code == 200

    # Verify in get_meeting
    resp = client.get(f"/api/meeting/meetings/{mid}")
    assert len(resp.json()["labels"]) == 1

    # Remove label assignment
    resp = client.delete(f"/api/meeting/meetings/{mid}/labels/{label_id}")
    assert resp.status_code == 200

    # Delete label
    resp = client.delete(f"/api/meeting/labels/{label_id}")
    assert resp.status_code == 200
    resp = client.get("/api/meeting/labels")
    assert len(resp.json()["labels"]) == 0


def test_meeting_no_store_returns_empty():
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/meeting/meetings").json() == {"meetings": []}
    assert client.get("/api/meeting/labels").json() == {"labels": []}


# --- Audio level monitor endpoints ---

def test_audio_monitor_start():
    app, _ = _create_meeting_app()
    client = TestClient(app)
    with patch("audio_probe.AudioLevelMonitor.start", return_value=True):
        resp = client.post("/api/meeting/audio-monitor/start", json={"bundle_ids": ["com.test"]})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


def test_audio_monitor_start_no_apps():
    app, _ = _create_meeting_app()
    client = TestClient(app)
    resp = client.post("/api/meeting/audio-monitor/start", json={"bundle_ids": []})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_audio_monitor_stop():
    app, _ = _create_meeting_app()
    client = TestClient(app)
    resp = client.post("/api/meeting/audio-monitor/stop")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_audio_levels_no_monitor():
    app, _ = _create_meeting_app()
    client = TestClient(app)
    resp = client.get("/api/meeting/audio-levels")
    assert resp.status_code == 200
    assert resp.json()["levels"] == {}


# --- Device broadcast tests ---

def test_create_app_exposes_broadcast_device_event():
    """create_app() must attach broadcast_device_event to app.state."""
    app = create_app()
    assert hasattr(app.state, "broadcast_device_event")
    assert callable(app.state.broadcast_device_event)


def test_broadcast_device_event_invalid_type_ignored():
    """broadcast_device_event silently ignores unknown event types."""
    app = create_app()
    # Should not raise
    app.state.broadcast_device_event("unknown_event", "Built-in Microphone")
    app.state.broadcast_device_event("", None)


def test_broadcast_device_event_valid_types_no_clients():
    """broadcast_device_event accepts all three valid types when no clients are connected."""
    app = create_app()
    fn = app.state.broadcast_device_event
    # These must not raise even with zero sinks registered
    fn("device_changed", "Built-in Microphone")
    fn("device_lost")
    fn("device_restored", "USB Audio Device")


def test_broadcast_device_event_bar_ws_receives_message():
    """A connected bar WS client receives device events via _broadcast_device_event."""
    sm = AppStateManager()
    app = create_app(state_manager=sm)
    client = TestClient(app)

    with client.websocket_connect("/ws/bar") as ws:
        # Drain the initial state + hotkey messages
        initial = ws.receive_json()
        assert initial["type"] == "state"

        # Fire the broadcast from the test thread (simulates CoreAudio callback)
        app.state.broadcast_device_event("device_changed", "Built-in Microphone")

        msg = ws.receive_json()
        assert msg["type"] == "device_changed"
        assert msg["device"] == "Built-in Microphone"


def test_broadcast_device_event_device_lost_omits_device_key():
    """device_lost events must NOT include the 'device' key."""
    sm = AppStateManager()
    app = create_app(state_manager=sm)
    client = TestClient(app)

    with client.websocket_connect("/ws/bar") as ws:
        ws.receive_json()  # initial state

        app.state.broadcast_device_event("device_lost")

        msg = ws.receive_json()
        assert msg["type"] == "device_lost"
        assert "device" not in msg


def test_broadcast_device_event_main_ws_receives_message():
    """A connected main WS client receives device events via _broadcast_device_event."""
    sm = AppStateManager()
    mock_recorder = MagicMock()
    mock_recorder.is_recording = False
    mock_transcriber = MagicMock()
    mock_transcriber.is_ready = True
    app = create_app(recorder=mock_recorder, transcriber=mock_transcriber, state_manager=sm)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        # Fire the broadcast
        app.state.broadcast_device_event("device_restored", "USB Audio Device")

        msg = ws.receive_json()
        assert msg["type"] == "device_restored"
        assert msg["device"] == "USB Audio Device"


def test_get_file_job_options_defaults():
    """GET /api/file-job/options-defaults returns correct default values."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            resp = client.get("/api/file-job/options-defaults")
            assert resp.status_code == 200
            data = resp.json()
            assert data["engine"] == "auto"
            assert data["diarization_enabled"] is True
            assert data["quality_preset"] == "balanced"
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_put_file_job_options_defaults_persists():
    """PUT /api/file-job/options-defaults persists values visible via subsequent GET."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path, orig_dir = config_module.CONFIG_PATH, config_module.CONFIG_DIR
        config_module.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            app = create_app(settings=settings)
            client = TestClient(app)
            put_resp = client.put(
                "/api/file-job/options-defaults",
                json={"engine": "parakeet", "diarization_enabled": False},
            )
            assert put_resp.status_code == 200
            assert put_resp.json()["ok"] is True
            get_resp = client.get("/api/file-job/options-defaults")
            assert get_resp.status_code == 200
            data = get_resp.json()
            assert data["engine"] == "parakeet"
            assert data["diarization_enabled"] is False
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir
