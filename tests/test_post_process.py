# tests/test_post_process.py
"""Tests for _post_process() in app.py."""
from unittest.mock import MagicMock, patch
from app import _post_process


class FakeSettings:
    """Minimal settings stub for post-processing tests."""
    def __init__(
        self,
        smart_cleanup=False,
        context_formatting=False,
        snippets_prompt_fragment=None,
        app_styles=None,
    ):
        self.smart_cleanup = smart_cleanup
        self.context_formatting = context_formatting
        self.snippets_prompt_fragment = snippets_prompt_fragment
        self.app_styles = app_styles or {}


def _make_llm(return_value="cleaned text"):
    llm = MagicMock()
    llm.generate.return_value = return_value
    return llm


# --- Short-circuit / passthrough tests ---

def test_returns_empty_text_unchanged():
    assert _post_process("", _make_llm(), FakeSettings(smart_cleanup=True)) == ""


def test_returns_none_unchanged():
    assert _post_process(None, _make_llm(), FakeSettings(smart_cleanup=True)) is None


def test_returns_short_text_unchanged():
    """Text with 5 or fewer words should bypass LLM."""
    llm = _make_llm()
    result = _post_process("hello world foo bar baz", llm, FakeSettings(smart_cleanup=True))
    assert result == "hello world foo bar baz"
    llm.generate.assert_not_called()


def test_returns_text_when_settings_none():
    llm = _make_llm()
    result = _post_process("this is a longer sentence here", llm, None)
    assert result == "this is a longer sentence here"
    llm.generate.assert_not_called()


def test_returns_text_when_all_toggles_off():
    """No LLM call when smart_cleanup, context_formatting, and snippets are all off."""
    llm = _make_llm()
    result = _post_process(
        "this is a longer sentence here",
        llm,
        FakeSettings(smart_cleanup=False, context_formatting=False, snippets_prompt_fragment=None),
    )
    assert result == "this is a longer sentence here"
    llm.generate.assert_not_called()


# --- LLM invocation tests ---

def test_smart_cleanup_calls_llm():
    llm = _make_llm("cleaned up text")
    result = _post_process(
        "um so I was like thinking about this thing",
        llm,
        FakeSettings(smart_cleanup=True),
    )
    assert result == "cleaned up text"
    llm.generate.assert_called_once()
    prompt = llm.generate.call_args.kwargs["system_prompt"]
    assert "verbal fillers" in prompt
    assert "Output ONLY the formatted text" in prompt


def test_smart_cleanup_off_adds_no_change_wording():
    """When smart_cleanup is off but snippets trigger LLM, prompt does not include filler removal."""
    llm = _make_llm("expanded text")
    result = _post_process(
        "please use my email snippet here today",
        llm,
        FakeSettings(smart_cleanup=False, snippets_prompt_fragment="Expand: /email -> test@example.com"),
    )
    assert result == "expanded text"
    prompt = llm.generate.call_args.kwargs["system_prompt"]
    assert "verbal fillers" not in prompt


def test_snippets_fragment_included_in_prompt():
    llm = _make_llm("with snippet")
    fragment = "Expand: /sig -> Best regards, Alice"
    _post_process(
        "please add my sig at the end of message",
        llm,
        FakeSettings(smart_cleanup=True, snippets_prompt_fragment=fragment),
    )
    prompt = llm.generate.call_args.kwargs["system_prompt"]
    assert fragment in prompt


def test_context_formatting_adds_app_info():
    llm = _make_llm("formatted")
    with patch("context.get_frontmost_app", return_value=("com.apple.mail", "Mail")), \
         patch("context.get_formatting_style", return_value="email"), \
         patch("context.get_style_prompt", return_value="Format as an email."):
        result = _post_process(
            "hey can you send me the report by friday",
            llm,
            FakeSettings(context_formatting=True),
        )
    assert result == "formatted"
    prompt = llm.generate.call_args.kwargs["system_prompt"]
    assert "Mail" in prompt
    assert "Format as an email." in prompt


def test_context_formatting_handles_import_error():
    """If context module raises, post-processing continues without context info."""
    llm = _make_llm("still works")
    with patch.dict("sys.modules", {"context": None}):
        # Even if context import fails, smart_cleanup alone should still work
        result = _post_process(
            "um so I was thinking about this thing today",
            llm,
            FakeSettings(smart_cleanup=True, context_formatting=True),
        )
    assert result == "still works"
    llm.generate.assert_called_once()


def test_llm_returns_empty_falls_back_to_original():
    """If LLM returns empty string, original text is preserved."""
    llm = _make_llm("")
    original = "this is a longer sentence that should be preserved"
    result = _post_process(original, llm, FakeSettings(smart_cleanup=True))
    assert result == original


def test_llm_returns_none_falls_back_to_original():
    """If LLM returns None, original text is preserved."""
    llm = _make_llm(None)
    original = "this is a longer sentence that should be preserved"
    result = _post_process(original, llm, FakeSettings(smart_cleanup=True))
    assert result == original


def test_first_arg_is_text_second_is_system_prompt():
    """Verify the text is passed as positional arg and system_prompt as kwarg."""
    llm = _make_llm("result")
    input_text = "um well I was going to say something important"
    _post_process(input_text, llm, FakeSettings(smart_cleanup=True))
    args, kwargs = llm.generate.call_args
    assert args[0] == input_text
    assert "system_prompt" in kwargs
