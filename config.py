# config.py
import os
import json
import threading

CONFIG_DIR = os.path.expanduser("~/.dashscribe")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DICTIONARY_PATH = os.path.join(CONFIG_DIR, "dictionary.txt")
SNIPPETS_PATH = os.path.join(CONFIG_DIR, "snippets.json")

DEFAULT_HOTKEY = "alt_r"
DEFAULT_REPASTE_KEY = "char:v"
LEGACY_REPASTE_MODIFIERS = ("cmd", "alt")
DEFAULT_AUTO_INSERT = True
DEFAULT_THEME_MODE = "auto"
VALID_THEME_MODES = frozenset({"auto", "light", "dark"})

# macOS virtual keycodes → serialized key names
KEYCODE_TO_NAME = {
    # Modifiers
    54: "cmd_r", 55: "cmd_l", 56: "shift_l", 57: "caps_lock",
    58: "alt_l", 59: "ctrl_l", 60: "shift_r", 61: "alt_r",
    62: "ctrl_r", 63: "fn",
    # Function keys (standard keycodes)
    122: "f1", 120: "f2", 99: "f3", 118: "f4",
    96: "f5", 97: "f6", 98: "f7", 100: "f8",
    101: "f9", 109: "f10", 103: "f11", 111: "f12",
    105: "f13", 107: "f14", 113: "f15", 106: "f16",
    64: "f17", 79: "f18", 80: "f19", 90: "f20",
    # MacBook media-mode keycodes (bare F-keys without Fn)
    145: "f1", 144: "f2", 160: "f3", 131: "f4",
    176: "f5", 177: "f6",
    # Special keys
    36: "enter", 48: "tab", 49: "space", 51: "backspace", 53: "esc",
    # Letters
    0: "char:a", 1: "char:s", 2: "char:d", 3: "char:f",
    4: "char:h", 5: "char:g", 6: "char:z", 7: "char:x",
    8: "char:c", 9: "char:v", 11: "char:b", 12: "char:q",
    13: "char:w", 14: "char:e", 15: "char:r", 16: "char:y",
    17: "char:t", 31: "char:o", 32: "char:u", 34: "char:i",
    35: "char:p", 37: "char:l", 38: "char:j", 40: "char:k",
    45: "char:n", 46: "char:m",
    # Numbers
    18: "char:1", 19: "char:2", 20: "char:3", 21: "char:4",
    23: "char:5", 22: "char:6", 26: "char:7", 28: "char:8",
    25: "char:9", 29: "char:0",
    # Arrow keys
    123: "left", 124: "right", 125: "down", 126: "up",
}

NAME_TO_KEYCODE = {v: k for k, v in KEYCODE_TO_NAME.items()}

# Reverse mapping: name → ALL keycodes (handles MacBook dual keycodes)
NAME_TO_KEYCODES: dict[str, frozenset[int]] = {}
for _kc, _name in KEYCODE_TO_NAME.items():
    NAME_TO_KEYCODES.setdefault(_name, set()).add(_kc)
NAME_TO_KEYCODES = {k: frozenset(v) for k, v in NAME_TO_KEYCODES.items()}

DISPLAY_NAMES = {
    "alt_r": "Right Option",
    "alt_l": "Left Option",
    "ctrl_r": "Right Control",
    "ctrl_l": "Left Control",
    "cmd_r": "Right Command",
    "cmd_l": "Left Command",
    "shift_r": "Right Shift",
    "shift_l": "Left Shift",
    "fn": "Fn",
    "space": "Space",
    "tab": "Tab",
    "caps_lock": "Caps Lock",
    "backspace": "Backspace",
    "enter": "Enter",
    "esc": "Escape",
    "left": "Left Arrow",
    "right": "Right Arrow",
    "up": "Up Arrow",
    "down": "Down Arrow",
    **{f"f{i}": f"F{i}" for i in range(1, 21)},
}

SHORTCUT_MODIFIER_ORDER = ("cmd", "alt", "ctrl", "shift", "fn")
SHORTCUT_MODIFIER_ALIASES = {
    "cmd": "cmd",
    "command": "cmd",
    "⌘": "cmd",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "⌥": "alt",
    "ctrl": "ctrl",
    "control": "ctrl",
    "^": "ctrl",
    "shift": "shift",
    "⇧": "shift",
    "fn": "fn",
}
SHORTCUT_MODIFIER_DISPLAY = {
    "cmd": "Cmd",
    "alt": "Option",
    "ctrl": "Control",
    "shift": "Shift",
    "fn": "Fn",
}
MODIFIER_KEY_TO_TOKEN = {
    "cmd_l": "cmd",
    "cmd_r": "cmd",
    "alt_l": "alt",
    "alt_r": "alt",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "fn": "fn",
}


