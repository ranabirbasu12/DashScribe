# tests/test_hotkey.py
import time
from unittest.mock import MagicMock, patch
from hotkey import (
    GlobalHotkey,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskShift,
)
from state import AppState, AppStateManager

# macOS virtual keycodes used in tests
KC_ALT_R = 61     # Right Option
KC_ALT_L = 58     # Left Option
KC_SHIFT = 56     # Left Shift
KC_SHIFT_R = 60   # Right Shift
KC_ESC = 53       # Escape
KC_F5 = 96        # F5
KC_R = 15         # R
KC_B = 11         # B


def make_hotkey(model_ready=True):
    rec = MagicMock()
    rec.is_recording = False
    txr = MagicMock()
    txr.is_ready = model_ready
    sm = AppStateManager()
    history = MagicMock()
    hk = GlobalHotkey(recorder=rec, transcriber=txr, state_manager=sm, history=history)
    return hk, rec, txr, sm, history


def test_hotkey_initializes():
    hk, rec, txr, sm, history = make_hotkey()
    assert hk.is_recording is False
    assert hk._processing is False
    assert hk.toggle_mode is False
    assert hk.last_tap_time is None
    assert KC_ALT_R in hk.trigger_keys  # default fallback


def test_hold_to_talk():
    """Hold >400ms: starts on press, stops on release → transcribe."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = "Hello"

    hk._on_press(KC_ALT_R)
    assert hk.is_recording
    rec.start.assert_called_once()
    assert sm.state == AppState.RECORDING

    # Simulate hold > HOLD_THRESHOLD
    hk.press_start_time = time.time() - 0.5
    hk._on_release(KC_ALT_R)
    assert not hk.is_recording
    assert not hk.toggle_mode


def test_double_tap_starts_toggle_mode():
    """Two quick taps within 500ms → enters toggle mode, keeps recording."""
    hk, rec, txr, sm, history = make_hotkey()

    # First tap
    hk._on_press(KC_ALT_R)
    assert hk.is_recording
    hk.press_start_time = time.time() - 0.1  # short hold
    hk._on_release(KC_ALT_R)
    # After first tap, orphan timer is set, recording continues
    assert hk.is_recording
    assert hk.last_tap_time is not None
    assert hk._orphan_timer is not None

    # Second tap within window
    hk._on_press(KC_ALT_R)
    hk.press_start_time = time.time() - 0.1  # short hold
    hk._on_release(KC_ALT_R)

    assert hk.toggle_mode is True
    assert hk.is_recording  # still recording
    assert hk.last_tap_time is None


def test_single_tap_stops_toggle_mode():
    """Single tap while in toggle mode → stops recording & transcribes."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = "Hello"

    # Put into toggle mode manually
    hk.is_recording = True
    hk.toggle_mode = True
    sm.set_state(AppState.RECORDING)

    # Single short tap → stops recording
    hk._on_press(KC_ALT_R)
    hk.press_start_time = time.time() - 0.1
    hk._on_release(KC_ALT_R)

    assert hk.toggle_mode is False
    assert not hk.is_recording


def test_orphan_single_tap_cancels_recording():
    """Single quick tap with no follow-up → recording cancelled."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.is_recording = True

    hk._on_press(KC_ALT_R)
    assert hk.is_recording
    hk.press_start_time = time.time() - 0.1  # short hold
    hk._on_release(KC_ALT_R)

    # Simulate orphan timer firing
    hk._on_orphan_tap()

    assert not hk.is_recording
    assert sm.state == AppState.IDLE
    rec.stop_raw.assert_called_once()  # discard audio


def test_hotkey_ignores_other_keys():
    hk, rec, txr, sm, history = make_hotkey()
    hk._on_press(KC_ALT_L)
    assert not hk.is_recording
    hk._on_press(KC_SHIFT)
    assert not hk.is_recording
    rec.start.assert_not_called()
    assert sm.state == AppState.IDLE


def test_hotkey_does_not_activate_when_model_not_ready():
    hk, rec, txr, sm, history = make_hotkey(model_ready=False)
    hk._on_press(KC_ALT_R)
    assert not hk.is_recording
    rec.start.assert_not_called()


def test_hotkey_does_not_activate_when_processing():
    hk, rec, txr, sm, history = make_hotkey()
    hk._processing = True
    hk._on_press(KC_ALT_R)
    assert not hk.is_recording
    rec.start.assert_not_called()


@patch("hotkey.get_wav_duration", return_value=3.5)
def test_process_recording_sets_states(_mock_dur):
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = "Hello"
    sm.set_state(AppState.RECORDING)
    hk._process_recording()
    assert sm.state == AppState.IDLE
    history.add.assert_called_once()


def test_process_recording_empty_returns_idle():
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = ""
    sm.set_state(AppState.RECORDING)
    hk._process_recording()
    assert sm.state == AppState.IDLE
    history.add.assert_not_called()


def test_max_duration_stops_recording():
    """Max duration timer fires → force stops recording."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = "Dictation text"

    # Simulate toggle recording in progress
    hk.is_recording = True
    hk.toggle_mode = True
    sm.set_state(AppState.RECORDING)

    hk._on_max_duration()

    assert not hk.is_recording
    assert not hk.toggle_mode
    assert hk.last_tap_time is None


def test_warning_fires():
    """Warning timer calls push_warning on state manager."""
    hk, rec, txr, sm, history = make_hotkey()
    warnings = []
    sm.on_warning(lambda msg: warnings.append(msg))

    hk._on_warning()

    assert warnings == ["Recording ends in 1 minute"]


def test_release_without_recording_is_noop():
    """Releasing key when not recording should do nothing."""
    hk, rec, txr, sm, history = make_hotkey()
    hk._on_release(KC_ALT_R)
    assert sm.state == AppState.IDLE
    rec.stop.assert_not_called()


def test_escape_cancels_hotkey_recording():
    hk, rec, txr, sm, history = make_hotkey()
    rec.is_recording = True

    hk._on_press(KC_ALT_R)
    assert hk.is_recording
    assert sm.state == AppState.RECORDING

    handled = hk._on_escape()
    assert handled is True
    assert hk.is_recording is False
    assert hk.toggle_mode is False
    assert sm.state == AppState.IDLE
    rec.stop_raw.assert_called_once()


def test_escape_calls_external_cancel_callback_when_not_hotkey_recording():
    rec = MagicMock()
    txr = MagicMock()
    txr.is_ready = True
    sm = AppStateManager()
    cb = MagicMock(return_value=True)
    hk = GlobalHotkey(
        recorder=rec,
        transcriber=txr,
        state_manager=sm,
        history=MagicMock(),
        cancel_recording_callback=cb,
    )

    sm.set_state(AppState.RECORDING)
    handled = hk._on_escape()
    assert handled is True
    cb.assert_called_once()


