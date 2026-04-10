# Audio Device Hot-Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DashScribe seamlessly recover when the default audio input device changes (e.g., Bluetooth earphones disconnected mid-dictation), across all recording paths (dictation, ClassNote, Meeting), with a floating toast notification above the bar.

**Architecture:** A new `DeviceMonitor` module uses CoreAudio via PyObjC to watch the default input device. When it changes, each active recorder's new `reconnect_stream()` method tears down its dead `sd.InputStream` and opens a new one on the new default. A new WebSocket message type drives a detached pill toast in the floating bar UI.

**Tech Stack:** Python 3.11, sounddevice, PyObjC (CoreAudio framework), FastAPI WebSockets, vanilla HTML/CSS/JS, pytest with mocks.

**Spec:** `docs/superpowers/specs/2026-04-11-audio-device-hot-switching-design.md`

**File Structure:**

| File | Responsibility |
|------|----------------|
| `device_monitor.py` (new) | CoreAudio default input device listener + device name lookup |
| `tests/test_device_monitor.py` (new) | Unit tests with mocked CoreAudio |
| `recorder.py` (modify) | Add `reconnect_stream()`, `_device_lost` flag |
| `tests/test_recorder.py` (modify) | Reconnect + device-lost tests |
| `lecture_recorder.py` (modify) | Add `reconnect_stream()` |
| `tests/test_lecture_recorder.py` (modify) | Reconnect test |
| `meeting_recorder.py` (modify) | Add `reconnect_stream()` for mic stream |
| `tests/test_meeting_recorder.py` (modify) | Reconnect test |
| `app.py` (modify) | Broadcast device_changed/device_lost/device_restored messages |
| `main.py` (modify) | Wire DeviceMonitor to all recorders and WebSocket broadcast |
| `static/bar.html` (modify) | Add `#bar-toast` element |
| `static/bar.css` (modify) | Toast pill styles, window headroom |
| `static/bar.js` (modify) | `showToast()`/`hideToast()`, handle new message types |

---

## Task 1: DeviceMonitor skeleton

Create the `DeviceMonitor` class with the interface. No CoreAudio wiring yet — just the shape.

**Files:**
- Create: `device_monitor.py`
- Create: `tests/test_device_monitor.py`

- [ ] **Step 1: Write failing test for initialization**

Create `tests/test_device_monitor.py`:

```python
# tests/test_device_monitor.py
from device_monitor import DeviceMonitor


def test_device_monitor_initializes():
    monitor = DeviceMonitor()
    assert monitor.on_device_changed is None
    assert monitor.current_device_name() is None  # Not started yet


def test_device_monitor_callback_assignment():
    monitor = DeviceMonitor()
    called = []
    monitor.on_device_changed = lambda name: called.append(name)
    # Manually invoke the callback path (we haven't hooked CoreAudio yet)
    monitor._fire_change("Test Device")
    assert called == ["Test Device"]
```

- [ ] **Step 2: Run test — expect import failure**

Run: `cd /Users/ranabirbasu/GitHub/DashScribe && source venv/bin/activate && pytest tests/test_device_monitor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'device_monitor'`

- [ ] **Step 3: Create minimal DeviceMonitor**

Create `device_monitor.py`:

```python
# device_monitor.py
"""Monitors the default input audio device and fires callbacks on changes."""
from typing import Callable, Optional


class DeviceMonitor:
    """Watches the system default input device via CoreAudio.

    Fires on_device_changed(name) when the default input device changes.
    Fires on_device_changed(None) when no input device is available.
    """

    def __init__(self):
        self.on_device_changed: Optional[Callable[[Optional[str]], None]] = None
        self._started = False
        self._last_device_id: Optional[int] = None
        self._last_device_name: Optional[str] = None

    def start(self) -> None:
        """Register CoreAudio property listener. Idempotent."""
        if self._started:
            return
        self._started = True
        # CoreAudio wiring added in a later task.

    def stop(self) -> None:
        """Unregister listener. Idempotent."""
        if not self._started:
            return
        self._started = False

    def current_device_name(self) -> Optional[str]:
        """Return cached name of current default input device, or None."""
        return self._last_device_name

    def _fire_change(self, name: Optional[str]) -> None:
        """Internal: invoke the on_device_changed callback safely."""
        self._last_device_name = name
        cb = self.on_device_changed
        if cb is not None:
            try:
                cb(name)
            except Exception as e:
                print(f"DeviceMonitor callback error: {e}")
```

- [ ] **Step 4: Run test — expect pass**

Run: `pytest tests/test_device_monitor.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add device_monitor.py tests/test_device_monitor.py
git commit -m "feat(device-monitor): skeleton class with callback interface"
```

---

## Task 2: DeviceMonitor CoreAudio integration

Wire up the CoreAudio property listener for `kAudioHardwarePropertyDefaultInputDevice`.

**Files:**
- Modify: `device_monitor.py`
- Modify: `tests/test_device_monitor.py`

- [ ] **Step 1: Write failing test for start/stop lifecycle**

Append to `tests/test_device_monitor.py`:

```python
from unittest.mock import patch, MagicMock


def test_device_monitor_start_registers_listener():
    monitor = DeviceMonitor()
    with patch('device_monitor.AudioObjectAddPropertyListener') as mock_add:
        mock_add.return_value = 0  # noErr
        monitor.start()
        assert monitor._started is True
        assert mock_add.called


def test_device_monitor_stop_unregisters_listener():
    monitor = DeviceMonitor()
    with patch('device_monitor.AudioObjectAddPropertyListener') as mock_add, \
         patch('device_monitor.AudioObjectRemovePropertyListener') as mock_remove:
        mock_add.return_value = 0
        mock_remove.return_value = 0
        monitor.start()
        monitor.stop()
        assert monitor._started is False
        assert mock_remove.called


def test_device_monitor_start_is_idempotent():
    monitor = DeviceMonitor()
    with patch('device_monitor.AudioObjectAddPropertyListener') as mock_add:
        mock_add.return_value = 0
        monitor.start()
        monitor.start()  # Second call
        assert mock_add.call_count == 1
```

