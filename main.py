# main.py
import os
import sys
import threading
import time

# Fix SSL certificates in py2app bundle.
# __boot__.py sets SSL_CERT_FILE to a non-existent path; point it at certifi's CA bundle.
if getattr(sys, 'frozen', None) == 'macosx_app':
    import certifi
    os.environ['SSL_CERT_FILE'] = certifi.where()
    os.environ.pop('SSL_CERT_DIR', None)

import objc
import AppKit
import uvicorn
import webview

from app import create_app
from recorder import AudioRecorder
from transcriber import WhisperTranscriber
from hotkey import GlobalHotkey
from pipeline import StreamingPipeline
from state import AppState, AppStateManager
from history import TranscriptionHistory
from config import SettingsManager
from internal_clipboard import InternalClipboard
from updater import UpdateManager
from llm import LocalLLM
from lecture_store import LectureStore
from classnote import ClassNotePipeline
from meeting_store import MeetingStore
from meeting import MeetingPipeline

HOST = "127.0.0.1"
PORT = 8765
_app_quitting = False

# Bar dimensions per state
BAR_IDLE_W, BAR_IDLE_H = 80, 20
BAR_RECORDING_W, BAR_RECORDING_H = 244, 42
BAR_PROCESSING_W, BAR_PROCESSING_H = 120, 42
BAR_ERROR_W, BAR_ERROR_H = 42, 42
BAR_ANIM_DURATION = 0.3
BAR_ANIM_FRAME_SEC = 0.014


def start_server(app):
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def get_bar_position(width, height):
    """Center horizontally, 70px above screen bottom."""
    try:
        import AppKit
        screen = AppKit.NSScreen.mainScreen()
        frame = screen.frame()
        screen_w = int(frame.size.width)
        screen_h = int(frame.size.height)
    except ImportError:
        screen_w, screen_h = 1440, 900
    x = (screen_w - width) // 2
    y = screen_h - 70 - height
    return x, y



def _setup_dock_menu(main_window):
    """Add 'Open Dashboard' and 'Quit' to the macOS dock right-click menu."""
    import webview.platforms.cocoa as cocoa_backend

    AppDelegate = cocoa_backend.BrowserView.AppDelegate

    def applicationDockMenu_(self, sender):
        menu = AppKit.NSMenu.alloc().init()
        dash_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open Dashboard", "openDashboard:", "",
        )
        dash_item.setTarget_(self)
        menu.addItem_(dash_item)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit DashScribe", "quitApp:", "",
        )
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)
        return menu

    def openDashboard_(self, sender):
        main_window.show()

    def quitApp_(self, sender):
        global _app_quitting
        _app_quitting = True
        AppKit.NSApplication.sharedApplication().terminate_(None)

    objc.classAddMethod(AppDelegate, b"applicationDockMenu:", applicationDockMenu_)
    objc.classAddMethod(AppDelegate, b"openDashboard:", openDashboard_)
    objc.classAddMethod(AppDelegate, b"quitApp:", quitApp_)

    # Replace pywebview's applicationShouldTerminate: so standard Quit works too.
    # The original checks window.events.closing on every window, but our main window
    # closing handler returns False (to hide instead of close), which blocks quit.
    def applicationShouldTerminate_(self, app):
        global _app_quitting
        _app_quitting = True
        return AppKit.NSTerminateNow

    AppDelegate.applicationShouldTerminate_ = applicationShouldTerminate_


def _patch_accepts_first_mouse():
    """Patch WKWebView classes so clicks work without prior activation.

    By default WKWebView returns NO from acceptsFirstMouse:, so the first
    click on a NonactivatingPanel is silently swallowed. This patches both
    pywebview's WebKitHost (Python subclass of WKWebView) and WKFlippedView
    (WKWebView's internal hit-test subview) at the class level.

    Must be called ONCE before any pywebview windows are created.
    """
    import webview.platforms.cocoa as cocoa_backend

    def _accepts_first_mouse(self, event):
        return True

    sig = objc.selector(
        _accepts_first_mouse,
        selector=b"acceptsFirstMouse:",
        signature=b"Z@:@",
    )

    # Patch pywebview's WebKitHost (Python subclass of WKWebView)
    try:
        objc.classAddMethod(cocoa_backend.BrowserView.WebKitHost, b"acceptsFirstMouse:", sig)
    except Exception:
        pass

    # Patch WKFlippedView (WKWebView's internal subview that receives hit tests)
    try:
        WKFlippedView = objc.lookUpClass("WKFlippedView")
        objc.classAddMethod(WKFlippedView, b"acceptsFirstMouse:", sig)
    except Exception:
        pass