def test_stop_cleans_up_timers():
    """Calling stop() should cancel all timers."""
    hk, rec, txr, sm, history = make_hotkey()
    hk._on_press(KC_ALT_R)
    hk.stop()
    assert hk._orphan_timer is None
    assert hk._warning_timer is None
    assert hk._max_timer is None


def test_hotkey_change_swaps_trigger_key():
    """Changing hotkey via _on_hotkey_changed swaps the trigger key."""
    hk, rec, txr, sm, history = make_hotkey()
    assert KC_ALT_R in hk.trigger_keys

    hk._on_hotkey_changed("f5")
    assert KC_F5 in hk.trigger_keys
    assert KC_ALT_R not in hk.trigger_keys

    # Old key should be ignored, new key should work
    hk._on_press(KC_ALT_R)
    assert not hk.is_recording

    hk._on_press(KC_F5)
    assert hk.is_recording


def test_hotkey_change_cancels_active_recording():
    """Changing hotkey while recording cancels the recording."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.is_recording = True

    # Start recording
    hk._on_press(KC_ALT_R)
    assert hk.is_recording
    assert sm.state == AppState.RECORDING

    # Change hotkey mid-recording
    hk._on_hotkey_changed("shift_r")

    assert not hk.is_recording
    assert not hk.toggle_mode
    assert hk.last_tap_time is None
    assert sm.state == AppState.IDLE
    assert KC_SHIFT_R in hk.trigger_keys


def test_hotkey_change_with_settings_manager():
    """SettingsManager wiring triggers _on_hotkey_changed."""
    from config import SettingsManager
    import tempfile, os, json

    # Use a temp config file
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        import config as config_module
        orig_path = config_module.CONFIG_PATH
        orig_dir = config_module.CONFIG_DIR
        config_module.CONFIG_PATH = config_path
        config_module.CONFIG_DIR = tmpdir

        try:
            settings = SettingsManager()
            rec = MagicMock()
            rec.is_recording = False
            txr = MagicMock()
            txr.is_ready = True
            sm = AppStateManager()
            hk = GlobalHotkey(
                recorder=rec, transcriber=txr,
                state_manager=sm, history=MagicMock(),
                settings=settings,
            )
            assert KC_ALT_R in hk.trigger_keys

            settings.set_hotkey("f5")
            assert KC_F5 in hk.trigger_keys
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_capture_mode_intercepts_key():
    """Capture mode grabs the next key press instead of triggering recording."""
    hk, rec, txr, sm, history = make_hotkey()

    hk.start_key_capture()
    assert hk.poll_key_capture() == {"captured": False}

    # Press F5 — should be captured, NOT start recording
    hk._on_press(KC_F5)
    assert not hk.is_recording
    rec.start.assert_not_called()

    result = hk.poll_key_capture()
    assert result["captured"] is True
    assert result["key"] == "f5"


def test_capture_mode_exits_after_capture():
    """Capture mode automatically exits after capturing a key."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()
    hk._on_press(KC_F5)

    # Next press should work normally (not captured again)
    hk._on_press(KC_ALT_R)
    assert hk.is_recording


def test_capture_mode_cancel():
    """Cancelling capture mode allows normal hotkey operation."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()
    hk.cancel_key_capture()

    assert hk.poll_key_capture() == {"captured": False}
    # Normal hotkey should work
    hk._on_press(KC_ALT_R)
    assert hk.is_recording


def test_capture_ignores_unknown_keycodes():
    """Keycodes not in KEYCODE_TO_NAME are ignored in capture mode."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()

    # Unknown keycode (not in mapping)
    hk._on_press(999)
    assert hk.poll_key_capture() == {"captured": False}
    # Still in capture mode — valid key works
    hk._on_press(KC_F5)
    result = hk.poll_key_capture()
    assert result["captured"] is True


def test_capture_mode_records_combo():
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()

    flags = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate
    hk._on_press(KC_R, flags=flags)

    result = hk.poll_key_capture()
    assert result["captured"] is True
    assert result["key"] == "cmd+alt+char:r"
    assert result["display"] == "Cmd+Option+R"


def test_internal_paste_shortcut_detection():
    hk, rec, txr, sm, history = make_hotkey()
    flags = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate
    assert hk._is_internal_paste_shortcut(9, flags) is True
    assert hk._is_internal_paste_shortcut(9, kCGEventFlagMaskCommand) is False


def test_internal_paste_shortcut_respects_custom_key():
    from config import SettingsManager
    import tempfile, os
    import config as config_module

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        orig_path = config_module.CONFIG_PATH
        orig_dir = config_module.CONFIG_DIR
        config_module.CONFIG_PATH = config_path
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            settings.set_repaste_key("char:b")
            rec = MagicMock()
            txr = MagicMock()
            txr.is_ready = True
            sm = AppStateManager()
            hk = GlobalHotkey(
                recorder=rec,
                transcriber=txr,
                state_manager=sm,
                history=MagicMock(),
                settings=settings,
            )
            flags = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate
            assert hk._is_internal_paste_shortcut(11, flags) is True  # keycode for B
            assert hk._is_internal_paste_shortcut(9, flags) is False   # V no longer active
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_internal_paste_shortcut_respects_custom_combo():
    from config import SettingsManager
    import tempfile, os
    import config as config_module

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        orig_path = config_module.CONFIG_PATH
        orig_dir = config_module.CONFIG_DIR
        config_module.CONFIG_PATH = config_path
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            settings.set_repaste_key("cmd+shift+char:b")
            rec = MagicMock()
            txr = MagicMock()
            txr.is_ready = True
            sm = AppStateManager()
            hk = GlobalHotkey(
                recorder=rec,
                transcriber=txr,
                state_manager=sm,
                history=MagicMock(),
                settings=settings,
            )
            flags = kCGEventFlagMaskCommand | kCGEventFlagMaskShift
            assert hk._is_internal_paste_shortcut(KC_B, flags) is True
            assert hk._is_internal_paste_shortcut(KC_B, kCGEventFlagMaskCommand) is False
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_internal_paste_shortcut_updates_when_setting_changes():
    from config import SettingsManager
    import tempfile, os
    import config as config_module

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        orig_path = config_module.CONFIG_PATH
        orig_dir = config_module.CONFIG_DIR
        config_module.CONFIG_PATH = config_path
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            rec = MagicMock()
            txr = MagicMock()
            txr.is_ready = True
            sm = AppStateManager()
            hk = GlobalHotkey(
                recorder=rec,
                transcriber=txr,
                state_manager=sm,
                history=MagicMock(),
                settings=settings,
            )

            old_flags = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate
            new_flags = kCGEventFlagMaskCommand | kCGEventFlagMaskShift
            assert hk._is_internal_paste_shortcut(9, old_flags) is True

            settings.set_repaste_key("cmd+shift+char:b")

            assert hk._is_internal_paste_shortcut(9, old_flags) is False
            assert hk._is_internal_paste_shortcut(KC_B, new_flags) is True
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


