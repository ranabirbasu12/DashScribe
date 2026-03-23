# DashScribe Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local, offline dictation app for macOS using mlx-whisper on Apple Silicon with a PyWebView + FastAPI architecture.

**Architecture:** FastAPI backend handles audio recording (sounddevice), transcription (mlx-whisper), and clipboard management. Frontend is a dark-themed single-page app served as static files. PyWebView wraps everything in a native macOS window. Communication via WebSocket.

**Tech Stack:** Python 3.11+, mlx-whisper, FastAPI, uvicorn, sounddevice, numpy, scipy, pyperclip, pywebview

---

### Task 1: Project scaffolding and dependencies

**Files:**
- Create: `requirements.txt`
- Create: `tests/__init__.py`

**Step 1: Create requirements.txt**

```
mlx-whisper
fastapi
uvicorn[standard]
websockets
sounddevice
numpy
scipy
pyperclip
pywebview
pytest
pytest-asyncio
httpx
```

**Step 2: Create test directory**

Create an empty `tests/__init__.py`.

**Step 3: Create virtual environment and install**

Run:
```bash
cd /path/to/DashScribe
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Expected: All packages install successfully. mlx-whisper will pull in mlx and related Apple Silicon dependencies.

**Step 4: Verify key imports**

Run:
```bash
source venv/bin/activate
python3 -c "import mlx_whisper; import fastapi; import sounddevice; import webview; print('All imports OK')"
```

Expected: `All imports OK`

**Step 5: Commit**

```bash
git add requirements.txt tests/__init__.py
git commit -m "feat: add project scaffolding and dependencies"
```

---

### Task 2: Clipboard module

**Files:**
- Create: `clipboard.py`
- Create: `tests/test_clipboard.py`

**Step 1: Write the failing test**

```python
# tests/test_clipboard.py
import pyperclip
from clipboard import copy_to_clipboard


def test_copy_to_clipboard():
    copy_to_clipboard("hello dashscribe")
    assert pyperclip.paste() == "hello dashscribe"


def test_copy_empty_string():
    copy_to_clipboard("")
    assert pyperclip.paste() == ""
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_clipboard.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'clipboard'`

**Step 3: Write minimal implementation**

```python
# clipboard.py
import pyperclip


def copy_to_clipboard(text: str) -> None:
    pyperclip.copy(text)
```

**Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_clipboard.py -v`

Expected: 2 passed

**Step 5: Commit**

```bash
git add clipboard.py tests/test_clipboard.py
git commit -m "feat: add clipboard module"
```

---

### Task 3: Recorder module

**Files:**
- Create: `recorder.py`
- Create: `tests/test_recorder.py`

**Step 1: Write the failing test**

```python
# tests/test_recorder.py
import numpy as np
from unittest.mock import patch, MagicMock
from recorder import AudioRecorder

SAMPLE_RATE = 16000


def test_recorder_initializes_with_correct_settings():
    rec = AudioRecorder()
    assert rec.sample_rate == SAMPLE_RATE
    assert rec.channels == 1
    assert rec.is_recording is False


def test_recorder_start_sets_recording_flag():
    rec = AudioRecorder()
    with patch.object(rec, '_stream', create=True):
        with patch('recorder.sd.InputStream') as mock_stream:
            mock_instance = MagicMock()
            mock_stream.return_value = mock_instance
            rec.start()
            assert rec.is_recording is True
            mock_instance.start.assert_called_once()


def test_recorder_stop_returns_wav_path():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = [np.zeros((1600, 1), dtype=np.float32)]
    with patch('recorder.sd.InputStream'):
        rec._stream = MagicMock()
        path = rec.stop()
        assert path.endswith('.wav')
        assert rec.is_recording is False


def test_recorder_callback_appends_chunks():
    rec = AudioRecorder()
    rec.is_recording = True
    rec._chunks = []
    fake_data = np.random.randn(1600, 1).astype(np.float32)
    rec._audio_callback(fake_data, 1600, None, None)
    assert len(rec._chunks) == 1
    np.testing.assert_array_equal(rec._chunks[0], fake_data)
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_recorder.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'recorder'`

