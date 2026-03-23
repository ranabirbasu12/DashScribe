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


def paste_text(text: str) -> None:
    """Insert text into the currently focused input field without using clipboard."""
    if not text:
        return

    # Small delay to avoid racing with focus changes right after recording stops.
    time.sleep(0.02)
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
