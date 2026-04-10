# hotkey.py
import gc
import os
import time
import threading

import Quartz
from Quartz import (
    CGEventTapCreate,
    CGEventTapEnable,
    CGEventGetIntegerValueField,
    CGEventGetFlags,
    CFMachPortCreateRunLoopSource,
    CFRunLoopGetCurrent,
    CFRunLoopAddSource,
    CFRunLoopRun,
    CFRunLoopStop,
    kCGHIDEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventFlagsChanged,
    kCGEventTapDisabledByTimeout,
    kCGKeyboardEventKeycode,
    kCGKeyboardEventAutorepeat,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCFAllocatorDefault,
    kCFRunLoopCommonModes,
)

from recorder import AudioRecorder, get_wav_duration
from transcriber import WhisperTranscriber
from clipboard import paste_text
from state import AppState, AppStateManager
from internal_clipboard import InternalClipboard

# NX_SYSDEFINED event type for media/special function keys
NX_SYSDEFINED = 14
NX_SUBTYPE_AUX_CONTROL_BUTTONS = 8
# NX key types → serialized key names (for media keys in default MacBook mode)
NX_KEYTYPE_TO_NAME = {
    0: "f12",   # NX_KEYTYPE_SOUND_UP → Volume Up (F12 on MacBook)
    1: "f11",   # NX_KEYTYPE_SOUND_DOWN → Volume Down (F11 on MacBook)
    7: "f10",   # NX_KEYTYPE_MUTE → Mute (F10 on MacBook)
    16: "f8",   # NX_KEYTYPE_PLAY → Play/Pause (F8 on MacBook)
    17: "f9",   # NX_KEYTYPE_NEXT → Next Track (F9 on MacBook)
    18: "f7",   # NX_KEYTYPE_PREVIOUS → Previous Track (F7 on MacBook)
    2: "f2",    # NX_KEYTYPE_BRIGHTNESS_UP (F2 on MacBook)
    3: "f1",    # NX_KEYTYPE_BRIGHTNESS_DOWN (F1 on MacBook)
    21: "f6",   # NX_KEYTYPE_ILLUMINATION_UP (F6 on some MacBooks)
    22: "f6",   # NX_KEYTYPE_ILLUMINATION_DOWN (F6 on some MacBooks)
}


