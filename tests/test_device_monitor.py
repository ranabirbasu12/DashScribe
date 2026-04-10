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