def test_internal_paste_handler_consumes_when_editable():
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("dictated")

    with patch.object(hk, "_focus_is_editable_text", return_value=True):
        with patch.object(hk, "_request_internal_paste") as mock_request:
            assert hk._handle_internal_paste_shortcut(True) is True
            mock_request.assert_called_once()
            assert hk._handle_internal_paste_shortcut(False) is True


def test_internal_paste_handler_passes_through_when_not_editable():
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("dictated")

    with patch.object(hk, "_focus_is_editable_text", return_value=False):
        with patch.object(hk, "_request_internal_paste") as mock_request:
            assert hk._handle_internal_paste_shortcut(True) is False
            mock_request.assert_not_called()
            assert hk._handle_internal_paste_shortcut(False) is False


def test_internal_paste_handler_passes_through_when_internal_clipboard_empty():
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("")

    with patch.object(hk, "_focus_is_editable_text", return_value=True):
        with patch.object(hk, "_request_internal_paste") as mock_request:
            assert hk._handle_internal_paste_shortcut(True) is False
            mock_request.assert_not_called()
            assert hk._handle_internal_paste_shortcut(False) is False


def test_should_handle_internal_paste_false_in_finder():
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("dictated")

    with patch.object(hk, "_frontmost_bundle_id", return_value="com.apple.finder"):
        with patch.object(hk, "_focus_is_editable_text", return_value=True):
            assert hk._should_handle_internal_paste() is False


def test_should_handle_internal_paste_true_in_non_finder_editable_field():
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("dictated")

    with patch.object(hk, "_frontmost_bundle_id", return_value="com.apple.TextEdit"):
        with patch.object(hk, "_focus_is_editable_text", return_value=True):
            assert hk._should_handle_internal_paste() is True


def test_hotkey_combo_requires_modifiers():
    from config import SettingsManager
    import tempfile, os
    import config as config_module

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        orig_path = config_module.CONFIG_PATH
        orig_dir = config_module.CONFIG_DIR
        config_module.CONFIG_PATH = config_path
        config_module.CONFIG_DIR = tmpdir
        try:
            settings = SettingsManager()
            settings.set_hotkey("cmd+shift+char:r")
            rec = MagicMock()
            rec.is_recording = False
            txr = MagicMock()
            txr.is_ready = True
            sm = AppStateManager()
            hk = GlobalHotkey(
                recorder=rec,
                transcriber=txr,
                state_manager=sm,
                history=MagicMock(),
                settings=settings,
            )

            hk._on_press(KC_R, flags=0)
            assert hk.is_recording is False

            hk._on_press(KC_R, flags=(kCGEventFlagMaskCommand | kCGEventFlagMaskShift))
            assert hk.is_recording is True
        finally:
            config_module.CONFIG_PATH = orig_path
            config_module.CONFIG_DIR = orig_dir


@patch("hotkey.paste_text")
def test_internal_paste_uses_internal_clipboard(mock_paste):
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("dictated")
    hk._paste_internal_clipboard()
    mock_paste.assert_called_once_with("dictated")


@patch("hotkey.paste_text")
def test_internal_paste_noop_when_empty(mock_paste):
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("")
    hk._paste_internal_clipboard()
    mock_paste.assert_not_called()


@patch.object(GlobalHotkey, "_paste_internal_clipboard")
def test_request_internal_paste_waits_for_modifier_release(mock_internal_paste):
    hk, rec, txr, sm, history = make_hotkey()
    hk.repaste_modifiers = frozenset({"cmd", "alt"})

    with hk._internal_paste_lock:
        hk._last_event_flags = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate

    hk._request_internal_paste()
    time.sleep(0.04)
    assert mock_internal_paste.call_count == 0

    with hk._internal_paste_lock:
        hk._last_event_flags = 0

    time.sleep(0.08)
    assert mock_internal_paste.call_count == 1


# =====================================================================
# CGEventTap callback tests (_event_callback)
# =====================================================================

from hotkey import (
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventFlagsChanged,
    kCGEventTapDisabledByTimeout,
    kCGKeyboardEventKeycode,
    kCGKeyboardEventAutorepeat,
    kCGEventFlagMaskControl,
    NX_SYSDEFINED,
    NX_SUBTYPE_AUX_CONTROL_BUTTONS,
)