**Step 3: Write minimal implementation**

```python
# recorder.py
import tempfile
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 16000


class AudioRecorder:
    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.channels = 1
        self.is_recording = False
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None

    def _audio_callback(self, indata, frames, time, status):
        if status:
            print(f"Audio status: {status}")
        if self.is_recording:
            self._chunks.append(indata.copy())

    def start(self):
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=np.float32,
            callback=self._audio_callback,
        )
        self._stream.start()
        self.is_recording = True

    def stop(self) -> str:
        self.is_recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._chunks:
            raise RuntimeError("No audio recorded")

        audio = np.concatenate(self._chunks, axis=0)
        audio_int16 = np.int16(audio * 32767)

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wavfile.write(tmp.name, self.sample_rate, audio_int16)
        tmp.close()
        return tmp.name
```

**Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_recorder.py -v`

Expected: 4 passed

**Step 5: Commit**

```bash
git add recorder.py tests/test_recorder.py
git commit -m "feat: add audio recorder module"
```

---

### Task 4: Transcriber module

**Files:**
- Create: `transcriber.py`
- Create: `tests/test_transcriber.py`

**Step 1: Write the failing test**

```python
# tests/test_transcriber.py
from unittest.mock import patch
from transcriber import WhisperTranscriber


def test_transcriber_initializes_with_model_name():
    t = WhisperTranscriber()
    assert t.model_repo == "mlx-community/whisper-large-v3-turbo"
    assert t.is_ready is False


@patch("transcriber.mlx_whisper.transcribe")
def test_transcribe_returns_text(mock_transcribe):
    mock_transcribe.return_value = {"text": " Hello world."}
    t = WhisperTranscriber()
    t.is_ready = True
    result = t.transcribe("/tmp/test.wav")
    assert result == "Hello world."
    mock_transcribe.assert_called_once_with(
        "/tmp/test.wav",
        path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
        language="en",
    )


@patch("transcriber.mlx_whisper.transcribe")
def test_transcribe_strips_whitespace(mock_transcribe):
    mock_transcribe.return_value = {"text": "  Some text  "}
    t = WhisperTranscriber()
    t.is_ready = True
    result = t.transcribe("/tmp/test.wav")
    assert result == "Some text"
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_transcriber.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'transcriber'`

**Step 3: Write minimal implementation**

```python
# transcriber.py
import mlx_whisper

MODEL_REPO = "mlx-community/whisper-large-v3-turbo"


class WhisperTranscriber:
    def __init__(self, model_repo: str = MODEL_REPO):
        self.model_repo = model_repo
        self.is_ready = False

    def load_model(self):
        """Warm up the model by running a dummy transcription."""
        # mlx_whisper loads the model lazily on first transcribe call.
        # We mark ready after the first successful call in the app.
        self.is_ready = True

    def transcribe(self, audio_path: str) -> str:
        result = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=self.model_repo,
            language="en",
        )
        self.is_ready = True
        return result["text"].strip()
```

**Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_transcriber.py -v`

Expected: 3 passed

**Step 5: Commit**

```bash
git add transcriber.py tests/test_transcriber.py
git commit -m "feat: add whisper transcriber module"
```

---

### Task 5: FastAPI backend with WebSocket

**Files:**
- Create: `app.py`
- Create: `tests/test_app.py`

**Step 1: Write the failing test**

```python
# tests/test_app.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from app import create_app


def test_static_index_served():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "DashScribe" in resp.text


def test_websocket_start_stop_flow():
    mock_recorder = MagicMock()
    mock_recorder.stop.return_value = "/tmp/fake.wav"
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = "Hello world."
    mock_transcriber.is_ready = True

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
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest tests/test_app.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app'` or import error

**Step 3: Create the static frontend placeholder first**

Create `static/index.html` with minimal content (full frontend comes in Task 6):

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DashScribe</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div id="app">
        <h1>DashScribe</h1>
        <p>Loading...</p>
    </div>
    <script src="/static/app.js"></script>
