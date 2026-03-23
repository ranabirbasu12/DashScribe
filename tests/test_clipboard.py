import pyperclip
from unittest.mock import patch, MagicMock, call
from clipboard import copy_to_clipboard


def test_copy_to_clipboard():
    with patch.object(pyperclip, "copy") as mock_copy:
        copy_to_clipboard("hello dashscribe")
        mock_copy.assert_called_once_with("hello dashscribe")


def test_copy_empty_string():
    with patch.object(pyperclip, "copy") as mock_copy:
        copy_to_clipboard("")
        mock_copy.assert_called_once_with("")


@patch("clipboard.time")
@patch("clipboard.CGEventPost")
@patch("clipboard.CGEventSetFlags")
@patch("clipboard.CGEventCreateKeyboardEvent")
@patch("clipboard.CGEventSourceCreate")
def test_paste_clipboard(mock_source_create, mock_create_kb, mock_set_flags,
                         mock_post, mock_time):
    from clipboard import paste_clipboard
    mock_source_create.return_value = "source"
    mock_create_kb.side_effect = ["event_down", "event_up"]
    paste_clipboard()
    mock_time.sleep.assert_any_call(0.05)
    assert mock_create_kb.call_count == 2
    assert mock_set_flags.call_count == 2
    assert mock_post.call_count == 2


@patch("clipboard.CGEventPost")
@patch("clipboard.CGEventKeyboardSetUnicodeString")
@patch("clipboard.CGEventCreateKeyboardEvent")
@patch("clipboard.CGEventSourceCreate")
@patch("clipboard.time")
def test_paste_text(mock_time, mock_source_create, mock_create_kb,
                    mock_set_unicode, mock_post):
    from clipboard import paste_text
    mock_source_create.return_value = "source"
    mock_create_kb.return_value = "event"
    paste_text("Hello")
    mock_time.sleep.assert_called_with(0.02)
    assert mock_create_kb.call_count == 2  # down + up
    assert mock_set_unicode.call_count == 2
    assert mock_post.call_count == 2


def test_paste_text_empty():
    from clipboard import paste_text
    # Should return immediately without doing anything
    with patch("clipboard.CGEventSourceCreate") as mock_source:
        paste_text("")
        mock_source.assert_not_called()


@patch("clipboard.CGEventPost")
@patch("clipboard.CGEventKeyboardSetUnicodeString")
@patch("clipboard.CGEventCreateKeyboardEvent")
@patch("clipboard.CGEventSourceCreate")
@patch("clipboard.time")
def test_paste_text_long_chunks(mock_time, mock_source_create, mock_create_kb,
                                mock_set_unicode, mock_post):
    """paste_text() splits text into 256-char chunks."""
    from clipboard import paste_text
    mock_source_create.return_value = "source"
    mock_create_kb.return_value = "event"
    long_text = "a" * 512  # Two chunks of 256
    paste_text(long_text)
    # 2 chunks * 2 events (down + up) = 4 keyboard events
    assert mock_create_kb.call_count == 4
    assert mock_post.call_count == 4


# ------------------------------------------------------------------
# Additional coverage: paste_clipboard lines 27-43, paste_text line 49
# ------------------------------------------------------------------

@patch("clipboard.time")
@patch("clipboard.CGEventPost")
@patch("clipboard.CGEventSetFlags")
@patch("clipboard.CGEventCreateKeyboardEvent")
@patch("clipboard.CGEventSourceCreate")
def test_paste_clipboard_creates_source(mock_source_create, mock_create_kb,
                                        mock_set_flags, mock_post, mock_time):
    """paste_clipboard() creates event source with correct state (line 30)."""
    from clipboard import paste_clipboard, kCGEventSourceStateCombinedSessionState
    mock_source_create.return_value = "test_source"
    mock_create_kb.side_effect = ["down", "up"]
    paste_clipboard()
    mock_source_create.assert_called_once_with(kCGEventSourceStateCombinedSessionState)


@patch("clipboard.time")
@patch("clipboard.CGEventPost")
@patch("clipboard.CGEventSetFlags")
@patch("clipboard.CGEventCreateKeyboardEvent")
@patch("clipboard.CGEventSourceCreate")
def test_paste_clipboard_sets_command_flag(mock_source_create, mock_create_kb,
                                           mock_set_flags, mock_post, mock_time):
    """paste_clipboard() sets Command modifier on both events (lines 34, 38)."""
    from clipboard import paste_clipboard, kCGEventFlagMaskCommand
    mock_source_create.return_value = "source"
    mock_create_kb.side_effect = ["event_down", "event_up"]
    paste_clipboard()
    # Both calls should use kCGEventFlagMaskCommand
    assert mock_set_flags.call_count == 2
    mock_set_flags.assert_any_call("event_down", kCGEventFlagMaskCommand)
    mock_set_flags.assert_any_call("event_up", kCGEventFlagMaskCommand)


@patch("clipboard.time")
@patch("clipboard.CGEventPost")
@patch("clipboard.CGEventSetFlags")
@patch("clipboard.CGEventCreateKeyboardEvent")
@patch("clipboard.CGEventSourceCreate")
def test_paste_clipboard_posts_at_hid_tap(mock_source_create, mock_create_kb,
                                           mock_set_flags, mock_post, mock_time):
    """paste_clipboard() posts events at kCGHIDEventTap (lines 41-43)."""
    from clipboard import paste_clipboard, kCGHIDEventTap
    mock_source_create.return_value = "source"
    mock_create_kb.side_effect = ["event_down", "event_up"]
    paste_clipboard()
    assert mock_post.call_count == 2
    mock_post.assert_any_call(kCGHIDEventTap, "event_down")
    mock_post.assert_any_call(kCGHIDEventTap, "event_up")


def test_paste_text_empty_noop():
    """paste_text('') returns immediately (line 49)."""
    from clipboard import paste_text
    with patch("clipboard.CGEventSourceCreate") as mock_source:
        paste_text("")
        mock_source.assert_not_called()


def test_paste_text_none_like_empty():
    """paste_text with falsy string is a no-op."""
    from clipboard import paste_text
    with patch("clipboard.CGEventSourceCreate") as mock_source:
        paste_text("")
        mock_source.assert_not_called()