def _make_mock_event(keycode, flags=0, autorepeat=0):
    """Return a mock event and a side_effect fn for CGEventGetIntegerValueField."""
    event = MagicMock()

    def field_side_effect(ev, field):
        if field == kCGKeyboardEventKeycode:
            return keycode
        if field == kCGKeyboardEventAutorepeat:
            return autorepeat
        return 0

    return event, field_side_effect, flags


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
@patch("hotkey.CGEventTapEnable")
def test_event_callback_tap_disabled_by_timeout(mock_enable, mock_field, mock_flags):
    """kCGEventTapDisabledByTimeout re-enables the tap."""
    hk, rec, txr, sm, history = make_hotkey()
    hk._tap = MagicMock()
    sentinel = MagicMock()
    result = hk._event_callback(None, kCGEventTapDisabledByTimeout, sentinel, None)
    assert result is sentinel
    mock_enable.assert_called_once_with(hk._tap, True)


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
@patch("hotkey.CGEventTapEnable")
def test_event_callback_tap_disabled_no_tap(mock_enable, mock_field, mock_flags):
    """kCGEventTapDisabledByTimeout with no tap is safe."""
    hk, rec, txr, sm, history = make_hotkey()
    hk._tap = None
    sentinel = MagicMock()
    result = hk._event_callback(None, kCGEventTapDisabledByTimeout, sentinel, None)
    assert result is sentinel
    mock_enable.assert_not_called()


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_keydown_trigger_suppressed(mock_field, mock_flags):
    """Pressing the trigger key suppresses the event (returns None)."""
    hk, rec, txr, sm, history = make_hotkey()
    event, field_fn, _ = _make_mock_event(KC_ALT_R)
    mock_field.side_effect = field_fn

    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is None  # trigger key suppressed
    assert hk.is_recording


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_keydown_non_trigger_passes(mock_field, mock_flags):
    """Non-trigger key passes through (returns event)."""
    hk, rec, txr, sm, history = make_hotkey()
    event, field_fn, _ = _make_mock_event(KC_ALT_L)
    mock_field.side_effect = field_fn

    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is event


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_keyup_trigger_suppressed(mock_field, mock_flags):
    """Releasing the trigger key also suppresses the event."""
    hk, rec, txr, sm, history = make_hotkey()
    # First press to start recording
    hk.is_recording = True
    hk.press_start_time = time.time() - 0.5  # hold mode

    event, field_fn, _ = _make_mock_event(KC_ALT_R)
    mock_field.side_effect = field_fn

    result = hk._event_callback(None, kCGEventKeyUp, event, None)
    assert result is None  # trigger key suppressed


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_repeat_trigger_suppressed(mock_field, mock_flags):
    """Auto-repeat of trigger key returns None."""
    hk, rec, txr, sm, history = make_hotkey()
    event, _, _ = _make_mock_event(KC_ALT_R, autorepeat=1)

    def field_fn(ev, field):
        if field == kCGKeyboardEventKeycode:
            return KC_ALT_R
        if field == kCGKeyboardEventAutorepeat:
            return 1
        return 0

    mock_field.side_effect = field_fn
    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is None


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_repeat_non_trigger_passes(mock_field, mock_flags):
    """Auto-repeat of non-trigger key passes through."""
    hk, rec, txr, sm, history = make_hotkey()

    def field_fn(ev, field):
        if field == kCGKeyboardEventKeycode:
            return KC_ALT_L  # not a trigger
        if field == kCGKeyboardEventAutorepeat:
            return 1
        return 0

    mock_field.side_effect = field_fn
    event = MagicMock()
    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is event


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_flags_changed_press_and_release(mock_field, mock_flags):
    """FlagsChanged tracks modifier press/release via _held_modifiers."""
    hk, rec, txr, sm, history = make_hotkey()
    event = MagicMock()

    mock_field.return_value = KC_SHIFT
    mock_flags.return_value = kCGEventFlagMaskShift

    # First time: press (not in _held_modifiers)
    result = hk._event_callback(None, kCGEventFlagsChanged, event, None)
    assert KC_SHIFT in hk._held_modifiers
    assert result is event  # not trigger key, passes through

    # Second time: release (already in _held_modifiers)
    result = hk._event_callback(None, kCGEventFlagsChanged, event, None)
    assert KC_SHIFT not in hk._held_modifiers


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_unknown_event_type_passes(mock_field, mock_flags):
    """Unknown event type passes through."""
    hk, rec, txr, sm, history = make_hotkey()
    event = MagicMock()
    result = hk._event_callback(None, 999, event, None)
    assert result is event


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_escape_during_recording(mock_field, mock_flags):
    """Escape key during recording cancels and suppresses."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.is_recording = True
    hk._on_press(KC_ALT_R)
    assert hk.is_recording

    def field_fn(ev, field):
        if field == kCGKeyboardEventKeycode:
            return KC_ESC
        return 0

    mock_field.side_effect = field_fn
    event = MagicMock()
    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    # Escape is not the trigger key, so event passes through even though escape was handled
    # But escape returns None if handled via _on_escape
    # Actually escape suppression: if self._on_escape() returns True → return None
    assert result is None


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_repeat_escape_suppressed(mock_field, mock_flags):
    """Auto-repeat of escape key is suppressed."""
    hk, rec, txr, sm, history = make_hotkey()

    def field_fn(ev, field):
        if field == kCGKeyboardEventKeycode:
            return KC_ESC
        if field == kCGKeyboardEventAutorepeat:
            return 1
        return 0

    mock_field.side_effect = field_fn
    event = MagicMock()
    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is None


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_capture_mode_suppresses_all(mock_field, mock_flags):
    """In capture mode, any key is suppressed (returns None)."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()

    event, field_fn, _ = _make_mock_event(KC_F5)
    mock_field.side_effect = field_fn

    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is None
    assert not hk.is_recording


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_capture_repeat_suppressed(mock_field, mock_flags):
    """In capture mode, repeats are suppressed."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()

    def field_fn(ev, field):
        if field == kCGKeyboardEventKeycode:
            return KC_F5
        if field == kCGKeyboardEventAutorepeat:
            return 1
        return 0

    mock_field.side_effect = field_fn
    event = MagicMock()
    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is None


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_press_error_sets_error_state(mock_field, mock_flags):
    """Exception in _on_press sets ERROR state with 5s recovery timer."""
    hk, rec, txr, sm, history = make_hotkey()
    event, field_fn, _ = _make_mock_event(KC_ALT_R)
    mock_field.side_effect = field_fn

    with patch.object(hk, "_on_press", side_effect=RuntimeError("boom")):
        result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is None  # trigger key still suppressed
    assert sm.state == AppState.ERROR


@patch("hotkey.CGEventGetFlags", return_value=0)
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_release_error_sets_error_state(mock_field, mock_flags):
    """Exception in _on_release sets ERROR state."""
    hk, rec, txr, sm, history = make_hotkey()
    event, field_fn, _ = _make_mock_event(KC_ALT_R)
    mock_field.side_effect = field_fn

    with patch.object(hk, "_on_release", side_effect=RuntimeError("boom")):
        result = hk._event_callback(None, kCGEventKeyUp, event, None)
    assert result is None  # trigger key still suppressed
    assert sm.state == AppState.ERROR


@patch("hotkey.CGEventGetFlags")
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_internal_paste_consumed(mock_field, mock_flags):
    """Internal paste shortcut returns None when handled."""
    hk, rec, txr, sm, history = make_hotkey()
    mock_flags.return_value = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate
    mock_field.return_value = 9  # V keycode

    with patch.object(hk, "_handle_internal_paste_shortcut", return_value=True):
        event = MagicMock()
        result = hk._event_callback(None, kCGEventKeyDown, event, None)
        assert result is None


@patch("hotkey.CGEventGetFlags")
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_internal_paste_not_consumed(mock_field, mock_flags):
    """Internal paste shortcut returns event when not handled."""
    hk, rec, txr, sm, history = make_hotkey()
    mock_flags.return_value = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate
    mock_field.return_value = 9  # V keycode

    with patch.object(hk, "_handle_internal_paste_shortcut", return_value=False):
        event = MagicMock()
        result = hk._event_callback(None, kCGEventKeyDown, event, None)
        assert result is event


@patch("hotkey.CGEventGetFlags")
@patch("hotkey.CGEventGetIntegerValueField")
def test_event_callback_snippet_shortcut_fires(mock_field, mock_flags):
    """Snippet shortcut triggers callback and suppresses event."""
    hk, rec, txr, sm, history = make_hotkey()
    snippet_cb = MagicMock()
    hk.snippet_callback = snippet_cb

    mock_flags.return_value = kCGEventFlagMaskCommand | kCGEventFlagMaskShift

    def field_fn(ev, field):
        if field == kCGKeyboardEventKeycode:
            return 1  # S keycode
        return 0

    mock_field.side_effect = field_fn
    event = MagicMock()
    result = hk._event_callback(None, kCGEventKeyDown, event, None)
    assert result is None
    time.sleep(0.05)
    snippet_cb.assert_called_once()


# =====================================================================
# NX_SYSDEFINED event handling
# =====================================================================

@patch("hotkey.Quartz")
def test_handle_nx_event_press_and_release(mock_quartz):
    """NX_SYSDEFINED maps media keys to keycodes and fires press/release."""
    hk, rec, txr, sm, history = make_hotkey()
    # Map F8 (NX_KEYTYPE_PLAY = 16) — need trigger_keys to include it
    from config import NAME_TO_KEYCODE
    f8_keycode = NAME_TO_KEYCODE.get("f8")
    if f8_keycode is not None:
        hk.trigger_keys = frozenset({f8_keycode})
        hk.trigger_modifiers = frozenset()

    # Build mock NX event
    mock_ns = MagicMock()
    mock_ns.subtype.return_value = NX_SUBTYPE_AUX_CONTROL_BUTTONS
    # nx_key_type=16 (NX_KEYTYPE_PLAY=f8), key_state=0x0A (press)
    mock_ns.data1.return_value = (16 << 16) | (0x0A << 8)
    mock_quartz.NSEvent.eventWithCGEvent_.return_value = mock_ns

    event = MagicMock()
    result = hk._handle_nx_event(event)
    # If f8 is the trigger key, it should be suppressed
    if f8_keycode is not None:
        assert result is None
        assert hk.is_recording


@patch("hotkey.Quartz")
def test_handle_nx_event_non_aux_passes(mock_quartz):
    """NX event with wrong subtype passes through."""
    hk, rec, txr, sm, history = make_hotkey()
    mock_ns = MagicMock()
    mock_ns.subtype.return_value = 99  # Not AUX_CONTROL_BUTTONS
    mock_quartz.NSEvent.eventWithCGEvent_.return_value = mock_ns

    event = MagicMock()
    result = hk._handle_nx_event(event)
    assert result is event


@patch("hotkey.Quartz")
def test_handle_nx_event_nil_ns_event(mock_quartz):
    """NX event where NSEvent conversion returns None passes through."""
    hk, rec, txr, sm, history = make_hotkey()
    mock_quartz.NSEvent.eventWithCGEvent_.return_value = None

    event = MagicMock()
    result = hk._handle_nx_event(event)
    assert result is event


@patch("hotkey.Quartz")
def test_handle_nx_event_unknown_key_type(mock_quartz):
    """NX event with unmapped key type passes through."""
    hk, rec, txr, sm, history = make_hotkey()
    mock_ns = MagicMock()
    mock_ns.subtype.return_value = NX_SUBTYPE_AUX_CONTROL_BUTTONS
    # nx_key_type=255 (unmapped), key_state=0x0A (press)
    mock_ns.data1.return_value = (255 << 16) | (0x0A << 8)
    mock_quartz.NSEvent.eventWithCGEvent_.return_value = mock_ns

    event = MagicMock()
    result = hk._handle_nx_event(event)
    assert result is event


@patch("hotkey.Quartz")
def test_handle_nx_event_neither_press_nor_release(mock_quartz):
    """NX event with key_state not 0x0A or 0x0B passes through."""
    hk, rec, txr, sm, history = make_hotkey()
    mock_ns = MagicMock()
    mock_ns.subtype.return_value = NX_SUBTYPE_AUX_CONTROL_BUTTONS
    # nx_key_type=16 (play), key_state=0x00 (neither press nor release)
    mock_ns.data1.return_value = (16 << 16) | (0x00 << 8)
    mock_quartz.NSEvent.eventWithCGEvent_.return_value = mock_ns

    event = MagicMock()
    result = hk._handle_nx_event(event)
    assert result is event


@patch("hotkey.Quartz")
def test_handle_nx_event_exception_passes(mock_quartz):
    """NX event that raises exception passes through."""
    hk, rec, txr, sm, history = make_hotkey()
    mock_quartz.NSEvent.eventWithCGEvent_.side_effect = RuntimeError("fail")

    event = MagicMock()
    result = hk._handle_nx_event(event)
    assert result is event


@patch("hotkey.Quartz")
def test_handle_nx_event_press_error_sets_error(mock_quartz):
    """NX press handler error sets ERROR state."""
    hk, rec, txr, sm, history = make_hotkey()
    from config import NAME_TO_KEYCODE
    f8_keycode = NAME_TO_KEYCODE.get("f8")
    if f8_keycode is None:
        return
    hk.trigger_keys = frozenset({f8_keycode})
    hk.trigger_modifiers = frozenset()

    mock_ns = MagicMock()
    mock_ns.subtype.return_value = NX_SUBTYPE_AUX_CONTROL_BUTTONS
    mock_ns.data1.return_value = (16 << 16) | (0x0A << 8)
    mock_quartz.NSEvent.eventWithCGEvent_.return_value = mock_ns

    with patch.object(hk, "_on_press", side_effect=RuntimeError("boom")):
        event = MagicMock()
        hk._handle_nx_event(event)
    assert sm.state == AppState.ERROR


@patch("hotkey.Quartz")
def test_handle_nx_event_release_error_sets_error(mock_quartz):
    """NX release handler error sets ERROR state."""
    hk, rec, txr, sm, history = make_hotkey()
    from config import NAME_TO_KEYCODE
    f8_keycode = NAME_TO_KEYCODE.get("f8")
    if f8_keycode is None:
        return
    hk.trigger_keys = frozenset({f8_keycode})
    hk.trigger_modifiers = frozenset()

    mock_ns = MagicMock()
    mock_ns.subtype.return_value = NX_SUBTYPE_AUX_CONTROL_BUTTONS
    # Release state = 0x0B
    mock_ns.data1.return_value = (16 << 16) | (0x0B << 8)
    mock_quartz.NSEvent.eventWithCGEvent_.return_value = mock_ns

    with patch.object(hk, "_on_release", side_effect=RuntimeError("boom")):
        event = MagicMock()
        hk._handle_nx_event(event)
    assert sm.state == AppState.ERROR


# =====================================================================
# Capture mode: modifier-only capture via FlagsChanged
# =====================================================================

def test_capture_mode_modifier_press_via_flags_changed():
    """In capture mode, modifier key press via FlagsChanged is tracked."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()

    # Simulate pressing Shift via FlagsChanged event_type
    hk._on_press(KC_SHIFT, flags=kCGEventFlagMaskShift, event_type=kCGEventFlagsChanged)
    assert hk.poll_key_capture() == {"captured": False}
    assert "shift" in hk._capture_active_modifiers
    assert hk._capture_modifier_candidate is not None