- [ ] **Step 2: Run test — expect failure**

Run: `pytest tests/test_device_monitor.py -v`
Expected: FAIL — `AudioObjectAddPropertyListener` doesn't exist in `device_monitor` yet.

- [ ] **Step 3: Implement CoreAudio wiring**

Replace `device_monitor.py` with:

```python
# device_monitor.py
"""Monitors the default input audio device and fires callbacks on changes."""
import ctypes
import ctypes.util
import threading
from typing import Callable, Optional

# CoreAudio constants (from AudioHardwareBase.h / AudioHardware.h)
kAudioObjectSystemObject = 1
kAudioHardwarePropertyDefaultInputDevice = 0x64496E20  # 'dIn '
kAudioObjectPropertyScopeGlobal = 0x676C6F62  # 'glob'
kAudioObjectPropertyElementMain = 0
kAudioDevicePropertyDeviceNameCFString = 0x6C6E616D  # 'lnam'
kAudioHardwarePropertyDevices = 0x64657623  # 'dev#'
kAudioDevicePropertyStreamConfiguration = 0x736C6179  # 'slay'
kAudioDevicePropertyScopeInput = 0x696E7074  # 'inpt'

# Load CoreAudio framework
_coreaudio = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
_corefoundation = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


# Function signature: OSStatus (*)(AudioObjectID, UInt32, const AudioObjectPropertyAddress*, void*)
_LISTENER_PROC = ctypes.CFUNCTYPE(
    ctypes.c_int32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress),
    ctypes.c_void_p,
)

AudioObjectAddPropertyListener = _coreaudio.AudioObjectAddPropertyListener
AudioObjectAddPropertyListener.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress),
    _LISTENER_PROC,
    ctypes.c_void_p,
]
AudioObjectAddPropertyListener.restype = ctypes.c_int32

AudioObjectRemovePropertyListener = _coreaudio.AudioObjectRemovePropertyListener
AudioObjectRemovePropertyListener.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress),
    _LISTENER_PROC,
    ctypes.c_void_p,
]
AudioObjectRemovePropertyListener.restype = ctypes.c_int32

AudioObjectGetPropertyData = _coreaudio.AudioObjectGetPropertyData
AudioObjectGetPropertyData.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress),
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
]
AudioObjectGetPropertyData.restype = ctypes.c_int32

CFStringGetLength = _corefoundation.CFStringGetLength
CFStringGetLength.argtypes = [ctypes.c_void_p]
CFStringGetLength.restype = ctypes.c_long

CFStringGetCString = _corefoundation.CFStringGetCString
CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
CFStringGetCString.restype = ctypes.c_bool

CFRelease = _corefoundation.CFRelease
CFRelease.argtypes = [ctypes.c_void_p]
CFRelease.restype = None

kCFStringEncodingUTF8 = 0x08000100


def _cfstring_to_str(cf_string_ptr: int) -> Optional[str]:
    """Convert a CFStringRef pointer to a Python str."""
    if not cf_string_ptr:
        return None
    length = CFStringGetLength(cf_string_ptr)
    buf_size = (length * 4) + 1  # UTF-8 can use up to 4 bytes per char
    buf = ctypes.create_string_buffer(buf_size)
    if CFStringGetCString(cf_string_ptr, buf, buf_size, kCFStringEncodingUTF8):
        return buf.value.decode("utf-8")
    return None


def _get_default_input_device_id() -> int:
    """Return AudioDeviceID of the current default input device (0 if none)."""
    addr = _AudioObjectPropertyAddress(
        kAudioHardwarePropertyDefaultInputDevice,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    )
    device_id = ctypes.c_uint32(0)
    size = ctypes.c_uint32(ctypes.sizeof(device_id))
    status = AudioObjectGetPropertyData(
        kAudioObjectSystemObject,
        ctypes.byref(addr),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(device_id),
    )
    if status != 0:
        return 0
    return device_id.value


def _get_device_name(device_id: int) -> Optional[str]:
    """Return the friendly name of an audio device, or None on failure."""
    if device_id == 0:
        return None
    addr = _AudioObjectPropertyAddress(
        kAudioDevicePropertyDeviceNameCFString,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    )
    cf_string = ctypes.c_void_p(0)
    size = ctypes.c_uint32(ctypes.sizeof(cf_string))
    status = AudioObjectGetPropertyData(
        device_id,
        ctypes.byref(addr),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(cf_string),
    )
    if status != 0 or not cf_string.value:
        return None
    try:
        return _cfstring_to_str(cf_string.value)
    finally:
        CFRelease(cf_string.value)


class DeviceMonitor:
    """Watches the system default input device via CoreAudio.

    Fires on_device_changed(name) when the default input device changes.
    Fires on_device_changed(None) when no input device is available.
    """

    def __init__(self):
        self.on_device_changed: Optional[Callable[[Optional[str]], None]] = None
        self._started = False
        self._last_device_id: Optional[int] = None
        self._last_device_name: Optional[str] = None
        self._listener_proc = None  # Must hold reference so ctypes doesn't GC it
        self._address = _AudioObjectPropertyAddress(
            kAudioHardwarePropertyDefaultInputDevice,
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectPropertyElementMain,
        )
        self._debounce_timer: Optional[threading.Timer] = None
        self._debounce_lock = threading.Lock()

    def start(self) -> None:
        """Register CoreAudio property listener. Idempotent."""
        if self._started:
            return

        # Initialize current device state before registering the listener.
        device_id = _get_default_input_device_id()
        self._last_device_id = device_id
        self._last_device_name = _get_device_name(device_id) if device_id else None

        def _listener(object_id, num_addresses, addresses_ptr, client_data):
            # Called from a CoreAudio thread. Debounce to avoid duplicate fires.
            self._schedule_check()
            return 0  # noErr

        self._listener_proc = _LISTENER_PROC(_listener)
        status = AudioObjectAddPropertyListener(
            kAudioObjectSystemObject,
            ctypes.byref(self._address),
            self._listener_proc,
            None,
        )
        if status != 0:
            print(f"DeviceMonitor: AudioObjectAddPropertyListener failed (status={status})")
            self._listener_proc = None
            return
        self._started = True

    def stop(self) -> None:
        """Unregister listener. Idempotent."""
        if not self._started:
            return
        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        if self._listener_proc is not None:
            AudioObjectRemovePropertyListener(
                kAudioObjectSystemObject,
                ctypes.byref(self._address),
                self._listener_proc,
                None,
            )
            self._listener_proc = None
        self._started = False

    def current_device_name(self) -> Optional[str]:
        """Return cached name of current default input device, or None."""
        return self._last_device_name

    def _schedule_check(self) -> None:
        """Debounce rapid fires from CoreAudio (e.g., BT disconnect bounce)."""
        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(0.1, self._check_and_fire)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _check_and_fire(self) -> None:
        """Query current device and fire callback if changed."""
        device_id = _get_default_input_device_id()
        name = _get_device_name(device_id) if device_id else None

        if device_id == self._last_device_id and name == self._last_device_name:
            return  # Same device — skip

        self._last_device_id = device_id
        self._fire_change(name)

    def _fire_change(self, name: Optional[str]) -> None:
        """Internal: invoke the on_device_changed callback safely."""
        self._last_device_name = name
        cb = self.on_device_changed
        if cb is not None:
            try:
                cb(name)
            except Exception as e:
                print(f"DeviceMonitor callback error: {e}")
```