def key_to_string(keycode: int) -> str:
    """Convert a macOS virtual keycode to its serialized string form."""
    return KEYCODE_TO_NAME.get(keycode, "")


def string_to_key(s: str) -> int:
    """Convert a serialized key string to a macOS virtual keycode."""
    if s in NAME_TO_KEYCODE:
        return NAME_TO_KEYCODE[s]
    raise KeyError(f"Unknown key: {s}")


def string_to_keycodes(s: str) -> frozenset[int]:
    """Return ALL macOS keycodes for a key name (handles MacBook dual keycodes)."""
    if s in NAME_TO_KEYCODES:
        return NAME_TO_KEYCODES[s]
    raise KeyError(f"Unknown key: {s}")


def display_name(serialized: str) -> str:
    """Return human-readable name for a serialized key string."""
    if serialized in DISPLAY_NAMES:
        return DISPLAY_NAMES[serialized]
    if serialized.startswith("char:"):
        return serialized[5:].upper()
    return serialized.replace("_", " ").title()


def modifier_token_for_key(serialized: str) -> str | None:
    """Return normalized modifier token for a modifier key name."""
    return MODIFIER_KEY_TO_TOKEN.get(serialized)


def is_modifier_key(serialized: str) -> bool:
    return modifier_token_for_key(serialized) is not None


def repaste_implicit_modifiers(serialized: str) -> tuple[str, ...]:
    """Legacy values like 'char:v' implicitly mean Cmd+Option+<key>."""
    return () if "+" in str(serialized).strip() else LEGACY_REPASTE_MODIFIERS


def _normalize_shortcut_key_token(token: str) -> str:
    key = token.strip().lower()
    if not key:
        raise ValueError("Missing key")
    if key in NAME_TO_KEYCODES:
        return key
    if len(key) == 1 and key.isprintable() and not key.isspace():
        return f"char:{key}"
    raise KeyError(f"Unknown key: {token}")


def _normalize_modifier_token(token: str) -> str:
    mod = SHORTCUT_MODIFIER_ALIASES.get(token.strip().lower())
    if not mod:
        raise KeyError(f"Unknown modifier: {token}")
    return mod


def parse_shortcut(
    serialized: str,
    *,
    implicit_modifiers: tuple[str, ...] = (),
) -> tuple[frozenset[str], str]:
    """Parse serialized shortcut into (modifiers, key)."""
    raw = str(serialized).strip().lower()
    if not raw:
        raise ValueError("Missing shortcut")

    parts = [p.strip().lower() for p in raw.split("+")]
    if any(not p for p in parts):
        raise ValueError("Invalid shortcut")

    modifiers = set(implicit_modifiers)
    if len(parts) == 1:
        key = _normalize_shortcut_key_token(parts[0])
    else:
        key = _normalize_shortcut_key_token(parts[-1])
        for mod_token in parts[:-1]:
            modifiers.add(_normalize_modifier_token(mod_token))

    return frozenset(modifiers), key


def format_shortcut(modifiers: frozenset[str] | set[str] | tuple[str, ...], key: str) -> str:
    ordered = [m for m in SHORTCUT_MODIFIER_ORDER if m in modifiers]
    if not ordered:
        return key
    return "+".join([*ordered, key])


def canonical_shortcut(
    serialized: str,
    *,
    implicit_modifiers: tuple[str, ...] = (),
) -> str:
    modifiers, key = parse_shortcut(serialized, implicit_modifiers=implicit_modifiers)
    return format_shortcut(modifiers, key)


def shortcut_keycodes(
    serialized: str,
    *,
    implicit_modifiers: tuple[str, ...] = (),
) -> frozenset[int]:
    _, key = parse_shortcut(serialized, implicit_modifiers=implicit_modifiers)
    return string_to_keycodes(key)


def shortcut_modifiers(
    serialized: str,
    *,
    implicit_modifiers: tuple[str, ...] = (),
) -> frozenset[str]:
    modifiers, _ = parse_shortcut(serialized, implicit_modifiers=implicit_modifiers)
    return modifiers


def shortcut_display(
    serialized: str,
    *,
    implicit_modifiers: tuple[str, ...] = (),
) -> str:
    modifiers, key = parse_shortcut(serialized, implicit_modifiers=implicit_modifiers)
    ordered_mods = [m for m in SHORTCUT_MODIFIER_ORDER if m in modifiers]
    parts = [SHORTCUT_MODIFIER_DISPLAY[m] for m in ordered_mods]
    parts.append(display_name(key))
    return "+".join(parts)