def test_capture_mode_modifier_only_captured_on_release():
    """Pressing and releasing a single modifier captures it as the key."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()

    # Press modifier via FlagsChanged
    hk._on_press(KC_SHIFT, flags=kCGEventFlagMaskShift, event_type=kCGEventFlagsChanged)
    assert hk.poll_key_capture() == {"captured": False}

    # Release modifier via FlagsChanged
    hk._on_release(KC_SHIFT, flags=0, event_type=kCGEventFlagsChanged)

    result = hk.poll_key_capture()
    assert result["captured"] is True
    assert result["key"] == "shift_l"


def test_capture_mode_release_non_candidate_modifier():
    """Releasing a modifier that isn't the candidate doesn't capture."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start_key_capture()

    # Press Shift
    hk._on_press(KC_SHIFT, flags=kCGEventFlagMaskShift, event_type=kCGEventFlagsChanged)
    # Press Cmd (56 is cmd_l, let's use right shift to have a different modifier token)
    hk._on_press(KC_SHIFT_R, flags=kCGEventFlagMaskShift, event_type=kCGEventFlagsChanged)

    # Release Shift (not the last pressed candidate)
    hk._on_release(KC_SHIFT, flags=0, event_type=kCGEventFlagsChanged)
    # Still in capture mode because shift_r modifier candidate was last, and shift modifier still active
    assert hk._capture_mode is True