def _patch_window_host_as_panel():
    """Replace pywebview's WindowHost (NSWindow) with NSPanel.

    NSPanel is required for a window to reliably appear above full-screen apps.
    NSWindow + FullScreenAuxiliary is unreliable — macOS treats NSPanel specially
    for full-screen Space participation (confirmed by Helium app and Electron).
    """
    import webview.platforms.cocoa as cocoa_backend

    # Define an NSPanel subclass that floats above full-screen apps.
    # Collection behavior and hidesOnDeactivate must be set at init time —
    # setting them after the window is shown is too late for Space membership.
    class _PanelHost(AppKit.NSPanel):
        def initWithContentRect_styleMask_backing_defer_(self, rect, mask, backing, defer):
            # Add NonactivatingPanel so clicking won't steal focus from full-screen apps
            mask |= 1 << 7  # NSWindowStyleMaskNonactivatingPanel
            self = objc.super(_PanelHost, self).initWithContentRect_styleMask_backing_defer_(
                rect, mask, backing, defer,
            )
            if self is not None:
                self.setHidesOnDeactivate_(False)
                self.setCollectionBehavior_(
                    1 << 0   # NSWindowCollectionBehaviorCanJoinAllSpaces
                    | 1 << 8  # NSWindowCollectionBehaviorFullScreenAuxiliary
                )
            return self

        def canBecomeKeyWindow(self):
            return True

        def canBecomeMainWindow(self):
            return True

    cocoa_backend.BrowserView.WindowHost = _PanelHost


def _configure_bar_window(bar_window):
    """Make the bar float above full-screen apps and appear on all Spaces."""
    nswindow = bar_window.native
    if nswindow is None:
        return

    nswindow.setLevel_(AppKit.NSStatusWindowLevel)
    nswindow.setHidesOnDeactivate_(False)

    # NonactivatingPanel: clicking the bar won't steal focus from the full-screen app
    mask = nswindow.styleMask() | (1 << 7)  # NSWindowStyleMaskNonactivatingPanel
    nswindow.setStyleMask_(mask)

    behavior = (
        1 << 0   # NSWindowCollectionBehaviorCanJoinAllSpaces
        | 1 << 8  # NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    nswindow.setCollectionBehavior_(behavior)

    # Show pointer cursor when hovering, even when the bar isn't key window.
    # WKWebView's internal tracking areas only work in key window, so we add
    # our own always-active tracking area with a cursorUpdate handler.
    content_view = nswindow.contentView()
    if content_view:
        nswindow.setAcceptsMouseMovedEvents_(True)
        tracking_area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            content_view.bounds(),
            (
                AppKit.NSTrackingCursorUpdate
                | AppKit.NSTrackingActiveAlways
                | AppKit.NSTrackingInVisibleRect
            ),
            content_view,
            None,
        )
        content_view.addTrackingArea_(tracking_area)

        # Override cursorUpdate: to set the pointing hand cursor
        def cursorUpdate_(self, event):
            AppKit.NSCursor.pointingHandCursor().set()

        objc.classAddMethod(content_view.__class__, b"cursorUpdate:", cursorUpdate_)



def _configure_main_window(main_window):
    """Undo NSPanel defaults on the main dashboard window.

    The monkey-patched _PanelHost gives all windows FullScreenAuxiliary
    and NonactivatingPanel, but only the bar should have those traits.
    """
    nswindow = main_window.native
    if nswindow is None:
        return
    nswindow.setHidesOnDeactivate_(False)
    # Remove NonactivatingPanel so the main window behaves like a normal
    # window — stays visible when clicking outside it.
    mask = nswindow.styleMask() & ~(1 << 7)  # clear NSWindowStyleMaskNonactivatingPanel
    nswindow.setStyleMask_(mask)
    # Reset to normal managed behavior — don't join full-screen Spaces
    nswindow.setCollectionBehavior_(
        1 << 2  # NSWindowCollectionBehaviorManaged
    )