class GlobalHotkey:
    """Listens for a configurable key via HID-level CGEventTap.

    Uses kCGHIDEventTap to intercept keys BEFORE macOS processes them,
    allowing capture of system function keys (F5/dictation, brightness, etc.)
    that pynput's kCGSessionEventTap cannot see.
    """

    # Timing constants
    HOLD_THRESHOLD = 0.4       # seconds — above this = hold-to-talk
    DOUBLE_TAP_WINDOW = 0.5    # seconds — max gap between taps
    MAX_RECORD_SECONDS = 600   # 10 minutes
    WARNING_SECONDS = 540      # 9 minutes
    PROCESSING_TIMEOUT_S = 5   # auto-cancel processing after 5 seconds

    def __init__(
        self,
        recorder: AudioRecorder,
        transcriber: WhisperTranscriber,
        state_manager: AppStateManager,
        internal_clipboard: InternalClipboard | None = None,
        history=None,
        settings=None,
        pipeline=None,
        cancel_recording_callback=None,
        get_classnote_pipeline=None,
        llm=None,
        formatter=None,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.formatter = formatter
        self.state_manager = state_manager
        self.internal_clipboard = internal_clipboard or InternalClipboard()
        self.history = history
        self.llm = llm
        self.settings = settings
        self.pipeline = pipeline
        self.cancel_recording_callback = cancel_recording_callback
        self._get_classnote_pipeline = get_classnote_pipeline or (lambda: None)
        self._broadcast_error = None  # Set by main.py after app.state is available
        self.snippet_callback = None  # Called when Cmd+Shift+S is pressed
        self._snippet_keys = frozenset({1})  # 'S' keycode
        self._snippet_modifiers = frozenset({"cmd", "shift"})
        self.is_recording = False
        self._processing = False
        self.escape_keys = frozenset({53})  # Escape

        # Configurable trigger key(s) — frozenset of macOS keycodes
        # Multiple keycodes for the same key (e.g. F5 = {96, 176} on MacBooks)
        if settings:
            self.trigger_keys = settings.hotkey_key
            self.trigger_modifiers = settings.hotkey_modifiers
            settings.on_hotkey_change(self._on_hotkey_changed)
            self.repaste_keys = settings.repaste_keycodes
            self.repaste_modifiers = settings.repaste_modifiers
            settings.on_repaste_change(self._on_repaste_changed)
        else:
            from config import (
                DEFAULT_HOTKEY,
                DEFAULT_REPASTE_KEY,
                LEGACY_REPASTE_MODIFIERS,
                shortcut_keycodes,
                shortcut_modifiers,
            )
            self.trigger_keys = shortcut_keycodes(DEFAULT_HOTKEY)
            self.trigger_modifiers = shortcut_modifiers(DEFAULT_HOTKEY)
            self.repaste_keys = shortcut_keycodes(
                DEFAULT_REPASTE_KEY,
                implicit_modifiers=LEGACY_REPASTE_MODIFIERS,
            )
            self.repaste_modifiers = shortcut_modifiers(
                DEFAULT_REPASTE_KEY,
                implicit_modifiers=LEGACY_REPASTE_MODIFIERS,
            )

        # Whether the event tap has full (active) or listen-only access
        self.has_active_tap = False

        # Key capture mode (for settings UI)
        self._capture_mode = False
        self._captured_key: str | None = None
        self._capture_active_modifiers: set[str] = set()
        self._capture_modifier_candidate: str | None = None

        # Double-tap state machine
        self.toggle_mode = False
        self.last_tap_time: float | None = None
        self.press_start_time: float = 0.0
        self._orphan_timer: threading.Timer | None = None
        self._warning_timer: threading.Timer | None = None
        self._max_timer: threading.Timer | None = None
        self._processing_timeout_timer: threading.Timer | None = None

        # CGEventTap state
        self._tap = None
        self._run_loop_ref = None
        self._held_modifiers: set[int] = set()
        self._flag_mask_fn = int(getattr(Quartz, "kCGEventFlagMaskSecondaryFn", 0))
        self._last_event_flags = 0
        self._internal_paste_lock = threading.Lock()
        self._internal_paste_pending = False
        self._suppress_repaste_keyup = False

    # --- Key capture for settings UI ---

    def start_key_capture(self):
        """Enter capture mode — next key press will be captured for settings."""
        self._captured_key = None
        self._capture_active_modifiers.clear()
        self._capture_modifier_candidate = None
        self._capture_mode = True

    def poll_key_capture(self) -> dict:
        """Return captured key if available."""
        if self._captured_key is not None:
            from config import shortcut_display
            return {
                "captured": True,
                "key": self._captured_key,
                "display": shortcut_display(self._captured_key),
            }
        return {"captured": False}

    def cancel_key_capture(self):
        """Exit capture mode without saving."""
        self._capture_mode = False
        self._captured_key = None
        self._capture_active_modifiers.clear()
        self._capture_modifier_candidate = None

    # --- CGEventTap callback ---

    def _event_callback(self, proxy, event_type, event, refcon):
        """CGEventTap callback — runs on the event tap thread."""
        if event_type == kCGEventTapDisabledByTimeout:
            if self._tap:
                CGEventTapEnable(self._tap, True)
            return event

        keycode = None
        is_press = None

        if event_type in (kCGEventKeyDown, kCGEventKeyUp):
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            is_press = (event_type == kCGEventKeyDown)
            flags = CGEventGetFlags(event)
            with self._internal_paste_lock:
                self._last_event_flags = flags

            if (
                not self._capture_mode
                and self._is_internal_paste_shortcut(keycode, flags)
            ):
                if self._handle_internal_paste_shortcut(is_press):
                    return None
                return event

            if (
                not self._capture_mode
                and is_press
                and self._is_snippet_shortcut(keycode, flags)
                and self.snippet_callback
            ):
                threading.Thread(target=self.snippet_callback, daemon=True).start()
                return None

            is_repeat = bool(CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat))
            if is_repeat:
                # Suppress repeats of trigger key, pass through others
                if self._capture_mode or self._is_hotkey_shortcut(keycode, flags) or keycode in self.escape_keys:
                    return None
                return event

            if not self._capture_mode and is_press and keycode in self.escape_keys:
                if self._on_escape():
                    return None

        elif event_type == kCGEventFlagsChanged:
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            flags = CGEventGetFlags(event)
            with self._internal_paste_lock:
                self._last_event_flags = flags
            # Determine press vs release by tracking held modifiers
            if keycode in self._held_modifiers:
                is_press = False
                self._held_modifiers.discard(keycode)
            else:
                is_press = True
                self._held_modifiers.add(keycode)

        elif event_type == NX_SYSDEFINED:
            return self._handle_nx_event(event)

        else:
            return event

        if keycode is None:
            return event

        # Remember capture state before _on_press might change it
        was_capture = self._capture_mode

        if is_press:
            try:
                self._on_press(keycode, flags=flags, event_type=event_type)
            except Exception as e:
                print(f"Hotkey press handler error: {e}")
                self.state_manager.set_state(AppState.ERROR)
                threading.Timer(5.0, lambda: self.state_manager.set_state(AppState.IDLE) if self.state_manager.state == AppState.ERROR else None).start()
        else:
            try:
                self._on_release(keycode, flags=flags, event_type=event_type)
            except Exception as e:
                print(f"Hotkey release handler error: {e}")
                self.state_manager.set_state(AppState.ERROR)
                threading.Timer(5.0, lambda: self.state_manager.set_state(AppState.IDLE) if self.state_manager.state == AppState.ERROR else None).start()

        # Suppress trigger key and capture-mode keys
        if was_capture or self._is_hotkey_shortcut(keycode, flags):
            return None
        return event

    def _handle_nx_event(self, event):
        """Handle NX_SYSDEFINED events (media/special function keys on MacBooks)."""
        try:
            ns_event = Quartz.NSEvent.eventWithCGEvent_(event)
            if ns_event is None:
                return event
            if ns_event.subtype() != NX_SUBTYPE_AUX_CONTROL_BUTTONS:
                return event

            data1 = ns_event.data1()
            nx_key_type = (data1 & 0xFFFF0000) >> 16
            key_flags = data1 & 0x0000FFFF
            key_state = (key_flags & 0xFF00) >> 8
            is_press = (key_state == 0x0A)
            is_release = (key_state == 0x0B)

            if not (is_press or is_release):
                return event

            # Map NX key type to our serialized name
            name = NX_KEYTYPE_TO_NAME.get(nx_key_type)
            if not name:
                return event

            from config import NAME_TO_KEYCODE
            keycode = NAME_TO_KEYCODE.get(name)
            if keycode is None:
                return event

            was_capture = self._capture_mode

            if is_press:
                try:
                    self._on_press(keycode, flags=0, event_type=NX_SYSDEFINED)
                except Exception as e:
                    print(f"Hotkey NX press handler error: {e}")
                    self.state_manager.set_state(AppState.ERROR)
                    threading.Timer(5.0, lambda: self.state_manager.set_state(AppState.IDLE) if self.state_manager.state == AppState.ERROR else None).start()
            elif is_release:
                try:
                    self._on_release(keycode, flags=0, event_type=NX_SYSDEFINED)
                except Exception as e:
                    print(f"Hotkey NX release handler error: {e}")
                    self.state_manager.set_state(AppState.ERROR)
                    threading.Timer(5.0, lambda: self.state_manager.set_state(AppState.IDLE) if self.state_manager.state == AppState.ERROR else None).start()

            if was_capture or self._is_hotkey_shortcut(keycode, 0):
                return None
            return event

        except Exception:
            return event

    # --- Press/release handlers (also called directly by tests) ---

    def _flags_to_modifiers(self, flags: int) -> set[str]:
        modifiers: set[str] = set()
        if flags & kCGEventFlagMaskCommand:
            modifiers.add("cmd")
        if flags & kCGEventFlagMaskAlternate:
            modifiers.add("alt")
        if flags & kCGEventFlagMaskControl:
            modifiers.add("ctrl")
        if flags & kCGEventFlagMaskShift:
            modifiers.add("shift")
        if self._flag_mask_fn and (flags & self._flag_mask_fn):
            modifiers.add("fn")
        return modifiers

    def _matches_shortcut(
        self,
        keycode: int,
        flags: int,
        keycodes: frozenset[int],
        required_modifiers: frozenset[str],
    ) -> bool:
        if keycode not in keycodes:
            return False
        if not required_modifiers:
            return True
        active_mods = self._flags_to_modifiers(flags)
        return required_modifiers.issubset(active_mods)

    def _is_hotkey_shortcut(self, keycode: int, flags: int) -> bool:
        return self._matches_shortcut(
            keycode,
            flags,
            self.trigger_keys,
            self.trigger_modifiers,
        )

    def _is_internal_paste_shortcut(self, keycode: int, flags: int) -> bool:
        return self._matches_shortcut(
            keycode,
            flags,
            self.repaste_keys,
            self.repaste_modifiers,
        )

    def _is_snippet_shortcut(self, keycode: int, flags: int) -> bool:
        return self._matches_shortcut(
            keycode,
            flags,
            self._snippet_keys,
            self._snippet_modifiers,
        )

    def _is_process_trusted_for_accessibility(self) -> bool:
        try:
            import ApplicationServices as AS
            return bool(AS.AXIsProcessTrusted())
        except Exception:
            return False

    def _frontmost_bundle_id(self) -> str:
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return ""
            bundle_id = app.bundleIdentifier()
            return str(bundle_id or "")
        except Exception:
            return ""

    def _ax_copy_attr(self, element, attribute: str):
        try:
            import ApplicationServices as AS
            err, value = AS.AXUIElementCopyAttributeValue(element, attribute, None)
            if err == 0:
                return value
        except Exception:
            pass
        return None

    def _ax_is_attr_settable(self, element, attribute: str) -> bool | None:
        try:
            import ApplicationServices as AS
            err, settable = AS.AXUIElementIsAttributeSettable(element, attribute, None)
            if err == 0:
                return bool(settable)
        except Exception:
            pass
        return None

    def _ax_bool_attr(self, element, attribute: str) -> bool | None:
        value = self._ax_copy_attr(element, attribute)
        if value is None:
            return None
        try:
            return bool(value)
        except Exception:
            return None

    def _ax_element_allows_text_input(self, element) -> bool:
        if element is None:
            return False

        editable_flag = self._ax_bool_attr(element, "AXEditable")
        if editable_flag is True:
            return True

        role = self._ax_copy_attr(element, "AXRole")
        subrole = self._ax_copy_attr(element, "AXSubrole")
        editable_roles = {
            "AXTextField",
            "AXTextArea",
            "AXSearchField",
            "AXTextView",
            "AXSecureTextField",
            "AXComboBox",
        }
        if role in editable_roles:
            value_settable = self._ax_is_attr_settable(element, "AXValue")
            if value_settable is True:
                return True
            selected_text_range = self._ax_copy_attr(element, "AXSelectedTextRange")
            if selected_text_range is not None:
                return True
        if subrole in editable_roles:
            value_settable = self._ax_is_attr_settable(element, "AXValue")
            if value_settable is True:
                return True

        return False

    def _focus_is_editable_text(self) -> bool:
        if not self._is_process_trusted_for_accessibility():
            return False
        try:
            import ApplicationServices as AS
            system_wide = AS.AXUIElementCreateSystemWide()
            focused = self._ax_copy_attr(system_wide, AS.kAXFocusedUIElementAttribute)
            return self._ax_element_allows_text_input(focused)
        except Exception:
            return False

    def _should_handle_internal_paste(self) -> bool:
        if not self.internal_clipboard.has_text():
            return False
        if self._frontmost_bundle_id() == "com.apple.finder":
            return False
        return self._focus_is_editable_text()

    def _handle_internal_paste_shortcut(self, is_press: bool) -> bool:
        """Return True when this shortcut event should be consumed."""
        if is_press:
            if self._should_handle_internal_paste():
                self._suppress_repaste_keyup = True
                self._request_internal_paste()
                return True
            self._suppress_repaste_keyup = False
            return False

        if self._suppress_repaste_keyup:
            self._suppress_repaste_keyup = False
            return True
        return False

    def _should_auto_insert(self) -> bool:
        if self.settings is None:
            return True
        return self.settings.auto_insert

    def _paste_internal_clipboard(self):
        text = self.internal_clipboard.get_text()
        if not text:
            return
        try:
            paste_text(text)
        except Exception as e:
            print(f"Internal paste failed: {e}")

    def _request_internal_paste(self):
        """Delay internal paste until shortcut modifiers are released."""
        with self._internal_paste_lock:
            if self._internal_paste_pending:
                return
            self._internal_paste_pending = True

        threading.Thread(
            target=self._run_deferred_internal_paste,
            daemon=True,
        ).start()

    def _run_deferred_internal_paste(self):
        try:
            deadline = time.time() + 0.6
            while time.time() < deadline:
                with self._internal_paste_lock:
                    flags = self._last_event_flags
                active_mods = self._flags_to_modifiers(flags)
                if not (active_mods & self.repaste_modifiers):
                    break
                time.sleep(0.01)

            # Let key-up events settle before typing text.
            time.sleep(0.01)
            self._paste_internal_clipboard()
        finally:
            with self._internal_paste_lock:
                self._internal_paste_pending = False

    def _cancel_hotkey_recording(self) -> bool:
        if not self.is_recording:
            return False

        self.toggle_mode = False
        self.is_recording = False
        self.last_tap_time = None
        self._cancel_orphan_timer()
        self._cancel_duration_timers()
        self.recorder.on_vad_chunk = None
        if self.pipeline is not None:
            self.pipeline.cancel()
        if self.recorder.is_recording:
            self.recorder.stop_raw()  # discard audio
        self.state_manager.set_state(AppState.IDLE)
        # Resume ClassNote if it was paused for dictation
        cn_pipeline = self._get_classnote_pipeline()
        if cn_pipeline and cn_pipeline.is_paused:
            cn_pipeline.resume()
        return True

    def _on_escape(self) -> bool:
        """Cancel active recording or processing on Escape. Returns True when handled."""
        if self._capture_mode:
            return False

        if self._cancel_hotkey_recording():
            return True

        # Cancel processing (timeout or manual escape)
        if self.state_manager.state == AppState.PROCESSING:
            self._cancel_processing_timeout()
            self.state_manager.set_state(AppState.IDLE)
            return True

        if self.state_manager.state == AppState.RECORDING and self.cancel_recording_callback:
            try:
                result = self.cancel_recording_callback()
                return True if result is None else bool(result)
            except Exception as e:
                print(f"Escape cancel callback failed: {e}")
                return False

        return False

    def _on_press(self, keycode, flags: int = 0, event_type: int = kCGEventKeyDown):
        # Capture mode: intercept any key for settings UI
        if self._capture_mode:
            from config import format_shortcut, key_to_string, modifier_token_for_key
            serialized = key_to_string(keycode)
            if not serialized:
                return

            modifier_token = modifier_token_for_key(serialized)
            if event_type == kCGEventFlagsChanged:
                if modifier_token:
                    self._capture_active_modifiers.add(modifier_token)
                    self._capture_modifier_candidate = serialized
                return

            # Some keyboards may emit key-down for modifier keys; keep waiting.
            if modifier_token:
                self._capture_active_modifiers.add(modifier_token)
                self._capture_modifier_candidate = serialized
                return

            self._captured_key = format_shortcut(self._flags_to_modifiers(flags), serialized)
            self._capture_mode = False
            self._capture_active_modifiers.clear()
            self._capture_modifier_candidate = None
            return

        if not self._is_hotkey_shortcut(keycode, flags):
            return
        if self._processing:
            return
        if not self.transcriber.is_ready:
            return

        if self.toggle_mode and self.is_recording:
            # Already in toggle-recording mode — this press is a potential stop-tap
            self.press_start_time = time.time()
        elif not self.is_recording:
            # Start recording immediately (responsive for hold-to-talk)
            self._cancel_orphan_timer()
            self.is_recording = True
            # Pause ClassNote if active (yields mic)
            cn_pipeline = self._get_classnote_pipeline()
            if cn_pipeline and cn_pipeline.is_active:
                cn_pipeline.pause()
                time.sleep(0.05)  # Brief delay for mic release
            try:
                self.recorder.start()
            except Exception as e:
                self.is_recording = False
                self.state_manager.set_state(AppState.ERROR)
                threading.Timer(5.0, lambda: self.state_manager.set_state(AppState.IDLE) if self.state_manager.state == AppState.ERROR else None).start()
                print(f"Hotkey recorder start failed: {e}")
                return
            self.state_manager.set_state(AppState.RECORDING)
            self.press_start_time = time.time()
            self._start_duration_timers()
            # Start streaming pipeline if available
            if self.pipeline is not None and self.pipeline.vad_available:
                sys_chunks = self.recorder.get_sys_audio_chunks()
                started = self.pipeline.start(sys_audio_chunks=sys_chunks)
                self.recorder.on_vad_chunk = self.pipeline.feed if started else None

    def _on_release(self, keycode, flags: int = 0, event_type: int = kCGEventKeyUp):
        if self._capture_mode and event_type == kCGEventFlagsChanged:
            from config import key_to_string, modifier_token_for_key

            serialized = key_to_string(keycode)
            if serialized:
                modifier_token = modifier_token_for_key(serialized)
                if modifier_token:
                    self._capture_active_modifiers.discard(modifier_token)
                    if (
                        self._capture_modifier_candidate == serialized
                        and not self._capture_active_modifiers
                    ):
                        self._captured_key = serialized
                        self._capture_mode = False
                        self._capture_modifier_candidate = None
                        self._capture_active_modifiers.clear()
            return

        if not self._is_hotkey_shortcut(keycode, flags):
            return
        if not self.is_recording:
            return

        hold_duration = time.time() - self.press_start_time

        if self.toggle_mode:
            # In toggle mode — single tap to stop
            if hold_duration < self.HOLD_THRESHOLD:
                self.last_tap_time = None
                self.toggle_mode = False
                self.is_recording = False
                self._cancel_duration_timers()
                threading.Thread(target=self._process_recording, daemon=True).start()
            # Hold in toggle mode — ignore (user just held the key briefly)
        else:
            # Not in toggle mode
            if hold_duration >= self.HOLD_THRESHOLD:
                # Hold-to-talk: stop & transcribe
                self.is_recording = False
                self._cancel_duration_timers()
                threading.Thread(target=self._process_recording, daemon=True).start()
            else:
                # Short tap — check for double-tap to enter toggle mode
                if (self.last_tap_time is not None
                        and (time.time() - self.last_tap_time) < self.DOUBLE_TAP_WINDOW):
                    # Second tap within window → enter toggle mode, keep recording
                    self.last_tap_time = None
                    self.toggle_mode = True
                    self._cancel_orphan_timer()
                else:
                    # First tap — set orphan timer to cancel if no second tap
                    self.last_tap_time = time.time()
                    self._cancel_orphan_timer()
                    self._orphan_timer = threading.Timer(
                        self.DOUBLE_TAP_WINDOW, self._on_orphan_tap
                    )
                    self._orphan_timer.daemon = True
                    self._orphan_timer.start()

    # --- Orphan tap / timer handlers ---

    def _on_orphan_tap(self):
        """Single tap with no follow-up — cancel recording."""
        self.last_tap_time = None
        if self.is_recording and not self.toggle_mode:
            self._cancel_hotkey_recording()

    def _cancel_orphan_timer(self):
        if self._orphan_timer is not None:
            self._orphan_timer.cancel()
            self._orphan_timer = None

    def _on_hotkey_changed(self, serialized: str):
        """Called when user changes the hotkey in settings."""
        from config import shortcut_keycodes, shortcut_modifiers
        new_keycodes = shortcut_keycodes(serialized)
        new_modifiers = shortcut_modifiers(serialized)

        # If currently recording, cancel it cleanly
        if self.is_recording:
            self._cancel_hotkey_recording()

        self.trigger_keys = new_keycodes
        self.trigger_modifiers = new_modifiers

    def _on_repaste_changed(self, serialized: str):
        from config import repaste_implicit_modifiers, shortcut_keycodes, shortcut_modifiers
        implicit_modifiers = repaste_implicit_modifiers(serialized)
        self.repaste_keys = shortcut_keycodes(
            serialized,
            implicit_modifiers=implicit_modifiers,
        )
        self.repaste_modifiers = shortcut_modifiers(
            serialized,
            implicit_modifiers=implicit_modifiers,
        )

    def _start_duration_timers(self):
        self._cancel_duration_timers()
        self._warning_timer = threading.Timer(
            self.WARNING_SECONDS, self._on_warning
        )
        self._warning_timer.daemon = True
        self._warning_timer.start()

        self._max_timer = threading.Timer(
            self.MAX_RECORD_SECONDS, self._on_max_duration
        )
        self._max_timer.daemon = True
        self._max_timer.start()

    def _cancel_duration_timers(self):
        if self._warning_timer is not None:
            self._warning_timer.cancel()
            self._warning_timer = None
        if self._max_timer is not None:
            self._max_timer.cancel()
            self._max_timer = None

    def _arm_processing_timeout(self):
        self._cancel_processing_timeout()
        def _on_timeout():
            if self.state_manager.state == AppState.PROCESSING:
                print(f"Hotkey processing timed out after {self.PROCESSING_TIMEOUT_S}s")
                self.state_manager.set_state(AppState.ERROR)
                self.state_manager.push_warning("Processing timed out")
                if self._broadcast_error:
                    try:
                        self._broadcast_error("Processing timed out")
                    except Exception:
                        pass
                threading.Timer(5.0, lambda: self.state_manager.set_state(AppState.IDLE) if self.state_manager.state == AppState.ERROR else None).start()
        self._processing_timeout_timer = threading.Timer(self.PROCESSING_TIMEOUT_S, _on_timeout)
        self._processing_timeout_timer.daemon = True
        self._processing_timeout_timer.start()

    def _cancel_processing_timeout(self):
        if self._processing_timeout_timer is not None:
            self._processing_timeout_timer.cancel()
            self._processing_timeout_timer = None

    def _on_warning(self):
        self.state_manager.push_warning("Recording ends in 1 minute")

    def _on_max_duration(self):
        """Force stop recording after max duration."""
        if self.is_recording:
            self.toggle_mode = False
            self.is_recording = False
            self.last_tap_time = None
            self._cancel_duration_timers()
            threading.Thread(target=self._process_recording, daemon=True).start()

    def _process_recording(self):
        """Stop recording, transcribe, copy to clipboard."""
        self._processing = True
        self.state_manager.set_state(AppState.PROCESSING)
        self._arm_processing_timeout()
        cn_pipeline = self._get_classnote_pipeline()
        try:
            use_streaming = (
                self.pipeline is not None
                and self.pipeline.vad_available
                and self.pipeline._active
            )

            if use_streaming:
                self.recorder.on_vad_chunk = None
                mic_audio, sys_audio = self.recorder.stop_raw()

                if mic_audio is None or len(mic_audio) == 0:
                    self.pipeline.cancel()
                    self._cancel_processing_timeout()
                    self.state_manager.set_state(AppState.IDLE)
                    return

                audio_duration = round(len(mic_audio) / self.recorder.sample_rate, 2)

                if audio_duration < self.pipeline.SHORT_RECORDING_THRESHOLD_S:
                    # Short recording — single-pass with tuned params
                    self.pipeline.cancel()
                    if sys_audio is not None and len(sys_audio) > 0:
                        try:
                            from aec import nlms_echo_cancel, noise_gate
                            mic_audio = nlms_echo_cancel(mic_audio, sys_audio)
                            mic_audio = noise_gate(mic_audio, sample_rate=self.recorder.sample_rate)
                        except Exception:
                            pass
                    del sys_audio
                    start_time = time.time()
                    text = self.transcriber.transcribe_array(mic_audio)
                    elapsed = round(time.time() - start_time, 2)
                    # Cache audio for retry
                    from app import _last_audio_cache
                    _last_audio_cache["audio"] = mic_audio.copy()
                    _last_audio_cache["sample_rate"] = self.recorder.sample_rate
                    del mic_audio
                else:
                    # Streaming path — pipeline already transcribed intermediate segments
                    del mic_audio
                    start_time = time.time()
                    results = self.pipeline.stop(sys_audio)
                    elapsed = round(time.time() - start_time, 2)
                    del sys_audio
                    text = " ".join(r.text for r in results if r.text)
                    # Pipeline results don't expose raw audio, clear cache
                    from app import _last_audio_cache
                    _last_audio_cache["audio"] = None

                self._cancel_processing_timeout()
                # Bail if timed out or cancelled while transcribing
                if self.state_manager.state != AppState.PROCESSING:
                    return

                # Debug: force error on odd attempts for retry testing
                from app import _DEBUG_FORCE_FIRST_ERROR, _debug_error_count
                import app as _app_module
                if _DEBUG_FORCE_FIRST_ERROR:
                    _app_module._debug_error_count += 1
                    if _app_module._debug_error_count % 2 == 1:
                        raise RuntimeError("DEBUG: forced error for retry testing")

                if text:
                    # Two-stage post-processing
                    try:
                        from app import _post_process
                        text, stage1_text, raw_text = _post_process(
                            text, self.llm, self.settings, formatter=self.formatter
                        )
                    except Exception as e:
                        print(f"Hotkey post-process failed: {e}")
                        stage1_text, raw_text = None, None
                    # Check again after post-processing
                    if self.state_manager.state != AppState.PROCESSING:
                        return
                    self.internal_clipboard.set_text(text)
                    if self._should_auto_insert():
                        try:
                            paste_text(text)
                        except Exception as e:
                            print(f"Paste operation failed: {e}")
                    if self.history:
                        self.history.add(
                            text,
                            duration=audio_duration,
                            latency=elapsed,
                            source="dictation",
                            raw_text=raw_text,
                            stage1_text=stage1_text,
                            transcriber_model=self.transcriber.model_repo,
                            formatter_model=self.llm.model_repo if (self.llm and raw_text and stage1_text != raw_text) else None,
                            punct_model=self.formatter.model_repo if (self.formatter and stage1_text) else None,
                        )
                gc.collect()
                self.state_manager.set_state(AppState.IDLE)
            else:
                # Original single-pass flow
                wav_path = self.recorder.stop()
                if not wav_path:
                    self._cancel_processing_timeout()
                    self.state_manager.set_state(AppState.IDLE)
                    return
                audio_duration = round(get_wav_duration(wav_path), 2)
                # Cache audio from WAV for retry
                try:
                    import numpy as np
                    from scipy.io import wavfile as _wavfile
                    from app import _last_audio_cache
                    _sr, _data = _wavfile.read(wav_path)
                    _last_audio_cache["audio"] = _data.astype(np.float32) / 32767.0
                    _last_audio_cache["sample_rate"] = _sr
                except Exception:
                    pass
                start_time = time.time()
                text = self.transcriber.transcribe(wav_path)
                elapsed = round(time.time() - start_time, 2)

                self._cancel_processing_timeout()
                # Bail if timed out or cancelled while transcribing
                if self.state_manager.state != AppState.PROCESSING:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                    return

                # Debug: force error on odd attempts for retry testing
                from app import _DEBUG_FORCE_FIRST_ERROR, _debug_error_count
                import app as _app_module
                if _DEBUG_FORCE_FIRST_ERROR:
                    _app_module._debug_error_count += 1
                    if _app_module._debug_error_count % 2 == 1:
                        raise RuntimeError("DEBUG: forced error for retry testing")
                if text:
                    # Two-stage post-processing
                    try:
                        from app import _post_process
                        text, stage1_text, raw_text = _post_process(
                            text, self.llm, self.settings, formatter=self.formatter
                        )
                    except Exception as e:
                        print(f"Hotkey post-process failed: {e}")
                        stage1_text, raw_text = None, None
                    # Check again after post-processing
                    if self.state_manager.state != AppState.PROCESSING:
                        try:
                            os.unlink(wav_path)
                        except OSError:
                            pass
                        return
                    self.internal_clipboard.set_text(text)
                    if self._should_auto_insert():
                        try:
                            paste_text(text)
                        except Exception as e:
                            print(f"Paste operation failed: {e}")
                    if self.history:
                        self.history.add(
                            text,
                            duration=audio_duration,
                            latency=elapsed,
                            source="dictation",
                            raw_text=raw_text,
                            stage1_text=stage1_text,
                            transcriber_model=self.transcriber.model_repo,
                            formatter_model=self.llm.model_repo if (self.llm and raw_text and stage1_text != raw_text) else None,
                            punct_model=self.formatter.model_repo if (self.formatter and stage1_text) else None,
                        )
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
                gc.collect()
                self.state_manager.set_state(AppState.IDLE)
        except Exception as e:
            self._cancel_processing_timeout()
            # Don't overwrite if already timed out to ERROR
            if self.state_manager.state == AppState.PROCESSING:
                print(f"Hotkey transcription error: {e}")
                self.state_manager.set_state(AppState.ERROR)
                threading.Timer(5.0, lambda: self.state_manager.set_state(AppState.IDLE) if self.state_manager.state == AppState.ERROR else None).start()
        finally:
            self._processing = False
            # Resume ClassNote if it was paused for dictation
            if cn_pipeline and cn_pipeline.is_paused:
                cn_pipeline.resume()

    # --- Lifecycle ---

    def start(self):
        """Start HID-level event tap in a background thread.

        Tries active tap first (can suppress system key actions like dictation).
        Falls back to listen-only tap if Accessibility permission is missing.
        """
        event_mask = (
            (1 << kCGEventKeyDown)
            | (1 << kCGEventKeyUp)
            | (1 << kCGEventFlagsChanged)
            | (1 << NX_SYSDEFINED)
        )

        # Try active tap first (requires Accessibility permission)
        self._tap = CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            event_mask,
            self._event_callback,
            None,
        )

        if self._tap is not None:
            self.has_active_tap = True
            print("HID event tap: active mode (can suppress system key actions)")
        else:
            # Fall back to listen-only (requires Input Monitoring permission)
            self._tap = CGEventTapCreate(
                kCGHIDEventTap,
                kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                event_mask,
                self._event_callback,
                None,
            )
            if self._tap is not None:
                print(
                    "HID event tap: listen-only mode (system key actions still fire).\n"
                    "For full suppression, grant Accessibility permission in:\n"
                    "  System Settings > Privacy & Security > Accessibility"
                )
            else:
                print(
                    "ERROR: Failed to create any event tap.\n"
                    "Grant Input Monitoring permission in:\n"
                    "  System Settings > Privacy & Security > Input Monitoring"
                )
                return

        source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, self._tap, 0)
        thread = threading.Thread(target=self._run_tap, args=(source,), daemon=True)
        thread.start()

    def _run_tap(self, source):
        """Run the event tap's CFRunLoop (blocks until stopped)."""
        self._run_loop_ref = CFRunLoopGetCurrent()
        CFRunLoopAddSource(self._run_loop_ref, source, kCFRunLoopCommonModes)
        CGEventTapEnable(self._tap, True)
        CFRunLoopRun()

    def stop(self):
        if self._tap:
            CGEventTapEnable(self._tap, False)
            self._tap = None
        if self._run_loop_ref:
            CFRunLoopStop(self._run_loop_ref)
            self._run_loop_ref = None
        self._cancel_orphan_timer()
        self._cancel_duration_timers()
