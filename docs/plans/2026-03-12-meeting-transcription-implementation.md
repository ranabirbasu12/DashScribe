# Meeting Transcription Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add per-app meeting transcription with speaker labels ("You" vs "Others") to DashScribe.

**Architecture:** Two parallel VAD pipelines (system audio + optional mic) feeding a single worker thread. Separate MeetingStore. WebSocket live updates.

**Tech Stack:** ScreenCaptureKit (per-app audio), sounddevice (mic), Silero VAD, mlx-whisper, SQLite, FastAPI WebSocket

---

### Task 1: MeetingStore — database layer

**Files:**
- Create: `meeting_store.py`
- Test: `tests/test_meeting_store.py`

**Step 1: Write failing tests**

```python
# tests/test_meeting_store.py
import os, tempfile, pytest
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

def test_add_segment_with_speaker(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    sid = store.add_segment(mid, 0, "hello", 0, 1000, speaker="others")
    segs = store.get_segments(mid)
    assert len(segs) == 1
    assert segs[0]["speaker"] == "others"

def test_list_meetings(store):
    store.create_meeting("M1", app_name="Zoom", mode="listen")
    store.create_meeting("M2", app_name="Teams", mode="full")
    meetings = store.list_meetings()
    assert len(meetings) == 2

def test_search_meetings(store):
    store.create_meeting("Sprint Planning", app_name="Zoom", mode="listen")
    store.add_segment(1, 0, "discuss the roadmap", 0, 1000, speaker="others")
    results = store.search_meetings("roadmap")
    assert len(results) == 1

def test_delete_meeting(store):
    mid = store.create_meeting("Test", app_name="Zoom", mode="listen")
    store.delete_meeting(mid)
    assert store.get_meeting(mid) is None
```

**Step 2: Run tests — expect FAIL**

Run: `python3 -m pytest tests/test_meeting_store.py -v`

**Step 3: Implement MeetingStore**

Fork `lecture_store.py` with these changes:
- `meetings` table: add `app_name TEXT`, `mode TEXT` (listen/full), `system_audio_path TEXT`, `mic_audio_path TEXT`
- `meeting_segments` table: add `speaker TEXT NOT NULL DEFAULT 'others'`
- `meeting_labels` + `meeting_label_map` — same as ClassNote
- `create_meeting(title, app_name, mode)` → returns meeting ID
- `add_segment(meeting_id, index, text, start_ms, end_ms, speaker)` — speaker is "you" or "others"
- All other CRUD methods mirror LectureStore

**Step 4: Run tests — expect PASS**

**Step 5: Commit**
```bash
git add meeting_store.py tests/test_meeting_store.py
git commit -m "feat(meeting): add MeetingStore with speaker-labeled segments"
```

---

### Task 2: Extend SystemAudioCapture for per-app filtering

**Files:**
- Modify: `system_audio.py`
- Test: `tests/test_system_audio.py`

**Step 1: Write failing tests**

```python
def test_start_with_app_filter():
    """SystemAudioCapture accepts app_bundle_id for per-app filtering."""
    with patch("system_audio.SCShareableContent") as mock_sc:
        mock_content = MagicMock()
        mock_app = MagicMock()
        mock_app.bundleIdentifier.return_value = "us.zoom.xos"
        mock_content.applications.return_value = [mock_app]
        mock_sc.getShareableContentWithCompletionHandler_.side_effect = lambda h: h(mock_content, None)
        cap = SystemAudioCapture()
        cap.start(app_bundle_id="us.zoom.xos")
        # Verify filter was created with app
```

**Step 2: Implement per-app filtering**

Add optional `app_bundle_id` parameter to `SystemAudioCapture.start()`:
- If provided, query `SCShareableContent` for running apps
- Find matching app by bundle ID
- Build `SCContentFilter` that captures only that app's audio
- If not provided, use existing display-level capture (backwards compatible)

Add class method `get_running_apps() -> list[dict]`:
- Returns `[{"name": "Zoom", "bundle_id": "us.zoom.xos"}, ...]`
- Queries `SCShareableContent.getShareableContentWithCompletionHandler_`

