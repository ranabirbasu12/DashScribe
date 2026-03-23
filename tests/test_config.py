# tests/test_config.py
"""Tests for config.py – SettingsManager, key mappings, shortcut parsing."""
import json
import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

import config as cfg
from config import (
    KEYCODE_TO_NAME,
    NAME_TO_KEYCODE,
    NAME_TO_KEYCODES,
    DISPLAY_NAMES,
    SettingsManager,
    canonical_shortcut,
    display_name,
    format_shortcut,
    is_modifier_key,
    key_to_string,
    modifier_token_for_key,
    parse_shortcut,
    repaste_implicit_modifiers,
    shortcut_display,
    shortcut_keycodes,
    shortcut_modifiers,
    string_to_key,
    string_to_keycodes,
)


# --- key_to_string / string_to_key / string_to_keycodes ---


def test_key_to_string_known():
    assert key_to_string(61) == "alt_r"


def test_key_to_string_unknown():
    assert key_to_string(9999) == ""


def test_string_to_key_known():
    assert string_to_key("alt_r") == 61


def test_string_to_key_unknown():
    with pytest.raises(KeyError, match="Unknown key"):
        string_to_key("nonexistent_key")


def test_string_to_keycodes_known():
    codes = string_to_keycodes("f5")
    assert isinstance(codes, frozenset)
    assert 96 in codes  # standard
    assert 176 in codes  # MacBook bare


def test_string_to_keycodes_unknown():
    with pytest.raises(KeyError, match="Unknown key"):
        string_to_keycodes("nonexistent_key")


# --- display_name ---


def test_display_name_known():
    assert display_name("alt_r") == "Right Option"


def test_display_name_char():
    assert display_name("char:v") == "V"


def test_display_name_fallback():
    assert display_name("some_thing") == "Some Thing"


# --- modifier helpers ---


def test_modifier_token_for_key():
    assert modifier_token_for_key("cmd_l") == "cmd"
    assert modifier_token_for_key("alt_r") == "alt"
    assert modifier_token_for_key("space") is None


def test_is_modifier_key():
    assert is_modifier_key("shift_l")
    assert not is_modifier_key("char:v")


# --- repaste_implicit_modifiers ---


def test_repaste_implicit_modifiers_bare_key():
    assert repaste_implicit_modifiers("char:v") == ("cmd", "alt")


def test_repaste_implicit_modifiers_explicit():
    assert repaste_implicit_modifiers("cmd+char:v") == ()


# --- _normalize helpers ---


def test_normalize_shortcut_key_token_empty():
    with pytest.raises(ValueError, match="Missing key"):
        cfg._normalize_shortcut_key_token("")


def test_normalize_shortcut_key_token_single_char():
    assert cfg._normalize_shortcut_key_token("v") == "char:v"


def test_normalize_shortcut_key_token_known():
    assert cfg._normalize_shortcut_key_token("alt_r") == "alt_r"


def test_normalize_shortcut_key_token_unknown():
    with pytest.raises(KeyError, match="Unknown key"):
        cfg._normalize_shortcut_key_token("bogus_thing")


def test_normalize_modifier_token_valid():
    assert cfg._normalize_modifier_token("command") == "cmd"
    assert cfg._normalize_modifier_token("⌥") == "alt"


def test_normalize_modifier_token_invalid():
    with pytest.raises(KeyError, match="Unknown modifier"):
        cfg._normalize_modifier_token("bogus")


# --- parse_shortcut ---


def test_parse_shortcut_single_key():
    mods, key = parse_shortcut("alt_r")
    assert key == "alt_r"
    assert mods == frozenset()


def test_parse_shortcut_with_modifiers():
    mods, key = parse_shortcut("cmd+alt+char:v")
    assert key == "char:v"
    assert "cmd" in mods
    assert "alt" in mods


def test_parse_shortcut_empty():
    with pytest.raises(ValueError, match="Missing shortcut"):
        parse_shortcut("")


def test_parse_shortcut_invalid_separator():
    with pytest.raises(ValueError, match="Invalid shortcut"):
        parse_shortcut("cmd++v")


def test_parse_shortcut_implicit_modifiers():
    mods, key = parse_shortcut("char:v", implicit_modifiers=("cmd", "alt"))
    assert "cmd" in mods
    assert "alt" in mods


# --- format_shortcut ---


def test_format_shortcut_no_mods():
    assert format_shortcut(frozenset(), "char:v") == "char:v"


def test_format_shortcut_with_mods():
    result = format_shortcut(frozenset({"cmd", "alt"}), "char:v")
    assert result == "cmd+alt+char:v"