</body>
</html>
```

Create empty `static/style.css` and `static/app.js` as placeholders.

**Step 4: Write the FastAPI app**

```python
# app.py
import os
import threading
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from recorder import AudioRecorder
from transcriber import WhisperTranscriber
from clipboard import copy_to_clipboard

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app(
    recorder: AudioRecorder | None = None,
    transcriber: WhisperTranscriber | None = None,
) -> FastAPI:
    app = FastAPI()

    rec = recorder or AudioRecorder()
    txr = transcriber or WhisperTranscriber()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index():
        index_path = os.path.join(STATIC_DIR, "index.html")
        with open(index_path) as f:
            return HTMLResponse(f.read())

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                data = await ws.receive_json()
                action = data.get("action")

                if action == "start":
                    rec.start()
                    await ws.send_json({"type": "status", "status": "recording"})

                elif action == "stop":
                    await ws.send_json({"type": "status", "status": "transcribing"})
                    wav_path = rec.stop()
                    start_time = time.time()
                    text = txr.transcribe(wav_path)
                    elapsed = round(time.time() - start_time, 2)
                    copy_to_clipboard(text)
                    # Clean up temp file
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                    await ws.send_json({
                        "type": "result",
                        "text": text,
                        "latency": elapsed,
                    })

                elif action == "status":
                    await ws.send_json({
                        "type": "model_status",
                        "ready": txr.is_ready,
                    })

        except WebSocketDisconnect:
            pass

    return app
```

**Step 5: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest tests/test_app.py -v`

Expected: 2 passed

**Step 6: Commit**

```bash
git add app.py tests/test_app.py static/index.html static/style.css static/app.js
git commit -m "feat: add FastAPI backend with WebSocket endpoint"
```

---

### Task 6: Frontend UI

**Files:**
- Modify: `static/index.html`
- Modify: `static/style.css`
- Modify: `static/app.js`

**Step 1: Write the HTML**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DashScribe</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div id="app">
        <h1 class="title">DashScribe</h1>

        <div class="mic-container">
            <button id="mic-btn" class="mic-btn" aria-label="Hold to record">
                <svg class="mic-icon" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
                    <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
                </svg>
            </button>
            <p id="mic-label" class="mic-label">Hold to Record</p>
        </div>

        <div id="result-area" class="result-area">
            <p id="result-text" class="result-text"></p>
        </div>

        <div id="toast" class="toast hidden">Copied to clipboard</div>

        <div class="status-bar">
            <span id="model-status" class="model-status">
                <span class="dot loading"></span> Loading model...
            </span>
            <span id="latency" class="latency"></span>
        </div>
    </div>
    <script src="/static/app.js"></script>
</body>
</html>
```

**Step 2: Write the CSS**

```css
/* static/style.css */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    height: 100vh;
    overflow: hidden;
    user-select: none;
    -webkit-user-select: none;
}

#app {
    display: flex;
    flex-direction: column;
    align-items: center;
    height: 100vh;
    padding: 24px;
}

.title {
    font-size: 20px;
    font-weight: 600;
    color: #a0a0c0;
    letter-spacing: 1px;
    margin-bottom: 32px;
}

.mic-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin-bottom: 24px;
}

.mic-btn {
    width: 100px;
    height: 100px;
    border-radius: 50%;
    border: 3px solid #3a3a5c;
    background: #2a2a4a;
    color: #8080b0;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    outline: none;
}

.mic-btn:hover {
    border-color: #5a5a8c;
    background: #32325a;
}

.mic-btn.recording {
    border-color: #ff4444;
    background: #4a1a1a;
    color: #ff4444;
    animation: pulse 1.5s ease-in-out infinite;
}

.mic-btn.transcribing {
    border-color: #4488ff;
    background: #1a2a4a;
    color: #4488ff;
    animation: spin-border 1.5s linear infinite;
}

@keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(255, 68, 68, 0.4); }
    50% { box-shadow: 0 0 0 16px rgba(255, 68, 68, 0); }
}