**Step 3: Run tests — expect PASS**

**Step 4: Commit**
```bash
git add system_audio.py tests/test_system_audio.py
git commit -m "feat(meeting): add per-app audio filtering to SystemAudioCapture"
```

---

### Task 3: MeetingRecorder — dual-stream audio capture

**Files:**
- Create: `meeting_recorder.py`
- Test: `tests/test_meeting_recorder.py`

**Step 1: Write failing tests**

Test listen mode (system only) and full mode (system + mic). Verify:
- WAV files created at correct paths
- Callbacks fire with audio chunks
- Stop returns audio paths
- Pause/resume work

**Step 2: Implement MeetingRecorder**

```python
class MeetingRecorder:
    def __init__(self, mode="listen", app_bundle_id=None):
        self.mode = mode  # "listen" or "full"
        self._sys_capture = SystemAudioCapture()
        self._mic_recorder = None  # LectureRecorder-style, only in full mode

    def start(self, system_wav_path, mic_wav_path=None):
        self._sys_capture.start(app_bundle_id=self._app_bundle_id)
        if self.mode == "full":
            # Start mic recording with AEC

    def stop(self) -> dict:
        # Returns {"system_audio_path": ..., "mic_audio_path": ...}
```

Key: In full mode, mic audio goes through AEC (NLMS filter) using system audio as reference.

**Step 3: Run tests — expect PASS**

**Step 4: Commit**
```bash
git add meeting_recorder.py tests/test_meeting_recorder.py
git commit -m "feat(meeting): add MeetingRecorder with dual-stream capture"
```

---

### Task 4: MeetingPipeline — dual VAD + transcription orchestrator

**Files:**
- Create: `meeting.py`
- Test: `tests/test_meeting.py`

**Step 1: Write failing tests**

Test:
- Start/stop lifecycle
- System audio segments tagged "others"
- Mic segments tagged "you" (full mode)
- Segments ordered by timestamp across both streams
- Periodic flush to MeetingStore
- Status callbacks

**Step 2: Implement MeetingPipeline**

Fork `classnote.py` with these changes:
- Two `VADSegmenter` instances (system + mic) with meeting-tuned params (silence=400ms, max=20s)
- Single worker thread dequeues from both segmenters
- Each segment carries `speaker` tag based on source
- No Stream B correction (single stream only)
- `MeetingRecorder` integration for audio capture
- `_flush_pending()` writes to MeetingStore with speaker labels

**Step 3: Run tests — expect PASS**

**Step 4: Commit**
```bash
git add meeting.py tests/test_meeting.py
git commit -m "feat(meeting): add MeetingPipeline with dual VAD and speaker labels"
```

---

### Task 5: App detection API

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

**Step 1: Write failing tests**

```python
def test_meeting_apps_endpoint():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/meeting/apps")
    assert resp.status_code == 200
    assert "apps" in resp.json()
```

**Step 2: Implement endpoint**

`GET /api/meeting/apps` — calls `SystemAudioCapture.get_running_apps()`, filters against known meeting bundle IDs, returns sorted list with known apps first.

Known bundle IDs stored as a constant dict in `meeting.py`:
```python
KNOWN_MEETING_APPS = {
    "us.zoom.xos": "Zoom",
    "com.microsoft.teams2": "Microsoft Teams",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.apple.FaceTime": "FaceTime",
    ...
}
```

**Step 3: Run tests — expect PASS**

**Step 4: Commit**
```bash
git add app.py tests/test_app.py meeting.py
git commit -m "feat(meeting): add app detection endpoint"
```

---

### Task 6: WebSocket /ws/meeting + REST API endpoints

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

**Step 1: Write failing tests for REST endpoints**