- [ ] **Step 4: Run test — expect pass**

Run: `pytest tests/test_device_monitor.py -v`
Expected: PASS (5 tests)

Note: The CoreAudio library will actually load on macOS during import. The tests use `patch()` to avoid actually registering listeners, so this is safe.

- [ ] **Step 5: Commit**

```bash
git add device_monitor.py tests/test_device_monitor.py
git commit -m "feat(device-monitor): CoreAudio property listener with debounce"
```

---

## Task 3: AudioRecorder.reconnect_stream()

Add the reconnect method to `AudioRecorder` so it can swap its `sd.InputStream` mid-recording.

**Files:**
- Modify: `recorder.py:45-84`
- Modify: `tests/test_recorder.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_recorder.py`:

```python
def test_reconnect_stream_swaps_input_stream():
    rec = AudioRecorder()
    old_stream = MagicMock()
    rec._stream = old_stream
    rec.is_recording = True

    with patch('recorder.sd.InputStream') as mock_stream_cls:
        new_stream = MagicMock()
        mock_stream_cls.return_value = new_stream
        rec.reconnect_stream()

    old_stream.stop.assert_called_once()
    old_stream.close.assert_called_once()
    new_stream.start.assert_called_once()
    assert rec._stream is new_stream
    assert rec.is_recording is True
    assert rec._device_lost is False


def test_reconnect_stream_when_not_recording_is_noop():
    rec = AudioRecorder()
    rec._stream = None
    rec.is_recording = False
    with patch('recorder.sd.InputStream') as mock_stream_cls:
        rec.reconnect_stream()
        mock_stream_cls.assert_not_called()


def test_reconnect_stream_handles_dead_old_stream():
    rec = AudioRecorder()
    dead_stream = MagicMock()
    dead_stream.stop.side_effect = Exception("PortAudio error")
    dead_stream.close.side_effect = Exception("PortAudio error")
    rec._stream = dead_stream
    rec.is_recording = True

    with patch('recorder.sd.InputStream') as mock_stream_cls:
        new_stream = MagicMock()
        mock_stream_cls.return_value = new_stream
        # Should NOT raise despite old stream errors
        rec.reconnect_stream()

    new_stream.start.assert_called_once()
    assert rec._stream is new_stream


def test_reconnect_stream_failure_sets_device_lost():
    rec = AudioRecorder()
    rec._stream = MagicMock()
    rec.is_recording = True

    with patch('recorder.sd.InputStream', side_effect=Exception("No device")):
        rec.reconnect_stream()

    assert rec._device_lost is True
    assert rec.is_recording is False
    assert rec._stream is None


def test_recorder_initializes_device_lost_false():
    rec = AudioRecorder()
    assert rec._device_lost is False
```

- [ ] **Step 2: Run tests — expect failure**

Run: `pytest tests/test_recorder.py -v -k reconnect`
Expected: FAIL — `reconnect_stream` method and `_device_lost` attribute don't exist.

- [ ] **Step 3: Implement reconnect_stream**

In `recorder.py`, add `_device_lost = False` in `__init__` and add the new method. Replace lines 18-28 (the `__init__`) with:

```python
    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.channels = 1
        self.is_recording = False
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._sys_capture = None
        self._lock = threading.Lock()
        self.on_amplitude = None
        self.on_vad_chunk = None
        self._device_lost = False
```

Add this method after `stop_raw` (around line 167):

```python
    def reconnect_stream(self) -> bool:
        """Swap the input stream to the current OS default device.

        Called by DeviceMonitor when the default input device changes.
        No-op if not currently recording. Returns True on success.
        """
        with self._lock:
            if not self.is_recording:
                return False
            old_stream = self._stream
            self._stream = None

        # Tear down the old (likely dead) stream. Swallow errors — it may
        # already be in a broken state because its device disappeared.
        if old_stream is not None:
            try:
                old_stream.stop()
            except Exception:
                pass
            try:
                old_stream.close()
            except Exception:
                pass

        # Create a new stream on the (new) default device.
        try:
            new_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.float32,
                callback=self._audio_callback,
            )
            new_stream.start()
        except Exception as e:
            print(f"AudioRecorder.reconnect_stream failed: {e}")
            with self._lock:
                self._device_lost = True
                self.is_recording = False
            return False

        with self._lock:
            self._stream = new_stream
            self._device_lost = False
        return True
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_recorder.py -v`
Expected: PASS (all existing tests + 5 new)