@keyframes spin-border {
    0% { box-shadow: 0 0 0 3px rgba(68, 136, 255, 0.3); }
    50% { box-shadow: 0 0 0 6px rgba(68, 136, 255, 0.1); }
    100% { box-shadow: 0 0 0 3px rgba(68, 136, 255, 0.3); }
}

.mic-icon {
    width: 40px;
    height: 40px;
}

.mic-label {
    margin-top: 12px;
    font-size: 13px;
    color: #6a6a8a;
}

.result-area {
    flex: 1;
    width: 100%;
    max-width: 380px;
    background: #16162a;
    border-radius: 12px;
    padding: 16px;
    overflow-y: auto;
    margin-bottom: 16px;
    min-height: 120px;
}

.result-text {
    font-size: 15px;
    line-height: 1.6;
    color: #d0d0e0;
    white-space: pre-wrap;
}

.toast {
    position: fixed;
    bottom: 60px;
    background: #2a4a2a;
    color: #66cc66;
    padding: 8px 20px;
    border-radius: 20px;
    font-size: 13px;
    transition: opacity 0.3s ease;
}

.toast.hidden {
    opacity: 0;
    pointer-events: none;
}

.status-bar {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 4px;
    font-size: 12px;
    color: #5a5a7a;
}

.model-status {
    display: flex;
    align-items: center;
    gap: 6px;
}

.dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
}

.dot.loading {
    background: #cc8800;
    animation: blink 1s ease-in-out infinite;
}

.dot.ready {
    background: #44cc44;
}

@keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

