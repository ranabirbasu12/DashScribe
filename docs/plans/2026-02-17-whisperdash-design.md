# DashScribe Design Document

**Date:** 2026-02-17
**Status:** Approved

## Overview

DashScribe is a local, offline, low-latency dictation tool for macOS. It replaces cloud-dependent tools like Wispr Flow by running OpenAI's open-source Whisper model directly on Apple Silicon via mlx-whisper.

## Requirements

- **Platform:** macOS (Apple Silicon M4 Pro)
- **Interaction:** Windowed web-based app with push-to-talk recording
- **Model:** mlx-whisper large-v3-turbo (~1.5GB)
- **Output:** Raw Whisper transcription, auto-copied to clipboard
- **Language:** English only
- **Network:** Fully offline, zero network dependency
- **Future:** Global hotkey binding (phase 2)

## Architecture

```
PyWebView Window
├── Frontend (HTML/CSS/JS)
│   ├── Record button (push-to-talk)
│   ├── Status indicator (idle/recording/transcribing)
│   ├── Transcription display
│   └── "Copied!" feedback
│
│   WebSocket
│
└── FastAPI Backend
    ├── Recorder (sounddevice) → WAV audio
    ├── Transcriber (mlx-whisper) → text
    └── Clipboard Manager (pyperclip)
```

### Data Flow

1. User holds record button → frontend sends "start" via WebSocket
2. Backend starts recording audio via sounddevice (16kHz mono)
3. User releases → frontend sends "stop"
4. Backend stops recording, saves temp WAV, feeds to mlx-whisper
5. Transcription result sent back via WebSocket
6. Backend copies text to clipboard via pyperclip
7. Frontend shows the text + "Copied to clipboard" confirmation

## Modules

### recorder.py

Captures audio from the default microphone using `sounddevice`. Records into a NumPy array at 16kHz mono (Whisper's native sample rate — no resampling needed). Writes to a temporary WAV file when recording stops.

### transcriber.py

Loads the mlx-whisper large-v3-turbo model on startup (one-time ~2-3s load). Takes a WAV file path, runs inference, returns transcribed text. Model stays warm in memory for instant subsequent transcriptions.

### clipboard.py

Thin wrapper around `pyperclip` to copy transcribed text to the system clipboard.

### app.py

FastAPI server with a WebSocket endpoint. Manages the record → transcribe → clipboard pipeline. Serves the static frontend files. PyWebView opens a native macOS window pointing at localhost. Recording and transcription run in background threads to keep the event loop responsive.

### Frontend (static/)

Single-page app: index.html, style.css, app.js.

- Large circular mic button (hold to record, pulses red while recording, spinner while transcribing)
- Transcription result area below the button
- Bottom status bar: model status (loading/ready), last transcription latency
- "Copied to clipboard" toast feedback
- Dark theme, ~450x550px window

## Project Structure

```
DashScribe/
├── app.py
├── recorder.py
├── transcriber.py
├── clipboard.py
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── requirements.txt
└── docs/
    └── plans/
```

## Dependencies

| Package | Purpose |
|---------|---------|
| mlx-whisper | Whisper inference optimized for Apple Silicon |
| fastapi | Backend server |
| uvicorn | ASGI server for FastAPI |
| websockets | WebSocket support |
| sounddevice | Audio capture |
| numpy | Audio data as arrays |
| scipy | WAV file writing |
| pyperclip | Clipboard access |
| pywebview | Native macOS window |

## Technical Decisions

1. **16kHz mono recording** — Whisper's native sample rate, no resampling overhead.
2. **Model pre-loaded in memory** — Load once at startup, keep warm. First transcription ~2-3s for model load, subsequent ones near-instant.
3. **Temp WAV files** — Record to NumPy array, write WAV to temp file, feed to mlx-whisper. Clean up after transcription.
4. **Single WebSocket connection** — One persistent connection for all events (start/stop/result/status) as JSON messages.
5. **Background threads** — Recording and transcription run in threads so FastAPI event loop stays responsive.

## Phase 2 (Future)

- Global keyboard shortcut (e.g. Cmd+Shift+Space) to start/stop recording from anywhere
- Menubar icon with recording state
- Transcription history
