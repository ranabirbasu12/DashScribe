# app.py
import asyncio
import gc
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from recorder import AudioRecorder, get_wav_duration
from transcriber import WhisperTranscriber
from clipboard import paste_text
from state import AppState, AppStateManager
from history import TranscriptionHistory
from internal_clipboard import InternalClipboard
from diagnostics import MemoryTelemetry
from version import __version__
from file_job import FileJob, FileJobOptions, FileJobRunner
from diarizer import Diarizer
from exporter import write_export, FORMATS

logger = logging.getLogger(__name__)


def _get_static_dir():
    """Resolve static/ path for both development and py2app bundle."""
    if getattr(sys, 'frozen', None) == 'macosx_app':
        resource_path = os.environ.get('RESOURCEPATH', '.')
        return os.path.join(resource_path, 'static')
    return os.path.join(os.path.dirname(__file__), 'static')


STATIC_DIR = _get_static_dir()
SUPPORTED_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".wma", ".aac"}
SUPPORTED_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg"}
SUPPORTED_MEDIA_EXT = SUPPORTED_AUDIO_EXT | SUPPORTED_VIDEO_EXT
MAX_RECORD_SECONDS = 600
PROCESSING_TIMEOUT_S = 5


def _extract_audio(video_path: str) -> str:
    """Extract audio from a video file to a temporary WAV using ffmpeg."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", tmp.name],
        capture_output=True, check=True,
    )
    return tmp.name
def _download_url(url: str) -> str:
    """Download bestaudio from a URL via yt-dlp; return local file path."""
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="dashscribe_url_")
    out_template = f"{tmp_dir}/%(id)s.%(ext)s"
    from yt_dlp import YoutubeDL
    with YoutubeDL({"format": "bestaudio/best", "outtmpl": out_template, "quiet": True}) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


WARNING_SECONDS = 540


def create_app(
    recorder: AudioRecorder | None = None,
    transcriber: WhisperTranscriber | None = None,
    state_manager: AppStateManager | None = None,
    history: TranscriptionHistory | None = None,
    internal_clipboard: InternalClipboard | None = None,
    memory_telemetry: MemoryTelemetry | None = None,
    settings=None,
    pipeline=None,
    updater=None,
    llm=None,
    formatter=None,
    classnote_pipeline=None,
    lecture_store=None,
    meeting_pipeline=None,
    meeting_store=None,
) -> FastAPI:
    rec = recorder or AudioRecorder()
    txr = transcriber or WhisperTranscriber()
    sm = state_manager or AppStateManager()
    hist = history or TranscriptionHistory()
    app_clip = internal_clipboard or InternalClipboard()
    mem_telemetry = memory_telemetry or MemoryTelemetry()
    pipe = pipeline
    cn_pipeline = classnote_pipeline
    cn_store = lecture_store
    mt_pipeline = meeting_pipeline
    mt_store = meeting_store
    audio_monitor = None

    # File-job state (Phase 1: in-memory only)
    file_jobs: dict[str, dict] = {}  # job_id -> {job, payload}
    sherpa_diarizer = Diarizer()
    pyannote_diarizer_holder = {"instance": None}

    def _diarizer_for(engine_name: str):
        if engine_name == "pyannote-community-1":
            inst = pyannote_diarizer_holder["instance"]
            if inst is None:
                from diarizer_pyannote import PyannoteDiarizer
                inst = PyannoteDiarizer()
                pyannote_diarizer_holder["instance"] = inst
            return inst
        return sherpa_diarizer

    from engine_registry import EngineRegistry
    from parakeet_transcriber import ParakeetTranscriber
    from transcriber import WhisperTranscriber as _W
    engines = EngineRegistry(
        whisper_turbo=txr,
        parakeet_factory=lambda: ParakeetTranscriber(),
        whisper_large_factory=lambda: _W(model_repo="mlx-community/whisper-large-v3"),
    )

    def _transcriber_for(engine: str):
        return engines.get(engine)

    file_runner = FileJobRunner(
        transcriber_factory=_transcriber_for,
        diarizer=sherpa_diarizer,  # default; overridden per-job below
    )

    # Detect and recover crashed lectures
    if cn_store:
        crashed = cn_store.detect_crashed_lectures(stale_minutes=5)
        for lecture in crashed:
            cn_store.mark_recovered(lecture["id"])
            audio_path = lecture.get("audio_path")
            if audio_path and os.path.exists(audio_path):
                try:
                    from lecture_recorder import LectureRecorder
                    LectureRecorder.recover_wav(audio_path)
                except Exception:
                    pass

    # Background cleanup of expired audio files
    def _cleanup_expired_audio(store, _settings):
        """Delete audio files past retention period."""
        from datetime import datetime as _dt, timezone as _tz
        retention_days = _settings.get("lecture_audio_retention_days", 30) if _settings else 30
        lectures = store.list_lectures(limit=10000)
        for lec in lectures:
            if lec["status"] == "recording":
                continue
            created = lec.get("created_at", "")
            if not created:
                continue
            try:
                created_dt = _dt.fromisoformat(created)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=_tz.utc)
                age_days = (_dt.now(_tz.utc) - created_dt).days
            except (ValueError, TypeError):
                continue
            if age_days > retention_days and lec.get("audio_path"):
                try:
                    os.remove(lec["audio_path"])
                    store.update_lecture(lec["id"], audio_path=None)
                except OSError:
                    pass

    if cn_store:
        import threading as _th
        _th.Thread(
            target=_cleanup_expired_audio,
            args=(cn_store, settings),
            daemon=True,
        ).start()

    def get_classnote_pipeline():
        return cn_pipeline if cn_pipeline and (cn_pipeline.is_active or cn_pipeline.is_paused) else None

    stop_lock = threading.Lock()
    record_timer_lock = threading.Lock()
    record_warning_timer: threading.Timer | None = None
    record_max_timer: threading.Timer | None = None
    processing_timeout_timer: threading.Timer | None = None

    # Wire recorder amplitude to state manager
    rec.on_amplitude = sm.push_amplitude

    # --- Server-push broadcast infrastructure ---
    # Sinks are (loop, enqueue_fn) pairs registered by each active WS connection.
    # Main WS connections register in _main_ws_sinks; bar WS connections in _bar_ws_sinks.
    # _broadcast_error pushes only to main sinks. _broadcast_device_event pushes to both.
    _main_ws_sinks: list = []
    _bar_ws_sinks: list = []
    _sinks_lock = threading.Lock()

    _VALID_DEVICE_EVENTS = frozenset({"device_changed", "device_lost", "device_restored"})

    def _broadcast_error(message: str):
        """Push an error message to all connected main window /ws clients."""
        msg = {"type": "error", "message": message}
        with _sinks_lock:
            sinks = list(_main_ws_sinks)
        for (loop, enqueue) in sinks:
            try:
                loop.call_soon_threadsafe(enqueue, msg)
            except Exception:
                pass

    def _broadcast_device_event(event_type: str, device_name: str | None = None):
        """Broadcast a device change event to all main and bar WS clients.

        event_type: one of "device_changed", "device_lost", "device_restored"
        device_name: friendly name of the device (None for device_lost)
        """
        if event_type not in _VALID_DEVICE_EVENTS:
            return
        msg: dict = {"type": event_type}
        if device_name is not None:
            msg["device"] = device_name
        with _sinks_lock:
            sinks = list(_main_ws_sinks) + list(_bar_ws_sinks)
        for (loop, enqueue) in sinks:
            try:
                loop.call_soon_threadsafe(enqueue, msg)
            except Exception:
                pass

    def _arm_processing_timeout():
        """Start a timer that fires ERROR if processing exceeds PROCESSING_TIMEOUT_S."""
        nonlocal processing_timeout_timer
        _cancel_processing_timeout()
        def _on_timeout():
            if sm.state == AppState.PROCESSING:
                print(f"Processing timed out after {PROCESSING_TIMEOUT_S}s")
                sm.set_state(AppState.ERROR)
                sm.push_warning("Processing timed out")
                _broadcast_error("Processing timed out")
                threading.Timer(5.0, lambda: sm.set_state(AppState.IDLE) if sm.state == AppState.ERROR else None).start()
        processing_timeout_timer = threading.Timer(PROCESSING_TIMEOUT_S, _on_timeout)
        processing_timeout_timer.daemon = True
        processing_timeout_timer.start()

    def _cancel_processing_timeout():
        nonlocal processing_timeout_timer
        if processing_timeout_timer is not None:
            processing_timeout_timer.cancel()
            processing_timeout_timer = None

    def _cancel_processing():
        """Cancel an in-flight processing operation (timeout or user-initiated)."""
        _cancel_processing_timeout()
        if sm.state == AppState.PROCESSING:
            sm.set_state(AppState.IDLE)

    def _should_auto_insert() -> bool:
        if not settings:
            return True
        return settings.auto_insert

    def _cancel_record_timers():
        nonlocal record_warning_timer, record_max_timer
        with record_timer_lock:
            if record_warning_timer is not None:
                record_warning_timer.cancel()
                record_warning_timer = None
            if record_max_timer is not None:
                record_max_timer.cancel()
                record_max_timer = None

    def _run_bar_stop():
        with stop_lock:
            _bar_stop_and_transcribe(
                rec,
                txr,
                sm,
                hist,
                app_clip,
                _should_auto_insert(),
                pipe,
                llm=llm,
                settings=settings,
                formatter=formatter,
                arm_timeout=_arm_processing_timeout,
                cancel_timeout=_cancel_processing_timeout,
            )

    def _cancel_active_recording() -> bool:
        """Discard current recording immediately (no transcription)."""
        if not rec.is_recording and sm.state != AppState.RECORDING:
            return False

        _cancel_record_timers()
        rec.on_vad_chunk = None
        if pipe is not None:
            pipe.cancel()

        with stop_lock:
            if rec.is_recording:
                rec.stop_raw()

        sm.set_state(AppState.IDLE)
        return True

    def _arm_record_timers():
        nonlocal record_warning_timer, record_max_timer
        _cancel_record_timers()

        record_warning_timer = threading.Timer(
            WARNING_SECONDS, lambda: sm.push_warning("Recording ends in 1 minute")
        )
        record_warning_timer.daemon = True
        record_warning_timer.start()

        def _on_max():
            if rec.is_recording:
                _cancel_record_timers()
                threading.Thread(target=_run_bar_stop, daemon=True).start()

        record_max_timer = threading.Timer(MAX_RECORD_SECONDS, _on_max)
        record_max_timer.daemon = True
        record_max_timer.start()

    update_mgr = updater

    @asynccontextmanager
    async def lifespan(app):
        mem_telemetry.start()
        if update_mgr:
            update_mgr.start()

        def _init_models():
            if pipe is not None:
                pipe.load_vad()
            if hasattr(txr, 'warmup'):
                txr.warmup()
            # Always preload Stage 1 formatter (punctuation/caps)
            if formatter:
                formatter.download_in_background()
            # Preload Stage 2 LLM if AI features are enabled
            if llm and settings:
                if settings.smart_cleanup or settings.context_formatting:
                    llm.download_in_background()
        threading.Thread(target=_init_models, daemon=True).start()
        try:
            yield
        finally:
            if update_mgr:
                update_mgr.stop()
            mem_telemetry.stop()

    app = FastAPI(lifespan=lifespan)

    # Store references for external access
    app.state.state_manager = sm
    app.state.history = hist
    app.state.cancel_active_recording = _cancel_active_recording
    app.state.cancel_processing = _cancel_processing
    app.state.broadcast_error = _broadcast_error
    app.state.memory_telemetry = mem_telemetry
    app.state.broadcast_device_event = _broadcast_device_event
    app.state.broadcast_error = _broadcast_error
    app.state.cancel_processing = _cancel_processing
    app.state.recorder = rec

    @app.get("/")
    async def index():
        index_path = os.path.join(STATIC_DIR, "index.html")
        with open(index_path, encoding="utf-8") as f:
            return HTMLResponse(f.read())

    @app.get("/bar")
    async def bar_page():
        bar_path = os.path.join(STATIC_DIR, "bar.html")
        with open(bar_path, encoding="utf-8") as f:
            return HTMLResponse(f.read())

    @app.get("/api/history")
    async def get_history(limit: int = 50, offset: int = 0):
        entries = hist.get_recent(limit=limit, offset=offset)
        return JSONResponse({"entries": entries, "total": hist.count()})

    @app.get("/api/history/search")
    async def search_history(q: str = "", limit: int = 50):
        entries = hist.search(q, limit=limit)
        return JSONResponse({"entries": entries})

    @app.get("/api/history/stats")
    async def get_history_stats():
        return JSONResponse(hist.get_usage_stats())

    @app.get("/api/diagnostics/memory")
    async def get_memory_diagnostics(
        top: bool = False,
        top_limit: int = 20,
        history: int = 120,
    ):
        top_limit = max(1, min(top_limit, 100))
        history = max(0, min(history, 2000))
        report = mem_telemetry.get_report(
            include_top=top,
            top_limit=top_limit,
            history=history,
            refresh=True,
        )
        return JSONResponse(report)

    @app.get("/api/browse-file")
    async def browse_file():
        main_window = getattr(app.state, "main_window", None)
        if not main_window:
            return JSONResponse({"path": None})
        try:
            import webview
            result = main_window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    "Media Files (*.wav;*.mp3;*.m4a;*.flac;*.ogg;*.webm;*.wma;*.aac;"
                    "*.mp4;*.mov;*.mkv;*.avi;*.wmv;*.flv;*.m4v;*.mpg;*.mpeg)",
                ),
            )
            path = result[0] if result else None
            return JSONResponse({"path": path})
        except Exception:
            return JSONResponse({"path": None})

    @app.get("/api/file-job/options-defaults")
    async def get_file_job_defaults():
        defaults = (settings.get("file_job_defaults", {}) if settings else {}) or {}
        merged = {**FileJobOptions().__dict__, **defaults}
        return JSONResponse(merged)

    @app.put("/api/file-job/options-defaults")
    async def put_file_job_defaults(payload: dict):
        if settings:
            settings.set("file_job_defaults", payload)
        return JSONResponse({"ok": True})

    @app.get("/api/file-job/{job_id}/payload")
    async def get_file_job_payload(job_id: str):
        entry = file_jobs.get(job_id)
        if not entry:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(entry["payload"])

    @app.post("/api/file-job/{job_id}/export")
    async def export_file_job(job_id: str, body: dict):
        entry = file_jobs.get(job_id)
        if not entry:
            return JSONResponse({"error": "not found"}, status_code=404)
        fmt = body.get("format", "txt")
        dest = body.get("dest_path")
        if not dest or fmt not in FORMATS:
            return JSONResponse({"error": "bad request"}, status_code=400)
        write_export(entry["payload"], fmt, dest)
        return JSONResponse({"path": dest})

    @app.get("/api/file-job/{job_id}/audio")
    async def get_file_job_audio(job_id: str):
        from fastapi.responses import FileResponse
        entry = file_jobs.get(job_id)
        if not entry:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(entry["payload"]["audio_path"])

    @app.post("/api/file-job/from-url")
    async def from_url(payload: dict):
        url = (payload or {}).get("url", "").strip()
        if not url:
            return JSONResponse({"error": "missing url"}, status_code=400)
        try:
            path = await asyncio.to_thread(_download_url, url)
            return JSONResponse({"path": path})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/diarizer/enhanced/status")
    async def enhanced_status():
        from diarizer_pyannote import is_pyannote_installed, CACHE_DIR
        installed = is_pyannote_installed()
        weights_present = (CACHE_DIR / "speaker-diarization-community-1").exists()
        return JSONResponse({"installed": installed, "weights_present": weights_present})

    @app.post("/api/diarizer/enhanced/install")
    async def enhanced_install():
        import sys, subprocess
        from diarizer_pyannote import CACHE_DIR, WEIGHTS_URL
        # Refuse to download from the placeholder URL.
        if "github.com/dashscribe/dashscribe" in WEIGHTS_URL:
            return JSONResponse({
                "error": "Enhanced diarization weights URL not yet configured. "
                         "DashScribe needs to host the pyannote-community-1 weights "
                         "(CC-BY-4.0 with attribution) on its own GitHub Releases first."
            }, status_code=501)
        target = str(Path("~/.dashscribe/pyannote_pkgs").expanduser())
        try:
            subprocess.run([
                sys.executable, "-m", "pip", "install", "--target", target,
                "pyannote.audio", "torch>=2.6,<3",
                "--index-url", "https://download.pytorch.org/whl/cpu",
            ], check=True, capture_output=True)
            sys.path.insert(0, target)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            import urllib.request, tarfile
            tarball = CACHE_DIR / "weights.tar.bz2"
            urllib.request.urlretrieve(WEIGHTS_URL, tarball)
            with tarfile.open(tarball, "r:bz2") as tf:
                tf.extractall(CACHE_DIR, filter="data")
            tarball.unlink(missing_ok=True)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/settings/hotkey")
    async def get_hotkey():
        if not settings:
            return JSONResponse({"key": "alt_r", "display": "Right Option"})
        return JSONResponse({
            "key": settings.hotkey_string,
            "display": settings.hotkey_display,
        })

    @app.post("/api/settings/hotkey")
    async def set_hotkey(request: Request):
        body = await request.json()
        key_str = body.get("key", "")
        if not key_str:
            return JSONResponse({"ok": False, "error": "Missing key"}, status_code=400)
        if not settings:
            return JSONResponse({"ok": False, "error": "Settings not available"}, status_code=500)
        success = settings.set_hotkey(key_str)
        if not success:
            return JSONResponse({"ok": False, "error": "Invalid key"}, status_code=400)
        return JSONResponse({
            "ok": True,
            "key": settings.hotkey_string,
            "display": settings.hotkey_display,
        })

    @app.get("/api/settings/insertion")
    async def get_insertion_settings():
        if not settings:
            return JSONResponse({
                "auto_insert": True,
                "repaste_key": "char:v",
                "repaste_display": "Cmd+Option+V",
            })
        return JSONResponse({
            "auto_insert": settings.auto_insert,
            "repaste_key": settings.repaste_key_string,
            "repaste_display": settings.repaste_display,
        })

    @app.get("/api/settings/theme")
    async def get_theme_settings():
        if not settings:
            return JSONResponse({"theme": "auto"})
        return JSONResponse({"theme": settings.theme_mode})

    @app.post("/api/settings/theme")
    async def set_theme_settings(request: Request):
        body = await request.json()
        mode = str(body.get("theme", "")).strip().lower()
        if not mode:
            return JSONResponse({"ok": False, "error": "Missing theme"}, status_code=400)
        if not settings:
            return JSONResponse({"ok": False, "error": "Settings not available"}, status_code=500)
        success = settings.set_theme_mode(mode)
        if not success:
            return JSONResponse({"ok": False, "error": "Invalid theme"}, status_code=400)
        return JSONResponse({"ok": True, "theme": settings.theme_mode})

    @app.post("/api/settings/insertion/auto-insert")
    async def set_auto_insert(request: Request):
        body = await request.json()
        enabled = bool(body.get("enabled", True))
        if not settings:
            return JSONResponse({"ok": False, "error": "Settings not available"}, status_code=500)
        settings.set_auto_insert(enabled)
        return JSONResponse({"ok": True, "auto_insert": settings.auto_insert})

    @app.post("/api/settings/insertion/repaste-key")
    async def set_repaste_key(request: Request):
        body = await request.json()
        key_str = body.get("key", "")
        if not key_str:
            return JSONResponse({"ok": False, "error": "Missing key"}, status_code=400)
        if not settings:
            return JSONResponse({"ok": False, "error": "Settings not available"}, status_code=500)
        success = settings.set_repaste_key(key_str)
        if not success:
            return JSONResponse({"ok": False, "error": "Invalid key"}, status_code=400)
        return JSONResponse({
            "ok": True,
            "repaste_key": settings.repaste_key_string,
            "repaste_display": settings.repaste_display,
        })

    @app.post("/api/settings/hotkey/capture")
    async def start_capture():
        hotkey = getattr(app.state, "hotkey", None)
        if not hotkey:
            return JSONResponse({"ok": False, "error": "Hotkey not available"}, status_code=500)
        hotkey.start_key_capture()
        return JSONResponse({"ok": True})

    @app.get("/api/settings/hotkey/capture")
    async def poll_capture():
        hotkey = getattr(app.state, "hotkey", None)
        if not hotkey:
            return JSONResponse({"captured": False})
        return JSONResponse(hotkey.poll_key_capture())

    @app.delete("/api/settings/hotkey/capture")
    async def cancel_capture():
        hotkey = getattr(app.state, "hotkey", None)
        if hotkey:
            hotkey.cancel_key_capture()
        return JSONResponse({"ok": True})

    # --- Permissions / Onboarding ---
    @app.get("/api/permissions")
    async def get_permissions():
        from permissions import check_permissions
        perms = check_permissions()

        model_info = {
            "whisper": {
                "ready": txr.is_ready,
                "status": getattr(txr, "status", "not_started"),
                "message": getattr(txr, "status_message", "Initializing..."),
                "name": "Whisper Large V3 Turbo",
                "description": "Speech recognition model (~1.5 GB, downloads on first launch).",
                "required": True,
            },
            "vad": {
                "ready": pipe.vad_available if pipe else False,
                "name": "Voice Activity Detection",
                "description": "Small model (~2 MB) for real-time speech segmentation.",
                "required": False,
            },
        }

        onboarding_complete = False
        if settings:
            onboarding_complete = settings.get("setup_complete", False)

        return JSONResponse({
            "permissions": perms,
            "models": model_info,
            "onboarding_complete": onboarding_complete,
        })

    @app.post("/api/permissions/request-microphone")
    async def request_mic_permission():
        from permissions import request_microphone_access
        request_microphone_access()
        return JSONResponse({"ok": True})

    @app.post("/api/permissions/open-settings")
    async def open_settings_pane(request: Request):
        body = await request.json()
        url = body.get("url", "")
        if not url.startswith("x-apple.systempreferences:"):
            return JSONResponse({"ok": False, "error": "Invalid URL"}, status_code=400)
        from permissions import open_system_settings
        open_system_settings(url)
        return JSONResponse({"ok": True})

    @app.post("/api/permissions/dismiss-onboarding")
    async def dismiss_onboarding():
        if settings:
            settings.set("setup_complete", True)
        return JSONResponse({"ok": True})

    # --- System info ---
    @app.get("/api/system/ram")
    async def get_system_ram():
        """Return total physical RAM in GB."""
        try:
            import os as _os
            total_bytes = _os.sysconf("SC_PAGE_SIZE") * _os.sysconf("SC_PHYS_PAGES")
            total_gb = round(total_bytes / (1024 ** 3))
        except Exception:
            total_gb = 0
        return JSONResponse({"total_gb": total_gb})

    # --- Version & Updates ---
    @app.get("/api/version")
    async def get_version():
        return JSONResponse({"version": __version__})

    @app.get("/api/update/status")
    async def get_update_status():
        if not update_mgr:
            return JSONResponse({"status": "disabled", "current_version": __version__})
        status = update_mgr.get_status()
        return JSONResponse(status)

    @app.post("/api/update/check")
    async def check_for_update():
        if not update_mgr:
            return JSONResponse({"ok": False, "error": "Updater not available"}, status_code=500)
        update_mgr.check_now()
        return JSONResponse({"ok": True})

    @app.post("/api/update/download")
    async def download_update():
        if not update_mgr:
            return JSONResponse({"ok": False, "error": "Updater not available"}, status_code=500)
        update_mgr.download_update()
        return JSONResponse({"ok": True})

    @app.post("/api/update/cancel")
    async def cancel_update_download():
        if not update_mgr:
            return JSONResponse({"ok": False, "error": "Updater not available"}, status_code=500)
        update_mgr.cancel_download()
        return JSONResponse({"ok": True})

    @app.post("/api/update/install")
    async def install_update():
        if not update_mgr:
            return JSONResponse({"ok": False, "error": "Updater not available"}, status_code=500)
        update_mgr.install_update()
        return JSONResponse({"ok": True})

    @app.post("/api/update/skip")
    async def skip_update(request: Request):
        body = await request.json()
        version = body.get("version", "")
        if not version:
            return JSONResponse({"ok": False, "error": "Missing version"}, status_code=400)
        if not update_mgr:
            return JSONResponse({"ok": False, "error": "Updater not available"}, status_code=500)
        update_mgr.skip_version(version)
        return JSONResponse({"ok": True})

    @app.get("/api/update/settings")
    async def get_update_settings():
        return JSONResponse({
            "auto_check": settings.get("update_auto_check", True) if settings else True,
            "include_prerelease": settings.get("update_include_prerelease", False) if settings else False,
        })

    @app.post("/api/update/settings")
    async def set_update_settings(request: Request):
        body = await request.json()
        if not settings:
            return JSONResponse({"ok": False, "error": "Settings not available"}, status_code=500)
        if "auto_check" in body:
            settings.set("update_auto_check", bool(body["auto_check"]))
        if "include_prerelease" in body:
            settings.set("update_include_prerelease", bool(body["include_prerelease"]))
        return JSONResponse({"ok": True})

    # --- LLM model management ---

    @app.get("/api/llm/status")
    async def llm_status():
        """Check if the LLM model is cached, loaded, or downloading."""
        if not llm:
            return JSONResponse({"available": False, "cached": False, "loaded": False, "status": "unavailable"})
        progress = llm.get_download_progress() if llm.download_status == "downloading" else llm.download_progress
        return JSONResponse({
            "available": True,
            "cached": llm.is_cached(),
            "loaded": llm.is_loaded,
            "status": llm.download_status,
            "message": llm.download_message,
            "progress": round(progress, 3),
        })

    @app.get("/api/formatter/status")
    async def formatter_status():
        """Check if the Stage 1 punctuation formatter is loaded."""
        if not formatter:
            return JSONResponse({"available": False, "loaded": False, "status": "unavailable"})
        progress = formatter.get_download_progress() if formatter.download_status == "downloading" else formatter.download_progress
        return JSONResponse({
            "available": True,
            "cached": formatter.is_cached(),
            "loaded": formatter.is_loaded,
            "status": formatter.download_status,
            "message": formatter.download_message,
            "progress": round(progress, 3),
        })

    @app.post("/api/llm/download")
    async def llm_download():
        """Start downloading the LLM model in the background."""
        if not llm:
            return JSONResponse({"ok": False, "error": "LLM not available"}, status_code=503)
        llm.download_in_background()
        return JSONResponse({"ok": True})

    # --- Smart features settings endpoints ---

    @app.get("/api/settings/smart-cleanup")
    async def get_smart_cleanup():
        return {"enabled": settings.smart_cleanup if settings else False}

    @app.post("/api/settings/smart-cleanup")
    async def set_smart_cleanup(request: Request):
        data = await request.json()
        if settings:
            settings.smart_cleanup = data.get("enabled", False)
        return {"ok": True}

    @app.get("/api/settings/context-formatting")
    async def get_context_formatting():
        return {"enabled": settings.context_formatting if settings else False}

    @app.post("/api/settings/context-formatting")
    async def set_context_formatting(request: Request):
        data = await request.json()
        if settings:
            settings.context_formatting = data.get("enabled", False)
        return {"ok": True}

    @app.get("/api/settings/snippets")
    async def get_snippets():
        return {"snippets": settings.snippets if settings else []}

    @app.post("/api/settings/snippets")
    async def set_snippets_endpoint(request: Request):
        data = await request.json()
        if settings:
            settings.set_snippets(data.get("snippets", []))
        return {"ok": True}

    @app.get("/api/settings/dictionary")
    async def get_dictionary():
        prompt = settings.dictionary_prompt if settings else None
        terms = prompt.split(", ") if prompt else []
        return {"terms": terms}

    @app.post("/api/settings/dictionary")
    async def set_dictionary(request: Request):
        data = await request.json()
        if settings:
            settings.set_dictionary(data.get("terms", []))
        return {"ok": True}

    # --- Settings Export/Import ---

    @app.get("/api/settings/export")
    async def export_settings():
        """Export all portable settings as a JSON file."""
        from datetime import datetime
        export_data = {
            "version": __version__,
            "exported_at": datetime.now().isoformat(),
            "profile": {
                "display_name": settings.get("display_name", "") if settings else "",
            },
            "settings": {},
            "snippets": settings.snippets if settings else [],
            "dictionary": [],
        }
        if settings:
            # Portable settings only (exclude device-specific hotkey, auto_insert)
            for key in ("theme_mode", "smart_cleanup", "context_formatting", "app_styles"):
                val = settings.get(key)
                if val is not None:
                    export_data["settings"][key] = val
            # Dictionary
            prompt = settings.dictionary_prompt
            if prompt:
                export_data["dictionary"] = [t.strip() for t in prompt.split(",") if t.strip()]
        return JSONResponse(export_data)

    @app.post("/api/settings/import")
    async def import_settings(request: Request):
        """Import settings from a previously exported JSON file."""
        data = await request.json()
        if not settings:
            return JSONResponse({"ok": False, "error": "Settings not available"}, status_code=500)
        if not isinstance(data, dict) or "version" not in data:
            return JSONResponse({"ok": False, "error": "Invalid export file"}, status_code=400)
        # Profile
        profile = data.get("profile", {})
        if profile.get("display_name"):
            settings.set("display_name", profile["display_name"])
        # Portable settings
        imported_settings = data.get("settings", {})
        for key in ("theme_mode", "smart_cleanup", "context_formatting", "app_styles"):
            if key in imported_settings:
                settings.set(key, imported_settings[key])
        # Snippets
        snippets = data.get("snippets")
        if isinstance(snippets, list):
            settings.set_snippets(snippets)
        # Dictionary
        dictionary = data.get("dictionary")
        if isinstance(dictionary, list):
            settings.set_dictionary(dictionary)
        return JSONResponse({"ok": True})

    @app.post("/api/settings/reset")
    async def reset_settings():
        """Reset all settings to defaults."""
        if not settings:
            return JSONResponse({"ok": False, "error": "Settings not available"}, status_code=500)
        for key in ("theme_mode", "smart_cleanup", "context_formatting", "app_styles", "display_name"):
            settings.set(key, None)
        settings.set_snippets([])
        settings.set_dictionary([])
        return JSONResponse({"ok": True})

    @app.get("/api/profile")
    async def get_profile():
        """Get local user profile."""
        return JSONResponse({
            "display_name": settings.get("display_name", "") if settings else "",
        })

    @app.put("/api/profile")
    async def update_profile(request: Request):
        """Update local user profile."""
        body = await request.json()
        name = body.get("display_name", "").strip()[:100]
        if settings:
            settings.set("display_name", name)
        return JSONResponse({"ok": True, "display_name": name})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()

        # Queue for server-push messages (e.g. device events) from background threads
        loop = asyncio.get_event_loop()
        _main_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        def _main_enqueue(msg: dict):
            try:
                _main_queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass

        # Register as a main WS sink for server-push messages (errors + device events)
        _main_sink = (loop, _main_enqueue)
        with _sinks_lock:
            _main_ws_sinks.append(_main_sink)

        try:
            async def _push_server_messages():
                """Drain the server-push queue and forward to client."""
                while True:
                    msg = await _main_queue.get()
                    await ws.send_json(msg)

            async def _handle_commands():
                while True:
                    data = await ws.receive_json()
                    action = data.get("action")

                    if action == "start":
                        if rec.is_recording or sm.state == AppState.RECORDING:
                            await ws.send_json({"type": "status", "status": "recording"})
                            continue
                        try:
                            rec.start()
                        except Exception as e:
                            sm.set_state(AppState.ERROR)
                            await ws.send_json({
                                "type": "error",
                                "message": f"Failed to start recording: {e}",
                            })
                            threading.Timer(5.0, lambda: sm.set_state(AppState.IDLE) if sm.state == AppState.ERROR else None).start()
                            continue
                        _arm_record_timers()
                        sm.set_state(AppState.RECORDING)
                        if pipe is not None and pipe.vad_available:
                            sys_chunks = rec.get_sys_audio_chunks()
                            started = pipe.start(sys_audio_chunks=sys_chunks)
                            rec.on_vad_chunk = pipe.feed if started else None
                        await ws.send_json({"type": "status", "status": "recording"})

                    elif action == "stop":
                        try:
                            _cancel_record_timers()
                            sm.set_state(AppState.PROCESSING)
                            _arm_processing_timeout()
                            await ws.send_json({"type": "status", "status": "transcribing"})
                            def _ws_stop_locked():
                                with stop_lock:
                                    return _ws_stop_and_transcribe(rec, txr, pipe, llm=llm, settings=settings, formatter=formatter)
                            try:
                                text, elapsed, audio_duration, raw_text, stage1_text = await asyncio.wait_for(
                                    asyncio.to_thread(_ws_stop_locked), timeout=PROCESSING_TIMEOUT_S
                                )
                            except asyncio.TimeoutError:
                                # Timeout already handled by _arm_processing_timeout → ERROR state
                                _cancel_processing_timeout()
                                if sm.state != AppState.ERROR:
                                    sm.set_state(AppState.ERROR)
                                    threading.Timer(5.0, lambda: sm.set_state(AppState.IDLE) if sm.state == AppState.ERROR else None).start()
                                await ws.send_json({
                                    "type": "error",
                                    "message": "Processing timed out",
                                })
                                continue
                            _cancel_processing_timeout()
                            # Check if cancelled/timed out while we were waiting
                            if sm.state != AppState.PROCESSING:
                                continue
                            if text is None:
                                sm.set_state(AppState.IDLE)
                                await ws.send_json({
                                    "type": "error",
                                    "message": "Recording too short. Hold the button longer.",
                                })
                                continue
                            app_clip.set_text(text)
                            inserted = False
                            if _should_auto_insert():
                                try:
                                    paste_text(text)
                                    inserted = True
                                except Exception as e:
                                    print(f"Paste operation failed: {e}")
                            hist.add(
                                text,
                                duration=audio_duration,
                                latency=elapsed,
                                source="dictation",
                                raw_text=raw_text,
                                stage1_text=stage1_text,
                                transcriber_model=txr.model_repo,
                                formatter_model=llm.model_repo if (llm and raw_text and stage1_text != raw_text) else None,
                                punct_model=formatter.model_repo if (formatter and stage1_text) else None,
                            )
                            gc.collect()
                            sm.set_state(AppState.IDLE)
                            await ws.send_json({
                                "type": "result",
                                "text": text,
                                "latency": elapsed,
                                "inserted": inserted,
                            })
                        except Exception as e:
                            _cancel_record_timers()
                            _cancel_processing_timeout()
                            sm.set_state(AppState.ERROR)
                            await ws.send_json({
                                "type": "error",
                                "message": str(e),
                            })
                            threading.Timer(5.0, lambda: sm.set_state(AppState.IDLE) if sm.state == AppState.ERROR else None).start()

                    elif action == "cancel":
                        if sm.state == AppState.PROCESSING:
                            _cancel_processing()
                        else:
                            _cancel_active_recording()
                        await ws.send_json({"type": "status", "status": "idle"})

                    elif action == "start_file_job":
                        path = data.get("path", "")
                        if path == "__sample__":
                            from pathlib import Path as _P
                            path = str(_P(STATIC_DIR) / "samples" / "sample-en.m4a")
                        opts = FileJobOptions(**(data.get("options") or {}))
                        job = FileJob.new(source_path=path, options=opts)
                        file_jobs[job.job_id] = {"job": job, "payload": None}
                        await ws.send_json({"type": "file_job_started", "job_id": job.job_id})

                        async def _emit(job_id, **kw):
                            try:
                                await ws.send_json({"type": "file_progress", "job_id": job_id, **kw})
                            except Exception:
                                pass
                        loop = asyncio.get_event_loop()
                        file_runner._on_progress = lambda jid, **kw: asyncio.run_coroutine_threadsafe(
                            _emit(jid, **kw), loop)
                        # Swap diarizer based on per-job preference (sherpa-onnx default vs pyannote)
                        file_runner._diarizer = _diarizer_for(opts.diarization_engine)
                        try:
                            payload = await asyncio.to_thread(file_runner.run, job)
                            file_jobs[job.job_id]["payload"] = payload
                            await ws.send_json({"type": "file_job_done", "job_id": job.job_id, "payload": payload})
                        except Exception as e:
                            await ws.send_json({"type": "file_job_error", "job_id": job.job_id, "message": str(e)})

                    elif action == "cancel_file_job":
                        jid = data.get("job_id", "")
                        file_runner.cancel(jid)
                        await ws.send_json({"type": "file_job_cancelled", "job_id": jid})

                    elif action == "update_speaker_label":
                        jid = data.get("job_id", "")
                        sid = data.get("speaker_id", "")
                        label = data.get("label", "")
                        entry = file_jobs.get(jid)
                        if entry and entry["payload"]:
                            for sp in entry["payload"]["speakers"]:
                                if sp["id"] == sid:
                                    sp["label"] = label
                            await ws.send_json({"type": "speaker_label_updated", "job_id": jid,
                                                "speaker_id": sid, "label": label})

                    elif action == "save_transcript_edits":
                        jid = data.get("job_id", "")
                        segments = data.get("segments", [])
                        entry = file_jobs.get(jid)
                        if entry and entry["payload"]:
                            entry["payload"]["segments"] = segments
                            from pathlib import Path as _P
                            import json as _json
                            sidecar = _P(entry["payload"]["audio_path"]).with_suffix(".json")
                            sidecar.write_text(_json.dumps(entry["payload"], indent=2, ensure_ascii=False),
                                               encoding="utf-8")
                            await ws.send_json({"type": "transcript_saved", "job_id": jid})

                    elif action == "status":
                        status_data = {
                            "type": "model_status",
                            "ready": txr.is_ready,
                        }
                        if hasattr(txr, 'status'):
                            status_data["status"] = txr.status
                            status_data["message"] = txr.status_message
                        await ws.send_json(status_data)

            await asyncio.gather(_push_server_messages(), _handle_commands())
        except WebSocketDisconnect:
            gc.collect()
        finally:
            # Deregister main WS sink
            with _sinks_lock:
                try:
                    _main_ws_sinks.remove(_main_sink)
                except ValueError:
                    pass

    @app.websocket("/ws/bar")
    async def bar_websocket(ws: WebSocket):
        await ws.accept()
        # Send initial state
        await ws.send_json({"type": "state", "state": sm.state.value})

        # Queue for state changes and amplitude data pushed from background threads
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        def _enqueue(msg: dict):
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                # Drop high-frequency amplitude updates first under backpressure.
                if msg.get("type") == "amplitude":
                    return
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(msg)
                except asyncio.QueueFull:
                    pass

        def on_state_change(old, new):
            loop.call_soon_threadsafe(_enqueue, {"type": "state", "state": new.value})

        def on_amplitude(val):
            loop.call_soon_threadsafe(_enqueue, {"type": "amplitude", "value": round(val, 4)})

        def on_warning(msg):
            loop.call_soon_threadsafe(_enqueue, {"type": "warning", "message": msg})

        def on_hotkey_change(serialized):
            from config import shortcut_display
            loop.call_soon_threadsafe(
                _enqueue,
                {"type": "hotkey", "display": shortcut_display(serialized)}
            )

        sm.on_state_change(on_state_change)
        sm.on_amplitude(on_amplitude)
        sm.on_warning(on_warning)
        if settings:
            settings.on_hotkey_change(on_hotkey_change)

        # Register this connection as a bar WS sink (device events only)
        _bar_sink = (loop, _enqueue)
        with _sinks_lock:
            _bar_ws_sinks.append(_bar_sink)

        # Send initial hotkey display name
        if settings:
            await ws.send_json({"type": "hotkey", "display": settings.hotkey_display})

        try:
            # Run two tasks: listen for incoming messages and push outgoing updates
            async def push_updates():
                while True:
                    msg = await queue.get()
                    await ws.send_json(msg)

            async def receive_commands():
                while True:
                    data = await ws.receive_json()
                    action = data.get("action")
                    if action == "start":
                        if rec.is_recording or sm.state == AppState.RECORDING:
                            continue
                        try:
                            rec.start()
                        except Exception as e:
                            sm.set_state(AppState.ERROR)
                            loop.call_soon_threadsafe(_enqueue, {
                                "type": "warning",
                                "message": f"Failed to start recording: {e}",
                            })
                            threading.Timer(5.0, lambda: sm.set_state(AppState.IDLE) if sm.state == AppState.ERROR else None).start()
                            continue
                        _arm_record_timers()
                        sm.set_state(AppState.RECORDING)
                        if pipe is not None and pipe.vad_available:
                            sys_chunks = rec.get_sys_audio_chunks()
                            started = pipe.start(sys_audio_chunks=sys_chunks)
                            rec.on_vad_chunk = pipe.feed if started else None
                    elif action == "stop":
                        _cancel_record_timers()
                        threading.Thread(
                            target=_run_bar_stop,
                            daemon=True,
                        ).start()
                    elif action == "cancel":
                        if sm.state == AppState.PROCESSING:
                            _cancel_processing()
                        else:
                            _cancel_active_recording()
                    elif action == "retry":
                        if sm.state != AppState.ERROR:
                            continue
                        def _run_retry():
                            with stop_lock:
                                sm.set_state(AppState.PROCESSING)
                                try:
                                    result = _retry_transcribe(txr, settings=settings, llm=llm, formatter=formatter)
                                    if result is None:
                                        sm.set_state(AppState.ERROR)
                                        return
                                    text, elapsed, audio_duration, raw_text, stage1_text = result
                                    if not text:
                                        sm.set_state(AppState.ERROR)
                                        return
                                    app_clip.set_text(text)
                                    if _should_auto_insert():
                                        try:
                                            paste_text(text)
                                        except Exception:
                                            pass
                                    hist.add(
                                        text,
                                        duration=audio_duration,
                                        latency=elapsed,
                                        source="dictation",
                                        raw_text=raw_text,
                                        stage1_text=stage1_text,
                                        transcriber_model=txr.model_repo,
                                        formatter_model=llm.model_repo if (llm and raw_text and stage1_text != raw_text) else None,
                                        punct_model=formatter.model_repo if (formatter and stage1_text) else None,
                                    )
                                    gc.collect()
                                    sm.set_state(AppState.IDLE)
                                except Exception:
                                    sm.set_state(AppState.ERROR)
                        threading.Thread(target=_run_retry, daemon=True).start()

            await asyncio.gather(push_updates(), receive_commands())
        except WebSocketDisconnect:
            pass
        finally:
            _cancel_record_timers()
            # Remove callbacks
            if hasattr(sm, "off_state_change"):
                sm.off_state_change(on_state_change)
            elif on_state_change in sm._state_callbacks:
                sm._state_callbacks.remove(on_state_change)

            if hasattr(sm, "off_amplitude"):
                sm.off_amplitude(on_amplitude)
            elif on_amplitude in sm._amplitude_callbacks:
                sm._amplitude_callbacks.remove(on_amplitude)

            if hasattr(sm, "off_warning"):
                sm.off_warning(on_warning)
            elif on_warning in sm._warning_callbacks:
                sm._warning_callbacks.remove(on_warning)

            if settings:
                if hasattr(settings, "off_hotkey_change"):
                    settings.off_hotkey_change(on_hotkey_change)
                elif on_hotkey_change in settings._hotkey_callbacks:
                    settings._hotkey_callbacks.remove(on_hotkey_change)

            # Deregister bar WS sink
            with _sinks_lock:
                try:
                    _bar_ws_sinks.remove(_bar_sink)
                except ValueError:
                    pass

    # --- ClassNote WebSocket ---

    @app.websocket("/ws/classnote")
    async def classnote_websocket(ws: WebSocket):
        await ws.accept()

        if not cn_pipeline:
            await ws.send_json({"type": "error", "message": "ClassNote not available", "recoverable": False})
            await ws.close()
            return

        loop = asyncio.get_event_loop()
        msg_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        def _enqueue(msg: dict):
            try:
                loop.call_soon_threadsafe(msg_queue.put_nowait, msg)
            except Exception:
                pass

        # Wire callbacks
        def on_segment(seg):
            _enqueue({"type": "segment", **seg})

        def on_correction(corr):
            _enqueue({"type": "correction", **corr})

        def on_status(state):
            _enqueue({"type": "status", "state": state})

        def on_error(message, recoverable):
            _enqueue({"type": "error", "message": message, "recoverable": recoverable})

        cn_pipeline.on_segment = on_segment
        cn_pipeline.on_correction = on_correction
        cn_pipeline.on_status = on_status
        cn_pipeline.on_error = on_error

        VALID_ACTIONS = {"start", "pause", "resume", "stop", "discard"}

        try:
            async def push_updates():
                while True:
                    msg = await msg_queue.get()
                    await ws.send_json(msg)

            async def receive_commands():
                while True:
                    data = await ws.receive_json()
                    action = data.get("action", "")

                    if action not in VALID_ACTIONS:
                        await ws.send_json({"type": "error", "message": f"Unknown action: {action}", "recoverable": True})
                        continue

                    if action == "start":
                        title = data.get("title", "")
                        # Sanitize title
                        title = re.sub(r'<[^>]+>', '', title)  # strip HTML
                        title = title[:255]
                        if not title:
                            from datetime import datetime
                            title = f"Lecture — {datetime.now().strftime('%b %d, %I:%M %p')}"
                        try:
                            result = cn_pipeline.start(title)
                            _enqueue({"type": "status", "state": "recording", "lecture_id": result["lecture_id"]})
                        except Exception as e:
                            await ws.send_json({"type": "error", "message": str(e), "recoverable": True})

                    elif action == "pause":
                        cn_pipeline.pause()

                    elif action == "resume":
                        cn_pipeline.resume()

                    elif action == "stop":
                        result = cn_pipeline.stop()
                        _enqueue({"type": "status", "state": "stopped", **result})

                    elif action == "discard":
                        cn_pipeline.discard()
                        _enqueue({"type": "status", "state": "discarded"})

            await asyncio.gather(push_updates(), receive_commands())

        except WebSocketDisconnect:
            pass
        finally:
            cn_pipeline.on_segment = None
            cn_pipeline.on_correction = None
            cn_pipeline.on_status = None
            cn_pipeline.on_error = None

    # --- ClassNote REST API ---

    @app.get("/api/classnote/lectures")
    async def list_lectures(limit: int = 100, offset: int = 0, q: str = ""):
        if not cn_store:
            return JSONResponse({"lectures": []})
        if q:
            lectures = cn_store.search_lectures(q, limit=limit)
        else:
            lectures = cn_store.list_lectures(limit=limit, offset=offset)
        # Attach labels to each lecture for client-side filtering
        for lec in lectures:
            lec["labels"] = cn_store.get_lecture_labels(lec["id"])
        return JSONResponse({"lectures": lectures})

    @app.get("/api/classnote/lectures/{lecture_id}")
    async def get_lecture(lecture_id: int):
        if not cn_store:
            return JSONResponse({"error": "not found"}, status_code=404)
        lecture = cn_store.get_lecture(lecture_id)
        if not lecture:
            return JSONResponse({"error": "not found"}, status_code=404)
        segments = cn_store.get_segments(lecture_id)
        labels = cn_store.get_lecture_labels(lecture_id)
        return JSONResponse({"lecture": lecture, "segments": segments, "labels": labels})

    @app.get("/api/classnote/lectures/{lecture_id}/segments")
    async def get_segments(lecture_id: int):
        if not cn_store:
            return JSONResponse({"segments": []})
        segments = cn_store.get_segments(lecture_id)
        return JSONResponse({"segments": segments})

    @app.delete("/api/classnote/lectures/{lecture_id}")
    async def delete_lecture(lecture_id: int):
        if not cn_store:
            return JSONResponse({"error": "not found"}, status_code=404)
        lecture = cn_store.get_lecture(lecture_id)
        if not lecture:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Delete audio file if it exists
        if lecture.get("audio_path") and os.path.exists(lecture["audio_path"]):
            try:
                os.remove(lecture["audio_path"])
            except OSError:
                pass
        cn_store.delete_lecture(lecture_id)
        return JSONResponse({"ok": True})

    @app.get("/api/classnote/lectures/{lecture_id}/audio")
    async def get_lecture_audio(lecture_id: int):
        if not cn_store:
            return JSONResponse({"error": "not found"}, status_code=404)
        lecture = cn_store.get_lecture(lecture_id)
        if not lecture:
            return JSONResponse({"error": "not found"}, status_code=404)
        audio_path = lecture.get("audio_path")
        if not audio_path or not os.path.exists(audio_path):
            return JSONResponse({"error": "audio not found"}, status_code=404)
        from starlette.responses import FileResponse
        return FileResponse(audio_path, media_type="audio/wav")

    @app.get("/api/classnote/labels")
    async def list_labels():
        if not cn_store:
            return JSONResponse({"labels": []})
        return JSONResponse({"labels": cn_store.list_labels()})

    @app.post("/api/classnote/labels")
    async def create_label(request: Request):
        if not cn_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        data = await request.json()
        name = data.get("name", "").strip()[:50]
        color = data.get("color", "#8b5cf6")
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        label_id = cn_store.create_label(name, color)
        return JSONResponse({"id": label_id, "name": name, "color": color})

    @app.post("/api/classnote/lectures/{lecture_id}/labels/{label_id}")
    async def assign_label(lecture_id: int, label_id: int):
        if not cn_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        cn_store.assign_label(lecture_id, label_id)
        return JSONResponse({"ok": True})

    @app.delete("/api/classnote/lectures/{lecture_id}/labels/{label_id}")
    async def remove_label(lecture_id: int, label_id: int):
        if not cn_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        cn_store.remove_label(lecture_id, label_id)
        return JSONResponse({"ok": True})

    @app.delete("/api/classnote/labels/{label_id}")
    async def delete_label(label_id: int):
        """Permanently delete a label and all its assignments."""
        if not cn_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        cn_store.delete_label(label_id)
        return JSONResponse({"ok": True})

    @app.post("/api/classnote/lectures/{lecture_id}/retranscribe")
    async def retranscribe_lecture(lecture_id: int):
        """Re-transcribe a lecture from its saved audio (runs in background thread)."""
        if not cn_pipeline or not cn_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        if cn_pipeline.is_active:
            return JSONResponse({"error": "Cannot re-transcribe while recording"}, status_code=409)
        lecture = cn_store.get_lecture(lecture_id)
        if not lecture:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not lecture.get("audio_path") or not os.path.exists(lecture["audio_path"]):
            return JSONResponse({"error": "Audio file not found"}, status_code=404)

        import asyncio
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, cn_pipeline.retranscribe, lecture_id
            )
            return JSONResponse({"ok": True, **result})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/classnote/lectures/{lecture_id}/export")
    async def export_lecture_transcript(lecture_id: int):
        if not cn_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        lecture = cn_store.get_lecture(lecture_id)
        if not lecture:
            return JSONResponse({"error": "not found"}, status_code=404)
        segments = cn_store.get_segments(lecture_id)
        title = lecture.get("title", "Untitled")
        text = title + "\n\n"
        for seg in segments:
            text += seg["text"] + "\n\n"
        text = text.strip()
        # Build clean filename: replace special chars, collapse spaces, trim
        clean = title.replace("\u2014", "-").replace("\u2013", "-")  # em/en dash
        clean = re.sub(r'[/:*?"<>|\\]', '', clean)  # macOS/Windows illegal chars
        clean = re.sub(r'\s+', ' ', clean).strip()
        clean = clean or "Transcript"
        filename = clean + ".txt"

        # Use pywebview native save dialog
        main_window = getattr(app.state, "main_window", None)
        if not main_window:
            return JSONResponse({"error": "No window available"}, status_code=500)
        try:
            import webview
            result = main_window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=filename,
            )
            if not result:
                return JSONResponse({"ok": False, "cancelled": True})
            save_path = result if isinstance(result, str) else result[0]
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            return JSONResponse({"ok": True, "path": save_path})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.patch("/api/classnote/lectures/{lecture_id}/segments/{segment_index}")
    async def update_segment_text(lecture_id: int, segment_index: int, request: Request):
        if not cn_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse({"error": "Text cannot be empty"}, status_code=400)
        cn_store.update_segment_text(lecture_id, segment_index, text)
        return JSONResponse({"ok": True})

    @app.get("/api/classnote/status")
    async def classnote_status():
        if not cn_pipeline:
            return JSONResponse({"active": False})
        return JSONResponse({
            "active": cn_pipeline.is_active,
            "paused": cn_pipeline.is_paused,
            "lecture_id": cn_pipeline.lecture_id,
        })

    app.state.get_classnote_pipeline = get_classnote_pipeline

    # --- Meeting WebSocket ---

    @app.websocket("/ws/meeting")
    async def meeting_websocket(ws: WebSocket):
        await ws.accept()

        if not mt_pipeline:
            await ws.send_json({"type": "error", "message": "Meeting not available", "recoverable": False})
            await ws.close()
            return

        loop = asyncio.get_event_loop()
        msg_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        def _enqueue(msg: dict):
            try:
                loop.call_soon_threadsafe(msg_queue.put_nowait, msg)
            except Exception:
                pass

        def on_segment(seg):
            _enqueue({"type": "segment", **seg})

        def on_status(state):
            _enqueue({"type": "status", "state": state})

        def on_error(message, recoverable):
            _enqueue({"type": "error", "message": message, "recoverable": recoverable})

        mt_pipeline.on_segment = on_segment
        mt_pipeline.on_status = on_status
        mt_pipeline.on_error = on_error

        VALID_ACTIONS = {"start", "pause", "resume", "stop", "discard", "status"}

        try:
            async def push_updates():
                while True:
                    msg = await msg_queue.get()
                    await ws.send_json(msg)

            async def receive_commands():
                while True:
                    data = await ws.receive_json()
                    action = data.get("action", "")

                    if action not in VALID_ACTIONS:
                        await ws.send_json({"type": "error", "message": f"Unknown action: {action}", "recoverable": True})
                        continue

                    if action == "start":
                        title = data.get("title", "")
                        title = re.sub(r'<[^>]+>', '', title)[:255]
                        if not title:
                            from datetime import datetime
                            title = f"Meeting \u2014 {datetime.now().strftime('%b %d, %I:%M %p')}"
                        app_bundle_id = data.get("app_bundle_id", "")
                        mode = data.get("mode", "listen")
                        if mode not in ("listen", "full"):
                            mode = "listen"
                        try:
                            result = mt_pipeline.start(title=title, app_bundle_id=app_bundle_id, mode=mode)
                            _enqueue({"type": "status", "state": "recording", "meeting_id": result["meeting_id"]})
                        except Exception as e:
                            await ws.send_json({"type": "error", "message": str(e), "recoverable": True})

                    elif action == "pause":
                        mt_pipeline.pause()

                    elif action == "resume":
                        mt_pipeline.resume()

                    elif action == "stop":
                        result = mt_pipeline.stop()
                        _enqueue({"type": "status", "state": "stopped", **result})

                    elif action == "discard":
                        mt_pipeline.discard()
                        _enqueue({"type": "status", "state": "discarded"})

                    elif action == "status":
                        _enqueue({
                            "type": "status",
                            "state": "recording" if mt_pipeline.is_active else ("paused" if mt_pipeline.is_paused else "idle"),
                            "meeting_id": mt_pipeline.meeting_id,
                        })

            await asyncio.gather(push_updates(), receive_commands())

        except WebSocketDisconnect:
            pass
        finally:
            mt_pipeline.on_segment = None
            mt_pipeline.on_status = None
            mt_pipeline.on_error = None

    # --- Meeting REST API ---

    @app.get("/api/meeting/apps")
    async def meeting_apps():
        try:
            from system_audio import SystemAudioCapture
            from meeting import KNOWN_MEETING_APPS
            running = SystemAudioCapture.get_running_apps()
            # Sort: known meeting apps first
            known = [a for a in running if a["bundle_id"] in KNOWN_MEETING_APPS]
            other = [a for a in running if a["bundle_id"] not in KNOWN_MEETING_APPS]
            # Add friendly names for known apps
            for a in known:
                a["display_name"] = KNOWN_MEETING_APPS.get(a["bundle_id"], a["name"])
            return JSONResponse({"apps": known + other})
        except Exception as e:
            return JSONResponse({"apps": [], "error": str(e)})

    @app.post("/api/meeting/audio-monitor/start")
    async def meeting_audio_monitor_start(request: Request):
        """Start monitoring audio levels for listed apps."""
        nonlocal audio_monitor
        try:
            data = await request.json()
            bundle_ids = data.get("bundle_ids", [])
            if not bundle_ids:
                return JSONResponse({"ok": False, "error": "No apps specified"})
            from audio_probe import AudioLevelMonitor
            if audio_monitor is None:
                audio_monitor = AudioLevelMonitor()
            ok = audio_monitor.start(bundle_ids)
            return JSONResponse({"ok": ok})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/meeting/audio-monitor/stop")
    async def meeting_audio_monitor_stop():
        """Stop monitoring audio levels."""
        nonlocal audio_monitor
        if audio_monitor:
            audio_monitor.stop()
        return JSONResponse({"ok": True})

    @app.get("/api/meeting/audio-levels")
    async def meeting_audio_levels():
        """Get current audio levels for monitored apps."""
        if not audio_monitor or not audio_monitor.is_active:
            return JSONResponse({"levels": {}})
        return JSONResponse({"levels": audio_monitor.get_levels()})

    @app.get("/api/meeting/status")
    async def meeting_status():
        if not mt_pipeline:
            return JSONResponse({"active": False})
        return JSONResponse({
            "active": mt_pipeline.is_active,
            "paused": mt_pipeline.is_paused,
            "meeting_id": mt_pipeline.meeting_id,
        })

    @app.get("/api/meeting/meetings")
    async def list_meetings(limit: int = 100, offset: int = 0, q: str = ""):
        if not mt_store:
            return JSONResponse({"meetings": []})
        if q:
            meetings = mt_store.search_meetings(q, limit=limit)
        else:
            meetings = mt_store.list_meetings(limit=limit, offset=offset)
        for m in meetings:
            m["labels"] = mt_store.get_meeting_labels(m["id"])
        return JSONResponse({"meetings": meetings})

    @app.get("/api/meeting/meetings/{meeting_id}")
    async def get_meeting(meeting_id: int):
        if not mt_store:
            return JSONResponse({"error": "not found"}, status_code=404)
        meeting = mt_store.get_meeting(meeting_id)
        if not meeting:
            return JSONResponse({"error": "not found"}, status_code=404)
        segments = mt_store.get_segments(meeting_id)
        labels = mt_store.get_meeting_labels(meeting_id)
        return JSONResponse({"meeting": meeting, "segments": segments, "labels": labels})

    @app.delete("/api/meeting/meetings/{meeting_id}")
    async def delete_meeting(meeting_id: int):
        if not mt_store:
            return JSONResponse({"error": "not found"}, status_code=404)
        meeting = mt_store.get_meeting(meeting_id)
        if not meeting:
            return JSONResponse({"error": "not found"}, status_code=404)
        for key in ("system_audio_path", "mic_audio_path"):
            path = meeting.get(key)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        mt_store.delete_meeting(meeting_id)
        return JSONResponse({"ok": True})

    @app.get("/api/meeting/meetings/{meeting_id}/audio")
    async def get_meeting_audio(meeting_id: int):
        if not mt_store:
            return JSONResponse({"error": "not found"}, status_code=404)
        meeting = mt_store.get_meeting(meeting_id)
        if not meeting:
            return JSONResponse({"error": "not found"}, status_code=404)
        audio_path = meeting.get("system_audio_path")
        if not audio_path or not os.path.exists(audio_path):
            return JSONResponse({"error": "audio not found"}, status_code=404)
        from starlette.responses import FileResponse
        return FileResponse(audio_path, media_type="audio/wav")

    @app.patch("/api/meeting/meetings/{meeting_id}/segments/{segment_index}")
    async def update_meeting_segment(meeting_id: int, segment_index: int, request: Request):
        if not mt_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse({"error": "Text cannot be empty"}, status_code=400)
        mt_store.update_segment_text(meeting_id, segment_index, text)
        return JSONResponse({"ok": True})

    @app.post("/api/meeting/meetings/{meeting_id}/export")
    async def export_meeting_transcript(meeting_id: int):
        if not mt_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        meeting = mt_store.get_meeting(meeting_id)
        if not meeting:
            return JSONResponse({"error": "not found"}, status_code=404)
        segments = mt_store.get_segments(meeting_id)
        title = meeting.get("title", "Untitled Meeting")
        text = title + "\n\n"
        for seg in segments:
            speaker = seg.get("speaker", "others").capitalize()
            text += f"[{speaker}] {seg['text']}\n\n"
        text = text.strip()
        clean = title.replace("\u2014", "-").replace("\u2013", "-")
        clean = re.sub(r'[/:*?"<>|\\]', '', clean)
        clean = re.sub(r'\s+', ' ', clean).strip() or "Meeting Transcript"
        filename = clean + ".txt"

        main_window = getattr(app.state, "main_window", None)
        if not main_window:
            return JSONResponse({"error": "No window available"}, status_code=500)
        try:
            import webview
            result = main_window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=filename,
            )
            if not result:
                return JSONResponse({"ok": False, "cancelled": True})
            save_path = result if isinstance(result, str) else result[0]
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            return JSONResponse({"ok": True, "path": save_path})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # --- Meeting Labels ---

    @app.get("/api/meeting/labels")
    async def list_meeting_labels():
        if not mt_store:
            return JSONResponse({"labels": []})
        return JSONResponse({"labels": mt_store.list_labels()})

    @app.post("/api/meeting/labels")
    async def create_meeting_label(request: Request):
        if not mt_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        data = await request.json()
        name = data.get("name", "").strip()[:50]
        color = data.get("color", "#3b82f6")
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        label_id = mt_store.create_label(name, color)
        return JSONResponse({"id": label_id, "name": name, "color": color})

    @app.post("/api/meeting/meetings/{meeting_id}/labels/{label_id}")
    async def assign_meeting_label(meeting_id: int, label_id: int):
        if not mt_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        mt_store.assign_label(meeting_id, label_id)
        return JSONResponse({"ok": True})

    @app.delete("/api/meeting/meetings/{meeting_id}/labels/{label_id}")
    async def remove_meeting_label(meeting_id: int, label_id: int):
        if not mt_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        mt_store.remove_label(meeting_id, label_id)
        return JSONResponse({"ok": True})

    @app.delete("/api/meeting/labels/{label_id}")
    async def delete_meeting_label(label_id: int):
        if not mt_store:
            return JSONResponse({"error": "not available"}, status_code=503)
        mt_store.delete_label(label_id)
        return JSONResponse({"ok": True})

    return app


def _post_process(text: str, llm, settings, formatter=None) -> tuple[str, str | None, str | None]:
    """Two-stage formatting pipeline.

    Stage 1: Punctuation/capitalization/segmentation (always runs if formatter loaded)
    Stage 2: LLM intelligent cleanup (runs if AI features enabled)

    Returns (final_text, stage1_text, raw_text):
        - final_text: the text to paste/display
        - stage1_text: after Stage 1 (None if Stage 1 didn't run)
        - raw_text: original input (None if no processing ran)
    """
    if not text or not text.strip():
        return text, None, None

    raw_text = None
    stage1_text = None
    current = text

    # --- Stage 1: Punctuation model (runs for tracking, does not override Whisper) ---
    # Whisper's punctuation is audio-informed (it hears pauses). The text-only
    # punct model sometimes places punctuation wrong because it can't hear audio.
    # We run Stage 1 for comparison/tracking only — Whisper's output is the primary.
    if formatter and formatter.is_loaded:
        try:
            formatted = formatter.format(current)
            if formatted:
                raw_text = text
                stage1_text = formatted
                # Do NOT override current — keep Whisper's audio-informed punctuation
        except Exception as e:
            print(f"Stage 1 formatting failed: {e}")

    # --- Stage 2: LLM cleanup (only if AI features enabled) ---
    if not settings or not llm:
        return current, stage1_text, raw_text

    needs_llm = settings.smart_cleanup or settings.context_formatting or settings.snippets_prompt_fragment
    if not needs_llm:
        return current, stage1_text, raw_text

    if len(current.split()) <= 5:
        return current, stage1_text, raw_text

    # Build Stage 2 prompt — punctuation/caps already handled by Stage 1
    lines = [
        "You are a dictation post-processor. The text below has already been "
        "punctuated and capitalized. Your job is to refine it further.",
        "",
        "Rules:",
        "- Add paragraph breaks (blank lines) when the topic or thought clearly shifts.",
        "- Collapse self-corrections (e.g., 'I went to the store no the mall' becomes 'I went to the mall').",
        "- Never add information that wasn't spoken. Never answer questions in the text.",
        "- Do not change punctuation or capitalization — that is already correct.",
        "- Preserve the speaker's natural voice and word choices.",
    ]

    if settings.smart_cleanup:
        lines.append(
            "- Remove verbal fillers (um, uh, like, you know, I mean, so, basically) "
            "and false starts, but only when they are clearly disfluencies — keep them "
            "if they are part of natural casual speech."
        )

    if settings.context_formatting:
        try:
            from context import get_frontmost_app, get_formatting_style, get_style_prompt
            bundle_id, app_name = get_frontmost_app()
            style = get_formatting_style(bundle_id, settings.app_styles)
            style_prompt = get_style_prompt(style)
            lines.append(f"- The user is typing in {app_name}. {style_prompt}")
        except Exception:
            pass

    snippet_fragment = settings.snippets_prompt_fragment
    if snippet_fragment:
        lines.append(f"- {snippet_fragment}")

    lines.append("")
    lines.append("Output ONLY the refined text. No commentary, no preamble, no explanations.")

    system_prompt = "\n".join(lines)
    result = llm.generate(current, system_prompt=system_prompt)

    if result and result != current:
        if raw_text is None:
            raw_text = text  # Stage 1 didn't run but Stage 2 did
        return result, stage1_text, raw_text

    return current, stage1_text, raw_text


# Cache for last recording's audio (numpy array) for retry
# Cache for last recording's audio (numpy array) for retry
_last_audio_cache = {"audio": None, "sample_rate": 16000}

_DEBUG_FORCE_FIRST_ERROR = False
_debug_error_count = 0


def _stop_and_transcribe(rec, txr, pipe, settings=None):
    """Shared stop+transcribe logic for websocket and bar handlers.

    Returns (text, elapsed, audio_duration) or (None, 0, 0) if no audio.
    Caches the last audio in _last_audio_cache for retry.
    """
    global _debug_error_count
    initial_prompt = settings.dictionary_prompt if settings else None
    use_streaming = pipe is not None and pipe.vad_available and pipe._active

    if use_streaming:
        rec.on_vad_chunk = None
        mic_audio, sys_audio = rec.stop_raw()

        if mic_audio is None or len(mic_audio) == 0:
            pipe.cancel()
            return None, 0, 0

        audio_duration = round(len(mic_audio) / rec.sample_rate, 2)

        if audio_duration < pipe.SHORT_RECORDING_THRESHOLD_S:
            pipe.cancel()
            if sys_audio is not None and len(sys_audio) > 0:
                try:
                    from aec import nlms_echo_cancel, noise_gate
                    mic_audio = nlms_echo_cancel(mic_audio, sys_audio)
                    mic_audio = noise_gate(mic_audio, sample_rate=rec.sample_rate)
                except Exception:
                    pass
            del sys_audio
            _last_audio_cache["audio"] = mic_audio.copy()
            _last_audio_cache["sample_rate"] = rec.sample_rate
            start_time = time.time()
            text = txr.transcribe_array(mic_audio, initial_prompt=initial_prompt)
            elapsed = round(time.time() - start_time, 2)
            del mic_audio
        else:
            del mic_audio
            start_time = time.time()
            results = pipe.stop(sys_audio)
            elapsed = round(time.time() - start_time, 2)
            del sys_audio
            text = " ".join(r.text for r in results if r.text)
            # Pipeline results don't expose raw audio, clear cache
            _last_audio_cache["audio"] = None

        if _DEBUG_FORCE_FIRST_ERROR:
            _debug_error_count += 1
            if _debug_error_count % 2 == 1:
                raise RuntimeError("DEBUG: forced error for retry testing")
        return text or None, elapsed, audio_duration
    else:
        wav_path = rec.stop()
        if not wav_path:
            return None, 0, 0
        # Cache audio from WAV before transcribing
        try:
            from scipy.io import wavfile as _wavfile
            _sr, _data = _wavfile.read(wav_path)
            _last_audio_cache["audio"] = _data.astype(np.float32) / 32767.0
            _last_audio_cache["sample_rate"] = _sr
        except Exception:
            _last_audio_cache["audio"] = None
        audio_duration = round(get_wav_duration(wav_path), 2)
        start_time = time.time()
        text = txr.transcribe(wav_path, initial_prompt=initial_prompt)
        elapsed = round(time.time() - start_time, 2)
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        if _DEBUG_FORCE_FIRST_ERROR:
            _debug_error_count += 1
            if _debug_error_count % 2 == 1:
                raise RuntimeError("DEBUG: forced error for retry testing")
        return text or None, elapsed, audio_duration


def _retry_transcribe(txr, settings=None, llm=None, formatter=None):
    """Re-transcribe cached audio. Returns (text, elapsed, audio_duration, raw_text, stage1_text) or None."""
    audio = _last_audio_cache.get("audio")
    if audio is None:
        return None
    sample_rate = _last_audio_cache.get("sample_rate", 16000)
    audio_duration = round(len(audio) / sample_rate, 2)
    initial_prompt = settings.dictionary_prompt if settings else None
    start_time = time.time()
    text = txr.transcribe_array(audio, initial_prompt=initial_prompt)
    elapsed = round(time.time() - start_time, 2)
    if text:
        text, stage1_text, raw_text = _post_process(text, llm, settings, formatter=formatter)
    else:
        stage1_text, raw_text = None, None
    return text, elapsed, audio_duration, raw_text, stage1_text


def _ws_stop_and_transcribe(rec, txr, pipe, llm=None, settings=None, formatter=None):
    """Called from asyncio.to_thread for websocket stop.

    Returns (text, elapsed, audio_duration, raw_text, stage1_text).
    """
    text, elapsed, audio_duration = _stop_and_transcribe(rec, txr, pipe, settings=settings)
    stage1_text = None
    raw_text = None
    if text:
        text, stage1_text, raw_text = _post_process(text, llm, settings, formatter=formatter)
    gc.collect()
    return text, elapsed, audio_duration, raw_text, stage1_text


def _bar_stop_and_transcribe(
    rec,
    txr,
    sm,
    hist,
    app_clip: InternalClipboard,
    auto_insert: bool,
    pipe=None,
    llm=None,
    settings=None,
    formatter=None,
    arm_timeout=None,
    cancel_timeout=None,
):
    """Background thread: stop recording, transcribe, update state."""
    sm.set_state(AppState.PROCESSING)
    if arm_timeout:
        arm_timeout()
    try:
        text, elapsed, audio_duration = _stop_and_transcribe(rec, txr, pipe, settings=settings)
        if cancel_timeout:
            cancel_timeout()
        # Bail if cancelled or timed out while transcribing
        if sm.state != AppState.PROCESSING:
            return
        if not text:
            sm.set_state(AppState.IDLE)
            return
        text, stage1_text, raw_text = _post_process(text, llm, settings, formatter=formatter)
        # Check again after post-processing
        if sm.state != AppState.PROCESSING:
            return
        app_clip.set_text(text)
        if auto_insert:
            try:
                paste_text(text)
            except Exception as e:
                print(f"Paste operation failed: {e}")
        hist.add(
            text,
            duration=audio_duration,
            latency=elapsed,
            source="dictation",
            raw_text=raw_text,
            stage1_text=stage1_text,
            transcriber_model=txr.model_repo if txr else None,
            formatter_model=llm.model_repo if (llm and raw_text and stage1_text != raw_text) else None,
            punct_model=formatter.model_repo if (formatter and stage1_text) else None,
        )
        gc.collect()
        sm.set_state(AppState.IDLE)
    except Exception as e:
        if cancel_timeout:
            cancel_timeout()
        # Don't overwrite if already timed out to ERROR
        if sm.state == AppState.PROCESSING:
            print(f"Bar transcription error: {e}")
            sm.set_state(AppState.ERROR)
            threading.Timer(5.0, lambda: sm.set_state(AppState.IDLE) if sm.state == AppState.ERROR else None).start()
