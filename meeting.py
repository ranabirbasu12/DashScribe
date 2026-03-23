# meeting.py
"""MeetingPipeline: live meeting transcription with dual VAD and speaker labels."""
import os
import queue
import threading
import time
from datetime import datetime

import numpy as np

from meeting_recorder import MeetingRecorder
from meeting_store import MeetingStore
from vad import SealedSegment, SileroVAD, VADSegmenter

KNOWN_MEETING_APPS = {
    "us.zoom.xos": "Zoom",
    "com.microsoft.teams2": "Microsoft Teams",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.apple.FaceTime": "FaceTime",
    "com.hnc.Discord": "Discord",
    "com.google.Chrome": "Chrome",
    "company.thebrowser.Browser": "Arc",
    "com.apple.Safari": "Safari",
    "com.microsoft.edgemac": "Edge",
    "org.mozilla.firefox": "Firefox",
}


class MeetingPipeline:
    """Manages a meeting transcription session with dual VAD pipelines."""

    FLUSH_INTERVAL_S = 30.0

    def __init__(
        self,
        transcriber,
        store: MeetingStore,
        meetings_dir: str | None = None,
        sample_rate: int = 16000,
    ):
        self._transcriber = transcriber
        self._store = store
        self._meetings_dir = meetings_dir or os.path.expanduser(
            "~/.dashscribe/meetings"
        )
        self._sample_rate = sample_rate

        # State
        self._meeting_id = None
        self._active = False
        self._paused = False
        self._mode = "listen"
        self._lock = threading.Lock()

        # VAD — separate instances per stream (SileroVAD has mutable LSTM state,
        # not safe to share across threads)
        self._sys_vad = SileroVAD()
        self._mic_vad = SileroVAD()
        self._vad_loaded = False
        self._sys_segmenter = None
        self._mic_segmenter = None

        # Recorder
        self._recorder = None

        # Worker
        self._worker_thread = None
        self._stop_event = threading.Event()
        self._segment_queue = queue.Queue()

        # Segment counter (across both streams)
        self._segment_index = 0

        # Pending results for flush
        self._pending_results = []

        # Flush timer
        self._flush_timer = None

        # Callbacks
        self.on_segment = None  # fn({index, text, speaker, start_ms, end_ms})
        self.on_status = None  # fn(status_string)
        self.on_error = None  # fn(message, recoverable)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def meeting_id(self) -> int | None:
        return self._meeting_id

    def load_vad(self):
        self._sys_vad.load()
        self._mic_vad.load()
        self._vad_loaded = True

    def start(self, title: str, app_bundle_id: str, mode: str = "listen") -> dict:
        """Start a new meeting transcription session."""
        with self._lock:
            if self._active:
                raise RuntimeError("A meeting session is already active")

        self._mode = mode
        os.makedirs(self._meetings_dir, mode=0o700, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        sys_wav = os.path.join(self._meetings_dir, f"meeting_{timestamp}_system.wav")
        mic_wav = os.path.join(self._meetings_dir, f"meeting_{timestamp}_mic.wav") if mode == "full" else None

        # Create DB record
        meeting_id = self._store.create_meeting(title, app_name=app_bundle_id, mode=mode)
        self._meeting_id = meeting_id

        # Create recorder
        if self._recorder is None:
            self._recorder = MeetingRecorder(
                mode=mode,
                app_bundle_id=app_bundle_id,
                sample_rate=self._sample_rate,
            )

        # Create VAD segmenters with meeting-tuned params (each has its own VAD instance)
        self._sys_segmenter = VADSegmenter(
            self._sys_vad,
            self._sample_rate,
            max_segment_duration_s=20.0,
            silence_threshold_ms=400,
            min_segment_duration_s=1.5,
        )

        if mode == "full":
            self._mic_segmenter = VADSegmenter(
                self._mic_vad,
                self._sample_rate,
                max_segment_duration_s=20.0,
                silence_threshold_ms=400,
                min_segment_duration_s=1.5,
            )

        # Wire recorder callbacks -> segmenters
        self._recorder.on_system_audio = self._on_system_audio
        if mode == "full":
            self._recorder.on_mic_audio = self._on_mic_audio

        # Start worker thread
        self._stop_event.clear()
        self._segment_queue = queue.Queue()
        self._segment_index = 0
        self._pending_results = []

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        # Start recording
        self._recorder.start(system_wav_path=sys_wav, mic_wav_path=mic_wav)
        self._active = True
        self._paused = False

        # Start periodic flush
        self._schedule_flush()

        if self.on_status:
            self.on_status("recording")

        # Update audio paths in DB
        self._store.update_meeting(
            meeting_id,
            system_audio_path=sys_wav,
            mic_audio_path=mic_wav,
        )

        return {"meeting_id": meeting_id}

    def stop(self) -> dict:
        """Stop recording, finalize session."""
        if not self._active and not self._paused:
            return {}

        self._active = False
        self._paused = False

        # Cancel flush timer
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None

        # Stop recorder
        audio_paths = {}
        if self._recorder:
            audio_paths = self._recorder.stop()

        # Seal final segments
        if self._sys_segmenter:
            final = self._sys_segmenter.seal_final()
            if final is not None:
                self._segment_queue.put(("others", final))
            self._sys_segmenter.signal_done()

        if self._mic_segmenter:
            final = self._mic_segmenter.seal_final()
            if final is not None:
                self._segment_queue.put(("you", final))
            self._mic_segmenter.signal_done()

        # Signal worker to exit
        self._segment_queue.put(None)
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=60)

        # Final flush
        self._flush_to_db()

        # Update meeting record
        total_words = sum(len(r["text"].split()) for r in self._pending_results)
        self._store.update_meeting(
            self._meeting_id,
            status="stopped",
            word_count=total_words,
        )

        if self.on_status:
            self.on_status("stopped")

        return {
            "meeting_id": self._meeting_id,
            "word_count": total_words,
        }

    def pause(self):
        """Pause recording."""
        if not self._active:
            return
        self._active = False
        self._paused = True

        if self._recorder:
            self._recorder.pause()

        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None

        if self.on_status:
            self.on_status("paused")

    def resume(self):
        """Resume recording after pause."""
        if not self._paused:
            return
        self._paused = False
        self._active = True

        if self._recorder:
            self._recorder.resume()

        self._schedule_flush()

        if self.on_status:
            self.on_status("recording")

    def discard(self):
        """Discard the current session."""
        self._active = False
        self._paused = False

        if self._flush_timer:
            self._flush_timer.cancel()
        if self._recorder:
            self._recorder.stop()
        if self._sys_segmenter:
            self._sys_segmenter.signal_done()
        if self._mic_segmenter:
            self._mic_segmenter.signal_done()

        self._segment_queue.put(None)
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)

        # Delete DB record
        if self._meeting_id:
            self._store.delete_meeting(self._meeting_id)

        self._meeting_id = None

    # --- Audio callbacks ---

    def _on_system_audio(self, audio: np.ndarray):
        """System audio chunk -> system VAD segmenter."""
        if not self._active or self._sys_segmenter is None:
            return
        self._sys_segmenter.feed(audio)
        # Check for sealed segments
        while not self._sys_segmenter.segment_queue.empty():
            try:
                seg = self._sys_segmenter.segment_queue.get_nowait()
                if seg is not None:
                    self._segment_queue.put(("others", seg))
            except Exception:
                break

    def _on_mic_audio(self, audio: np.ndarray):
        """Mic audio chunk -> mic VAD segmenter."""
        if not self._active or self._mic_segmenter is None:
            return
        self._mic_segmenter.feed(audio)
        while not self._mic_segmenter.segment_queue.empty():
            try:
                seg = self._mic_segmenter.segment_queue.get_nowait()
                if seg is not None:
                    self._segment_queue.put(("you", seg))
            except Exception:
                break

    # --- Worker ---

    def _worker_loop(self):
        """Dequeue segments from both pipelines and transcribe."""
        while not self._stop_event.is_set():
            try:
                item = self._segment_queue.get(timeout=1.0)
            except Exception:
                continue

            if item is None:  # Sentinel
                break

            speaker, segment = item
            try:
                self._process_segment(segment, speaker=speaker)
            except Exception as e:
                if self.on_error:
                    self.on_error(str(e), True)

    def _process_segment(self, segment: SealedSegment, speaker: str = "others"):
        """Transcribe a single segment and tag with speaker."""
        result = self._transcriber.transcribe_array(segment.mic_audio)
        text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()

        if not text:
            return

        start_ms = int(segment.start_sample / self._sample_rate * 1000)
        end_ms = int(segment.end_sample / self._sample_rate * 1000)

        seg_result = {
            "index": self._segment_index,
            "text": text,
            "speaker": speaker,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        self._segment_index += 1
        self._pending_results.append(seg_result)

        if self.on_segment:
            self.on_segment(seg_result)

    # --- Periodic flush ---

    def _schedule_flush(self):
        if self._active:
            self._flush_timer = threading.Timer(
                self.FLUSH_INTERVAL_S, self._periodic_flush
            )
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _periodic_flush(self):
        self._flush_to_db()
        self._schedule_flush()

    def _flush_to_db(self):
        """Write pending segments to SQLite."""
        if not self._meeting_id or not self._pending_results:
            return

        segments = [
            {
                "index": r["index"],
                "text": r["text"],
                "start_ms": r["start_ms"],
                "end_ms": r["end_ms"],
                "speaker": r["speaker"],
            }
            for r in self._pending_results
        ]
        try:
            self._store.flush_segments(self._meeting_id, segments)
        except Exception:
            pass  # Non-fatal
