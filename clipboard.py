import time

import pyperclip
import Quartz
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventKeyboardSetUnicodeString,
    CGEventSetFlags,
    CGEventPost,
    CGEventSourceCreate,
    kCGHIDEventTap,
    kCGEventFlagMaskCommand,
    kCGEventSourceStateCombinedSessionState,
)

# macOS keycode for 'V'
_KC_V = 9


def copy_to_clipboard(text: str) -> None:
    pyperclip.copy(text)


def paste_clipboard() -> None:
    """Simulate Cmd+V to paste into the currently focused input field."""
    # Small delay to ensure clipboard is updated
    time.sleep(0.05)

    # Create a proper event source so macOS treats the events as legitimate
    source = CGEventSourceCreate(kCGEventSourceStateCombinedSessionState)

    # Key down: V with Command modifier
    event_down = CGEventCreateKeyboardEvent(source, _KC_V, True)
    CGEventSetFlags(event_down, kCGEventFlagMaskCommand)

    # Key up: V with Command modifier
    event_up = CGEventCreateKeyboardEvent(source, _KC_V, False)
    CGEventSetFlags(event_up, kCGEventFlagMaskCommand)

    # Post at HID level so events go through the full macOS event pipeline
    CGEventPost(kCGHIDEventTap, event_down)
    time.sleep(0.01)
    CGEventPost(kCGHIDEventTap, event_up)


def _needs_leading_space() -> bool:
    """Check if the character before the cursor in the focused field is a non-space.

    Uses macOS Accessibility API to read the focused element's text and cursor
    position. Returns True if a space should be prepended before pasting.
    Fails silently (returns False) if Accessibility is unavailable.
    """
    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
        )
        system = AXUIElementCreateSystemWide()
        err, focused = AXUIElementCopyAttributeValue(system, 'AXFocusedUIElement', None)
        if err != 0 or focused is None:
            return False

        # Get text content
        err, value = AXUIElementCopyAttributeValue(focused, 'AXValue', None)
        if err != 0 or not value or not isinstance(value, str):
            return False

        # Get cursor position (selected text range)
        err, sel_range = AXUIElementCopyAttributeValue(focused, 'AXSelectedTextRange', None)
        if err != 0 or sel_range is None:
            return False

        cursor_pos = sel_range.location
        if cursor_pos <= 0 or cursor_pos > len(value):
            return False

        char_before = value[cursor_pos - 1]
        # Need a space if the character before cursor is not already a space/newline
        return char_before not in (' ', '\t', '\n', '\r')
    except Exception:
        return False


def paste_text(text: str) -> None:
    """Insert text into the currently focused input field without using clipboard.

    Automatically prepends a space if the cursor follows existing text
    (e.g., inserting after "Hello." produces "Hello. How are you" not "Hello.How are you").
    """
    if not text:
        return

    # Small delay to avoid racing with focus changes right after recording stops.
    time.sleep(0.02)

    # Check if we need a leading space
    if _needs_leading_space():
        text = " " + text

    source = CGEventSourceCreate(kCGEventSourceStateCombinedSessionState)

    # Post in chunks to avoid oversized event payloads in some apps.
    chunk_size = 256
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        down = CGEventCreateKeyboardEvent(source, 0, True)
        up = CGEventCreateKeyboardEvent(source, 0, False)
        CGEventKeyboardSetUnicodeString(down, len(chunk), chunk)
        CGEventKeyboardSetUnicodeString(up, len(chunk), chunk)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)
