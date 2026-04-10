# Audio Device Hot-Switching

**Date:** 2026-04-11
**Status:** Approved

## Problem

When an audio input device changes while DashScribe is running (e.g., Bluetooth earphones disconnected mid-dictation), all recording paths fail. The `sd.InputStream` is bound to the device that was default at stream creation time. When that device disappears, the stream's callback receives errors that are logged but ignored, and subsequent recording attempts fail because PortAudio's internal state is stale.

There is zero device-change detection in the codebase. The app is blind to audio topology changes.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Mid-recording behavior | Seamless continuation | Don't interrupt dictation flow; small audio gap (~100-200ms) is acceptable |
| Toast style | Detached pill above bar | macOS-native feel, doesn't alter bar silhouette |
| Audio gap handling | Accept small silence | Gap is under 200ms, well below VAD's 600ms silence threshold |
| Scope | All recording paths | Dictation (UI + hotkey), ClassNote, and Meeting all share the vulnerability |
| No-device handling | Stop gracefully, auto-resume on reconnect | Trust OS for normal switches; handle catastrophic case (no devices) specially |
| Toast duration | 4 seconds, all states | Consistent behavior regardless of recording/idle state |

## Architecture

### 1. Device Monitor (`device_monitor.py`)

New module. Uses CoreAudio via PyObjC to watch for default input device changes.

**Responsibilities:**
- Register `AudioObjectAddPropertyListenerBlock` on `kAudioObjectSystemObject` for property `kAudioHardwarePropertyDefaultInputDevice`
- When callback fires, query new default device name via `kAudioDevicePropertyDeviceNameCFString`
- Check if any input device exists (empty device list = no-device case)
- Expose callback interface: `on_device_changed(device_name: str | None)` where `None` means no input device available

**Interface:**
```python
class DeviceMonitor:
    def __init__(self):
        self.on_device_changed: Callable[[str | None], None] | None = None

    def start(self) -> None:
        """Register CoreAudio property listener. Call once at app startup."""

    def stop(self) -> None:
        """Unregister listener. Call on app quit."""

    def current_device_name(self) -> str | None:
        """Return name of current default input device, or None."""
```

**Threading:** CoreAudio callbacks are delivered on an internal dispatch queue. The monitor normalizes this by dispatching to a known thread before invoking `on_device_changed`.

**Isolation:** The monitor does not touch sounddevice or any recorder. It observes and notifies only.

### 2. Stream Recovery in AudioRecorder (`recorder.py`)

New method `reconnect_stream()`:

1. Stop and close the current (dead) `sd.InputStream` -- wrapped in try/except since the stream may be in a broken state
2. Create a new `sd.InputStream` with no `device=` param (picks up new OS default)
3. Start the new stream
4. Re-wire the `on_vad_chunk` callback so the pipeline keeps getting fed

**When called:**
- Device monitor fires `on_device_changed(name)` with a non-None name
- If `self.is_recording` is True: `reconnect_stream()` runs immediately
- If `self.is_recording` is False: no action needed; next `start()` naturally picks up new default

**The audio gap:** Between `old_stream.close()` and `new_stream.start()` (~100-200ms), the callback stops firing. The VAD segmenter sees no chunks and treats it as silence. This is well under the 600ms silence threshold -- no false segment boundary is triggered.

**Error in reconnect:** If the new `sd.InputStream()` constructor fails (no device available), set `_device_lost = True` flag and stop recording gracefully. The no-device handling takes over.

**Callback error escalation:** The existing `_audio_callback` logs `status` errors but ignores them. After this change, fatal status errors (device removed) are noted so that `reconnect_stream()` can be triggered even if CoreAudio's property listener is slightly delayed.

### 3. Stream Recovery in LectureRecorder and MeetingRecorder

Same pattern as AudioRecorder, adapted to each recorder's specifics.

**LectureRecorder (`lecture_recorder.py`):**
- Has its own `sd.InputStream` that writes directly to a WAV file
- `reconnect_stream()` closes old stream, opens new one
- WAV file stays open -- audio continues writing to the same file with a small gap
- The gap appears as silence in the WAV, which is fine for lecture recording

**MeetingRecorder (`meeting_recorder.py`):**
- Has a mic `sd.InputStream` (Full mode) plus `SystemAudioCapture` for system audio
- Only the mic stream needs recovery -- ScreenCaptureKit system audio is unaffected by input device changes
- Same `reconnect_stream()` pattern for the mic stream only

### 4. No-Device Handling

When the device monitor reports `None` (no input devices available):

**If recording (any path):**
- Stop the stream, keep chunks captured so far
- Set `_device_lost = True` flag on the active recorder
- Show toast: "No microphone found -- reconnect to continue"
- Do NOT transition to error state or discard audio

**If idle:**
- Set `_device_lost = True`
- Next `start()` attempt returns an error explaining no mic is available

