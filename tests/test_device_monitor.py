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