- `GET /api/meeting/status` — active/paused state
- `GET /api/meeting/meetings` — list meetings (with search)
- `GET /api/meeting/meetings/{id}` — get meeting with segments
- `DELETE /api/meeting/meetings/{id}` — delete meeting + audio
- `GET /api/meeting/meetings/{id}/audio` — serve audio file
- `POST /api/meeting/meetings/{id}/export` — native save dialog
- `PATCH /api/meeting/meetings/{id}/segments/{index}` — edit segment text
- Labels CRUD endpoints (same pattern as ClassNote)

**Step 2: Implement WebSocket handler**

`/ws/meeting` handles: `start` (with app_bundle_id, mode, title), `stop`, `pause`, `resume`, `discard`, `status`

Sends: `{type: "segment", text, speaker, start_ms, end_ms, ghost}`, `{type: "status", ...}`, `{type: "error", ...}`

**Step 3: Implement REST endpoints** — mirror ClassNote patterns

**Step 4: Run tests — expect PASS**

**Step 5: Commit**
```bash
git add app.py tests/test_app.py
git commit -m "feat(meeting): add WebSocket and REST API endpoints"
```

---

### Task 7: Wire MeetingPipeline into main.py

**Files:**
- Modify: `main.py`

**Step 1: Add MeetingStore + MeetingPipeline initialization**

```python
from meeting_store import MeetingStore
from meeting import MeetingPipeline

meeting_store = MeetingStore()
meeting_pipeline = MeetingPipeline(transcriber=transcriber, store=meeting_store)
```

Pass to `create_app()`.

**Step 2: Commit**
```bash
git add main.py
git commit -m "feat(meeting): wire MeetingPipeline into app startup"
```

---

### Task 8: Frontend — HTML + CSS

**Files:**
- Modify: `static/index.html`
- Modify: `static/style.css`

**Step 1: Add Meeting sidebar item + page structure**

- Sidebar: "Meeting" item with video/phone icon
- Hero section: blue/purple gradient, pills: "Live transcription", "Per-app capture", "Speaker labels", "Offline & private"
- Recording view: app picker dropdown, mode toggle (Listen/Full), start/stop button, live transcript area
- Review view: segments with speaker labels, click-to-seek, edit button
- Meeting list: cards with title, app name, duration, mode badge, delete/download buttons

**Step 2: Add CSS**

- `.page-hero-meeting` — blue/purple gradient
- `.segment-you` — blue left-border
- `.segment-others` — default styling
- `.speaker-label` — small inline badge
- `.mode-toggle` — Listen/Full toggle button group
- `.app-picker` — styled dropdown

**Step 3: Commit**
```bash
git add static/index.html static/style.css
git commit -m "feat(meeting): add Meeting HTML structure and CSS"
```

---

### Task 9: Frontend — meeting.js

**Files:**
- Create: `static/meeting.js`
- Modify: `static/index.html` (add script tag)

**Step 1: Implement meeting.js**

Structure mirrors `classnote.js`:
- WebSocket connection to `/ws/meeting`
- App picker: fetch `/api/meeting/apps`, populate dropdown
- Mode toggle: Listen/Full
- Recording: start/stop with countdown
- Live transcript: render segments with speaker labels + colors
- Review view: click-to-seek audio, edit mode
- Meeting list: fetch/render/delete/download
- Export via POST to `/api/meeting/meetings/{id}/export`

**Step 2: Commit**
```bash
git add static/meeting.js static/index.html
git commit -m "feat(meeting): add meeting.js with recording, review, and list UI"
```

---

### Task 10: Integration tests + hotkey preemption

**Files:**
- Modify: `tests/test_app.py`
- Modify: `tests/test_meeting.py`

**Step 1: Add integration tests**

- Full lifecycle: start meeting → receive segments → stop → review
- Hotkey preemption: dictation pauses meeting, resumes after
- Mode switching: listen vs full mode segments
- App detection with mock SCShareableContent

**Step 2: Verify coverage**

Run: `python3 -m pytest tests/ -v --cov=. --cov-report=term-missing`
Target: 80%+ on new files

**Step 3: Commit**
```bash
git add tests/
git commit -m "test(meeting): add integration tests and hotkey preemption tests"
```