**When a device reappears:**
- Device monitor fires again with the new device name
- If `_device_lost` was True and we were recording: auto-resume by creating a new stream, clear the flag, show toast "Input switched to {name}"
- If we were idle: clear the flag, show toast "{name} connected"

**ClassNote/Meeting specifics:** These are long-running sessions. On device loss, they pause (stop feeding the VAD) rather than fully stopping. On device reappearance, they resume seamlessly with the new stream.

### 5. Toast Notification in the Bar

**Window mechanics:**
- The bar window currently resizes per state (idle: 80x20, recording: 244x42, etc.)
- When a toast needs to show, temporarily expand the window height upward by ~36px to make room
- The capsule stays anchored at the bottom of the window; the toast renders in the transparent space above
- After the toast dismisses (4s), shrink the window height back
- The expansion is fast (150ms) and the toast fades in over 200ms

**Toast element (`bar.html`):**
- New `<div id="bar-toast">` element, absolutely positioned above the capsule
- Styled as detached pill: `border-radius: 999px`, dark translucent background (`rgba(30, 30, 30, 0.92)`), light text
- Blue dot indicator + mic icon + device name text
- `bottom: 100%; margin-bottom: 10px` relative to the bar container

**Toast lifecycle (`bar.js`):**
- `showToast(message)`: expand window, fade in toast, set 4s dismiss timer
- `hideToast()`: fade out over 400ms, then shrink window
- Debounced: if a new toast arrives while one is showing, replace text and reset timer

**Communication:**
- Device monitor notifies via new WebSocket message types:
  - `{"type": "device_changed", "device": "Built-in Microphone"}` -- normal switch
  - `{"type": "device_lost"}` -- no input device available
  - `{"type": "device_restored", "device": "Built-in Microphone"}` -- device reappeared after loss
- Sent to both `/ws/bar` and `/ws` (main window) clients
- `bar.js` handles these messages to show/hide the toast
- Main window `app.js` can show a subtler inline notification if desired

### 6. Wiring in `main.py`

- Create `DeviceMonitor` instance in `main()`
- Register a callback that:
  1. Determines which recorder(s) are currently active
  2. Calls `reconnect_stream()` on active recorders (UI recorder, hotkey recorder, ClassNote/Meeting recorders)
  3. Sends the appropriate WebSocket notification to bar and main window clients
  4. Handles no-device / device-reappearance state transitions
- Start the monitor after recorders and event tap are initialized
- Stop the monitor on app quit

## State Machine

```
NORMAL (device present, stream healthy)
  |
  |-- device_changed(new_name) --> if recording: reconnect_stream() + toast
  |                                if idle: toast only
  |
  |-- device_lost(None) --> DEVICE_LOST
  
DEVICE_LOST (no input device)
  |
  |-- if was recording: stream stopped, chunks preserved, toast "No mic found"
  |-- if was idle: flag set, start() blocked
  |
  |-- device_changed(new_name) --> NORMAL
  |   if was recording: auto-resume + toast "Input switched to {name}"
  |   if was idle: clear flag + toast "{name} connected"
```

## Files to Create

| File | Purpose |
|------|---------|
| `device_monitor.py` | CoreAudio default input device listener |

## Files to Modify

| File | Changes |
|------|---------|
| `recorder.py` | Add `reconnect_stream()`, `_device_lost` flag, improve callback error handling |
| `lecture_recorder.py` | Add `reconnect_stream()` for ClassNote recovery |
| `meeting_recorder.py` | Add `reconnect_stream()` for Meeting mic recovery |
| `main.py` | Wire DeviceMonitor, connect to all recorders and WebSocket broadcast |
| `app.py` | Add device_changed/device_lost/device_restored WebSocket message types |
| `static/bar.html` | Add `#bar-toast` element |
| `static/bar.css` | Toast pill styles, window headroom |
| `static/bar.js` | `showToast()`/`hideToast()`, handle new WebSocket message types |

## Edge Cases

1. **Rapid device switches** (BT disconnect -> built-in -> new BT): Debounce in device monitor. Only fire callback after 100ms stability to avoid redundant reconnects.
2. **Device switch during processing** (stream already stopped): Toast only, no stream work needed.
3. **Device switch during ClassNote pause**: ClassNote is already paused for dictation. The dictation recorder reconnects. ClassNote resumes on its own schedule.
4. **Multiple recorders active simultaneously**: Unlikely but possible (e.g., meeting + hotkey). Each recorder reconnects independently.
5. **App launch with no input device**: `start()` fails with clear error message. Device monitor watches for first device appearance.
6. **Same device re-selected**: CoreAudio may fire the listener even when the "new" default is the same device. Check device ID before reconnecting; skip if unchanged.

## Future Work

- **Device picker in settings**: Let users pin a specific input device instead of always following the OS default. Fall back to default if pinned device disappears.
- **Device quality indicator**: Show input level/quality in the bar to help users confirm the right mic is active.
