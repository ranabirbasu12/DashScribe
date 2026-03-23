# Floating Bar & Transcription History Design

**Date:** 2026-02-17
**Status:** Approved

## Overview

Two features for DashScribe Phase 2:

1. **Floating bar** — a small always-on-top pill that shows recording/processing state with real-time audio waveforms, inspired by Wispr Flow's dictation bar
2. **Transcription history** — SQLite-backed history of all transcriptions, displayed in the main window

## Floating Bar

### Approach

Second PyWebView window with `frameless=True`, `transparent=True`, `on_top=True`. The bar is rendered as HTML/CSS/JS. State changes dynamically resize the window and update web content. Audio waveforms rendered on a `<canvas>` element fed amplitude data via WebSocket.

### States & Dimensions

**Idle**
- Small dark pill/capsule, centered above the dock
- ~120x24px, semi-transparent dark background (`rgba(30, 30, 30, 0.85)`), rounded corners
- On hover: tooltip "Click or hold Right Option to dictate"
- Click to start recording

**Recording**
- Expands to ~280x40px, anchored at center-bottom
- Left: X button (cancel recording, discard audio)
- Center: Real-time audio waveform bars (canvas, ~15-20 vertical bars moving with amplitude)
- Right: Red stop button (square-in-circle icon)
- Click stop OR release Right Option to finish

**Processing**
- Same ~280x40px
- Waveform replaced by pulsing/spinning indicator
- Buttons hidden
- Returns to idle on completion

**Error**
- Brief red flash, returns to idle
- For "recording too short" and similar errors

### Positioning

- Horizontally centered on screen
- ~70px above screen bottom (above the dock)
- Draggable via `pywebview-drag-region` CSS class

## State Manager

New module `state.py` — central state tracker with callback system.

### Responsibilities

- Tracks current app state: `idle`, `recording`, `processing`, `error`
- Holds registered callbacks — fires on state change
- Both hotkey module and WebSocket handler update state through this
- Floating bar frontend receives state updates via dedicated WebSocket

### Data Flow — Hotkey Trigger

1. Hotkey `_on_press` → state to `recording`, starts recorder
2. State manager notifies floating bar → bar expands, shows waveform
3. During recording, recorder streams amplitude data → state manager → bar canvas
4. Hotkey `_on_release` → state to `processing`, stops recorder
5. Bar shows processing animation
6. Transcription completes → clipboard copy → state to `idle`
7. Bar shrinks to idle pill

### Data Flow — Bar Click Trigger

1. Click on bar → sends "start" via WebSocket
2. Backend starts recording, state to `recording`
3. Same flow from step 2 above
4. Click stop button (or release Right Option) → sends "stop"

### Audio Amplitude for Waveforms

- `AudioRecorder._audio_callback` already receives chunks in real-time
- Add RMS amplitude calculation: `rms = np.sqrt(np.mean(chunk**2))`
- Push amplitude values into state manager
- Floating bar WebSocket receives amplitude at ~30fps, renders on canvas

### WebSocket Endpoints

- `/ws` — existing, for main window UI
- `/ws/bar` — new, lightweight: state changes + amplitude data only

## Transcription History

### Storage

- New module `history.py` with `TranscriptionHistory` class
- SQLite database at `~/.dashscribe/history.db`
- Schema: `id INTEGER PRIMARY KEY`, `text TEXT`, `timestamp TEXT`, `duration_seconds REAL`, `latency_seconds REAL`
- Save after every successful transcription (hotkey or UI)

### Main Window UI

- Scrollable history list below mic button and result area
- Each entry: timestamp, transcription text, copy button
- Most recent at top
- Search bar to filter by text content
- Load last 50, "load more" for pagination
- No sidebar — single-page layout preserved

## Main Window Lifecycle

### Current Behavior

`webview.start()` blocks. Window close = process exit.

### New Behavior

- Closing main window hides it; app keeps running (floating bar + hotkey stay active)
- Floating bar remains visible
- Reopen main window via floating bar tooltip or context menu
- Quit: right-click floating bar → "Quit DashScribe", or Cmd+Q

### Implementation

- Intercept PyWebView `closing` event to hide instead of destroy
- Floating bar window stays alive as separate window
- `webview.start()` stays running as long as floating bar window exists

### Launch Sequence

1. Start FastAPI server in daemon thread
2. Create floating bar window (always exists)
3. Create main window (closable/reopenable)
4. Start global hotkey listener
5. Warm up model in background

## New Files

| File | Purpose |
|------|---------|
| `state.py` | Central state manager with callbacks |
| `history.py` | SQLite transcription history |
| `static/bar.html` | Floating bar frontend |
| `static/bar.css` | Floating bar styles |
| `static/bar.js` | Floating bar logic + canvas waveform |

## Modified Files

| File | Changes |
|------|---------|
| `main.py` | Create two windows, new launch sequence |
| `app.py` | Add `/ws/bar` endpoint, history integration |
| `recorder.py` | Add amplitude streaming |
| `hotkey.py` | Use state manager instead of direct recording |
| `static/index.html` | Add history section |
| `static/style.css` | History styles |
| `static/app.js` | History loading, search, pagination |

## Dependencies

No new Python dependencies. SQLite is in the standard library. PyWebView already supports all required window features.