# --- canonical_shortcut ---


def test_canonical_shortcut():
    assert canonical_shortcut("alt+cmd+char:v") == "cmd+alt+char:v"


# --- shortcut_keycodes / shortcut_modifiers / shortcut_display ---


def test_shortcut_keycodes():
    codes = shortcut_keycodes("cmd+char:v")
    assert 9 in codes  # char:v keycode


def test_shortcut_modifiers():
    mods = shortcut_modifiers("cmd+alt+char:v")
    assert mods == frozenset({"cmd", "alt"})


def test_shortcut_display():
    result = shortcut_display("cmd+alt+char:v")
    assert "Cmd" in result
    assert "Option" in result
    assert "V" in result


# --- SettingsManager ---


@pytest.fixture
def settings_dir(tmp_path):
    """Patch config paths to use a temp directory."""
    config_path = str(tmp_path / "config.json")
    dict_path = str(tmp_path / "dictionary.txt")
    snippets_path = str(tmp_path / "snippets.json")
    with patch.object(cfg, "CONFIG_DIR", str(tmp_path)), \
         patch.object(cfg, "CONFIG_PATH", config_path), \
         patch.object(cfg, "DICTIONARY_PATH", dict_path), \
         patch.object(cfg, "SNIPPETS_PATH", snippets_path):
        yield tmp_path, config_path, dict_path, snippets_path


def _make_sm(settings_dir):
    """Create a SettingsManager with patched paths."""
    return SettingsManager()


def test_settings_manager_defaults(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.hotkey_string == "alt_r"
    assert sm.auto_insert is True
    assert sm.theme_mode == "auto"


def test_settings_manager_load_existing(settings_dir):
    tmp_path, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"hotkey": "f5", "auto_insert": False, "theme_mode": "dark"}, f)
    sm = _make_sm(settings_dir)
    assert sm.hotkey_string == "f5"
    assert sm.auto_insert is False
    assert sm.theme_mode == "dark"


def test_settings_manager_load_corrupt_json(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        f.write("{corrupt json")
    sm = _make_sm(settings_dir)
    assert sm.hotkey_string == "alt_r"  # defaults


def test_set_hotkey_valid(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_hotkey("f5") is True
    assert sm.hotkey_string == "f5"


def test_set_hotkey_invalid(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_hotkey("") is False


def test_set_hotkey_modifier_plus_modifier(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_hotkey("cmd+alt_r") is False


def test_set_hotkey_fires_callback(settings_dir):
    sm = _make_sm(settings_dir)
    received = []
    sm.on_hotkey_change(lambda v: received.append(v))
    sm.set_hotkey("f6")
    assert len(received) == 1
    assert received[0] == "f6"


def test_set_hotkey_callback_exception_swallowed(settings_dir):
    sm = _make_sm(settings_dir)
    sm.on_hotkey_change(lambda v: (_ for _ in ()).throw(RuntimeError("boom")))
    # Should not raise
    sm.set_hotkey("f6")


def test_set_hotkey_no_callback_if_same(settings_dir):
    sm = _make_sm(settings_dir)
    sm.set_hotkey("f5")
    received = []
    sm.on_hotkey_change(lambda v: received.append(v))
    sm.set_hotkey("f5")  # same value
    assert len(received) == 0


def test_hotkey_display_fallback(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"hotkey": "totally_bogus_key"}, f)
    sm = _make_sm(settings_dir)
    # Should fall back to default display
    assert "Option" in sm.hotkey_display


def test_hotkey_key_fallback(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"hotkey": "totally_bogus_key"}, f)
    sm = _make_sm(settings_dir)
    assert isinstance(sm.hotkey_key, frozenset)


def test_hotkey_modifiers_fallback(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"hotkey": "totally_bogus_key"}, f)
    sm = _make_sm(settings_dir)
    assert isinstance(sm.hotkey_modifiers, frozenset)


def test_set_auto_insert(settings_dir):
    sm = _make_sm(settings_dir)
    sm.set_auto_insert(False)
    assert sm.auto_insert is False


def test_theme_mode_invalid_value(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"theme_mode": "invalid"}, f)
    sm = _make_sm(settings_dir)
    assert sm.theme_mode == "auto"


