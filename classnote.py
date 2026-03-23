# classnote.py
"""ClassNote pipeline: live lecture transcription with two-stream architecture."""
import os
import queue
import threading
import time
from datetime import datetime

import numpy as np

from lecture_recorder import LectureRecorder
from lecture_store import LectureStore
from vad import SealedSegment, SileroVAD, VADSegmenter


class ClassNotePipeline:
    """Manages a ClassNote lecture recording session.

    Stream A: Fast per-segment transcription -> ghost text
    Stream B: Opportunistic rolling correction -> solidified text
    """

    FLUSH_INTERVAL_S = 30.0

    def __init__(
        self,
        transcriber,
        store: LectureStore,
        lectures_dir: str | None = None,
        sample_rate: int = 16000,
    ):
        self._transcriber = transcriber
        self._store = store
        self._lectures_dir = lectures_dir or os.path.expanduser(
            "~/.dashscribe/lectures"
        )
        self._sample_rate = sample_rate

        # State
        self._lecture_id = None
        self._active = False
        self._paused = False
        self._lock = threading.Lock()

        # VAD
        self._vad = SileroVAD()
        self._vad_loaded = False
        self._segmenter = None

        # Recorder
        self._recorder = None

        # Worker
        self._worker_thread = None
        self._stop_event = threading.Event()

        # Segments
        self._completed_segments = []  # SealedSegments for Stream B
        self._pending_results = []  # {index, text, ghost, start_ms, end_ms}
        self._correction_counter = 0
        self._last_corrected_idx = 0

        # Flush timer
        self._flush_timer = None

        # Callbacks
        self.on_segment = None  # fn({index, text, ghost, start_ms, end_ms})
        self.on_correction = None  # fn({start_index, end_index, text, start_ms, end_ms})
        self.on_status = None  # fn(status_string)
        self.on_error = None  # fn(message, recoverable)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def lecture_id(self) -> int | None:
        return self._lecture_id

    def load_vad(self):
        self._vad.load()
        self._vad_loaded = True

    def start(self, title: str) -> dict:
        """Start a new lecture recording session."""
        with self._lock:
            if self._active:
                raise RuntimeError("A lecture session is already active")

        os.makedirs(self._lectures_dir, mode=0o700, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = os.path.join(
            self._lectures_dir, f"lecture_{timestamp}.wav"
        )

        # Create DB record
        lecture_id = self._store.create_lecture(title, wav_path)
        self._lecture_id = lecture_id

        # Create recorder with error propagation
        self._recorder = LectureRecorder(self._sample_rate)
        self._recorder.on_write_error = self._on_recorder_error

        # Create VAD segmenter with lecture-tuned params
        self._segmenter = VADSegmenter(
            self._vad,
            self._sample_rate,
            max_segment_duration_s=30.0,
            silence_threshold_ms=600,
            min_segment_duration_s=2.0,
        )

        # Wire recorder -> segmenter
        self._recorder.on_vad_chunk = self._segmenter.feed

        # Start worker thread
        self._stop_event.clear()
        self._completed_segments.clear()
        self._pending_results.clear()
        self._correction_counter = 0
        self._last_corrected_idx = 0
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True
        )
        self._worker_thread.start()

        # Start recording
        self._recorder.start(wav_path)
        self._active = True
        self._paused = False

        # Start periodic flush
        self._schedule_flush()

        if self.on_status:
            self.on_status("recording")

        # Update wav_path with actual lecture_id
        final_path = os.path.join(
            self._lectures_dir, f"lecture_{lecture_id}_{timestamp}.wav"
        )
        if wav_path != final_path:
            try:
                os.rename(wav_path, final_path)
            except OSError:
                final_path = wav_path  # rename failed, keep original
            else:
                self._store.update_lecture(lecture_id, audio_path=final_path)
                self._recorder._wav_path = final_path

        return {"lecture_id": lecture_id, "wav_path": final_path}

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
        if self._recorder:
            self._recorder.stop()

        # Seal final segment and enqueue it, then signal worker to exit
        if self._segmenter:
            final_seg = self._segmenter.seal_final()
            if final_seg is not None:
                self._segmenter.segment_queue.put(final_seg)
            self._segmenter.signal_done()

        # Wait for worker
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=60)

        # Final flush
        self._flush_to_db()

        # Update lecture record
        elapsed = self._recorder.elapsed_seconds if self._recorder else 0
        total_words = sum(
            len(r["text"].split()) for r in self._pending_results
        )
        self._store.update_lecture(
            self._lecture_id,
            status="stopped",
            duration_seconds=elapsed,
            word_count=total_words,
        )

        if self.on_status:
            self.on_status("stopped")

        return {
            "lecture_id": self._lecture_id,
            "duration": elapsed,
            "word_count": total_words,
        }

    def pause(self):
        """Pause recording (for dictation preemption or user pause)."""
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
        """Discard the current session -- delete audio + DB records."""
        self._active = False
        self._paused = False

        if self._flush_timer:
            self._flush_timer.cancel()
        if self._recorder:
            self._recorder.stop()
        if self._segmenter:
            self._segmenter.signal_done()
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)

        # Delete audio file
        if self._recorder and self._recorder.wav_path:
            try:
                os.remove(self._recorder.wav_path)
            except OSError:
                pass

        # Delete DB record
        if self._lecture_id:
            self._store.delete_lecture(self._lecture_id)

        self._lecture_id = None

    def _on_recorder_error(self, message: str):
        """Propagate disk write errors to the frontend."""
        if self.on_error:
            self.on_error(message, True)

    def _check_disk_space(self):
        """Warn if disk space is critically low."""
        try:
            stat = os.statvfs(self._lectures_dir)
            free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
            if free_mb < 100:
                # Critical — stop recording to prevent data corruption
                if self.on_error:
                    self.on_error(
                        "Disk space critically low (<100MB). Stopping recording to save your data.",
                        False,
                    )
                self.stop()
            elif free_mb < 500:
                if self.on_error:
                    self.on_error(
                        f"Low disk space ({int(free_mb)}MB free). Recording may stop soon.",
                        True,
                    )
        except OSError:
            pass

    # --- Worker ---

    def _worker_loop(self):
        """Dequeue sealed segments from VAD and process via Stream A."""
        while not self._stop_event.is_set():
            try:
                segment = self._segmenter.segment_queue.get(timeout=1.0)
            except Exception:
                continue

            if segment is None:  # Sentinel
                break

            try:
                self._process_stream_a(segment)
                self._completed_segments.append(segment)
                # Try Stream B if enough segments and no pending work
                if (
                    len(self._completed_segments) >= 3
                    and self._segmenter.segment_queue.empty()
                    and not self._paused
                ):
                    self._try_stream_b_correction()
            except Exception as e:
                if self.on_error:
                    self.on_error(str(e), True)

    def _process_stream_a(self, segment: SealedSegment):
        """Transcribe a single segment -> ghost text."""
        result = self._transcriber.transcribe_array(segment.mic_audio)
        text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()

        if not text:
            return

        start_ms = int(segment.start_sample / self._sample_rate * 1000)
        end_ms = int(segment.end_sample / self._sample_rate * 1000)

        seg_result = {
            "index": segment.segment_index,
            "text": text,
            "ghost": True,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        self._pending_results.append(seg_result)

        if self.on_segment:
            self.on_segment(seg_result)

    def _try_stream_b_correction(self):
        """Opportunistic non-overlapping correction of uncorrected segments."""
        if self._paused or len(self._completed_segments) < 3:
            return

        # Only correct segments that haven't been corrected yet
        uncorrected = self._completed_segments[self._last_corrected_idx:]
        if len(uncorrected) < 3:
            return

        last_3 = uncorrected[:3]
        # Merge audio
        merged = np.concatenate([s.mic_audio for s in last_3])

        try:
            result = self._transcriber.transcribe_array(merged)
            text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()
        except Exception:
            return  # Stream B failure is silent

        if not text:
            return

        self._correction_counter += 1
        start_idx = last_3[0].segment_index
        end_idx = last_3[-1].segment_index
        start_ms = int(last_3[0].start_sample / self._sample_rate * 1000)
        end_ms = int(last_3[-1].end_sample / self._sample_rate * 1000)

        # Update pending results: replace the 3 segments with 1 corrected entry
        self._pending_results = [
            r for r in self._pending_results
            if r["index"] < start_idx or r["index"] > end_idx
        ]
        self._pending_results.append({
            "index": start_idx,
            "text": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
        })

        # Persist correction
        self._store.apply_correction(
            self._lecture_id,
            start_idx,
            end_idx,
            text,
            self._correction_counter,
            start_ms=start_ms,
            end_ms=end_ms,
        )

        # Advance pointer past corrected segments
        self._last_corrected_idx += 3

        if self.on_correction:
            self.on_correction({
                "start_index": start_idx,
                "end_index": end_idx,
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "correction_group_id": self._correction_counter,
            })

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
        self._check_disk_space()
        self._schedule_flush()

    def _flush_to_db(self):
        """Write pending segments to SQLite + update duration/word_count."""
        if not self._lecture_id:
            return

        try:
            # Always update duration and word count (even if no new segments)
            elapsed = self._recorder.elapsed_seconds if self._recorder else 0
            total_words = sum(
                len(r["text"].split()) for r in self._pending_results
            )
            self._store.update_lecture(
                self._lecture_id,
                duration_seconds=elapsed,
                word_count=total_words,
            )
        except Exception:
            pass  # Non-fatal

        if not self._pending_results:
            return

        segments = [
            {
                "index": r["index"],
                "text": r["text"],
                "start_ms": r["start_ms"],
                "end_ms": r["end_ms"],
            }
            for r in self._pending_results
        ]
        try:
            self._store.flush_segments(self._lecture_id, segments)
        except Exception:
            pass  # Non-fatal -- will retry on next flush

    # --- Re-transcribe ---

    def retranscribe(self, lecture_id: int, on_progress=None):
        """Re-transcribe a lecture from its saved audio file.

        Runs in calling thread (meant to be called from a background thread).
        on_progress(current, total) called after each segment.
        """
        import wave as _wave

        lecture = self._store.get_lecture(lecture_id)
        if not lecture:
            raise ValueError("Lecture not found")
        audio_path = lecture.get("audio_path")
        if not audio_path or not os.path.exists(audio_path):
            raise ValueError("Audio file not found")

        # Load entire WAV as float32
        with _wave.open(audio_path, "rb") as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0

        # Run through VAD segmenter
        segmenter = VADSegmenter(
            self._vad,
            self._sample_rate,
            max_segment_duration_s=30.0,
            silence_threshold_ms=600,
            min_segment_duration_s=2.0,
        )

        chunk_size = 512
        for i in range(0, len(audio), chunk_size):
            segmenter.feed(audio[i:i + chunk_size])

        final_seg = segmenter.seal_final()
        if final_seg is not None:
            segmenter.segment_queue.put(final_seg)
        segmenter.signal_done()

        # Collect all segments
        sealed = []
        while True:
            try:
                seg = segmenter.segment_queue.get_nowait()
            except Exception:
                break
            if seg is None:
                break
            sealed.append(seg)

        # Transcribe each and update DB
        results = []
        for idx, seg in enumerate(sealed):
            result = self._transcriber.transcribe_array(seg.mic_audio)
            text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()
            if not text:
                continue
            start_ms = int(seg.start_sample / self._sample_rate * 1000)
            end_ms = int(seg.end_sample / self._sample_rate * 1000)
            results.append({"index": idx, "text": text, "start_ms": start_ms, "end_ms": end_ms})
            if on_progress:
                on_progress(idx + 1, len(sealed))

        # Clear old segments and write new ones
        self._store.replace_segments(lecture_id, results)

        # Update word count
        total_words = sum(len(r["text"].split()) for r in results)
        self._store.update_lecture(lecture_id, word_count=total_words)

        return {"segments": len(results), "word_count": total_words}
