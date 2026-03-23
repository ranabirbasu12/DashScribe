# ClassNote — Live Lecture Transcription

## Overview
Long-running lecture capture with live transcription, rolling correction, audio-mapped review, and label organization. Designed for university students capturing lectures on their laptops.

## Success Criteria
- **Ghost text latency**: <3s from end of speech to ghost text appearing in UI
- **Correction latency**: <10s from segment seal to corrected text replacing ghost text
- **Resource usage**: <30% CPU and <500MB additional RAM during a 1-hour lecture
- **Session reliability**: Crash at any point during a 3-hour lecture loses at most the current unsaved segment (~30s of text). Audio is never lost.
- **Storage**: ~115MB/hour WAV audio. 30-day auto-cleanup keeps disk usage bounded.
- **Accuracy**: Matches or exceeds single-pass Whisper accuracy via rolling correction (correction should never make text worse)

## Core Flow
Start → live ghost-text transcription → rolling correction solidifies text → stop → review with click-to-play audio segments

## Architecture

### Live Capture Phase
- **Audio capture**: Dedicated `LectureRecorder` wrapping `sd.InputStream` for long-running mic capture (not the dictation `AudioRecorder`)
- **Audio archival**: Raw audio streamed to WAV file incrementally via `wave` module (~115MB/hour at 16kHz mono 16-bit). WAV header written with placeholder size, fixed on stop.
- **Stream A (fast)**: VADSegmenter chunks audio at silence boundaries → each segment transcribed immediately → ghost text shown in UI
- **Stream B (rolling correction)**: Opportunistic — runs only when transcriber is idle. After each new segment, if no Stream A work is pending, re-transcribe last 2-3 segments as one merged chunk → corrected text replaces ghost lines. Correction is treated as an atomic window (not split back to individual segments).
- **Dictation preemption**: If F5 hotkey fires, ClassNote yields the mic and pauses its pipeline. Dictation AudioRecorder starts normally. After dictation completes, ClassNote reclaims the mic and resumes. Brief gap in audio (~5-30s) is acceptable; a "— Dictation break —" separator is inserted.
- **Periodic persistence**: Segments are flushed to SQLite every 30 seconds during recording, not just on stop. On crash, only the current in-progress segment is lost.

### Post-Lecture Phase
- **Review screen**: Read-only transcript with click-to-play audio segments (v1). Inline editing in v2.
- **Audio mapping**: Each transcript line stores (start_ms, end_ms) referencing the archived audio file
- **Re-transcribe**: Optional button to re-process entire lecture in large overlapping windows
- **Auto-cleanup**: Audio files deleted after configurable retention (default 30 days), transcript kept forever

### Crash Recovery
- WAV file is written incrementally — always valid up to the last written frame (header fixed on stop; on crash, audio is recoverable via raw PCM length)
- Segments flushed to SQLite every 30s. Lecture row has `status = 'recording'`
- On next app launch, detect lectures with `status = 'recording'` and `updated_at` older than 5 minutes → mark as `status = 'recovered'`
- Recovered lectures appear in the lecture list with a "Recovered" badge. User can re-transcribe from the saved audio or delete.
- WAV header fixup: on recovery, read raw data size and rewrite the RIFF/data chunk headers

## UX

### Starting a Session
1. User clicks "ClassNote" tab in dashboard sidebar
2. Sees past lectures list with label filters and search (empty state: "No lectures yet. Start your first lecture!" with prominent button)
3. Clicks "Start Lecture"
4. Optional: enters a title (defaults to "Lecture — Mar 11, 2:05 PM")
5. 3-second countdown with cancel button before recording begins (prevents accidental starts)
6. Recording begins after countdown

### During Recording
- **Top bar**: Editable title | red pulsing dot (CSS animation matching existing model-status dot) | elapsed time (client-side JS timer) | Pause button | Stop button | Discard button (with confirmation)
- **Main area**: Flowing document, newest text at bottom, auto-scrolls
  - Ghost lines: `color: #999999` (--text-secondary), `font-style: italic` — 4.1:1 contrast ratio against #1a1a1a, meets WCAG AA for large text
  - Solidified lines: `color: #f0f0f0` (--text-primary), normal weight, full opacity
  - Transition: CSS transition on color/font-style, 0.3s ease (--duration-normal, --ease-apple)