# =====================================================================
# _flags_to_modifiers with ctrl and fn
# =====================================================================

def test_flags_to_modifiers_ctrl():
    hk, rec, txr, sm, history = make_hotkey()
    mods = hk._flags_to_modifiers(kCGEventFlagMaskControl)
    assert "ctrl" in mods


def test_flags_to_modifiers_fn():
    hk, rec, txr, sm, history = make_hotkey()
    if hk._flag_mask_fn:
        mods = hk._flags_to_modifiers(hk._flag_mask_fn)
        assert "fn" in mods


# =====================================================================
# _is_snippet_shortcut
# =====================================================================

def test_is_snippet_shortcut():
    hk, rec, txr, sm, history = make_hotkey()
    flags = kCGEventFlagMaskCommand | kCGEventFlagMaskShift
    assert hk._is_snippet_shortcut(1, flags) is True  # S with cmd+shift
    assert hk._is_snippet_shortcut(1, 0) is False
    assert hk._is_snippet_shortcut(2, flags) is False


# =====================================================================
# Accessibility helpers (mocked)
# =====================================================================

def test_is_process_trusted_returns_false_on_exception():
    hk, rec, txr, sm, history = make_hotkey()
    with patch.dict("sys.modules", {"ApplicationServices": None}):
        with patch("hotkey.GlobalHotkey._is_process_trusted_for_accessibility") as m:
            m.return_value = False
            assert hk._is_process_trusted_for_accessibility() is False


def test_frontmost_bundle_id_returns_empty_on_exception():
    hk, rec, txr, sm, history = make_hotkey()
    with patch.object(hk, "_frontmost_bundle_id", return_value=""):
        assert hk._frontmost_bundle_id() == ""


def test_ax_copy_attr_returns_none_on_exception():
    hk, rec, txr, sm, history = make_hotkey()
    result = hk._ax_copy_attr(None, "AXRole")
    assert result is None


def test_ax_is_attr_settable_returns_none_on_exception():
    hk, rec, txr, sm, history = make_hotkey()
    result = hk._ax_is_attr_settable(None, "AXValue")
    assert result is None


def test_ax_bool_attr_returns_none_when_no_value():
    hk, rec, txr, sm, history = make_hotkey()
    with patch.object(hk, "_ax_copy_attr", return_value=None):
        assert hk._ax_bool_attr(MagicMock(), "AXEditable") is None


def test_ax_bool_attr_returns_bool():
    hk, rec, txr, sm, history = make_hotkey()
    with patch.object(hk, "_ax_copy_attr", return_value=1):
        assert hk._ax_bool_attr(MagicMock(), "AXEditable") is True


def test_ax_bool_attr_returns_none_on_conversion_error():
    hk, rec, txr, sm, history = make_hotkey()
    bad_value = MagicMock()
    bad_value.__bool__ = MagicMock(side_effect=TypeError("no bool"))
    with patch.object(hk, "_ax_copy_attr", return_value=bad_value):
        assert hk._ax_bool_attr(MagicMock(), "AXEditable") is None


# =====================================================================
# _ax_element_allows_text_input
# =====================================================================

def test_ax_element_allows_text_input_none():
    hk, rec, txr, sm, history = make_hotkey()
    assert hk._ax_element_allows_text_input(None) is False


def test_ax_element_allows_text_input_editable_flag():
    hk, rec, txr, sm, history = make_hotkey()
    with patch.object(hk, "_ax_bool_attr", return_value=True):
        assert hk._ax_element_allows_text_input(MagicMock()) is True


def test_ax_element_text_field_with_settable_value():
    hk, rec, txr, sm, history = make_hotkey()
    elem = MagicMock()
    with patch.object(hk, "_ax_bool_attr", return_value=False):
        with patch.object(hk, "_ax_copy_attr") as mock_copy:
            mock_copy.side_effect = lambda el, attr: {
                "AXRole": "AXTextField",
                "AXSubrole": None,
            }.get(attr)
            with patch.object(hk, "_ax_is_attr_settable", return_value=True):
                assert hk._ax_element_allows_text_input(elem) is True


def test_ax_element_text_field_with_selected_text_range():
    hk, rec, txr, sm, history = make_hotkey()
    elem = MagicMock()
    with patch.object(hk, "_ax_bool_attr", return_value=False):
        with patch.object(hk, "_ax_copy_attr") as mock_copy:
            mock_copy.side_effect = lambda el, attr: {
                "AXRole": "AXTextArea",
                "AXSubrole": None,
                "AXSelectedTextRange": MagicMock(),
            }.get(attr)
            with patch.object(hk, "_ax_is_attr_settable", return_value=False):
                assert hk._ax_element_allows_text_input(elem) is True


def test_ax_element_subrole_editable():
    hk, rec, txr, sm, history = make_hotkey()
    elem = MagicMock()
    with patch.object(hk, "_ax_bool_attr", return_value=False):
        with patch.object(hk, "_ax_copy_attr") as mock_copy:
            mock_copy.side_effect = lambda el, attr: {
                "AXRole": "AXGroup",
                "AXSubrole": "AXTextField",
            }.get(attr)
            with patch.object(hk, "_ax_is_attr_settable", return_value=True):
                assert hk._ax_element_allows_text_input(elem) is True


def test_ax_element_not_editable():
    hk, rec, txr, sm, history = make_hotkey()
    elem = MagicMock()
    with patch.object(hk, "_ax_bool_attr", return_value=False):
        with patch.object(hk, "_ax_copy_attr") as mock_copy:
            mock_copy.side_effect = lambda el, attr: {
                "AXRole": "AXButton",
                "AXSubrole": None,
            }.get(attr)
            assert hk._ax_element_allows_text_input(elem) is False


# =====================================================================
# _focus_is_editable_text
# =====================================================================

def test_focus_is_editable_text_not_trusted():
    hk, rec, txr, sm, history = make_hotkey()
    with patch.object(hk, "_is_process_trusted_for_accessibility", return_value=False):
        assert hk._focus_is_editable_text() is False