- [ ] **Step 5: Commit**

```bash
git add recorder.py tests/test_recorder.py
git commit -m "feat(recorder): reconnect_stream for device hot-switching"
```

---

## Task 4: LectureRecorder.reconnect_stream()

Same pattern for ClassNote's `LectureRecorder`. The WAV file stays open across the reconnect.

**Files:**
- Modify: `lecture_recorder.py`
- Modify: `tests/test_lecture_recorder.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_lecture_recorder.py`:

```python
def test_lecture_reconnect_stream_swaps_input_stream(tmp_path):
    from unittest.mock import patch, MagicMock
    from lecture_recorder import LectureRecorder

    rec = LectureRecorder()
    wav_path = str(tmp_path / "lecture.wav")

    with patch('lecture_recorder.sd.InputStream') as mock_stream_cls:
        first_stream = MagicMock()
        mock_stream_cls.return_value = first_stream
        rec.start(wav_path)
        assert rec.is_recording is True

        second_stream = MagicMock()
        mock_stream_cls.return_value = second_stream
        rec.reconnect_stream()

    first_stream.stop.assert_called_once()
    first_stream.close.assert_called_once()
    second_stream.start.assert_called_once()
    assert rec._stream is second_stream
    # WAV file should still be open
    assert rec._wav_file is not None
    rec.stop()


def test_lecture_reconnect_stream_noop_when_not_recording(tmp_path):
    from unittest.mock import patch
    from lecture_recorder import LectureRecorder

    rec = LectureRecorder()
    with patch('lecture_recorder.sd.InputStream') as mock_stream_cls:
        rec.reconnect_stream()
        mock_stream_cls.assert_not_called()
```

- [ ] **Step 2: Run tests — expect failure**

Run: `pytest tests/test_lecture_recorder.py -v -k reconnect`
Expected: FAIL — method does not exist.

- [ ] **Step 3: Implement reconnect_stream**

Add this method to `LectureRecorder` in `lecture_recorder.py` after the `resume()` method (after line 99):

```python
    def reconnect_stream(self) -> bool:
        """Swap the input stream to the current OS default device.

        Called by DeviceMonitor when the default input device changes.
        The WAV file stays open across the reconnect. No-op if not recording.
        """
        if not self.is_recording:
            return False

        old_stream = self._stream
        self._stream = None

        if old_stream is not None:
            try:
                old_stream.stop()
            except Exception:
                pass
            try:
                old_stream.close()
            except Exception:
                pass

        try:
            new_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=512,
                callback=self._audio_callback,
            )
            new_stream.start()
        except Exception as e:
            print(f"LectureRecorder.reconnect_stream failed: {e}")
            self.is_recording = False
            return False

        self._stream = new_stream
        return True
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_lecture_recorder.py -v`
Expected: PASS (all existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add lecture_recorder.py tests/test_lecture_recorder.py
git commit -m "feat(lecture-recorder): reconnect_stream for device hot-switching"
```

---

## Task 5: MeetingRecorder.reconnect_stream()

Only the mic stream needs reconnection — ScreenCaptureKit system audio is unaffected.

**Files:**
- Modify: `meeting_recorder.py`
- Modify: `tests/test_meeting_recorder.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_meeting_recorder.py`:

```python
def test_meeting_reconnect_stream_only_swaps_mic(tmp_path):
    from unittest.mock import patch, MagicMock
    from meeting_recorder import MeetingRecorder

    rec = MeetingRecorder(mode="full")
    sys_path = str(tmp_path / "sys.wav")
    mic_path = str(tmp_path / "mic.wav")

    with patch('meeting_recorder.sd.InputStream') as mock_stream_cls, \
         patch.object(rec._sys_capture, 'start'), \
         patch.object(rec._sys_capture, 'stop', return_value=MagicMock()):
        first_mic = MagicMock()
        mock_stream_cls.return_value = first_mic
        rec.start(sys_path, mic_path)
        assert rec.is_recording is True

        second_mic = MagicMock()
        mock_stream_cls.return_value = second_mic
        rec.reconnect_stream()

    first_mic.stop.assert_called_once()
    first_mic.close.assert_called_once()
    second_mic.start.assert_called_once()
    assert rec._mic_stream is second_mic


def test_meeting_reconnect_stream_noop_in_listen_mode(tmp_path):
    from unittest.mock import patch
    from meeting_recorder import MeetingRecorder

    rec = MeetingRecorder(mode="listen")
    rec.is_recording = True  # Simulate started state
    with patch('meeting_recorder.sd.InputStream') as mock_stream_cls:
        rec.reconnect_stream()
        # No mic stream in listen mode — nothing to reconnect
        mock_stream_cls.assert_not_called()
```

- [ ] **Step 2: Run tests — expect failure**

Run: `pytest tests/test_meeting_recorder.py -v -k reconnect`
Expected: FAIL — method does not exist.

- [ ] **Step 3: Implement reconnect_stream**

Add this method to `MeetingRecorder` in `meeting_recorder.py`. Add it after the `stop()` method (find the method and add this immediately after):

```python
    def reconnect_stream(self) -> bool:
        """Swap the mic input stream to the current OS default device.

        Only applies to full mode (where a mic stream exists). System audio
        via ScreenCaptureKit is unaffected by input device changes. No-op
        if not currently recording or not in full mode.
        """
        if not self.is_recording:
            return False
        if self.mode != "full":
            return False

        old_stream = self._mic_stream
        self._mic_stream = None
        self._mic_recorder = None

        if old_stream is not None:
            try:
                old_stream.stop()
            except Exception:
                pass
            try:
                old_stream.close()
            except Exception:
                pass

        try:
            new_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=512,
                callback=self._mic_callback,
            )
            new_stream.start()
        except Exception as e:
            print(f"MeetingRecorder.reconnect_stream failed: {e}")
            return False

        self._mic_stream = new_stream
        self._mic_recorder = new_stream
        return True
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_meeting_recorder.py -v`
Expected: PASS (all existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add meeting_recorder.py tests/test_meeting_recorder.py
git commit -m "feat(meeting-recorder): reconnect_stream for mic hot-switching"
```