- **Pause**: Stops mic capture, inserts "— Paused at 23:41 —" separator with timestamp, timer pauses. Resume button replaces pause.
- **Stop**: Ends session, final flush to SQLite, fixes WAV header, transitions to review screen
- **Discard**: Confirmation dialog → deletes audio file + DB records, returns to lecture list
- **Auto-scroll**: Scrolls to bottom as new lines appear. Pauses if user scrolls up more than 50px (dead zone prevents false triggers). "Jump to latest" button appears (keyboard-focusable).
- **Sidebar indicator**: Red dot on ClassNote nav item while recording is active, visible from any tab

### Error States During Recording
- **Mic disconnect**: Warning banner in top bar "Microphone disconnected — reconnect to continue". Recording pauses automatically. Auto-resumes when mic reconnects. Audio gap noted with separator.
- **Disk space low** (<500MB free): Warning banner "Low disk space — recording may stop soon". At <100MB, recording stops gracefully with save.
- **Transcription failure**: Individual segment failure is silent (ghost text just doesn't appear for that segment). 3+ consecutive failures show warning "Transcription issues — audio is still being saved".
- **WebSocket disconnect**: Client auto-reconnects. Missed segments fetched via REST fallback on reconnect.

### Navigation During Recording
- Recording continues in the background when navigating to other tabs (Home, File, etc.)
- Sidebar ClassNote item shows red dot indicator
- Clicking back to ClassNote tab shows current live transcript
- Closing the main window does NOT stop recording (same as minimizing). Only explicit Stop/Discard ends it.
- Cmd+Q shows confirmation: "A lecture is currently recording. Stop and save before quitting?"

### Dictation Preemption
- When F5 fires during ClassNote recording: brief "Dictation active" indicator in ClassNote top bar
- Capsule bar works normally for dictation
- ClassNote ghost lines pause and a "— Dictation break —" separator is inserted
- On dictation complete, ClassNote resumes with next segment

### Post-Lecture Review
- **Top bar**: Title (editable) | date & time | duration | word count | label chips (add/remove)
- **Main area**: Read-only transcript. Hover highlights segment. Click plays audio chunk via Web Audio API. Speaker icon on hover. Tab/Enter to play, arrow keys to navigate segments (keyboard accessible).
- **Bottom bar**: "Re-transcribe Lecture" (shows estimated time) | "Delete Lecture" (with confirmation)
- **Audio playback**: Inline player (play/pause, scrub within segment), active segment highlighted. Click another segment to switch. Playback stops at segment end (no auto-advance in v1).
- **Recovered lectures**: "Recovered" badge, option to re-transcribe from saved audio

### Lecture List (ClassNote Home)
- Cards: title, date, duration, word count, label chips, 1-line transcript preview
- Sort: date (default), title, duration
- Filter: by label, search by title/content
- "Start Lecture" button pinned at top
- Lectures with expired audio show "Audio expired" badge
- Recovered lectures show "Recovered" badge
- Empty state: illustration + "Start your first lecture" CTA

### Labels/Tags
- Colored chips using existing semantic color tokens (--red, --green, --blue, --orange, --accent)
- Autocomplete from existing or create new
- Auto-assigned color from palette, editable
- Filterable from lecture list

## Technical Details

### Audio Capture
- **LectureRecorder**: New class wrapping `sd.InputStream` for long-running capture. Separate from dictation `AudioRecorder` to avoid lifecycle conflicts.
- **Mic exclusivity**: Only one recorder active at a time. When dictation preempts, ClassNote's LectureRecorder stops and yields the mic. Dictation's AudioRecorder starts. On dictation complete, LectureRecorder restarts.
- **WAV streaming**: Uses Python `wave` module. Opens file on start, writes frames incrementally via `writeframes()`. On stop, closes file (which fixes header). On crash, header is invalid but PCM data is intact — recovery rewrites header from data size.
- **Single session constraint**: Backend enforces max one active ClassNote session at a time.

### VAD Tuning for Lectures
- All three thresholds passed as constructor parameters to `VADSegmenter` (refactor existing class to accept `silence_threshold_ms` and `min_segment_duration_s` as constructor params alongside existing `max_segment_duration_s`)
- SILENCE_THRESHOLD_MS = 1000 (up from 600ms — lectures have longer natural pauses)
- MAX_SEGMENT_DURATION_S = 30 (up from 20s — lecturers speak in longer stretches)
- MIN_SEGMENT_DURATION_S = 2 (up from 1s — avoid tiny fragments)

### Transcriber Access
- **Single transcriber, ClassNote-internal scheduling**: The priority queue lives inside `ClassNotePipeline` and only governs ClassNote's own Stream A and Stream B work. It does NOT wrap the global transcriber.
- **Dictation contention**: Dictation (via `hotkey.py`) continues to call `transcriber.transcribe_array()` directly, contending on the existing `WhisperTranscriber._lock` (RLock). ClassNote's streams also acquire this same lock. This means dictation may wait up to ~5s if Stream A/B is mid-inference on a 30s segment — acceptable since the user is switching mental context.
- **Stream B is opportunistic**: ClassNotePipeline only enqueues Stream B work when no Stream A jobs are pending and the pipeline is not paused. Stream B checks a flag before starting each job; if paused or Stream A work arrived, it yields.
- **No second model instance**: Avoids ~3GB RAM cost. Single mlx-whisper instance shared via the existing RLock.

### Rolling Correction
- After segment N completes (Stream A), if transcriber is idle, enqueue a Stream B job for segments [N-2, N-1, N]
- Merge the audio of those segments into one chunk and re-transcribe as a single pass
- **Atomic correction window**: The corrected text replaces the entire 3-segment window as one solidified block. No attempt to split back to individual segments. The correction window becomes a single entry in the UI with the combined time range.
- Each correction window stores `correction_group_id` linking the original segments, and the combined corrected text
- Mark all segments in the window as `is_corrected = true`
- If Stream B is preempted or skipped for some windows, those segments remain as ghost text until the next successful correction pass covers them

### WebSocket Protocol (/ws/classnote)
Server → Client:
- {type: "segment", index: N, text: "...", ghost: true, start_ms: N, end_ms: N} — Stream A result
- {type: "correction", start_index: N, end_index: M, text: "...", start_ms: N, end_ms: M} — Stream B atomic correction
- {type: "status", state: "recording|paused|stopped|recovered"}
- {type: "error", message: "...", recoverable: true|false}

Client → Server:
- {action: "start", title: "Physics 201"} — title validated: max 255 chars, HTML stripped
- {action: "pause"}
- {action: "resume"}
- {action: "stop"}

Action field validated against allowlist. Unknown actions rejected with error message.

### Dictation Preemption — Integration Design
- `ClassNotePipeline` exposes `pause()` and `resume()` methods
- `pause()`: stops LectureRecorder, inserts "— Dictation break —" separator, sets `_paused` flag so worker skips new jobs
- `resume()`: restarts LectureRecorder, clears `_paused` flag, worker resumes processing
- **Integration point**: `app.py:create_app()` stores the active `ClassNotePipeline` instance (or None) on the app state. `GlobalHotkey.__init__` receives a `get_classnote_pipeline` callback (a callable returning the current pipeline or None).
- **Modified hotkey flow in `_on_press`**:
  1. Check `get_classnote_pipeline()` — if an active pipeline exists, call `pipeline.pause()` (stops LectureRecorder, yields mic)
  2. Start dictation `AudioRecorder` as normal
  3. In `_process_recording` after transcription completes, check pipeline again and call `pipeline.resume()` (restarts LectureRecorder)
- **No ClassNote active**: `get_classnote_pipeline()` returns None, hotkey flow is unchanged from current behavior
- Brief audio gap during dictation is expected and marked in transcript

### Database Schema
All tables in existing `~/.dashscribe/history.db`. New `LectureStore` class follows `TranscriptionHistory` patterns (own `_connect()` with WAL mode, `_migrate_schema()` for future changes). All timestamps ISO 8601 UTC.

```sql
CREATE TABLE lectures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    duration_seconds REAL,
    word_count INTEGER DEFAULT 0,
    audio_path TEXT,
    status TEXT NOT NULL DEFAULT 'recording'
);

CREATE TABLE lecture_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    is_corrected BOOLEAN DEFAULT 0,
    correction_group_id INTEGER
);

CREATE INDEX idx_segments_lecture ON lecture_segments(lecture_id);

CREATE TABLE lecture_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL
);

CREATE TABLE lecture_label_map (
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES lecture_labels(id),
    PRIMARY KEY (lecture_id, label_id)
);
```

Note: `audio_expires_at` removed — computed dynamically from `created_at + retention_setting`. `ghost_text` column removed — not user-facing and doubles storage. `updated_at` added for crash detection and sort-by-modified.

### Storage
- Audio files: ~/.dashscribe/lectures/lecture_{id}_{timestamp}.wav (directory created with mode 0700)
- Auto-cleanup: daemon thread on app launch deletes audio where `created_at + retention_days < now` AND `status != 'recording'`
- Transcript and segment data remain permanently
- Audio paths constructed server-side only (never from client input), validated to be within ~/.dashscribe/lectures/

### New Files
- lecture_recorder.py — LectureRecorder (long-running mic capture + WAV streaming). Mirrors recorder.py pattern.
- lecture_store.py — LectureStore (SQLite CRUD for lectures, segments, labels). Mirrors history.py pattern.
- classnote.py — ClassNotePipeline (session management, streams A and B, coordinates recorder + store + transcriber). Mirrors pipeline.py pattern.
- static/classnote.js — ClassNote tab UI logic
- ClassNote tab additions to static/index.html and static/style.css
- New WebSocket endpoint /ws/classnote in app.py (or extracted to a router if app.py exceeds ~1600 lines)
- tests/test_lecture_recorder.py, tests/test_lecture_store.py, tests/test_classnote.py — one test file per module

### Memory Budget (3-hour lecture)
- LectureRecorder buffer: ~1.9MB (max 30s segment at 16kHz mono float32)
- VADSegmenter state: negligible
- Segment text in memory: ~180KB (3 hours of transcript text)
- Stream B sliding window: ~5.5MB (3 segments × 30s of audio)
- **Total additional RAM**: <10MB beyond the ~500MB mlx-whisper model

## Testing Strategy

### Unit Tests (one test file per module)
- **test_lecture_recorder.py**: Mock `sd.InputStream`, test start/stop/pause lifecycle, WAV file creation, frame writing
- **test_lecture_store.py**: Test CRUD operations, segment persistence, crash recovery detection, cascade deletes, migration. Use temp SQLite DB.
- **test_classnote.py**: Mock transcriber and recorder. Test:
  - Stream A: feed audio → get ghost segments
  - Stream B: correction triggers when idle, skipped when busy
  - Pause/resume lifecycle
  - Periodic flush (mock timer, verify DB writes)
  - Stop → final save
  - Dictation preemption: pause → resume → gap handling
- **Priority queue**: Test ordering (dictation > Stream A > Stream B), preemption behavior
- **Rolling correction**: Test atomic window merging, correction_group_id assignment
- **WAV recovery**: Test header fixup from raw PCM data

### WebSocket Tests (test_app.py additions)
- Connect to /ws/classnote, send start action, verify status message
- Send invalid action, verify error response
- Test title validation (too long, HTML content)
- Test pause/resume/stop lifecycle via WebSocket

### Integration Tests
- Full pipeline: mock audio input → VAD → transcription → WebSocket output → DB persistence
- Crash simulation: kill pipeline mid-session, verify recovery on restart

### Coverage Target
- All three modules (lecture_recorder.py, lecture_store.py, classnote.py): 80% each (matching project-wide target)
- Follow existing mock patterns from tests/test_pipeline.py (FakePipeline style)

## Not in v1
- Inline editing in review screen
- Export (PDF/Markdown/plain text)
- Calendar integration (EventKit auto-suggest — moved from v1 to v2)
- Auto-start from calendar notification
- Speaker diarization
- Full auto re-transcribe on stop
- Audio compression (FLAC/Opus)
- Per-lecture retention override

## Ship Order
1. v1: Live capture + read-only review with audio playback + labels + crash recovery
2. v2: Calendar integration + inline editing + re-transcribe with edit preservation
3. v3: Export formats + calendar auto-triggers + audio compression