class SettingsManager:
    """Loads/saves ~/.dashscribe/config.json and notifies on changes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict = {}
        self._hotkey_callbacks: list = []
        self._repaste_callbacks: list = []
        self._save_callbacks: list = []
        self._snippets_save_callbacks: list = []
        self._dictionary_save_callbacks: list = []
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._load()

    def _load(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        with open(CONFIG_PATH, "w") as f:
            json.dump(self._data, f, indent=2)
        for cb in tuple(self._save_callbacks):
            try:
                cb()
            except Exception:
                pass

    @property
    def hotkey_string(self) -> str:
        return self._data.get("hotkey", DEFAULT_HOTKEY)

    @property
    def hotkey_display(self) -> str:
        try:
            return shortcut_display(self.hotkey_string)
        except (KeyError, ValueError):
            return shortcut_display(DEFAULT_HOTKEY)

    @property
    def hotkey_key(self) -> frozenset[int]:
        """Return ALL macOS keycodes for the current hotkey."""
        try:
            return shortcut_keycodes(self.hotkey_string)
        except (KeyError, ValueError):
            return shortcut_keycodes(DEFAULT_HOTKEY)

    @property
    def hotkey_modifiers(self) -> frozenset[str]:
        try:
            return shortcut_modifiers(self.hotkey_string)
        except (KeyError, ValueError):
            return shortcut_modifiers(DEFAULT_HOTKEY)

    def set_hotkey(self, serialized: str) -> bool:
        """Validate, save, and notify. Returns True on success."""
        try:
            canonical = canonical_shortcut(serialized)
            modifiers, key = parse_shortcut(canonical)
        except (KeyError, ValueError):
            return False

        # Disallow nonsensical "modifier + modifier" combos.
        if modifiers and is_modifier_key(key):
            return False

        with self._lock:
            old = self.hotkey_string
            self._data["hotkey"] = canonical
            self._save()

        if old != canonical:
            for cb in tuple(self._hotkey_callbacks):
                try:
                    cb(canonical)
                except Exception:
                    pass
        return True

    @property
    def auto_insert(self) -> bool:
        val = self._data.get("auto_insert", DEFAULT_AUTO_INSERT)
        return bool(val)

    def set_auto_insert(self, enabled: bool):
        with self._lock:
            self._data["auto_insert"] = bool(enabled)
            self._save()

    @property
    def theme_mode(self) -> str:
        mode = self._data.get("theme_mode", DEFAULT_THEME_MODE)
        if mode not in VALID_THEME_MODES:
            return DEFAULT_THEME_MODE
        return mode

    def set_theme_mode(self, mode: str) -> bool:
        if mode not in VALID_THEME_MODES:
            return False
        with self._lock:
            self._data["theme_mode"] = mode
            self._save()
        return True

    @property
    def dictionary_prompt(self) -> str | None:
        """Return comma-separated dictionary terms for Whisper initial_prompt."""
        if not os.path.exists(DICTIONARY_PATH):
            return None
        try:
            with open(DICTIONARY_PATH, "r") as f:
                terms = [line.strip() for line in f if line.strip()]
            return ", ".join(terms) if terms else None
        except OSError:
            return None

    def set_dictionary(self, terms: list[str]):
        """Save dictionary terms to file."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(DICTIONARY_PATH, "w") as f:
            f.write("\n".join(terms) + "\n")
        for cb in tuple(self._dictionary_save_callbacks):
            try:
                cb()
            except Exception:
                pass

    @property
    def snippets(self) -> list[dict]:
        if not os.path.exists(SNIPPETS_PATH):
            return []
        try:
            with open(SNIPPETS_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def set_snippets(self, snippets: list[dict]):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SNIPPETS_PATH, "w") as f:
            json.dump(snippets, f, indent=2)
        for cb in tuple(self._snippets_save_callbacks):
            try:
                cb()
            except Exception:
                pass

    @property
    def snippets_prompt_fragment(self) -> str | None:
        """Return LLM prompt fragment for snippet expansion, or None if no snippets."""
        snips = self.snippets
        if not snips:
            return None
        lines = [f'- "{s["trigger"]}" -> "{s["expansion"]}"' for s in snips]
        return "If the text contains these trigger phrases, replace them with their expansions:\n" + "\n".join(lines)

    @property
    def smart_cleanup(self) -> bool:
        return self._data.get("smart_cleanup", False)

    @smart_cleanup.setter
    def smart_cleanup(self, value: bool):
        with self._lock:
            self._data["smart_cleanup"] = value
            self._save()

    @property
    def context_formatting(self) -> bool:
        return self._data.get("context_formatting", False)

    @context_formatting.setter
    def context_formatting(self, value: bool):
        with self._lock:
            self._data["context_formatting"] = value
            self._save()

    @property
    def app_styles(self) -> dict:
        return self._data.get("app_styles", {})

    @app_styles.setter
    def app_styles(self, value: dict):
        with self._lock:
            self._data["app_styles"] = value
            self._save()

    @property
    def repaste_key_string(self) -> str:
        return self._data.get("repaste_key", DEFAULT_REPASTE_KEY)

    @property
    def repaste_keycodes(self) -> frozenset[int]:
        try:
            return shortcut_keycodes(
                self.repaste_key_string,
                implicit_modifiers=repaste_implicit_modifiers(self.repaste_key_string),
            )
        except (KeyError, ValueError):
            return shortcut_keycodes(
                DEFAULT_REPASTE_KEY,
                implicit_modifiers=LEGACY_REPASTE_MODIFIERS,
            )

    @property
    def repaste_modifiers(self) -> frozenset[str]:
        try:
            return shortcut_modifiers(
                self.repaste_key_string,
                implicit_modifiers=repaste_implicit_modifiers(self.repaste_key_string),
            )
        except (KeyError, ValueError):
            return shortcut_modifiers(
                DEFAULT_REPASTE_KEY,
                implicit_modifiers=LEGACY_REPASTE_MODIFIERS,
            )

    @property
    def repaste_display(self) -> str:
        try:
            return shortcut_display(
                self.repaste_key_string,
                implicit_modifiers=repaste_implicit_modifiers(self.repaste_key_string),
            )
        except (KeyError, ValueError):
            return shortcut_display(
                DEFAULT_REPASTE_KEY,
                implicit_modifiers=LEGACY_REPASTE_MODIFIERS,
            )

    def set_repaste_key(self, serialized: str) -> bool:
        has_explicit_modifiers = "+" in str(serialized).strip()
        try:
            modifiers, key = parse_shortcut(
                serialized,
                implicit_modifiers=(() if has_explicit_modifiers else LEGACY_REPASTE_MODIFIERS),
            )
        except (KeyError, ValueError):
            return False

        # Avoid modifier-only shortcuts and "bare key" repaste mappings.
        if is_modifier_key(key):
            return False
        if not modifiers:
            return False

        if has_explicit_modifiers:
            stored = format_shortcut(modifiers, key)
        else:
            # Backward compatibility: keep single-key storage format.
            stored = key

        with self._lock:
            old = self.repaste_key_string
            self._data["repaste_key"] = stored
            self._save()

        if old != stored:
            for cb in tuple(self._repaste_callbacks):
                try:
                    cb(stored)
                except Exception:
                    pass
        return True

    def get(self, key: str, default=None):
        """Get an arbitrary config value."""
        return self._data.get(key, default)

    def set(self, key: str, value):
        """Set an arbitrary config value and persist."""
        with self._lock:
            self._data[key] = value
            self._save()

    def on_hotkey_change(self, callback):
        """Register a callback: fn(new_serialized_string)."""
        if callback not in self._hotkey_callbacks:
            self._hotkey_callbacks.append(callback)

    def off_hotkey_change(self, callback):
        try:
            self._hotkey_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def on_repaste_change(self, callback):
        """Register a callback: fn(new_serialized_string)."""
        if callback not in self._repaste_callbacks:
            self._repaste_callbacks.append(callback)

    def off_repaste_change(self, callback):
        try:
            self._repaste_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def on_save(self, callback):
        """Register a callback fired after every _save() (config.json write)."""
        if callback not in self._save_callbacks:
            self._save_callbacks.append(callback)

    def off_save(self, callback):
        try:
            self._save_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def on_snippets_save(self, callback):
        """Register a callback fired after every set_snippets() call."""
        if callback not in self._snippets_save_callbacks:
            self._snippets_save_callbacks.append(callback)

    def off_snippets_save(self, callback):
        try:
            self._snippets_save_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def on_dictionary_save(self, callback):
        """Register a callback fired after every set_dictionary() call."""
        if callback not in self._dictionary_save_callbacks:
            self._dictionary_save_callbacks.append(callback)

    def off_dictionary_save(self, callback):
        try:
            self._dictionary_save_callbacks.remove(callback)
            return True
        except ValueError:
            return False
