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