.latency {
    color: #5a5a7a;
}
```

**Step 3: Write the JavaScript**

```javascript
// static/app.js
(function () {
    const micBtn = document.getElementById('mic-btn');
    const micLabel = document.getElementById('mic-label');
    const resultText = document.getElementById('result-text');
    const toast = document.getElementById('toast');
    const modelStatus = document.getElementById('model-status');
    const latencyEl = document.getElementById('latency');

    let ws = null;
    let isRecording = false;

    function connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${location.host}/ws`);

        ws.onopen = () => {
            ws.send(JSON.stringify({ action: 'status' }));
        };

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);

            if (msg.type === 'status') {
                if (msg.status === 'recording') {
                    micBtn.classList.add('recording');
                    micBtn.classList.remove('transcribing');
                    micLabel.textContent = 'Recording...';
                } else if (msg.status === 'transcribing') {
                    micBtn.classList.remove('recording');
                    micBtn.classList.add('transcribing');
                    micLabel.textContent = 'Transcribing...';
                }
            } else if (msg.type === 'result') {
                micBtn.classList.remove('recording', 'transcribing');
                micLabel.textContent = 'Hold to Record';
                resultText.textContent = msg.text;
                latencyEl.textContent = msg.latency + 's';
                showToast();
                isRecording = false;
            } else if (msg.type === 'model_status') {
                if (msg.ready) {
                    modelStatus.innerHTML = '<span class="dot ready"></span> Ready';
                } else {
                    modelStatus.innerHTML = '<span class="dot loading"></span> Loading model...';
                }
            }
        };

        ws.onclose = () => {
            setTimeout(connect, 1000);
        };
    }

    function showToast() {
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 2000);
    }

    // Push-to-talk: mousedown = start, mouseup = stop
    micBtn.addEventListener('mousedown', (e) => {
        e.preventDefault();
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (isRecording) return;
        isRecording = true;
        ws.send(JSON.stringify({ action: 'start' }));
    });

    micBtn.addEventListener('mouseup', (e) => {
        e.preventDefault();
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (!isRecording) return;
        ws.send(JSON.stringify({ action: 'stop' }));
    });

    micBtn.addEventListener('mouseleave', (e) => {
        if (!isRecording) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ action: 'stop' }));
    });

    connect();
})();
```

**Step 4: Run existing tests to verify nothing broke**

Run: `source venv/bin/activate && python3 -m pytest tests/ -v`

Expected: All tests pass (the index test should now return the full HTML)

**Step 5: Commit**

```bash
git add static/index.html static/style.css static/app.js
git commit -m "feat: add dark-themed frontend UI with push-to-talk"
```

---

### Task 7: PyWebView launcher

**Files:**
- Create: `main.py`

**Step 1: Write the launcher**

```python
# main.py
import threading
import uvicorn
import webview

from app import create_app
from transcriber import WhisperTranscriber

HOST = "127.0.0.1"
PORT = 8765


def start_server(app):
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main():
    transcriber = WhisperTranscriber()
    app = create_app(transcriber=transcriber)

    server_thread = threading.Thread(
        target=start_server,
        args=(app,),
        daemon=True,
    )
    server_thread.start()

    webview.create_window(
        "DashScribe",
        f"http://{HOST}:{PORT}",
        width=450,
        height=550,
        resizable=True,
        min_size=(350, 450),
    )
    webview.start()


if __name__ == "__main__":
    main()
```

**Step 2: Commit**

```bash
git add main.py
git commit -m "feat: add PyWebView launcher with threaded server"
```

---

### Task 8: Model warmup on startup

**Files:**
- Modify: `app.py` — add startup event to warm up the model in a background thread
- Modify: `transcriber.py` — add warmup method using a tiny silent audio file

**Step 1: Add warmup to transcriber**

Add to `transcriber.py`:

```python
import tempfile
import numpy as np
from scipy.io import wavfile


class WhisperTranscriber:
    # ... existing code ...

    def warmup(self):
        """Run a tiny transcription to pre-load the model into memory."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        silence = np.zeros(16000, dtype=np.int16)  # 1 second of silence
        wavfile.write(tmp.name, 16000, silence)
        tmp.close()
        try:
            self.transcribe(tmp.name)
        except Exception:
            pass
        finally:
            import os
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        self.is_ready = True
```

**Step 2: Add startup event to app.py**

Add a `/ws` model_status broadcast after warmup completes. Modify `create_app` to accept a startup warmup flag and spawn a background thread:

In `app.py`, add after `app = FastAPI()`:

```python
@app.on_event("startup")
async def startup_warmup():
    def _warmup():
        txr.warmup()
    threading.Thread(target=_warmup, daemon=True).start()
```

Add `import threading` to the top of `app.py` if not present.

**Step 3: Run all tests**

Run: `source venv/bin/activate && python3 -m pytest tests/ -v`

Expected: All tests pass

**Step 4: Commit**

```bash
git add transcriber.py app.py
git commit -m "feat: add model warmup on startup"
```

---

### Task 9: End-to-end manual test

**Step 1: Run the app**

```bash
cd /path/to/DashScribe
source venv/bin/activate
python3 main.py
```

Expected: A native macOS window opens showing the DashScribe UI with dark theme.

**Step 2: Verify model loads**

Watch the status bar — it should show "Loading model..." then switch to "Ready" after the warmup completes (first run downloads the model, may take a few minutes).

**Step 3: Test push-to-talk**

Hold the mic button, speak a sentence, release. Verify:
- Button pulses red while recording
- Button shows blue spinner while transcribing
- Transcribed text appears in the result area
- "Copied to clipboard" toast appears
- Cmd+V pastes the transcribed text
- Latency is shown in the status bar

**Step 4: Fix any issues found during manual testing**

**Step 5: Final commit if any fixes were made**

```bash
git add -A
git commit -m "fix: adjustments from manual testing"
```

---

## Summary

| Task | What | Key Files |
|------|------|-----------|
| 1 | Project scaffolding | requirements.txt |
| 2 | Clipboard module | clipboard.py |
| 3 | Recorder module | recorder.py |
| 4 | Transcriber module | transcriber.py |
| 5 | FastAPI backend | app.py |
| 6 | Frontend UI | static/ |
| 7 | PyWebView launcher | main.py |
| 8 | Model warmup | transcriber.py, app.py |
| 9 | End-to-end test | Manual verification |
