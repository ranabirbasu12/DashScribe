from internal_clipboard import InternalClipboard


def test_internal_clipboard_set_get():
    clip = InternalClipboard()
    assert clip.get_text() == ""
    clip.set_text("hello")
    assert clip.get_text() == "hello"
    assert clip.has_text() is True


def test_internal_clipboard_empty():
    clip = InternalClipboard()
    clip.set_text("")
    assert clip.has_text() is False
