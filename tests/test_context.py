# tests/test_context.py
"""Tests for context detection and formatting style mapping."""
from unittest.mock import patch, MagicMock
from context import get_frontmost_app, get_formatting_style, get_style_prompt


def test_get_formatting_style_slack():
    assert get_formatting_style("com.tinyspeck.slackmacgap") == "casual"


def test_get_formatting_style_mail():
    assert get_formatting_style("com.apple.mail") == "professional"


def test_get_formatting_style_vscode():
    assert get_formatting_style("com.microsoft.VSCode") == "verbatim"


def test_get_formatting_style_unknown():
    assert get_formatting_style("com.unknown.app") == "default"


def test_get_formatting_style_user_override():
    overrides = {"com.unknown.app": "casual"}
    assert get_formatting_style("com.unknown.app", overrides) == "casual"


def test_get_formatting_style_override_beats_builtin():
    overrides = {"com.apple.mail": "casual"}
    assert get_formatting_style("com.apple.mail", overrides) == "casual"


def test_get_style_prompt_returns_string():
    prompt = get_style_prompt("casual")
    assert "casual" in prompt.lower() or "conversational" in prompt.lower()


def test_get_style_prompt_default():
    prompt = get_style_prompt("default")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_get_style_prompt_unknown_falls_back():
    prompt = get_style_prompt("nonexistent")
    assert prompt == get_style_prompt("default")


def test_get_frontmost_app_returns_tuple():
    with patch("context.NSWorkspace") as mock_ws:
        mock_app = MagicMock()
        mock_app.bundleIdentifier.return_value = "com.test.app"
        mock_app.localizedName.return_value = "TestApp"
        mock_ws.sharedWorkspace.return_value.frontmostApplication.return_value = mock_app
        bundle_id, name = get_frontmost_app()
        assert bundle_id == "com.test.app"
        assert name == "TestApp"


def test_get_frontmost_app_handles_error():
    with patch("context.NSWorkspace") as mock_ws:
        mock_ws.sharedWorkspace.side_effect = Exception("no display")
        bundle_id, name = get_frontmost_app()
        assert bundle_id == ""
        assert name == ""