---

## Task 6: WebSocket broadcast helper in app.py

Add a new broadcast function for device change events that reaches both main window and bar clients.

**Files:**
- Modify: `app.py` (around line 152 where `_broadcast_error` is defined)
- Modify: `tests/test_app.py`

- [ ] **Step 1: Find the bar WebSocket client set**

Read `app.py` around the `_broadcast_error` function (line 152) to locate the existing `_main_ws_clients` set and find where bar WS clients are tracked. The file also has a bar-specific client set around the `/ws/bar` endpoint.

Run: `grep -n "_bar_ws_clients\|_main_ws_clients\|ws_bar_lock\|main_ws_lock" app.py`
Expected: See line numbers for both sets and their locks.

- [ ] **Step 2: Write failing test**

Append to `tests/test_app.py`:

```python
def test_broadcast_device_event_sends_to_all_clients():
    """broadcast_device_event must push to both main and bar WS clients."""
    import app as app_module
    from unittest.mock import MagicMock, patch

    # Reset client sets for the test
    with patch.object(app_module, '_main_ws_clients', set()) as main_set, \
         patch.object(app_module, '_bar_ws_clients', set()) as bar_set:

        main_client = MagicMock()
        main_client.client_state = MagicMock()
        bar_client = MagicMock()
        bar_client.client_state = MagicMock()
        main_set.add(main_client)
        bar_set.add(bar_client)

        # We can't actually call the nested function; this test is a placeholder
        # to ensure the function exists. It will be wired in the create_app() scope.
        assert hasattr(app_module, '_broadcast_device_event') or True
```

Note: Because `broadcast_device_event` is a closure inside `create_app()`, directly unit-testing it is awkward. The key test is that `app.state.broadcast_device_event` is set after `create_app()`.

Replace the above test with this cleaner version:

```python
def test_create_app_exposes_broadcast_device_event():
    """After create_app(), the state must expose broadcast_device_event."""
    from app import create_app
    from unittest.mock import MagicMock

    rec = MagicMock()
    txr = MagicMock()
    sm = MagicMock()
    hist = MagicMock()
    clip = MagicMock()
    app_instance = create_app(
        recorder=rec,
        transcriber=txr,
        state_manager=sm,
        history=hist,
        clipboard=clip,
    )
    assert hasattr(app_instance.state, 'broadcast_device_event')
    assert callable(app_instance.state.broadcast_device_event)
```

Note: check the actual signature of `create_app` in `app.py` before finalizing this test — match its required args exactly.

- [ ] **Step 3: Run test — expect failure**

Run: `pytest tests/test_app.py::test_create_app_exposes_broadcast_device_event -v`
Expected: FAIL — `broadcast_device_event` not defined yet.

- [ ] **Step 4: Implement broadcast_device_event**

In `app.py`, locate `_broadcast_error` (around line 152). Immediately after it, add:

```python
    def _broadcast_device_event(event_type: str, device_name: str | None = None):
        """Broadcast a device change event to all main and bar WS clients.

        event_type: one of "device_changed", "device_lost", "device_restored"
        device_name: friendly name of the device (None for device_lost)
        """
        if event_type not in ("device_changed", "device_lost", "device_restored"):
            return

        msg: dict = {"type": event_type}
        if device_name is not None:
            msg["device"] = device_name

        # Main window clients
        with _main_ws_lock:
            main_clients = list(_main_ws_clients)
        for client in main_clients:
            try:
                asyncio.run_coroutine_threadsafe(client.send_json(msg), _ws_loop)
            except RuntimeError:
                pass
            except Exception:
                pass

        # Bar clients
        with _bar_ws_lock:
            bar_clients = list(_bar_ws_clients)
        for client in bar_clients:
            try:
                asyncio.run_coroutine_threadsafe(client.send_json(msg), _ws_loop)
            except RuntimeError:
                pass
            except Exception:
                pass
```

Below, where `app.state.broadcast_error = _broadcast_error` is set (around line 301), add:

```python
    app.state.broadcast_device_event = _broadcast_device_event
```

Important: Check how `_broadcast_error` sends messages. If it uses a different mechanism (e.g., `_enqueue` for bar, direct `send_json` for main), mirror that pattern exactly. Read lines 140-170 and 980-1040 of `app.py` to confirm the actual send mechanism for each client type, then adapt the code above to match.

- [ ] **Step 5: Run test — expect pass**

Run: `pytest tests/test_app.py::test_create_app_exposes_broadcast_device_event -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(app): broadcast_device_event WebSocket helper"
```

---

## Task 7: Wire DeviceMonitor in main.py

Create the DeviceMonitor, register a callback that calls `reconnect_stream()` on all active recorders and broadcasts the event, and start/stop with the app lifecycle.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Read the current main.py wiring section**

Run: `grep -n "hotkey_recorder\|lecture_\|meeting_\|hotkey\.start\|ui_pipeline\|hotkey_pipeline" main.py`
Expected: Find the lines where recorders are instantiated (around line 260+).

- [ ] **Step 2: Import DeviceMonitor at top of main.py**

In `main.py`, find the existing imports and add:

```python
from device_monitor import DeviceMonitor
```

- [ ] **Step 3: Create and wire the DeviceMonitor after recorders are initialized**

In `main.py`, inside `main()`, after all recorders are created (hotkey_recorder, and after `app.state.broadcast_device_event` is available), add this block. Place it after `hotkey.start()` is called but before `webview.start()`:

```python
    # --- Audio device hot-switching ---
    device_monitor = DeviceMonitor()
    # Track which recorders/pipelines we need to poke on device change
    _active_recorders_getter = lambda: [rec for rec in (
        recorder,  # UI AudioRecorder
        hotkey_recorder,  # Hotkey AudioRecorder
    ) if rec is not None]

    def _on_device_changed(device_name):
        """Called by DeviceMonitor on a CoreAudio background thread."""
        broadcast = getattr(app.state, "broadcast_device_event", None)

        if device_name is None:
            # No input device available
            for rec in _active_recorders_getter():
                try:
                    if rec.is_recording:
                        rec._device_lost = True
                        # Stop the stream; keep chunks
                        if rec._stream is not None:
                            try:
                                rec._stream.stop()
                            except Exception:
                                pass
                            try:
                                rec._stream.close()
                            except Exception:
                                pass
                            rec._stream = None
                except Exception as e:
                    print(f"Device lost handling error: {e}")
            # Also stop ClassNote / Meeting if active
            cn_pipeline = classnote_pipeline
            if cn_pipeline and hasattr(cn_pipeline, "_recorder") and cn_pipeline._recorder:
                try:
                    if cn_pipeline._recorder.is_recording:
                        cn_pipeline._recorder.pause()
                except Exception:
                    pass
            mt_pipeline = meeting_pipeline
            if mt_pipeline and hasattr(mt_pipeline, "_recorder") and mt_pipeline._recorder:
                try:
                    if mt_pipeline._recorder.is_recording and mt_pipeline._recorder.mode == "full":
                        if mt_pipeline._recorder._mic_stream is not None:
                            try:
                                mt_pipeline._recorder._mic_stream.stop()
                                mt_pipeline._recorder._mic_stream.close()
                            except Exception:
                                pass
                            mt_pipeline._recorder._mic_stream = None
                except Exception:
                    pass
            if broadcast:
                broadcast("device_lost")
            return

        # Device present — reconnect any active recorders
        any_was_lost = False
        for rec in _active_recorders_getter():
            try:
                if getattr(rec, "_device_lost", False):
                    any_was_lost = True
                    rec._device_lost = False
                    # Recorder was stopped on loss; user must restart manually.
                    # We don't auto-resume top-level dictation.
                elif rec.is_recording:
                    rec.reconnect_stream()
            except Exception as e:
                print(f"Device changed reconnect error: {e}")

        # ClassNote: auto-resume if it was paused by device loss
        cn_pipeline = classnote_pipeline
        if cn_pipeline and hasattr(cn_pipeline, "_recorder") and cn_pipeline._recorder:
            try:
                if cn_pipeline._recorder.is_paused:
                    cn_pipeline._recorder.resume()
                elif cn_pipeline._recorder.is_recording:
                    cn_pipeline._recorder.reconnect_stream()
            except Exception as e:
                print(f"ClassNote reconnect error: {e}")

        # Meeting: reconnect mic in full mode
        mt_pipeline = meeting_pipeline
        if mt_pipeline and hasattr(mt_pipeline, "_recorder") and mt_pipeline._recorder:
            try:
                if mt_pipeline._recorder.is_recording:
                    mt_pipeline._recorder.reconnect_stream()
            except Exception as e:
                print(f"Meeting reconnect error: {e}")

        if broadcast:
            event_type = "device_restored" if any_was_lost else "device_changed"
            broadcast(event_type, device_name)

    device_monitor.on_device_changed = _on_device_changed
    device_monitor.start()

    # Ensure cleanup on app quit
    def _stop_device_monitor_on_quit():
        try:
            device_monitor.stop()
        except Exception:
            pass

    # Attach to existing shutdown path — find where the hotkey is stopped and
    # add device_monitor.stop() nearby. If there's no existing shutdown hook,
    # register via webview.start(...)'s cleanup or use atexit.
    import atexit
    atexit.register(_stop_device_monitor_on_quit)
```

Important: The variable names `recorder`, `hotkey_recorder`, `classnote_pipeline`, and `meeting_pipeline` in this block must match the actual names used in `main.py`. Read the surrounding code to confirm before committing. If names differ, adapt the block.

- [ ] **Step 4: Smoke test that main.py still imports**

Run: `cd /Users/ranabirbasu/GitHub/DashScribe && source venv/bin/activate && python3 -c "import main; print('ok')"`
Expected: `ok` printed, no import errors.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(main): wire DeviceMonitor to all recorder reconnect paths"
```

---

## Task 8: Bar toast HTML element

Add the toast DOM element to `bar.html`.

**Files:**
- Modify: `static/bar.html`

- [ ] **Step 1: Add toast div**

In `static/bar.html`, after the existing `<div id="bar-warning">` line (line 41), add:

```html
    <div id="bar-toast" class="bar-toast hidden">
        <span class="bar-toast-dot"></span>
        <svg class="bar-toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            <line x1="12" y1="19" x2="12" y2="23"/>
            <line x1="8" y1="23" x2="16" y2="23"/>
        </svg>
        <span id="bar-toast-text"></span>
    </div>
```

- [ ] **Step 2: Verify HTML loads without error**

Run: `cd /Users/ranabirbasu/GitHub/DashScribe && source venv/bin/activate && python3 -c "from pathlib import Path; html = Path('static/bar.html').read_text(); assert 'bar-toast' in html; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add static/bar.html
git commit -m "feat(bar): add toast DOM element"
```

---

## Task 9: Bar toast CSS styles

Style the toast as a detached pill above the bar.

**Files:**
- Modify: `static/bar.css`

- [ ] **Step 1: Add toast styles**

In `static/bar.css`, append at the end:

```css
/* --- Device change toast (detached pill above bar) --- */
.bar-toast {
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%) translateY(4px);
    margin-bottom: 10px;
    background: rgba(30, 30, 30, 0.92);
    color: #e0e0e0;
    padding: 8px 14px;
    border-radius: 999px;
    font-size: 12px;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    white-space: nowrap;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.1);
    display: flex;
    align-items: center;
    gap: 8px;
    opacity: 1;
    transition: opacity 0.3s ease, transform 0.3s ease;
    pointer-events: none;
    z-index: 10;
}

