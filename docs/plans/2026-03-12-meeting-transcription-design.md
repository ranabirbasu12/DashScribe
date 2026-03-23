# Meeting Transcription — Design

## Goal
Add per-app meeting transcription to DashScribe. Capture audio from meeting apps (Zoom, Teams, Meet, etc.) via ScreenCaptureKit, transcribe in real-time, and display a speaker-labeled transcript.

## Recording Modes

**Listen mode:** System audio only (meeting app). All segments labeled "Others".

**Full mode:** System audio + microphone. Mic segments labeled "You", system segments labeled "Others". AEC (NLMS filter) removes meeting audio bleeding into mic.

User toggles between modes before starting a recording.

## App Selection

- Query `SCShareableContent` at recording start for running applications
- Match against known meeting bundle IDs:
  - Zoom: `us.zoom.xos`
  - Teams: `com.microsoft.teams2`
  - Slack: `com.tinyspeck.slackmacgap`
  - Discord: `com.ggerganov.whisper` / `com.discord`
  - FaceTime: `com.apple.FaceTime`
  - Browsers (Chrome, Arc, Safari) for Google Meet / web-based meetings
- Show dropdown of detected meeting apps + "Other apps..." fallback
- Remember last-used app in settings

## Audio Capture

**System audio stream:**
- Extend `SystemAudioCapture` to support per-app filtering
- Use `SCContentFilter` with `excludingApplications` — exclude all apps except selected meeting app
- 16kHz mono, same as existing capture
- Write to `~/.dashscribe/meetings/{id}_system.wav`

**Mic stream (Full mode only):**
- Reuse `LectureRecorder` pattern (sounddevice 16kHz mono)
- AEC via existing NLMS filter to deduplicate meeting audio from mic
- Write to `~/.dashscribe/meetings/{id}_mic.wav`

## Pipeline

Two parallel VAD pipelines feeding a single worker thread:

- **System pipeline:** System audio -> VADSegmenter (silence=400ms, max=20s, min=1.5s) -> Whisper -> segments tagged `speaker="others"`
- **Mic pipeline (Full mode):** Mic audio -> AEC -> VADSegmenter (same params) -> Whisper -> segments tagged `speaker="you"`

Single worker thread dequeues from both pipelines, processes whichever segment sealed first (timestamp-ordered). No Stream B correction — just fast Stream A transcription.

Shared `WhisperTranscriber` instance. Segments queue up if both pipelines seal simultaneously.

VAD thresholds tighter than ClassNote (400ms vs 600ms) for faster meeting conversation turn-taking.

## Data Model

Separate SQLite store (`meeting_store.py`):

```sql
meetings: id, title, app_name, mode (listen/full), created_at, updated_at,
          duration_seconds, word_count, system_audio_path, mic_audio_path,
          status (recording/stopped/recovered)

meeting_segments: id, meeting_id, segment_index, speaker (you/others),
                  text, start_ms, end_ms

meeting_labels / meeting_label_map: same pattern as ClassNote
```

## Frontend

**Main view:** Hero section (blue/purple gradient), filter bar, meeting list cards with delete/download buttons. Same layout pattern as ClassNote.

**Recording view:**
- App picker dropdown + mode toggle (Listen / Full) at top
- Manual start/stop button
- Live transcript with speaker-colored segments
  - "You" segments: subtle blue left-border
  - "Others" segments: neutral/default styling
- Speaker label inline on each segment

**Review view:**
- Click-to-seek on segments (plays system audio track)
- Edit mode via pencil button (same as ClassNote)
- Speaker labels persist in review
- Download exports to .txt with speaker labels

## Integration

- `MeetingPipeline` in `meeting.py`
- `MeetingStore` in `meeting_store.py`
- `MeetingRecorder` in `meeting_recorder.py`
- WebSocket `/ws/meeting` + REST `/api/meeting/*` in `app.py`
- `static/meeting.js` for frontend
- Hotkey preemption: dictation pauses meeting recording, resumes after (same as ClassNote)

## Permissions

Requires "Screen & System Audio Recording" permission (already requested for AEC). No new permissions needed.

## Future

- Speaker diarization (distinguish multiple remote speakers beyond "You" vs "Others")
- Auto-detect meeting start/stop
- Meeting summaries via LLM
