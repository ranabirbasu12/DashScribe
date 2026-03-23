# Floating Bar & Transcription History Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Wispr Flow-inspired floating dictation bar and SQLite-backed transcription history to DashScribe.

**Architecture:** A central state manager coordinates state between the hotkey, recorder, floating bar (second PyWebView window), and main window. The recorder streams amplitude data for real-time waveform rendering. Transcription history is stored in SQLite and displayed in the main window.

**Tech Stack:** Python 3.12, PyWebView (frameless/transparent/on_top), FastAPI WebSocket, SQLite3, HTML5 Canvas

---

### Task 1: State manager module

**Files:**
- Create: `state.py`
- Create: `tests/test_state.py`

**Step 1: Write the failing test**

```python
# tests/test_state.py
from state import AppState, AppStateManager


def test_initial_state_is_idle():
    sm = AppStateManager()
    assert sm.state == AppState.IDLE


def test_set_state_fires_callbacks():
    sm = AppStateManager()
    received = []
    sm.on_state_change(lambda old, new: received.append((old, new)))
    sm.set_state(AppState.RECORDING)
    assert received == [(AppState.IDLE, AppState.RECORDING)]


def test_multiple_callbacks_all_fire():
    sm = AppStateManager()
    a, b = [], []
    sm.on_state_change(lambda old, new: a.append(new))
    sm.on_state_change(lambda old, new: b.append(new))
    sm.set_state(AppState.PROCESSING)
    assert a == [AppState.PROCESSING]
    assert b == [AppState.PROCESSING]


def test_set_same_state_does_not_fire():
    sm = AppStateManager()
    received = []
    sm.on_state_change(lambda old, new: received.append(new))
    sm.set_state(AppState.IDLE)
    assert received == []


def test_push_amplitude():
    sm = AppStateManager()
    sm.push_amplitude(0.5)
    sm.push_amplitude(0.8)
    assert sm.get_amplitudes() == [0.5, 0.8]


def test_get_amplitudes_clears_buffer():
    sm = AppStateManager()
    sm.push_amplitude(0.3)
    sm.get_amplitudes()
    assert sm.get_amplitudes() == []


def test_amplitude_callback_fires():
    sm = AppStateManager()
    received = []
    sm.on_amplitude(lambda val: received.append(val))
    sm.push_amplitude(0.7)
    assert received == [0.7]
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_state.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'state'`

**Step 3: Write minimal implementation**

```python
# state.py
import threading
from enum import Enum


class AppState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"


class AppStateManager:
    """Central state tracker with callback system for UI synchronization."""

    def __init__(self):
        self._state = AppState.IDLE
        self._state_callbacks: list = []
        self._amplitude_callbacks: list = []
        self._amplitudes: list[float] = []
        self._lock = threading.Lock()

    @property
    def state(self) -> AppState:
        return self._state

    def set_state(self, new_state: AppState):
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        for cb in self._state_callbacks:
            try:
                cb(old, new_state)
            except Exception:
                pass

    def on_state_change(self, callback):
        self._state_callbacks.append(callback)

    def push_amplitude(self, value: float):
        with self._lock:
            self._amplitudes.append(value)
        for cb in self._amplitude_callbacks:
            try:
                cb(value)
            except Exception:
                pass

    def get_amplitudes(self) -> list[float]:
        with self._lock:
            amps = self._amplitudes[:]
            self._amplitudes.clear()
            return amps

    def on_amplitude(self, callback):
        self._amplitude_callbacks.append(callback)
```

**Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_state.py -v`

Expected: 7 passed

**Step 5: Commit**

```bash
git add state.py tests/test_state.py
git commit -m "feat: add central state manager with callbacks"
```

---

### Task 2: Transcription history module

**Files:**
- Create: `history.py`
- Create: `tests/test_history.py`

**Step 1: Write the failing test**

```python
# tests/test_history.py
import os
import tempfile
from history import TranscriptionHistory


def make_history(db_path=None):
    if db_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
    return TranscriptionHistory(db_path), db_path


def test_add_and_get_recent():
    h, path = make_history()
    h.add("Hello world", duration=2.5, latency=0.8)
    h.add("Second entry", duration=3.0, latency=0.5)
    entries = h.get_recent(limit=10)
    assert len(entries) == 2
    assert entries[0]["text"] == "Second entry"  # most recent first
    assert entries[1]["text"] == "Hello world"
    os.unlink(path)


def test_get_recent_respects_limit():
    h, path = make_history()
    for i in range(5):
        h.add(f"Entry {i}", duration=1.0, latency=0.5)
    entries = h.get_recent(limit=3)
    assert len(entries) == 3
    os.unlink(path)


def test_get_recent_respects_offset():
    h, path = make_history()
    for i in range(5):
        h.add(f"Entry {i}", duration=1.0, latency=0.5)
    entries = h.get_recent(limit=2, offset=2)
    assert len(entries) == 2
    assert entries[0]["text"] == "Entry 2"
    os.unlink(path)