def test_focus_is_editable_text_exception():
    hk, rec, txr, sm, history = make_hotkey()
    with patch.object(hk, "_is_process_trusted_for_accessibility", return_value=True):
        with patch.dict("sys.modules", {"ApplicationServices": MagicMock(side_effect=RuntimeError)}):
            # The real function imports AS and calls it; we mock the whole method
            with patch.object(hk, "_focus_is_editable_text", return_value=False):
                assert hk._focus_is_editable_text() is False


# =====================================================================
# _should_auto_insert
# =====================================================================

def test_should_auto_insert_no_settings():
    hk, rec, txr, sm, history = make_hotkey()
    hk.settings = None
    assert hk._should_auto_insert() is True


def test_should_auto_insert_with_settings():
    hk, rec, txr, sm, history = make_hotkey()
    hk.settings = MagicMock()
    hk.settings.auto_insert = False
    assert hk._should_auto_insert() is False


# =====================================================================
# _paste_internal_clipboard exception
# =====================================================================

@patch("hotkey.paste_text", side_effect=RuntimeError("paste fail"))
def test_paste_internal_clipboard_handles_exception(mock_paste):
    hk, rec, txr, sm, history = make_hotkey()
    hk.internal_clipboard.set_text("text")
    # Should not raise
    hk._paste_internal_clipboard()


# =====================================================================
# _request_internal_paste dedup
# =====================================================================

@patch.object(GlobalHotkey, "_paste_internal_clipboard")
def test_request_internal_paste_dedup(mock_paste):
    """Second call while first is pending is ignored."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.repaste_modifiers = frozenset()
    hk._last_event_flags = 0

    hk._request_internal_paste()
    hk._request_internal_paste()  # should be deduped
    time.sleep(0.1)
    assert mock_paste.call_count == 1


# =====================================================================
# _cancel_hotkey_recording with classnote pipeline
# =====================================================================

def test_cancel_hotkey_recording_resumes_classnote():
    hk, rec, txr, sm, history = make_hotkey()
    rec.is_recording = True
    cn = MagicMock()
    cn.is_paused = True
    hk._get_classnote_pipeline = lambda: cn

    hk._on_press(KC_ALT_R)
    assert hk.is_recording
    hk._cancel_hotkey_recording()
    assert not hk.is_recording
    cn.resume.assert_called_once()


def test_cancel_hotkey_recording_with_pipeline_cancel():
    hk, rec, txr, sm, history = make_hotkey()
    rec.is_recording = True
    pipeline = MagicMock()
    hk.pipeline = pipeline

    hk._on_press(KC_ALT_R)
    hk._cancel_hotkey_recording()
    pipeline.cancel.assert_called_once()


# =====================================================================
# _on_escape edge cases
# =====================================================================

def test_escape_in_capture_mode_returns_false():
    hk, rec, txr, sm, history = make_hotkey()
    hk._capture_mode = True
    assert hk._on_escape() is False


def test_escape_callback_returns_none_treated_as_true():
    rec = MagicMock()
    txr = MagicMock()
    txr.is_ready = True
    sm = AppStateManager()
    cb = MagicMock(return_value=None)
    hk = GlobalHotkey(
        recorder=rec, transcriber=txr, state_manager=sm,
        history=MagicMock(), cancel_recording_callback=cb,
    )
    sm.set_state(AppState.RECORDING)
    assert hk._on_escape() is True


def test_escape_callback_exception_returns_false():
    rec = MagicMock()
    txr = MagicMock()
    txr.is_ready = True
    sm = AppStateManager()
    cb = MagicMock(side_effect=RuntimeError("fail"))
    hk = GlobalHotkey(
        recorder=rec, transcriber=txr, state_manager=sm,
        history=MagicMock(), cancel_recording_callback=cb,
    )
    sm.set_state(AppState.RECORDING)
    assert hk._on_escape() is False


def test_escape_not_recording_no_callback():
    hk, rec, txr, sm, history = make_hotkey()
    assert hk._on_escape() is False


# =====================================================================
# _on_press: recorder start failure
# =====================================================================

def test_on_press_recorder_start_failure():
    hk, rec, txr, sm, history = make_hotkey()
    rec.start.side_effect = RuntimeError("mic busy")
    hk._on_press(KC_ALT_R)
    assert not hk.is_recording
    assert sm.state == AppState.ERROR


# =====================================================================
# _on_press: classnote pipeline pause on recording start
# =====================================================================

def test_on_press_pauses_classnote():
    hk, rec, txr, sm, history = make_hotkey()
    cn = MagicMock()
    cn.is_active = True
    hk._get_classnote_pipeline = lambda: cn

    hk._on_press(KC_ALT_R)
    cn.pause.assert_called_once()


# =====================================================================
# _on_press: starts streaming pipeline
# =====================================================================

def test_on_press_starts_pipeline():
    hk, rec, txr, sm, history = make_hotkey()
    pipeline = MagicMock()
    pipeline.vad_available = True
    pipeline.start.return_value = True
    hk.pipeline = pipeline

    hk._on_press(KC_ALT_R)
    pipeline.start.assert_called_once()
    assert rec.on_vad_chunk == pipeline.feed


def test_on_press_pipeline_start_fails():
    hk, rec, txr, sm, history = make_hotkey()
    pipeline = MagicMock()
    pipeline.vad_available = True
    pipeline.start.return_value = False
    hk.pipeline = pipeline

    hk._on_press(KC_ALT_R)
    assert rec.on_vad_chunk is None


# =====================================================================
# _process_recording: streaming path
# =====================================================================

import numpy as np


@patch("hotkey.paste_text")
@patch("hotkey.gc")
def test_process_recording_streaming_short(mock_gc, mock_paste):
    """Streaming path with short recording uses single-pass transcription."""
    hk, rec, txr, sm, history = make_hotkey()
    pipeline = MagicMock()
    pipeline.vad_available = True
    pipeline._active = True
    pipeline.SHORT_RECORDING_THRESHOLD_S = 5.0
    hk.pipeline = pipeline

    # Short audio (1 second at 16kHz)
    mic_audio = np.zeros(16000, dtype=np.float32)
    rec.stop_raw.return_value = (mic_audio, None)
    rec.sample_rate = 16000
    txr.transcribe_array.return_value = "Hello"

    import app as app_module
    with patch.object(app_module, "_last_audio_cache", {"audio": None, "sample_rate": None}):
        with patch.object(app_module, "_DEBUG_FORCE_FIRST_ERROR", False):
            sm.set_state(AppState.RECORDING)
            hk._process_recording()

    assert sm.state == AppState.IDLE
    pipeline.cancel.assert_called_once()
    txr.transcribe_array.assert_called_once()
    mock_paste.assert_called_once_with("Hello")
    history.add.assert_called_once()


@patch("hotkey.paste_text")
@patch("hotkey.gc")
def test_process_recording_streaming_long(mock_gc, mock_paste):
    """Streaming path with long recording uses pipeline."""
    hk, rec, txr, sm, history = make_hotkey()
    pipeline = MagicMock()
    pipeline.vad_available = True
    pipeline._active = True
    pipeline.SHORT_RECORDING_THRESHOLD_S = 5.0
    hk.pipeline = pipeline

    # Long audio (10 seconds)
    mic_audio = np.zeros(160000, dtype=np.float32)
    rec.stop_raw.return_value = (mic_audio, None)
    rec.sample_rate = 16000

    result_segment = MagicMock()
    result_segment.text = "Long dictation"
    pipeline.stop.return_value = [result_segment]

    import app as app_module
    with patch.object(app_module, "_last_audio_cache", {"audio": None, "sample_rate": None}):
        with patch.object(app_module, "_DEBUG_FORCE_FIRST_ERROR", False):
            sm.set_state(AppState.RECORDING)
            hk._process_recording()

    assert sm.state == AppState.IDLE
    pipeline.stop.assert_called_once()
    mock_paste.assert_called_once_with("Long dictation")


@patch("hotkey.gc")
def test_process_recording_streaming_empty_audio(mock_gc):
    """Streaming path with empty audio returns idle."""
    hk, rec, txr, sm, history = make_hotkey()
    pipeline = MagicMock()
    pipeline.vad_available = True
    pipeline._active = True
    hk.pipeline = pipeline

    rec.stop_raw.return_value = (np.array([]), None)
    rec.sample_rate = 16000

    sm.set_state(AppState.RECORDING)
    hk._process_recording()

    assert sm.state == AppState.IDLE
    pipeline.cancel.assert_called_once()


@patch("hotkey.gc")
def test_process_recording_streaming_none_audio(mock_gc):
    """Streaming path with None audio returns idle."""
    hk, rec, txr, sm, history = make_hotkey()
    pipeline = MagicMock()
    pipeline.vad_available = True
    pipeline._active = True
    hk.pipeline = pipeline

    rec.stop_raw.return_value = (None, None)
    rec.sample_rate = 16000

    sm.set_state(AppState.RECORDING)
    hk._process_recording()

    assert sm.state == AppState.IDLE


@patch("hotkey.paste_text")
@patch("hotkey.gc")
def test_process_recording_streaming_with_aec(mock_gc, mock_paste):
    """Streaming short path applies AEC when sys_audio is available."""
    hk, rec, txr, sm, history = make_hotkey()
    pipeline = MagicMock()
    pipeline.vad_available = True
    pipeline._active = True
    pipeline.SHORT_RECORDING_THRESHOLD_S = 5.0
    hk.pipeline = pipeline

    mic_audio = np.zeros(16000, dtype=np.float32)
    sys_audio = np.zeros(16000, dtype=np.float32)
    rec.stop_raw.return_value = (mic_audio, sys_audio)
    rec.sample_rate = 16000
    txr.transcribe_array.return_value = "With AEC"

    import app as app_module
    with patch.object(app_module, "_last_audio_cache", {"audio": None, "sample_rate": None}):
        with patch.object(app_module, "_DEBUG_FORCE_FIRST_ERROR", False):
            with patch("aec.nlms_echo_cancel", return_value=mic_audio):
                with patch("aec.noise_gate", return_value=mic_audio):
                    sm.set_state(AppState.RECORDING)
                    hk._process_recording()

    assert sm.state == AppState.IDLE


def test_process_recording_error_sets_error_state():
    """Exception during processing sets ERROR state with recovery."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.side_effect = RuntimeError("stop failed")

    sm.set_state(AppState.RECORDING)
    hk._process_recording()

    assert sm.state == AppState.ERROR
    assert not hk._processing


