# context.py
"""Detect frontmost application and map to formatting style."""
from AppKit import NSWorkspace

_APP_STYLES = {
    # Messaging
    "com.tinyspeck.slackmacgap": "casual",
    "com.hnc.Discord": "casual",
    "com.apple.MobileSMS": "casual",
    "org.telegram.desktop": "casual",
    "net.whatsapp.WhatsApp": "casual",
    "com.facebook.archon": "casual",
    # Email
    "com.apple.mail": "professional",
    "com.microsoft.Outlook": "professional",
    # Documents
    "notion.id": "structured",
    "com.microsoft.Word": "structured",
    "com.apple.iWork.Pages": "structured",
    # Code
    "com.microsoft.VSCode": "verbatim",
    "com.todesktop.230313mzl4w4u92": "verbatim",
    "com.apple.dt.Xcode": "verbatim",
    "com.googlecode.iterm2": "verbatim",
    "com.apple.Terminal": "verbatim",
}

_STYLE_PROMPTS = {
    "casual": "Keep it short and casual. No formal greetings or sign-offs. Lowercase is fine for short messages.",
    "professional": "Use complete sentences, proper grammar, and a professional tone. Add greeting/sign-off only if the speaker included one.",
    "structured": "Use proper paragraphs. Add line breaks between distinct points. Use numbered or bulleted lists if the speaker is listing items.",
    "verbatim": "Preserve the text exactly as spoken. Only fix obvious punctuation. Do not rephrase anything.",
    "default": "Format naturally with proper sentences and paragraphs.",
}


def get_frontmost_app() -> tuple[str, str]:
    """Return (bundle_id, app_name) of the frontmost application."""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_id = app.bundleIdentifier() or ""
        name = app.localizedName() or ""
        return bundle_id, name
    except Exception:
        return "", ""


def get_formatting_style(bundle_id: str, user_overrides: dict | None = None) -> str:
    """Map a bundle ID to a formatting style string."""
    if user_overrides and bundle_id in user_overrides:
        return user_overrides[bundle_id]
    return _APP_STYLES.get(bundle_id, "default")


def get_style_prompt(style: str) -> str:
    """Return the LLM prompt fragment for a given style."""
    return _STYLE_PROMPTS.get(style, _STYLE_PROMPTS["default"])