def main():
    transcriber = WhisperTranscriber()
    state_manager = AppStateManager()
    history = TranscriptionHistory()
    settings = SettingsManager()
    internal_clipboard = InternalClipboard()
    updater = UpdateManager(settings=settings)
    llm = LocalLLM()
    ui_pipeline = StreamingPipeline(transcriber)
    hotkey_pipeline = StreamingPipeline(transcriber)

    lecture_store = LectureStore()
    classnote_pipeline = ClassNotePipeline(
        transcriber=transcriber, store=lecture_store,
    )
    meeting_store = MeetingStore()
    meeting_pipeline = MeetingPipeline(
        transcriber=transcriber, store=meeting_store,
    )

    app = create_app(
        transcriber=transcriber,
        state_manager=state_manager,
        history=history,
        internal_clipboard=internal_clipboard,
        settings=settings,
        pipeline=ui_pipeline,
        updater=updater,
        llm=llm,
        classnote_pipeline=classnote_pipeline,
        lecture_store=lecture_store,
        meeting_pipeline=meeting_pipeline,
        meeting_store=meeting_store,
    )

    # Load hotkey VAD separately; app lifespan loads UI VAD + Whisper warmup.
    threading.Thread(target=hotkey_pipeline.load_vad, daemon=True).start()
    threading.Thread(target=classnote_pipeline.load_vad, daemon=True).start()
    threading.Thread(target=meeting_pipeline.load_vad, daemon=True).start()

    # Global hotkey uses its own recorder to avoid conflicts with the UI
    hotkey_recorder = AudioRecorder()
    hotkey_recorder.on_amplitude = state_manager.push_amplitude
    hotkey = GlobalHotkey(
        recorder=hotkey_recorder,
        transcriber=transcriber,
        state_manager=state_manager,
        internal_clipboard=internal_clipboard,
        history=history,
        settings=settings,
        pipeline=hotkey_pipeline,
        cancel_recording_callback=getattr(app.state, "cancel_active_recording", None),
        llm=llm,
    )
    app.state.hotkey = hotkey
    hotkey.start()

    if not hotkey.has_active_tap:
        print(
            "Accessibility permission not granted for this build.\n"
            "Grant it in: System Settings > Privacy & Security > Accessibility\n"
            "If DashScribe is already listed, toggle it OFF then ON."
        )

    server_thread = threading.Thread(
        target=start_server,
        args=(app,),
        daemon=True,
    )
    server_thread.start()

    # Use NSPanel instead of NSWindow so the bar can float above full-screen apps
    _patch_window_host_as_panel()
    _patch_accepts_first_mouse()

    # Calculate bar position
    bar_x, bar_y = get_bar_position(BAR_IDLE_W, BAR_IDLE_H)

    # Create floating bar window (always exists, keeps app alive)
    bar_window = webview.create_window(
        "",
        f"http://{HOST}:{PORT}/bar",
        width=BAR_IDLE_W,
        height=BAR_IDLE_H,
        x=bar_x,
        y=bar_y,
        min_size=(80, 20),
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
    )

    # Create main window
    main_window = webview.create_window(
        "DashScribe",
        f"http://{HOST}:{PORT}",
        width=1000,
        height=700,
        resizable=True,
        min_size=(900, 600),
    )

    # Store reference so /api/browse-file can open a file dialog
    app.state.main_window = main_window

    bar_anim_lock = threading.Lock()
    bar_anim_token = 0
    bar_size = {"w": BAR_IDLE_W, "h": BAR_IDLE_H}

    def animate_bar_to(target_w, target_h, duration=BAR_ANIM_DURATION):
        nonlocal bar_anim_token
        with bar_anim_lock:
            bar_anim_token += 1
            token = bar_anim_token
            start_w = bar_size["w"]
            start_h = bar_size["h"]

        if start_w == target_w and start_h == target_h:
            x, y = get_bar_position(target_w, target_h)
            bar_window.resize(target_w, target_h)
            bar_window.move(x, y)
            return

        steps = max(1, int(duration / BAR_ANIM_FRAME_SEC))
        for i in range(1, steps + 1):
            with bar_anim_lock:
                if token != bar_anim_token:
                    return

            t = i / steps
            # Smoothstep easing for gentler expand/shrink between phases.
            eased = t * t * (3 - (2 * t))
            w = round(start_w + (target_w - start_w) * eased)
            h = round(start_h + (target_h - start_h) * eased)
            x, y = get_bar_position(w, h)
            bar_window.resize(w, h)
            bar_window.move(x, y)

            with bar_anim_lock:
                bar_size["w"] = w
                bar_size["h"] = h
            time.sleep(duration / steps)

        with bar_anim_lock:
            if token != bar_anim_token:
                return
            bar_size["w"] = target_w
            bar_size["h"] = target_h

    def transition_bar_to(target_w, target_h, duration=BAR_ANIM_DURATION):
        threading.Thread(
            target=animate_bar_to,
            args=(target_w, target_h, duration),
            daemon=True,
        ).start()

    # Handle bar resize based on state changes
    def on_state_change(old_state, new_state):
        if new_state == AppState.RECORDING:
            transition_bar_to(BAR_RECORDING_W, BAR_RECORDING_H)
        elif new_state == AppState.PROCESSING:
            transition_bar_to(BAR_PROCESSING_W, BAR_PROCESSING_H)
        elif new_state == AppState.ERROR:
            transition_bar_to(BAR_ERROR_W, BAR_ERROR_H, duration=0.16)

            # Fallback shrink after 5s if user doesn't click retry.
            def shrink_if_still_error():
                time.sleep(5.0)
                if state_manager.state == AppState.ERROR:
                    state_manager.set_state(AppState.IDLE)

            threading.Thread(target=shrink_if_still_error, daemon=True).start()
        elif new_state == AppState.IDLE:
            transition_bar_to(BAR_IDLE_W, BAR_IDLE_H)

    state_manager.on_state_change(on_state_change)

    # Handle main window close: hide instead of destroy (unless quitting)
    def on_main_closing():
        if _app_quitting:
            return True
        main_window.hide()
        return False

    main_window.events.closing += on_main_closing

    def _show_snippet_overlay():
        main_window.show()
        main_window.evaluate_js("window.showSnippetOverlay && window.showSnippetOverlay()")

    hotkey.snippet_callback = _show_snippet_overlay

    def _on_start():
        _setup_dock_menu(main_window)
        _configure_bar_window(bar_window)
        _configure_main_window(main_window)

    webview.start(func=_on_start)


if __name__ == "__main__":
    main()