def test_search():
    h, path = make_history()
    h.add("The quick brown fox", duration=2.0, latency=0.5)
    h.add("Hello world", duration=1.5, latency=0.3)
    h.add("Fox jumps over", duration=1.0, latency=0.4)
    results = h.search("fox")
    assert len(results) == 2
    os.unlink(path)


def test_entry_has_all_fields():
    h, path = make_history()
    h.add("Test", duration=2.5, latency=0.8)
    entry = h.get_recent(limit=1)[0]
    assert "id" in entry
    assert "text" in entry
    assert "timestamp" in entry
    assert entry["duration_seconds"] == 2.5
    assert entry["latency_seconds"] == 0.8
    os.unlink(path)


def test_count():
    h, path = make_history()
    assert h.count() == 0
    h.add("One", duration=1.0, latency=0.5)
    h.add("Two", duration=1.0, latency=0.5)
    assert h.count() == 2
    os.unlink(path)
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_history.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'history'`

**Step 3: Write minimal implementation**

```python
# history.py
import os
import sqlite3
from datetime import datetime, timezone


DEFAULT_DB_PATH = os.path.expanduser("~/.dashscribe/history.db")


class TranscriptionHistory:
    """SQLite-backed transcription history."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    duration_seconds REAL,
                    latency_seconds REAL
                )
            """)

    def add(self, text: str, duration: float = 0.0, latency: float = 0.0):
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transcriptions (text, timestamp, duration_seconds, latency_seconds) VALUES (?, ?, ?, ?)",
                (text, ts, duration, latency),
            )

    def get_recent(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM transcriptions ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM transcriptions WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM transcriptions").fetchone()[0]
```

**Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_history.py -v`

Expected: 6 passed

**Step 5: Commit**

```bash
git add history.py tests/test_history.py
git commit -m "feat: add SQLite transcription history module"
```

---

### Task 3: Add amplitude streaming to recorder

**Files:**
- Modify: `recorder.py`
- Modify: `tests/test_recorder.py`

**Step 1: Write the failing test**

Append to `tests/test_recorder.py`:

```python
def test_audio_callback_fires_amplitude_callback():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    received = []
    rec.on_amplitude = lambda val: received.append(val)
    # Create a chunk with known RMS
    fake_data = np.ones((1600, 1), dtype=np.float32) * 0.5
    rec._audio_callback(fake_data, 1600, None, None)
    assert len(received) == 1
    assert abs(received[0] - 0.5) < 0.01


def test_amplitude_callback_not_called_when_not_recording():
    rec = AudioRecorder()
    rec.is_recording = False
    received = []
    rec.on_amplitude = lambda val: received.append(val)
    fake_data = np.ones((1600, 1), dtype=np.float32) * 0.5
    rec._audio_callback(fake_data, 1600, None, None)
    assert received == []
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_recorder.py::test_audio_callback_fires_amplitude_callback -v`

Expected: FAIL with `AttributeError: 'AudioRecorder' object has no attribute 'on_amplitude'`

**Step 3: Modify recorder.py**

Add `self.on_amplitude = None` to `__init__`, and add amplitude calculation + callback to `_audio_callback`:

In `__init__`, after `self._stream` line, add:

```python
        self.on_amplitude = None  # Optional callback: fn(rms_float)
```

In `_audio_callback`, after `self._chunks.append(indata.copy())`, add:

```python
            if self.on_amplitude is not None:
                rms = float(np.sqrt(np.mean(indata ** 2)))
                self.on_amplitude(rms)
```

The full `_audio_callback` becomes:

```python
    def _audio_callback(self, indata, frames, time, status):
        if status:
            print(f"Audio status: {status}")
        if self.is_recording:
            self._chunks.append(indata.copy())
            if self.on_amplitude is not None:
                rms = float(np.sqrt(np.mean(indata ** 2)))
                self.on_amplitude(rms)
```

**Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_recorder.py -v`

Expected: 7 passed (5 existing + 2 new)

**Step 5: Commit**

```bash
git add recorder.py tests/test_recorder.py
git commit -m "feat: add amplitude streaming callback to recorder"
```

---

### Task 4: Refactor hotkey to use state manager

**Files:**
- Modify: `hotkey.py`
- Modify: `tests/test_hotkey.py`

**Step 1: Write the failing test**

Replace `tests/test_hotkey.py` entirely:

```python
# tests/test_hotkey.py
from unittest.mock import MagicMock, patch
from pynput import keyboard
from hotkey import GlobalHotkey
from state import AppState, AppStateManager


def make_hotkey(model_ready=True):
    rec = MagicMock()
    txr = MagicMock()
    txr.is_ready = model_ready
    sm = AppStateManager()
    history = MagicMock()
    hk = GlobalHotkey(recorder=rec, transcriber=txr, state_manager=sm, history=history)
    return hk, rec, txr, sm, history


def test_hotkey_initializes():
    hk, rec, txr, sm, history = make_hotkey()
    assert hk.is_recording is False
    assert hk._processing is False


def test_hotkey_activates_on_right_option():
    hk, rec, txr, sm, history = make_hotkey()
    hk._on_press(keyboard.Key.alt_r)
    assert hk.is_recording
    rec.start.assert_called_once()
    assert sm.state == AppState.RECORDING


def test_hotkey_ignores_other_keys():
    hk, rec, txr, sm, history = make_hotkey()
    hk._on_press(keyboard.Key.alt_l)
    assert not hk.is_recording
    hk._on_press(keyboard.Key.shift)
    assert not hk.is_recording
    rec.start.assert_not_called()
    assert sm.state == AppState.IDLE


def test_hotkey_does_not_activate_when_model_not_ready():
    hk, rec, txr, sm, history = make_hotkey(model_ready=False)
    hk._on_press(keyboard.Key.alt_r)
    assert not hk.is_recording
    rec.start.assert_not_called()


def test_process_recording_sets_states():
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = "Hello"
    sm.set_state(AppState.RECORDING)
    hk._process_recording()
    assert sm.state == AppState.IDLE
    history.add.assert_called_once()


def test_process_recording_empty_returns_idle():
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = ""
    sm.set_state(AppState.RECORDING)
    hk._process_recording()
    assert sm.state == AppState.IDLE
    history.add.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_hotkey.py -v`

Expected: FAIL with `TypeError: GlobalHotkey.__init__() got an unexpected keyword argument 'state_manager'`

**Step 3: Rewrite hotkey.py**

```python
# hotkey.py
import os
import time
import threading
from pynput import keyboard

from recorder import AudioRecorder
from transcriber import WhisperTranscriber
from clipboard import copy_to_clipboard
from state import AppState, AppStateManager


class GlobalHotkey:
    """Listens for Right Option key to trigger push-to-talk recording."""

    TRIGGER_KEY = keyboard.Key.alt_r

    def __init__(
        self,
        recorder: AudioRecorder,
        transcriber: WhisperTranscriber,
        state_manager: AppStateManager,
        history=None,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.state_manager = state_manager
        self.history = history
        self.is_recording = False
        self._listener = None
        self._processing = False

    def _on_press(self, key):
        if key != self.TRIGGER_KEY:
            return
        if self.is_recording or self._processing:
            return
        if not self.transcriber.is_ready:
            return
        self.is_recording = True
        self.recorder.start()
        self.state_manager.set_state(AppState.RECORDING)

    def _on_release(self, key):
        if key != self.TRIGGER_KEY:
            return
        if not self.is_recording:
            return
        self.is_recording = False
        threading.Thread(target=self._process_recording, daemon=True).start()

    def _process_recording(self):
        """Stop recording, transcribe, copy to clipboard."""
        self._processing = True
        self.state_manager.set_state(AppState.PROCESSING)
        try:
            wav_path = self.recorder.stop()
            if not wav_path:
                self.state_manager.set_state(AppState.IDLE)
                return
            start_time = time.time()
            text = self.transcriber.transcribe(wav_path)
            elapsed = round(time.time() - start_time, 2)
            if text:
                copy_to_clipboard(text)
                if self.history:
                    self.history.add(text, latency=elapsed)
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            self.state_manager.set_state(AppState.IDLE)
        except Exception as e:
            print(f"Hotkey transcription error: {e}")
            self.state_manager.set_state(AppState.ERROR)
            # Auto-recover to idle after brief error
            threading.Timer(1.0, lambda: self.state_manager.set_state(AppState.IDLE)).start()
        finally:
            self._processing = False

    def start(self):
        """Start listening for the global hotkey in a background thread."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
```

**Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_hotkey.py -v`

Expected: 6 passed

**Step 5: Commit**

```bash
git add hotkey.py tests/test_hotkey.py
git commit -m "refactor: hotkey uses state manager and history"
```

---

### Task 5: Add bar WebSocket and history API to FastAPI

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`

**Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
import tempfile
import os
from history import TranscriptionHistory
from state import AppState, AppStateManager


def test_bar_page_served():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/bar")
    assert resp.status_code == 200
    assert "bar" in resp.text.lower()


def test_bar_websocket_receives_state():
    sm = AppStateManager()
    mock_recorder = MagicMock()
    mock_transcriber = MagicMock()
    mock_transcriber.is_ready = True
    app = create_app(recorder=mock_recorder, transcriber=mock_transcriber, state_manager=sm)
    client = TestClient(app)
    with client.websocket_connect("/ws/bar") as ws:
        # Should receive initial state
        msg = ws.receive_json()
        assert msg["type"] == "state"
        assert msg["state"] == "idle"


def test_bar_websocket_start_stop():
    sm = AppStateManager()
    mock_recorder = MagicMock()
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
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_app.py::test_bar_page_served -v`

Expected: FAIL (404 or missing route)

**Step 3: Rewrite app.py**

```python
# app.py
import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from recorder import AudioRecorder
from transcriber import WhisperTranscriber
from clipboard import copy_to_clipboard
from state import AppState, AppStateManager
from history import TranscriptionHistory

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app(
    recorder: AudioRecorder | None = None,
    transcriber: WhisperTranscriber | None = None,
    state_manager: AppStateManager | None = None,
    history: TranscriptionHistory | None = None,
) -> FastAPI:
    rec = recorder or AudioRecorder()
    txr = transcriber or WhisperTranscriber()
    sm = state_manager or AppStateManager()
    hist = history or TranscriptionHistory()

    # Wire recorder amplitude to state manager
    rec.on_amplitude = sm.push_amplitude

    @asynccontextmanager
    async def lifespan(app):
        if hasattr(txr, 'warmup'):
            threading.Thread(target=txr.warmup, daemon=True).start()
        yield

    app = FastAPI(lifespan=lifespan)

    # Store references for external access
    app.state.state_manager = sm
    app.state.history = hist

    @app.get("/")
    async def index():
        index_path = os.path.join(STATIC_DIR, "index.html")
        with open(index_path) as f:
            return HTMLResponse(f.read())

    @app.get("/bar")
    async def bar_page():
        bar_path = os.path.join(STATIC_DIR, "bar.html")
        with open(bar_path) as f:
            return HTMLResponse(f.read())

    @app.get("/api/history")
    async def get_history(limit: int = 50, offset: int = 0):
        entries = hist.get_recent(limit=limit, offset=offset)
        return JSONResponse({"entries": entries, "total": hist.count()})

    @app.get("/api/history/search")
    async def search_history(q: str = "", limit: int = 50):
        entries = hist.search(q, limit=limit)
        return JSONResponse({"entries": entries})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                data = await ws.receive_json()
                action = data.get("action")

                if action == "start":
                    rec.start()
                    sm.set_state(AppState.RECORDING)
                    await ws.send_json({"type": "status", "status": "recording"})

                elif action == "stop":
                    try:
                        wav_path = rec.stop()
                        if not wav_path:
                            sm.set_state(AppState.IDLE)
                            await ws.send_json({
                                "type": "error",
                                "message": "Recording too short. Hold the button longer.",
                            })
                            continue
                        sm.set_state(AppState.PROCESSING)
                        await ws.send_json({"type": "status", "status": "transcribing"})
                        start_time = time.time()
                        text = txr.transcribe(wav_path)
                        elapsed = round(time.time() - start_time, 2)
                        copy_to_clipboard(text)
                        hist.add(text, latency=elapsed)
                        try:
                            os.unlink(wav_path)
                        except OSError:
                            pass
                        sm.set_state(AppState.IDLE)
                        await ws.send_json({
                            "type": "result",
                            "text": text,
                            "latency": elapsed,
                        })
                    except Exception as e:
                        sm.set_state(AppState.ERROR)
                        await ws.send_json({
                            "type": "error",
                            "message": str(e),
                        })

                elif action == "status":
                    status_data = {
                        "type": "model_status",
                        "ready": txr.is_ready,
                    }
                    if hasattr(txr, 'status'):
                        status_data["status"] = txr.status
                        status_data["message"] = txr.status_message
                    await ws.send_json(status_data)

        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/bar")
    async def bar_websocket(ws: WebSocket):
        await ws.accept()
        # Send initial state
        await ws.send_json({"type": "state", "state": sm.state.value})

        # Queue for state changes and amplitude data pushed from background threads
        queue: asyncio.Queue = asyncio.Queue()

        def on_state_change(old, new):
            queue.put_nowait({"type": "state", "state": new.value})

        def on_amplitude(val):
            queue.put_nowait({"type": "amplitude", "value": round(val, 4)})

        sm.on_state_change(on_state_change)
        sm.on_amplitude(on_amplitude)

        try:
            # Run two tasks: listen for incoming messages and push outgoing updates
            async def push_updates():
                while True:
                    msg = await queue.get()
                    await ws.send_json(msg)

            async def receive_commands():
                while True:
                    data = await ws.receive_json()
                    action = data.get("action")
                    if action == "start":
                        rec.start()
                        sm.set_state(AppState.RECORDING)
                    elif action == "stop":
                        threading.Thread(
                            target=_bar_stop_and_transcribe,
                            args=(rec, txr, sm, hist),
                            daemon=True,
                        ).start()
                    elif action == "cancel":
                        if rec.is_recording:
                            rec.stop()  # discard audio
                        sm.set_state(AppState.IDLE)

            await asyncio.gather(push_updates(), receive_commands())
        except WebSocketDisconnect:
            pass
        finally:
            # Remove callbacks
            if on_state_change in sm._state_callbacks:
                sm._state_callbacks.remove(on_state_change)
            if on_amplitude in sm._amplitude_callbacks:
                sm._amplitude_callbacks.remove(on_amplitude)

    return app


def _bar_stop_and_transcribe(rec, txr, sm, hist):
    """Background thread: stop recording, transcribe, update state."""
    sm.set_state(AppState.PROCESSING)
    try:
        wav_path = rec.stop()
        if not wav_path:
            sm.set_state(AppState.IDLE)
            return
        start_time = time.time()
        text = txr.transcribe(wav_path)
        elapsed = round(time.time() - start_time, 2)
        if text:
            copy_to_clipboard(text)
            hist.add(text, latency=elapsed)
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        sm.set_state(AppState.IDLE)
    except Exception as e:
        print(f"Bar transcription error: {e}")
        sm.set_state(AppState.ERROR)
```

**Step 4: Run all tests to verify they pass**

Run: `source venv/bin/activate && python3 -m pytest tests/test_app.py -v`

Expected: 7 passed (2 existing + 5 new)

**Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add bar WebSocket, history API, wire state manager"
```

---

### Task 6: Floating bar frontend

**Files:**
- Create: `static/bar.html`
- Create: `static/bar.css`
- Create: `static/bar.js`

**Step 1: Create bar.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>DashScribe Bar</title>
    <link rel="stylesheet" href="/static/bar.css">
</head>
<body>
    <div id="bar" class="bar idle">
        <div class="bar-idle pywebview-drag-region">
            <div class="pill"></div>
        </div>
        <div class="bar-recording">
            <button id="cancel-btn" class="bar-btn cancel-btn" aria-label="Cancel">
                <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
                    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                </svg>
            </button>
            <canvas id="waveform" width="180" height="28"></canvas>
            <button id="stop-btn" class="bar-btn stop-btn" aria-label="Stop">
                <div class="stop-icon"></div>
            </button>
        </div>
        <div class="bar-processing">
            <div class="processing-dots">
                <span></span><span></span><span></span>
            </div>
        </div>
        <div class="bar-error">
            <div class="error-flash"></div>
        </div>
    </div>
    <div id="tooltip" class="tooltip hidden">Click or hold Right Option to dictate</div>
    <script src="/static/bar.js"></script>
</body>
</html>
```

**Step 2: Create bar.css**

```css
/* static/bar.css */
* { margin: 0; padding: 0; box-sizing: border-box; }

html, body {
    background: transparent;
    overflow: hidden;
    user-select: none;
    -webkit-user-select: none;
    height: 100%;
}

.bar {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
}

/* Hide all state containers by default */
.bar-idle, .bar-recording, .bar-processing, .bar-error {
    display: none;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
}

/* Show the active state */
.bar.idle .bar-idle { display: flex; }
.bar.recording .bar-recording { display: flex; }
.bar.processing .bar-processing { display: flex; }
.bar.error .bar-error { display: flex; }

/* Idle pill */
.bar-idle {
    cursor: pointer;
}

.pill {
    width: 60px;
    height: 6px;
    background: rgba(160, 160, 192, 0.6);
    border-radius: 3px;
    transition: background 0.2s;
}

.bar-idle:hover .pill {
    background: rgba(160, 160, 192, 0.9);
}

/* Recording */
.bar-recording {
    background: rgba(30, 30, 30, 0.9);
    border-radius: 20px;
    padding: 0 8px;
    gap: 8px;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.4);
}

.bar-btn {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.bar-btn:hover { opacity: 0.8; }

.cancel-btn {
    background: rgba(100, 100, 100, 0.5);
    color: #ccc;
}

.stop-btn {
    background: #e53935;
}

.stop-icon {
    width: 10px;
    height: 10px;
    background: white;
    border-radius: 2px;
}

#waveform {
    flex: 1;
    height: 28px;
}

/* Processing */
.bar-processing {
    background: rgba(30, 30, 30, 0.9);
    border-radius: 20px;
    padding: 0 16px;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.4);
}

.processing-dots {
    display: flex;
    gap: 6px;
    align-items: center;
}

.processing-dots span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #4488ff;
    animation: dot-bounce 1.2s ease-in-out infinite;
}

.processing-dots span:nth-child(2) { animation-delay: 0.15s; }
.processing-dots span:nth-child(3) { animation-delay: 0.3s; }

@keyframes dot-bounce {
    0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
    40% { opacity: 1; transform: scale(1.2); }
}

/* Error */
.bar-error {
    background: rgba(229, 57, 53, 0.9);
    border-radius: 20px;
    box-shadow: 0 2px 12px rgba(229, 57, 53, 0.4);
    animation: error-fade 1s ease-out forwards;
}

.error-flash {
    width: 100%;
    height: 100%;
    border-radius: 20px;
}

@keyframes error-fade {
    0% { opacity: 1; }
    70% { opacity: 1; }
    100% { opacity: 0; }
}

/* Tooltip */
.tooltip {
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    margin-bottom: 8px;
    background: rgba(50, 50, 50, 0.95);
    color: #e0e0e0;
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 12px;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    white-space: nowrap;
    pointer-events: none;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
    transition: opacity 0.15s;
}

.tooltip.hidden {
    opacity: 0;
}
```

**Step 3: Create bar.js**

```javascript
// static/bar.js
(function () {
    const bar = document.getElementById('bar');
    const tooltip = document.getElementById('tooltip');
    const cancelBtn = document.getElementById('cancel-btn');
    const stopBtn = document.getElementById('stop-btn');
    const canvas = document.getElementById('waveform');
    const ctx = canvas.getContext('2d');
    const barIdle = document.querySelector('.bar-idle');

    let ws = null;
    let currentState = 'idle';
    let amplitudes = [];
    const NUM_BARS = 20;
    let animFrameId = null;

    // Initialize amplitude array
    for (let i = 0; i < NUM_BARS; i++) amplitudes.push(0);

    function setState(state) {
        currentState = state;
        bar.className = 'bar ' + state;
        if (state === 'recording') {
            startWaveformAnimation();
        } else {
            stopWaveformAnimation();
        }
        // Notify PyWebView to resize
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.on_state_change(state);
        }
    }

    function drawWaveform() {
        const w = canvas.width;
        const h = canvas.height;
        ctx.clearRect(0, 0, w, h);

        const barWidth = w / NUM_BARS * 0.6;
        const gap = w / NUM_BARS * 0.4;
        const centerY = h / 2;

        for (let i = 0; i < NUM_BARS; i++) {
            const amp = amplitudes[i] || 0;
            const barHeight = Math.max(2, amp * h * 0.9);
            const x = i * (barWidth + gap) + gap / 2;
            const y = centerY - barHeight / 2;

            // Gradient from green to white
            const intensity = Math.min(amp * 3, 1);
            const r = Math.round(100 + intensity * 155);
            const g = Math.round(200 + intensity * 55);
            const b = Math.round(100 + intensity * 155);
            ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
            ctx.beginPath();
            ctx.roundRect(x, y, barWidth, barHeight, 2);
            ctx.fill();
        }

        animFrameId = requestAnimationFrame(drawWaveform);
    }

    function startWaveformAnimation() {
        if (animFrameId) return;
        drawWaveform();
    }

    function stopWaveformAnimation() {
        if (animFrameId) {
            cancelAnimationFrame(animFrameId);
            animFrameId = null;
        }
    }

    function pushAmplitude(value) {
        amplitudes.shift();
        amplitudes.push(value);
    }

    function connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${location.host}/ws/bar`);

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'state') {
                setState(msg.state);
            } else if (msg.type === 'amplitude') {
                pushAmplitude(msg.value);
            }
        };

        ws.onclose = () => {
            setTimeout(connect, 1000);
        };
    }

    // Click idle bar to start recording
    barIdle.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'start' }));
        }
    });

    // Stop button
    stopBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'stop' }));
        }
    });

    // Cancel button
    cancelBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'cancel' }));
        }
    });

    // Tooltip on hover (idle state only)
    barIdle.addEventListener('mouseenter', () => {
        if (currentState === 'idle') tooltip.classList.remove('hidden');
    });
    barIdle.addEventListener('mouseleave', () => {
        tooltip.classList.add('hidden');
    });

    connect();
})();
```

**Step 4: Run existing tests to verify nothing broke**

Run: `source venv/bin/activate && python3 -m pytest tests/ -v`

Expected: All tests pass

**Step 5: Commit**

```bash
git add static/bar.html static/bar.css static/bar.js
git commit -m "feat: add floating bar frontend with waveform canvas"
```

---

### Task 7: Add transcription history to main window UI

**Files:**
- Modify: `static/index.html`
- Modify: `static/style.css`
- Modify: `static/app.js`

**Step 1: Add history section to index.html**

After the `<div id="toast">` and before `<div id="progress-container">`, insert:

```html
        <div class="history-section">
            <div class="history-header">
                <h2 class="history-title">History</h2>
                <input id="history-search" type="text" class="history-search" placeholder="Search transcriptions...">
            </div>
            <div id="history-list" class="history-list"></div>
            <button id="load-more-btn" class="load-more-btn hidden">Load more</button>
        </div>
```

**Step 2: Add history styles to style.css**

Append to `static/style.css`:

```css
/* History section */
.history-section {
    width: 100%;
    max-width: 380px;
    flex: 1;
    display: flex;
    flex-direction: column;
    margin-bottom: 12px;
    min-height: 0;
}

.history-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}

.history-title {
    font-size: 14px;
    font-weight: 500;
    color: #8080a0;
}

.history-search {
    background: #16162a;
    border: 1px solid #2a2a4a;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    color: #d0d0e0;
    outline: none;
    width: 160px;
}

.history-search:focus {
    border-color: #4488ff;
}

.history-search::placeholder {
    color: #4a4a6a;
}

.history-list {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.history-entry {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px 10px;
    background: #16162a;
    border-radius: 8px;
    font-size: 13px;
}

.history-time {
    color: #5a5a7a;
    font-size: 11px;
    white-space: nowrap;
    padding-top: 1px;
    flex-shrink: 0;
}

.history-text {
    color: #c0c0d8;
    line-height: 1.4;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
}

.history-copy-btn {
    background: none;
    border: none;
    color: #5a5a7a;
    cursor: pointer;
    padding: 2px;
    flex-shrink: 0;
    opacity: 0;
    transition: opacity 0.15s;
}

.history-entry:hover .history-copy-btn {
    opacity: 1;
}

.history-copy-btn:hover {
    color: #4488ff;
}

.load-more-btn {
    background: #2a2a4a;
    border: none;
    color: #8080a0;
    padding: 6px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    margin-top: 8px;
    align-self: center;
}

.load-more-btn:hover {
    background: #32325a;
}

.load-more-btn.hidden {
    display: none;
}
```

**Step 3: Update app.js**

Change `result-area` to shrink and add history fetching. In the main IIFE, after the existing code but before `connect()`, add:

```javascript
    // --- History ---
    const historyList = document.getElementById('history-list');
    const historySearch = document.getElementById('history-search');
    const loadMoreBtn = document.getElementById('load-more-btn');
    let historyOffset = 0;
    const HISTORY_PAGE = 50;
    let totalHistory = 0;

    function formatTime(isoString) {
        const d = new Date(isoString);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function createHistoryEntry(entry) {
        const div = document.createElement('div');
        div.className = 'history-entry';
        div.innerHTML =
            '<span class="history-time">' + formatTime(entry.timestamp) + '</span>' +
            '<span class="history-text">' + escapeHtml(entry.text) + '</span>' +
            '<button class="history-copy-btn" aria-label="Copy">' +
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">' +
                    '<path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>' +
                '</svg>' +
            '</button>';
        div.querySelector('.history-copy-btn').addEventListener('click', () => {
            navigator.clipboard.writeText(entry.text);
            showToast();
        });
        return div;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async function loadHistory(append) {
        if (!append) {
            historyOffset = 0;
            historyList.innerHTML = '';
        }
        const resp = await fetch('/api/history?limit=' + HISTORY_PAGE + '&offset=' + historyOffset);
        const data = await resp.json();
        totalHistory = data.total;
        data.entries.forEach(e => historyList.appendChild(createHistoryEntry(e)));
        historyOffset += data.entries.length;
        loadMoreBtn.classList.toggle('hidden', historyOffset >= totalHistory);
    }

    async function searchHistory(query) {
        if (!query) {
            loadHistory(false);
            return;
        }
        historyList.innerHTML = '';
        const resp = await fetch('/api/history/search?q=' + encodeURIComponent(query));
        const data = await resp.json();
        data.entries.forEach(e => historyList.appendChild(createHistoryEntry(e)));
        loadMoreBtn.classList.add('hidden');
    }

    let searchTimeout = null;
    historySearch.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => searchHistory(historySearch.value.trim()), 300);
    });

    loadMoreBtn.addEventListener('click', () => loadHistory(true));
```

Also, in the existing `ws.onmessage` handler where `msg.type === 'result'` is handled, after `showToast()`, add:

```javascript
                loadHistory(false);  // Refresh history after new transcription
```

And call `loadHistory(false)` at the end of the IIFE, after `connect()`:

```javascript
    loadHistory(false);
```

**Step 4: Run tests**

Run: `source venv/bin/activate && python3 -m pytest tests/ -v`

Expected: All tests pass

**Step 5: Commit**

```bash
git add static/index.html static/style.css static/app.js
git commit -m "feat: add transcription history UI with search"
```

---

### Task 8: Update main.py for floating bar window and lifecycle

**Files:**
- Modify: `main.py`

**Step 1: Rewrite main.py**

```python
# main.py
import threading
import uvicorn
import webview

from app import create_app
from recorder import AudioRecorder
from transcriber import WhisperTranscriber
from hotkey import GlobalHotkey
from state import AppState, AppStateManager
from history import TranscriptionHistory

HOST = "127.0.0.1"
PORT = 8765

# Bar dimensions per state
BAR_IDLE_W, BAR_IDLE_H = 120, 24
BAR_ACTIVE_W, BAR_ACTIVE_H = 280, 40


def start_server(app):
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def get_bar_position(width, height):
    """Center horizontally, 70px above screen bottom."""
    # Default to reasonable screen dimensions; pywebview will adjust
    import AppKit
    screen = AppKit.NSScreen.mainScreen()
    frame = screen.frame()
    screen_w = int(frame.size.width)
    screen_h = int(frame.size.height)
    x = (screen_w - width) // 2
    y = screen_h - 70 - height
    return x, y


def main():
    transcriber = WhisperTranscriber()
    state_manager = AppStateManager()
    history = TranscriptionHistory()

    app = create_app(
        transcriber=transcriber,
        state_manager=state_manager,
        history=history,
    )

    # Global hotkey uses its own recorder to avoid conflicts with the UI
    hotkey_recorder = AudioRecorder()
    hotkey_recorder.on_amplitude = state_manager.push_amplitude
    hotkey = GlobalHotkey(
        recorder=hotkey_recorder,
        transcriber=transcriber,
        state_manager=state_manager,
        history=history,
    )
    hotkey.start()

    server_thread = threading.Thread(
        target=start_server,
        args=(app,),
        daemon=True,
    )
    server_thread.start()

    # Calculate bar position
    bar_x, bar_y = get_bar_position(BAR_IDLE_W, BAR_IDLE_H)

    # Create floating bar window (always exists, keeps app alive)
    bar_window = webview.create_window(
        "",
        f"http://{HOST}:{PORT}/bar",
        width=BAR_IDLE_W,
        height=BAR_IDLE_H,
        x=bar_x,
        y=bar_y,
        min_size=(80, 20),
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
    )

    # Create main window
    main_window = webview.create_window(
        "DashScribe",
        f"http://{HOST}:{PORT}",
        width=450,
        height=650,
        resizable=True,
        min_size=(350, 450),
    )

    # Handle bar resize based on state changes
    def on_state_change(old_state, new_state):
        if new_state == AppState.RECORDING or new_state == AppState.PROCESSING:
            cx, cy = get_bar_position(BAR_ACTIVE_W, BAR_ACTIVE_H)
            bar_window.resize(BAR_ACTIVE_W, BAR_ACTIVE_H)
            bar_window.move(cx, cy)
        elif new_state == AppState.IDLE:
            cx, cy = get_bar_position(BAR_IDLE_W, BAR_IDLE_H)
            bar_window.resize(BAR_IDLE_W, BAR_IDLE_H)
            bar_window.move(cx, cy)
        elif new_state == AppState.ERROR:
            # Stay at active size briefly, then shrink
            def shrink():
                import time
                time.sleep(1.0)
                cx, cy = get_bar_position(BAR_IDLE_W, BAR_IDLE_H)
                bar_window.resize(BAR_IDLE_W, BAR_IDLE_H)
                bar_window.move(cx, cy)
            threading.Thread(target=shrink, daemon=True).start()

    state_manager.on_state_change(on_state_change)

    # Handle main window close: hide instead of destroy
    def on_main_closing():
        main_window.hide()
        return False  # Prevent actual close

    main_window.events.closing += on_main_closing

    # Right-click on bar could reopen main window (TODO: context menu)
    # For now, clicking the bar-idle area reopens if main is hidden
    def setup_bar_api(window):
        def show_main_window():
            main_window.show()
        window.expose(show_main_window)

    webview.start(func=setup_bar_api, args=[bar_window])


if __name__ == "__main__":
    main()
```

**Step 2: Run all tests**

Run: `source venv/bin/activate && python3 -m pytest tests/ -v`

Expected: All tests pass

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add floating bar window and main window lifecycle"
```

---

### Task 9: End-to-end manual test

**Step 1: Run the app**

```bash
cd /path/to/DashScribe
source venv/bin/activate
python3 main.py
```

Expected: Two windows appear — the main DashScribe window and a small floating bar near the dock.

**Step 2: Verify floating bar states**

- Idle: small dark pill centered above dock
- Hover over pill: tooltip appears
- Hold Right Option: bar expands, shows waveform bars moving with voice
- Release: bar shows processing dots, then shrinks back to pill
- Transcribed text in clipboard — Cmd+V to verify

**Step 3: Verify click interaction on bar**

- Click the idle pill: bar expands to recording mode
- Click stop button: transcribes and returns to idle
- Click cancel button: discards recording, returns to idle

**Step 4: Verify transcription history**

- Open main window, see history section below mic button
- After a transcription, new entry appears at top
- Click copy button on an entry
- Type in search box — results filter
- Multiple transcriptions → "Load more" button appears after 50

**Step 5: Verify main window lifecycle**

- Close main window (red X) — floating bar stays, hotkey works
- Click bar pill — TODO: reopen main window (may need right-click context menu)
- Cmd+Q quits the entire app

**Step 6: Verify existing main window recording still works**

- Hold mic button in main window → recording → transcription → clipboard
- History updates after each

**Step 7: Fix any issues found**

**Step 8: Final commit**

```bash
git add -A
git commit -m "fix: adjustments from manual testing"
```

---

## Summary

| Task | What | Key Files |
|------|------|-----------|
| 1 | State manager | `state.py` |
| 2 | Transcription history | `history.py` |
| 3 | Recorder amplitude | `recorder.py` |
| 4 | Hotkey refactor | `hotkey.py` |
| 5 | FastAPI updates | `app.py` |
| 6 | Floating bar frontend | `static/bar.html`, `bar.css`, `bar.js` |
| 7 | Main window history UI | `static/index.html`, `style.css`, `app.js` |
| 8 | Launcher + lifecycle | `main.py` |
| 9 | E2E manual test | Manual verification |