def test_set_theme_mode_valid(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_theme_mode("dark") is True
    assert sm.theme_mode == "dark"


def test_set_theme_mode_invalid(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_theme_mode("neon") is False


# --- Dictionary ---


def test_dictionary_prompt_no_file(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.dictionary_prompt is None


def test_dictionary_prompt_with_terms(settings_dir):
    _, _, dict_path, _ = settings_dir
    with open(dict_path, "w") as f:
        f.write("LLM\nGPT\n")
    sm = _make_sm(settings_dir)
    assert sm.dictionary_prompt == "LLM, GPT"


def test_dictionary_prompt_empty_file(settings_dir):
    _, _, dict_path, _ = settings_dir
    with open(dict_path, "w") as f:
        f.write("\n\n")
    sm = _make_sm(settings_dir)
    assert sm.dictionary_prompt is None


def test_dictionary_prompt_os_error(settings_dir):
    _, _, dict_path, _ = settings_dir
    with open(dict_path, "w") as f:
        f.write("term\n")
    with patch("builtins.open", side_effect=OSError("read fail")):
        sm = _make_sm(settings_dir)
        # dictionary_prompt should handle OSError
        # Since _load also uses open, we need a targeted patch
    # Better approach: create SM first, then patch open for dictionary read only
    sm = _make_sm(settings_dir)
    with patch("builtins.open", side_effect=OSError("read fail")):
        assert sm.dictionary_prompt is None


def test_set_dictionary(settings_dir):
    _, _, dict_path, _ = settings_dir
    sm = _make_sm(settings_dir)
    sm.set_dictionary(["PyTorch", "ONNX"])
    with open(dict_path, "r") as f:
        content = f.read()
    assert "PyTorch" in content
    assert "ONNX" in content


def test_set_dictionary_fires_callback(settings_dir):
    sm = _make_sm(settings_dir)
    calls = []
    sm.on_dictionary_save(lambda: calls.append(1))
    sm.set_dictionary(["term"])
    assert len(calls) == 1


def test_set_dictionary_callback_exception(settings_dir):
    sm = _make_sm(settings_dir)
    sm.on_dictionary_save(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    sm.set_dictionary(["term"])  # should not raise


# --- Snippets ---


def test_snippets_empty(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.snippets == []


def test_snippets_corrupt(settings_dir):
    _, _, _, snippets_path = settings_dir
    with open(snippets_path, "w") as f:
        f.write("{bad json")
    sm = _make_sm(settings_dir)
    assert sm.snippets == []


def test_set_snippets(settings_dir):
    sm = _make_sm(settings_dir)
    data = [{"trigger": "brb", "expansion": "be right back"}]
    sm.set_snippets(data)
    assert sm.snippets == data


def test_set_snippets_fires_callback(settings_dir):
    sm = _make_sm(settings_dir)
    calls = []
    sm.on_snippets_save(lambda: calls.append(1))
    sm.set_snippets([{"trigger": "a", "expansion": "b"}])
    assert len(calls) == 1


def test_set_snippets_callback_exception(settings_dir):
    sm = _make_sm(settings_dir)
    sm.on_snippets_save(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    sm.set_snippets([])  # should not raise


def test_snippets_prompt_fragment_none(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.snippets_prompt_fragment is None


def test_snippets_prompt_fragment(settings_dir):
    sm = _make_sm(settings_dir)
    sm.set_snippets([{"trigger": "brb", "expansion": "be right back"}])
    frag = sm.snippets_prompt_fragment
    assert "brb" in frag
    assert "be right back" in frag


# --- smart_cleanup, context_formatting, app_styles ---


def test_smart_cleanup_default(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.smart_cleanup is False


def test_smart_cleanup_setter(settings_dir):
    sm = _make_sm(settings_dir)
    sm.smart_cleanup = True
    assert sm.smart_cleanup is True


def test_context_formatting_default(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.context_formatting is False


def test_context_formatting_setter(settings_dir):
    sm = _make_sm(settings_dir)
    sm.context_formatting = True
    assert sm.context_formatting is True


def test_app_styles_default(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.app_styles == {}


def test_app_styles_setter(settings_dir):
    sm = _make_sm(settings_dir)
    sm.app_styles = {"font_size": 14}
    assert sm.app_styles == {"font_size": 14}


# --- Repaste key ---


def test_repaste_key_default(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.repaste_key_string == "char:v"


def test_repaste_keycodes_default(settings_dir):
    sm = _make_sm(settings_dir)
    codes = sm.repaste_keycodes
    assert 9 in codes  # char:v


def test_repaste_modifiers_default(settings_dir):
    sm = _make_sm(settings_dir)
    mods = sm.repaste_modifiers
    assert "cmd" in mods
    assert "alt" in mods


def test_repaste_display_default(settings_dir):
    sm = _make_sm(settings_dir)
    disp = sm.repaste_display
    assert "Cmd" in disp


def test_set_repaste_key_valid(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_repaste_key("char:b") is True
    assert sm.repaste_key_string == "char:b"


def test_set_repaste_key_explicit_modifiers(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_repaste_key("ctrl+char:v") is True
    assert "ctrl" in sm.repaste_key_string


def test_set_repaste_key_invalid(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_repaste_key("") is False


def test_set_repaste_key_modifier_only(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.set_repaste_key("cmd_l") is False


def test_set_repaste_key_bare_no_modifiers(settings_dir):
    """A bare key with no implicit modifiers should fail (no mods = rejected)."""
    sm = _make_sm(settings_dir)
    # Explicit "+" but no modifier in front
    # Actually "char:v" has implicit modifiers so it works
    # "cmd+alt_r" should fail because alt_r is a modifier key
    assert sm.set_repaste_key("cmd+alt_r") is False


def test_set_repaste_key_fires_callback(settings_dir):
    sm = _make_sm(settings_dir)
    received = []
    sm.on_repaste_change(lambda v: received.append(v))
    sm.set_repaste_key("char:b")
    assert len(received) == 1


def test_set_repaste_key_callback_exception(settings_dir):
    sm = _make_sm(settings_dir)
    sm.on_repaste_change(lambda v: (_ for _ in ()).throw(RuntimeError("boom")))
    sm.set_repaste_key("char:b")  # should not raise


def test_set_repaste_key_no_callback_if_same(settings_dir):
    sm = _make_sm(settings_dir)
    sm.set_repaste_key("char:b")
    received = []
    sm.on_repaste_change(lambda v: received.append(v))
    sm.set_repaste_key("char:b")  # same
    assert len(received) == 0


def test_repaste_keycodes_fallback(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"repaste_key": "bogus_key_xyz"}, f)
    sm = _make_sm(settings_dir)
    codes = sm.repaste_keycodes
    assert isinstance(codes, frozenset)
    assert 9 in codes  # falls back to default char:v


def test_repaste_modifiers_fallback(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"repaste_key": "bogus_key_xyz"}, f)
    sm = _make_sm(settings_dir)
    mods = sm.repaste_modifiers
    assert "cmd" in mods


def test_repaste_display_fallback(settings_dir):
    _, config_path, _, _ = settings_dir
    with open(config_path, "w") as f:
        json.dump({"repaste_key": "bogus_key_xyz"}, f)
    sm = _make_sm(settings_dir)
    disp = sm.repaste_display
    assert "Cmd" in disp


# --- get/set arbitrary ---


def test_get_set_arbitrary(settings_dir):
    sm = _make_sm(settings_dir)
    assert sm.get("custom_key") is None
    assert sm.get("custom_key", 42) == 42
    sm.set("custom_key", "hello")
    assert sm.get("custom_key") == "hello"


# --- on/off callbacks ---


def test_off_hotkey_change(settings_dir):
    sm = _make_sm(settings_dir)
    cb = lambda v: None
    sm.on_hotkey_change(cb)
    assert sm.off_hotkey_change(cb) is True
    assert sm.off_hotkey_change(cb) is False


def test_off_repaste_change(settings_dir):
    sm = _make_sm(settings_dir)
    cb = lambda v: None
    sm.on_repaste_change(cb)
    assert sm.off_repaste_change(cb) is True
    assert sm.off_repaste_change(cb) is False


def test_off_save(settings_dir):
    sm = _make_sm(settings_dir)
    cb = lambda: None
    sm.on_save(cb)
    assert sm.off_save(cb) is True
    assert sm.off_save(cb) is False


def test_off_snippets_save(settings_dir):
    sm = _make_sm(settings_dir)
    cb = lambda: None
    sm.on_snippets_save(cb)
    assert sm.off_snippets_save(cb) is True
    assert sm.off_snippets_save(cb) is False


def test_off_dictionary_save(settings_dir):
    sm = _make_sm(settings_dir)
    cb = lambda: None
    sm.on_dictionary_save(cb)
    assert sm.off_dictionary_save(cb) is True
    assert sm.off_dictionary_save(cb) is False


# --- Save callback ---


def test_save_callback_fires(settings_dir):
    sm = _make_sm(settings_dir)
    calls = []
    sm.on_save(lambda: calls.append(1))
    sm.set_auto_insert(False)
    assert len(calls) == 1


def test_save_callback_exception_swallowed(settings_dir):
    sm = _make_sm(settings_dir)
    sm.on_save(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    sm.set_auto_insert(False)  # should not raise