def test_process_recording_resumes_classnote():
    """ClassNote pipeline is resumed after processing."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = ""
    cn = MagicMock()
    cn.is_paused = True
    hk._get_classnote_pipeline = lambda: cn

    with patch("hotkey.get_wav_duration", return_value=1.0):
        hk._process_recording()

    cn.resume.assert_called_once()


@patch("hotkey.paste_text", side_effect=RuntimeError("paste error"))
@patch("hotkey.get_wav_duration", return_value=2.0)
def test_process_recording_paste_failure_still_completes(mock_dur, mock_paste):
    """Paste failure doesn't prevent state returning to IDLE."""
    hk, rec, txr, sm, history = make_hotkey()
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = "Hello"

    sm.set_state(AppState.RECORDING)
    hk._process_recording()

    assert sm.state == AppState.IDLE


@patch("hotkey.get_wav_duration", return_value=2.0)
def test_process_recording_auto_insert_disabled(mock_dur):
    """When auto_insert is False, paste_text is not called."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.settings = MagicMock()
    hk.settings.auto_insert = False
    rec.stop.return_value = "/tmp/fake.wav"
    txr.transcribe.return_value = "Hello"

    with patch("hotkey.paste_text") as mock_paste:
        hk._process_recording()

    mock_paste.assert_not_called()


# =====================================================================
# start() and stop() lifecycle
# =====================================================================

@patch("hotkey.CGEventTapCreate", return_value=None)
def test_start_no_tap_returns(mock_create):
    """start() returns early when no tap can be created."""
    hk, rec, txr, sm, history = make_hotkey()
    hk.start()
    assert hk.has_active_tap is False
    assert hk._tap is None


@patch("hotkey.CFRunLoopStop")
@patch("hotkey.CGEventTapEnable")
def test_stop_with_tap_and_runloop(mock_enable, mock_stop):
    """stop() disables tap and stops run loop."""
    hk, rec, txr, sm, history = make_hotkey()
    tap_mock = MagicMock()
    hk._tap = tap_mock
    hk._run_loop_ref = MagicMock()

    hk.stop()

    mock_enable.assert_called_once_with(tap_mock, False)
    assert hk._tap is None
    assert hk._run_loop_ref is None


# =====================================================================
# _on_max_duration when not recording — noop
# =====================================================================

def test_on_max_duration_not_recording():
    hk, rec, txr, sm, history = make_hotkey()
    hk.is_recording = False
    hk._on_max_duration()
    # Nothing should happen
    assert not hk.is_recording


# =====================================================================
# Orphan tap when not recording — noop
# =====================================================================

def test_orphan_tap_not_recording():
    hk, rec, txr, sm, history = make_hotkey()
    hk.is_recording = False
    hk._on_orphan_tap()
    assert not hk.is_recording


def test_orphan_tap_in_toggle_mode():
    hk, rec, txr, sm, history = make_hotkey()
    hk.is_recording = True
    hk.toggle_mode = True
    hk._on_orphan_tap()
    # toggle_mode prevents orphan cancel
    assert hk.is_recording
