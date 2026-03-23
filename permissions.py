# permissions.py
"""macOS permission checks via PyObjC."""
import subprocess


def check_permissions() -> dict:
    """Check all required macOS permissions and return their status."""
    result = {}

    # Accessibility — AXIsProcessTrusted()
    try:
        from ApplicationServices import AXIsProcessTrusted
        ax_granted = AXIsProcessTrusted()
    except Exception:
        ax_granted = False

    result["accessibility"] = {
        "granted": bool(ax_granted),
        "required": True,
        "name": "Accessibility",
        "description": "Required for the global hotkey to intercept keys before macOS.",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    }

    # Microphone — AVCaptureDevice.authorizationStatusForMediaType_
    # 0 = notDetermined, 1 = restricted, 2 = denied, 3 = authorized
    try:
        import AVFoundation
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        mic_granted = (status == 3)
        mic_not_determined = (status == 0)
    except Exception:
        mic_granted = False
        mic_not_determined = False

    result["microphone"] = {
        "granted": mic_granted,
        "required": True,
        "not_determined": mic_not_determined,
        "name": "Microphone",
        "description": "Required to record your voice for transcription.",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    }

    # Screen Recording — CGPreflightScreenCaptureAccess()
    try:
        from Quartz import CGPreflightScreenCaptureAccess
        screen_granted = CGPreflightScreenCaptureAccess()
    except Exception:
        screen_granted = False

    result["screen_recording"] = {
        "granted": bool(screen_granted),
        "required": False,
        "name": "Screen & System Audio",
        "description": "Optional. Enables echo cancellation when audio is playing.",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    }

    return result


def request_microphone_access():
    """Trigger the native macOS microphone permission prompt."""
    try:
        import AVFoundation
        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio,
            lambda granted: None,
        )
    except Exception:
        pass


def open_system_settings(url: str):
    """Open a System Settings pane via URL scheme."""
    try:
        subprocess.Popen(["open", url])
    except Exception:
        pass