.bar-toast.hidden {
    opacity: 0;
    transform: translateX(-50%) translateY(8px);
}

.bar-toast-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #a0c4ff;
    flex-shrink: 0;
}

.bar-toast-icon {
    width: 12px;
    height: 12px;
    color: #a0c4ff;
    flex-shrink: 0;
}
```

Additionally, the `html, body` rule at the top (lines 4-10) currently has `overflow: hidden`. The toast extends above the bar, so we need to allow overflow. But the bar window is exactly the capsule size — the toast will be clipped. This is handled by Task 10 (window headroom). For now, change the body rule so the toast isn't clipped *within* the body:

Change lines 4-10 from:

```css
html, body {
    background: transparent;
    overflow: hidden;
    user-select: none;
    -webkit-user-select: none;
    height: 100%;
}
```

To:

```css
html, body {
    background: transparent;
    overflow: visible;
    user-select: none;
    -webkit-user-select: none;
    height: 100%;
}
```

- [ ] **Step 2: Commit**

```bash
git add static/bar.css
git commit -m "feat(bar): toast pill styles and allow overflow for toast"
```

---

## Task 10: Bar JS showToast/hideToast

Add the JS functions to handle the new WebSocket message types.

**Files:**
- Modify: `static/bar.js`

- [ ] **Step 1: Read current bar.js structure**

Run: `grep -n "onmessage\|showWarning\|function\|const\|let " static/bar.js | head -40`
Expected: Find where WebSocket `onmessage` is handled and where `showWarning` is defined.

- [ ] **Step 2: Add toast functions and message handling**

In `static/bar.js`, find the WebSocket `onmessage` handler. It currently handles `{"type": "warning", ...}`, status updates, etc. Add handling for the three new device event types.

Find a good location (near the other helper functions like `showWarning`) and add:

```javascript
// --- Device change toast ---
const toastEl = document.getElementById('bar-toast');
const toastTextEl = document.getElementById('bar-toast-text');
let toastTimer = null;

function showToast(message) {
    if (!toastEl || !toastTextEl) return;
    toastTextEl.textContent = message;
    toastEl.classList.remove('hidden');
    if (toastTimer) {
        clearTimeout(toastTimer);
    }
    toastTimer = setTimeout(() => {
        hideToast();
    }, 4000);
}

function hideToast() {
    if (!toastEl) return;
    toastEl.classList.add('hidden');
    if (toastTimer) {
        clearTimeout(toastTimer);
        toastTimer = null;
    }
}
```

Then, in the WebSocket `onmessage` handler (wherever `msg.type === 'warning'` or similar checks happen), add these branches:

```javascript
} else if (msg.type === 'device_changed') {
    showToast(`Input switched to ${msg.device}`);
} else if (msg.type === 'device_lost') {
    showToast('No microphone found — reconnect to continue');
} else if (msg.type === 'device_restored') {
    showToast(`${msg.device} connected`);
}
```

Place these branches alongside the other `else if` checks for message types. Read the existing handler structure first and mirror its style.

- [ ] **Step 3: Smoke test — syntax check**

Run: `cd /Users/ranabirbasu/GitHub/DashScribe && node -c static/bar.js 2>&1 || echo "node not available, skipping JS syntax check"`
Expected: Either no output (syntax OK) or the skip message.

Alternatively, open `static/bar.js` in an editor and visually verify the new code is placed correctly and parens/braces balance.

- [ ] **Step 4: Commit**

```bash
git add static/bar.js
git commit -m "feat(bar): showToast for device change events"
```

---

## Task 11: Bar window height headroom

The bar window is exactly the capsule size (e.g., 244x42 when recording). The toast renders above the capsule but will be clipped by the window bounds. Make the window taller so the toast has room to render.

**Files:**
- Modify: `main.py`
- Modify: `static/bar.css`

- [ ] **Step 1: Anchor capsule to bottom of window in CSS**

In `static/bar.css`, find the `.bar` rule (lines 12-22) and add `align-items: flex-end`. Change:

```css
.bar {
    --capsule-bg: rgba(30, 30, 30, 0.9);
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
    position: relative;
    overflow: hidden;
    border-radius: 999px;
}
```

To:

```css
.bar {
    --capsule-bg: rgba(30, 30, 30, 0.9);
    display: flex;
    align-items: flex-end;
    justify-content: center;
    height: 100%;
    width: 100%;
    position: relative;
    overflow: visible;
    border-radius: 0;
}
```

The capsule's own border-radius is already `999px` on `.bar-recording`, etc., so removing it from `.bar` is fine. The outer shape is now the capsule itself, anchored to the bottom.

- [ ] **Step 2: Add TOAST_HEADROOM constant to main.py**

In `main.py`, near the bar dimension constants (lines 40-46), add:

```python
# Extra vertical space above the capsule for device-change toast
BAR_TOAST_HEADROOM = 44
```

- [ ] **Step 3: Update bar window creation and animation to include headroom**

Change the initial bar window creation (around line 334) to include headroom in the window height:

```python
    # Create floating bar window (always exists, keeps app alive)
    bar_window = webview.create_window(
        "",
        f"http://{HOST}:{PORT}/bar",
        width=BAR_IDLE_W,
        height=BAR_IDLE_H + BAR_TOAST_HEADROOM,
        x=bar_x,
        y=bar_y - BAR_TOAST_HEADROOM,
        min_size=(80, 20 + BAR_TOAST_HEADROOM),
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
    )
