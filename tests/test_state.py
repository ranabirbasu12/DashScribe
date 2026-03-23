from state import AppState, AppStateManager


def test_initial_state_is_idle():
    sm = AppStateManager()
    assert sm.state == AppState.IDLE


def test_set_state_fires_callbacks():
    sm = AppStateManager()
    received = []
    sm.on_state_change(lambda old, new: received.append((old, new)))
    sm.set_state(AppState.RECORDING)
    assert received == [(AppState.IDLE, AppState.RECORDING)]


def test_multiple_callbacks_all_fire():
    sm = AppStateManager()
    a, b = [], []
    sm.on_state_change(lambda old, new: a.append(new))
    sm.on_state_change(lambda old, new: b.append(new))
    sm.set_state(AppState.PROCESSING)
    assert a == [AppState.PROCESSING]
    assert b == [AppState.PROCESSING]


def test_set_same_state_does_not_fire():
    sm = AppStateManager()
    received = []
    sm.on_state_change(lambda old, new: received.append(new))
    sm.set_state(AppState.IDLE)
    assert received == []


def test_push_amplitude():
    sm = AppStateManager()
    sm.push_amplitude(0.5)
    sm.push_amplitude(0.8)
    assert sm.get_amplitudes() == [0.5, 0.8]


def test_get_amplitudes_clears_buffer():
    sm = AppStateManager()
    sm.push_amplitude(0.3)
    sm.get_amplitudes()
    assert sm.get_amplitudes() == []


def test_amplitude_callback_fires():
    sm = AppStateManager()
    received = []
    sm.on_amplitude(lambda val: received.append(val))
    sm.push_amplitude(0.7)
    assert received == [0.7]


# ------------------------------------------------------------------
# Exception handling in callbacks (lines 36-37, 56-57, 80-81)
# ------------------------------------------------------------------

def test_state_callback_exception_swallowed():
    """State callbacks that raise exceptions don't break state changes."""
    sm = AppStateManager()
    received = []
    sm.on_state_change(lambda old, new: 1 / 0)  # Will raise ZeroDivisionError
    sm.on_state_change(lambda old, new: received.append(new))
    sm.set_state(AppState.RECORDING)
    # Second callback should still fire
    assert received == [AppState.RECORDING]


def test_amplitude_callback_exception_swallowed():
    """Amplitude callbacks that raise exceptions don't break push_amplitude."""
    sm = AppStateManager()
    received = []
    sm.on_amplitude(lambda v: 1 / 0)
    sm.on_amplitude(lambda v: received.append(v))
    sm.push_amplitude(0.5)
    assert received == [0.5]


def test_warning_callback_exception_swallowed():
    """Warning callbacks that raise exceptions don't break push_warning."""
    sm = AppStateManager()
    received = []
    sm.on_warning(lambda m: 1 / 0)
    sm.on_warning(lambda m: received.append(m))
    sm.push_warning("test warning")
    assert received == ["test warning"]


# ------------------------------------------------------------------
# off_* methods (lines 47-48, 73-74, 91-92)
# ------------------------------------------------------------------

def test_off_state_change_removes_callback():
    sm = AppStateManager()
    cb = lambda old, new: None
    sm.on_state_change(cb)
    result = sm.off_state_change(cb)
    assert result is True


def test_off_state_change_missing_callback():
    sm = AppStateManager()
    result = sm.off_state_change(lambda old, new: None)
    assert result is False


def test_off_amplitude_removes_callback():
    sm = AppStateManager()
    cb = lambda v: None
    sm.on_amplitude(cb)
    result = sm.off_amplitude(cb)
    assert result is True


def test_off_amplitude_missing_callback():
    sm = AppStateManager()
    result = sm.off_amplitude(lambda v: None)
    assert result is False


def test_off_warning_removes_callback():
    sm = AppStateManager()
    cb = lambda m: None
    sm.on_warning(cb)
    result = sm.off_warning(cb)
    assert result is True


def test_off_warning_missing_callback():
    sm = AppStateManager()
    result = sm.off_warning(lambda m: None)
    assert result is False


# ------------------------------------------------------------------
# push_warning (lines 76-81)
# ------------------------------------------------------------------

def test_push_warning():
    sm = AppStateManager()
    received = []
    sm.on_warning(lambda m: received.append(m))
    sm.push_warning("low battery")
    assert received == ["low battery"]


# ------------------------------------------------------------------
# on_* duplicate prevention
# ------------------------------------------------------------------

def test_on_state_change_no_duplicates():
    sm = AppStateManager()
    cb = lambda old, new: None
    sm.on_state_change(cb)
    sm.on_state_change(cb)  # duplicate
    assert sm._state_callbacks.count(cb) == 1


def test_on_amplitude_no_duplicates():
    sm = AppStateManager()
    cb = lambda v: None
    sm.on_amplitude(cb)
    sm.on_amplitude(cb)
    assert sm._amplitude_callbacks.count(cb) == 1


def test_on_warning_no_duplicates():
    sm = AppStateManager()
    cb = lambda m: None
    sm.on_warning(cb)
    sm.on_warning(cb)
    assert sm._warning_callbacks.count(cb) == 1


# ------------------------------------------------------------------
# Additional coverage for state.py
# ------------------------------------------------------------------

def test_state_callback_exception_does_not_prevent_state_change():
    """Exception in state callback doesn't prevent state from being set (lines 36-37)."""
    sm = AppStateManager()

    def bad_cb(old, new):
        raise ValueError("callback error")

    sm.on_state_change(bad_cb)
    sm.set_state(AppState.RECORDING)
    assert sm.state == AppState.RECORDING


def test_off_state_change_returns_false_for_unknown():
    """off_state_change returns False for callback not registered (lines 47-48)."""
    sm = AppStateManager()
    result = sm.off_state_change(lambda o, n: None)
    assert result is False


def test_amplitude_callback_exception_does_not_stop_others():
    """Exception in amplitude callback doesn't prevent others (lines 56-57)."""
    sm = AppStateManager()
    results = []
    sm.on_amplitude(lambda v: (_ for _ in ()).throw(RuntimeError("fail")))
    sm.on_amplitude(lambda v: results.append(v))
    sm.push_amplitude(0.42)
    assert results == [0.42]


def test_off_amplitude_returns_false_for_unknown():
    """off_amplitude returns False for unknown callback (lines 73-74)."""
    sm = AppStateManager()
    result = sm.off_amplitude(lambda v: None)
    assert result is False


def test_warning_callback_exception_does_not_stop_others():
    """Exception in warning callback doesn't prevent others (lines 80-81)."""
    sm = AppStateManager()
    results = []
    sm.on_warning(lambda m: (_ for _ in ()).throw(RuntimeError("fail")))
    sm.on_warning(lambda m: results.append(m))
    sm.push_warning("test")
    assert results == ["test"]


def test_off_warning_returns_false_for_unknown():
    """off_warning returns False for unknown callback (lines 91-92)."""
    sm = AppStateManager()
    result = sm.off_warning(lambda m: None)
    assert result is False


