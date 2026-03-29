# tests/test_post_process.py
"""Tests for _post_process() in app.py (two-stage pipeline)."""
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


def _make_formatter(return_value=None):
    """Create a mock formatter. If return_value is set, format() returns it."""
    fmt = MagicMock()
    fmt.is_loaded = return_value is not None
    fmt.model_repo = "test/punct-model"
    if return_value is not None:
        fmt.format.return_value = return_value
    return fmt


# --- Short-circuit / passthrough tests ---

def test_returns_empty_text_unchanged():
    text, s1, raw = _post_process("", _make_llm(), FakeSettings(smart_cleanup=True))
    assert text == ""
    assert s1 is None and raw is None


def test_returns_none_unchanged():
    text, s1, raw = _post_process(None, _make_llm(), FakeSettings(smart_cleanup=True))
    assert text is None
    assert s1 is None and raw is None


def test_returns_short_text_unchanged_by_llm():
    """Text with 5 or fewer words should bypass LLM but still go through formatter."""
    llm = _make_llm()
    text, s1, raw = _post_process("hello world foo bar baz", llm, FakeSettings(smart_cleanup=True))
    assert text == "hello world foo bar baz"
    llm.generate.assert_not_called()


def test_returns_text_when_settings_none():
    llm = _make_llm()
    text, s1, raw = _post_process("this is a longer sentence here", llm, None)
    assert text == "this is a longer sentence here"
    llm.generate.assert_not_called()


def test_returns_text_when_all_toggles_off():
    """No LLM call when smart_cleanup, context_formatting, and snippets are all off."""
    llm = _make_llm()
    text, s1, raw = _post_process(
        "this is a longer sentence here",
        llm,
        FakeSettings(smart_cleanup=False, context_formatting=False, snippets_prompt_fragment=None),
    )
    assert text == "this is a longer sentence here"
    llm.generate.assert_not_called()


# --- Stage 1 (formatter) tests ---

def test_stage1_runs_for_tracking_but_does_not_override():
    """Stage 1 runs and records output but does NOT override Whisper's text."""
    fmt = _make_formatter("Hello world, this is formatted.")
    text, s1, raw = _post_process(
        "hello world this is formatted",
        None,
        FakeSettings(),
        formatter=fmt,
    )
    assert text == "hello world this is formatted"  # Whisper's text preserved
    assert s1 == "Hello world, this is formatted."  # Stage 1 recorded for tracking
    assert raw == "hello world this is formatted"
    fmt.format.assert_called_once()


def test_stage1_skipped_when_formatter_not_loaded():
    fmt = _make_formatter(None)
    fmt.is_loaded = False
    text, s1, raw = _post_process(
        "hello world this is text",
        None,
        FakeSettings(),
        formatter=fmt,
    )
    assert text == "hello world this is text"
    assert s1 is None and raw is None


# --- Stage 2 (LLM) tests ---

def test_smart_cleanup_calls_llm():
    llm = _make_llm("cleaned up text")
    text, s1, raw = _post_process(
        "um so I was like thinking about this thing",
        llm,
        FakeSettings(smart_cleanup=True),
    )
    assert text == "cleaned up text"
    llm.generate.assert_called_once()
    prompt = llm.generate.call_args.kwargs["system_prompt"]
    assert "verbal fillers" in prompt
    assert "Output ONLY the refined text" in prompt


def test_smart_cleanup_off_no_filler_mention():
    """When smart_cleanup is off but snippets trigger LLM, prompt does not include filler removal."""
    llm = _make_llm("expanded text")
    text, s1, raw = _post_process(
        "please use my email snippet here today",
        llm,
        FakeSettings(smart_cleanup=False, snippets_prompt_fragment="Expand: /email -> test@example.com"),
    )
    assert text == "expanded text"
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
        text, s1, raw = _post_process(
            "hey can you send me the report by friday",
            llm,
            FakeSettings(context_formatting=True),
        )
    assert text == "formatted"
    prompt = llm.generate.call_args.kwargs["system_prompt"]
    assert "Mail" in prompt
    assert "Format as an email." in prompt


def test_context_formatting_handles_import_error():
    """If context module raises, post-processing continues without context info."""
    llm = _make_llm("still works")
    with patch.dict("sys.modules", {"context": None}):
        text, s1, raw = _post_process(
            "um so I was thinking about this thing today",
            llm,
            FakeSettings(smart_cleanup=True, context_formatting=True),
        )
    assert text == "still works"
    llm.generate.assert_called_once()


def test_llm_returns_empty_falls_back():
    """If LLM returns empty string, Stage 1 output or original is preserved."""
    llm = _make_llm("")
    original = "this is a longer sentence that should be preserved"
    text, s1, raw = _post_process(original, llm, FakeSettings(smart_cleanup=True))
    assert text == original


def test_llm_returns_none_falls_back():
    """If LLM returns None, Stage 1 output or original is preserved."""
    llm = _make_llm(None)
    original = "this is a longer sentence that should be preserved"
    text, s1, raw = _post_process(original, llm, FakeSettings(smart_cleanup=True))
    assert text == original


# --- Two-stage combined tests ---

def test_both_stages_run():
    """Stage 1 records for tracking, Stage 2 (LLM) operates on Whisper's original text."""
    fmt = _make_formatter("Hello world, this is properly punctuated.")
    llm = _make_llm("Hello world, this is properly punctuated and cleaned.")
    text, s1, raw = _post_process(
        "hello world this is properly punctuated",
        llm,
        FakeSettings(smart_cleanup=True),
        formatter=fmt,
    )
    assert text == "Hello world, this is properly punctuated and cleaned."
    assert s1 == "Hello world, this is properly punctuated."
    assert raw == "hello world this is properly punctuated"
    # LLM receives Whisper's original text (not Stage 1 output)
    args, kwargs = llm.generate.call_args
    assert args[0] == "hello world this is properly punctuated"


def test_stage1_only_no_llm_features():
    """Stage 1 runs for tracking but Whisper text is preserved when no LLM features."""
    fmt = _make_formatter("Formatted text here nicely.")
    llm = _make_llm("should not be called")
    text, s1, raw = _post_process(
        "formatted text here nicely",
        llm,
        FakeSettings(smart_cleanup=False),
        formatter=fmt,
    )
    assert text == "formatted text here nicely"  # Whisper's text preserved
    assert s1 == "Formatted text here nicely."  # Stage 1 tracked
    assert raw == "formatted text here nicely"
    llm.generate.assert_not_called()


def test_llm_receives_whisper_text_not_stage1():
    """LLM receives Whisper's original text, not Stage 1 output."""
    fmt = _make_formatter("Stage one output for the language model.")
    llm = _make_llm("final result")
    _post_process(
        "stage one output for the language model",
        llm,
        FakeSettings(smart_cleanup=True),
        formatter=fmt,
    )
    args, kwargs = llm.generate.call_args
    assert args[0] == "stage one output for the language model"
    assert "system_prompt" in kwargs