```

Change `animate_bar_to` to factor in the headroom. Replace the function body (around line 365) so every `resize` call adds `BAR_TOAST_HEADROOM` to the height and adjusts `y`:

```python
    def animate_bar_to(target_w, target_h, duration=BAR_ANIM_DURATION):
        nonlocal bar_anim_token
        with bar_anim_lock:
            bar_anim_token += 1
            token = bar_anim_token
            start_w = bar_size["w"]
            start_h = bar_size["h"]

        if start_w == target_w and start_h == target_h:
            x, y = get_bar_position(target_w, target_h)
            bar_window.resize(target_w, target_h + BAR_TOAST_HEADROOM)
            bar_window.move(x, y - BAR_TOAST_HEADROOM)
            return

        steps = max(1, int(duration / BAR_ANIM_FRAME_SEC))
        for i in range(1, steps + 1):
            with bar_anim_lock:
                if token != bar_anim_token:
                    return

            t = i / steps
            eased = t * t * (3 - (2 * t))
            w = round(start_w + (target_w - start_w) * eased)
            h = round(start_h + (target_h - start_h) * eased)
            x, y = get_bar_position(w, h)
            bar_window.resize(w, h + BAR_TOAST_HEADROOM)
            bar_window.move(x, y - BAR_TOAST_HEADROOM)

            with bar_anim_lock:
                bar_size["w"] = w
                bar_size["h"] = h
            time.sleep(duration / steps)

        with bar_anim_lock:
            if token != bar_anim_token:
                return
            bar_size["w"] = target_w
            bar_size["h"] = target_h
```

Note: `bar_size` still tracks the *capsule* size (not the window size). `get_bar_position` returns the capsule position; we shift `y` up by `BAR_TOAST_HEADROOM` so the larger window ends up with its bottom edge where the capsule should be.

- [ ] **Step 4: Smoke test that main.py still imports**

Run: `python3 -c "import main; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Manual smoke test (optional but recommended)**

Run: `source venv/bin/activate && python3 main.py`
Verify:
1. Bar appears at the correct position (capsule bottom 70px above screen bottom, same as before)
2. Bar states transition correctly (idle → recording → processing → idle)
3. Bar is not visually cut off or repositioned incorrectly

Then manually trigger a device change (disconnect BT headphones or change default input in System Settings) while idle. A toast should appear above the bar saying "Input switched to {name}". Toast should fade out after 4 seconds.

Then start recording, disconnect the input device mid-recording. Recording should continue with a brief gap, toast appears, and stop/transcribe should still work.

- [ ] **Step 6: Commit**

```bash
git add main.py static/bar.css
git commit -m "feat(bar): add toast headroom to window, anchor capsule to bottom"
```

---

## Task 12: Callback error escalation in AudioRecorder

The existing `_audio_callback` in `recorder.py` logs `status` errors but doesn't act on them. Add lightweight detection so if the callback sees repeated input errors (indicating the device is gone) before CoreAudio's property listener fires, we trigger reconnect.

Actually — on reflection, the CoreAudio property listener is reliable and fires within milliseconds. Adding a fallback here risks double-reconnects and race conditions. The CoreAudio path alone is sufficient.

**Skip this task.** No changes needed. Closing this as documentation.

- [ ] **Step 1: Document the decision**

No commit or code change. This task exists to mark that we considered and rejected fallback error escalation in the audio callback for simplicity.

---

## Task 13: Full test suite run

Ensure nothing broke.

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/ranabirbasu/GitHub/DashScribe && source venv/bin/activate && pytest -v 2>&1 | tail -40`
Expected: All tests pass. If anything broke, fix before proceeding.

- [ ] **Step 2: Commit any fixes**

If fixes were required:

```bash
git add -u
git commit -m "fix: test suite adjustments after device hot-switching"
```

---

## Self-Review (Notes for Plan Writer)

**Spec coverage check:**

| Spec Section | Covered By |
|--------------|------------|
| Device Monitor (§1) | Tasks 1-2 |
| AudioRecorder recovery (§2) | Task 3 |
| LectureRecorder/MeetingRecorder recovery (§3) | Tasks 4-5 |
| No-device handling (§4) | Task 3 (`_device_lost` flag) + Task 7 (main.py wiring) |
| Toast UI (§5) | Tasks 8-11 |
| main.py wiring (§6) | Task 7 |
| Edge case: debounce rapid switches | Task 2 (`_schedule_check` with 100ms debounce) |
| Edge case: same device re-selected | Task 2 (`_check_and_fire` skips if unchanged) |
| Edge case: switch during processing | Task 7 (reconnect_stream is a no-op when `is_recording=False`) |
| Edge case: app launch with no device | Task 2 (`start()` initializes state from current device) |
| Future work (device picker, quality indicator) | Not implemented — marked future work in spec |

All spec sections are covered.

**Type/signature consistency check:**
- `DeviceMonitor.on_device_changed: Callable[[Optional[str]], None]` — consistent across Tasks 1, 2, 7.
- `reconnect_stream() -> bool` — consistent across `AudioRecorder`, `LectureRecorder`, `MeetingRecorder` in Tasks 3-5.
- `_device_lost: bool` — defined in AudioRecorder (Task 3), referenced in main.py (Task 7).
- `broadcast_device_event(event_type: str, device_name: str | None)` — defined Task 6, called Task 7.
- WebSocket message shapes — `{"type": "device_changed", "device": name}`, `{"type": "device_lost"}`, `{"type": "device_restored", "device": name}` — consistent across Tasks 6, 7, 10.

**Placeholder scan:** No TBDs, no "add appropriate handling", no uncoded test references. Task 12 was intentionally rejected with a note explaining why.

**Note on Task 7:** The wiring block assumes variable names like `recorder`, `hotkey_recorder`, `classnote_pipeline`, `meeting_pipeline` in `main.py`. The engineer is instructed to verify and adapt to actual names. This is flagged as an "Important" note in the task, not a placeholder — the code is complete pending a name verification pass.
