# pipeline.py
"""Streaming transcription pipeline: VAD segmentation + overlapped AEC/transcription."""
import queue
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np

from vad import SileroVAD, VADSegmenter, SealedSegment


@dataclass
class SegmentResult:
    """Transcription result for one segment."""

    segment_index: int
    text: str
    audio_duration: float  # seconds


class StreamingPipeline:
    """Manages VAD segmentation + transcription worker thread.

    Usage:
        pipeline = StreamingPipeline(transcriber)
        pipeline.load_vad()               # During app startup
        pipeline.start(sys_audio_chunks)  # When recording begins
        # ... audio callback calls pipeline.feed(chunk) ...
        results = pipeline.stop(sys_audio) # When recording ends
        text = " ".join(r.text for r in results)
    """

    SHORT_RECORDING_THRESHOLD_S = 5.0

    def __init__(self, transcriber, sample_rate: int = 16000):
        self.transcriber = transcriber
        self.sample_rate = sample_rate

        self._vad = SileroVAD(threshold=0.5)
        self._vad_loaded = False

        self._segmenter: Optional[VADSegmenter] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._results: list[SegmentResult] = []
        self._results_lock = threading.Lock()
        self._sys_audio_chunks: Optional[list[np.ndarray]] = None
        self._sys_audio_base_sample: int = 0  # sample offset of first chunk after trimming
        self._active = False
        self._lifecycle_lock = threading.Lock()

    def load_vad(self):
        """Load the VAD model. Called during app startup (background thread)."""
        if not self._vad_loaded:
            self._vad_loaded = self._vad.load()
            if self._vad_loaded:
                print("VAD model loaded")
            else:
                print("VAD model not available, streaming disabled")

    @property
    def vad_available(self) -> bool:
        return self._vad_loaded and self._vad.is_available

    def start(self, sys_audio_chunks: Optional[list[np.ndarray]] = None):
        """Begin a new streaming session.

        Args:
            sys_audio_chunks: Reference to the system audio chunk list
                            being populated by SystemAudioCapture.
        """
        if not self.vad_available:
            return False

        with self._lifecycle_lock:
            if self._active:
                return False
            if self._worker_thread is not None:
                if self._worker_thread.is_alive():
                    # Previous worker is still draining; avoid overlapping sessions.
                    return False
                self._worker_thread = None

            self._sys_audio_chunks = sys_audio_chunks
            self._sys_audio_base_sample = 0
            self._results = []
            self._segmenter = VADSegmenter(self._vad, self.sample_rate)
            self._active = True

            segmenter = self._segmenter
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                args=(segmenter,),
                daemon=True,
            )
            self._worker_thread.start()
            return True

    def feed(self, chunk: np.ndarray):
        """Feed audio chunk to the VAD segmenter.

        Called from the sounddevice callback thread.
        """
        if self._active and self._segmenter is not None:
            self._segmenter.feed(chunk)

    def stop(self, sys_audio: Optional[np.ndarray]) -> list[SegmentResult]:
        """Stop the pipeline and return ordered results.

        Args:
            sys_audio: Complete system audio captured during recording,
                      or None if unavailable.

        Returns:
            List of SegmentResult in segment order.
        """
        with self._lifecycle_lock:
            if not self._active or self._segmenter is None:
                return []

            self._active = False
            segmenter = self._segmenter
            worker = self._worker_thread

        # Seal the final segment
        final_segment = segmenter.seal_final()

        # Signal worker that no more segments are coming
        segmenter.signal_done()

        # Wait for worker to finish queued segments
        worker_alive = False
        if worker is not None:
            worker.join(timeout=60)
            worker_alive = worker.is_alive()
            if worker_alive:
                print("WARNING: pipeline worker did not stop within 60s")

        # Process the final segment on this thread
        # (worker has stopped, no MLX contention)
        if final_segment is not None:
            result = self._process_segment(final_segment, sys_audio)
            if result is not None:
                with self._results_lock:
                    self._results.append(result)

        with self._results_lock:
            results = sorted(self._results, key=lambda r: r.segment_index)

        with self._lifecycle_lock:
            self._segmenter = None
            self._sys_audio_chunks = None
            self._sys_audio_base_sample = 0
            # Keep worker reference if it is still alive to block overlapping sessions.
            self._worker_thread = worker if worker_alive else None

        return results

    def cancel(self):
        """Stop the current streaming session without transcribing queued audio."""
        with self._lifecycle_lock:
            if not self._active or self._segmenter is None:
                self._sys_audio_chunks = None
                return
            self._active = False
            segmenter = self._segmenter
            worker = self._worker_thread

        segmenter.signal_done()

        worker_alive = False
        if worker is not None:
            worker.join(timeout=5)
            worker_alive = worker.is_alive()
            if worker_alive:
                print("WARNING: pipeline worker did not stop within 5s after cancel")

        with self._lifecycle_lock:
            self._segmenter = None
            self._sys_audio_chunks = None
            self._sys_audio_base_sample = 0
            self._results = []
            # Keep worker reference if it is still alive to block overlapping sessions.
            self._worker_thread = worker if worker_alive else None

    def _worker_loop(self, segmenter: VADSegmenter):
        """Worker thread: dequeue sealed segments and transcribe them."""
        prev_text = None  # Track previous segment for cross-segment context
        while True:
            try:
                segment = segmenter.segment_queue.get(timeout=0.1)
            except queue.Empty:
                if not self._active:
                    break
                continue

            if segment is None:  # Sentinel
                break

            sys_audio = self._get_sys_audio_window(
                segment.start_sample,
                segment.end_sample,
            )
            result = self._process_segment(
                segment, sys_audio, sys_audio_aligned=True, prev_text=prev_text
            )
            if result is not None:
                with self._results_lock:
                    self._results.append(result)
                prev_text = result.text  # Feed to next segment as context

            # Trim consumed system audio chunks to bound memory growth.
            self._trim_sys_audio_chunks(segment.end_sample)

    def _process_segment(
        self,
        segment: SealedSegment,
        sys_audio: Optional[np.ndarray],
        *,
        sys_audio_aligned: bool = False,
        prev_text: Optional[str] = None,
    ) -> Optional[SegmentResult]:
        """Apply AEC to a segment, then transcribe it."""
        mic = segment.mic_audio

        if sys_audio is not None and len(sys_audio) > 0:
            try:
                from aec import nlms_echo_cancel, noise_gate

                if sys_audio_aligned:
                    ref = sys_audio
                else:
                    ref = self._align_sys_audio(
                        sys_audio, segment.start_sample, segment.end_sample
                    )
                if ref is not None and len(ref) > 0:
                    mic = nlms_echo_cancel(mic, ref)
                    mic = noise_gate(mic, sample_rate=self.sample_rate)
            except Exception as e:
                print(f"Segment AEC failed, using raw audio: {e}")

        try:
            text = self.transcriber.transcribe_array(mic, initial_prompt=prev_text)
            if text:
                duration = len(segment.mic_audio) / self.sample_rate
                return SegmentResult(
                    segment_index=segment.segment_index,
                    text=text,
                    audio_duration=duration,
                )
        except Exception as e:
            print(f"Segment transcription failed: {e}")

        return None

    def _align_sys_audio(
        self,
        sys_audio: np.ndarray,
        start_sample: int,
        end_sample: int,
    ) -> Optional[np.ndarray]:
        """Extract the portion of system audio aligned with a mic segment."""
        needed_len = end_sample - start_sample

        if start_sample >= len(sys_audio):
            return None

        available_end = min(end_sample, len(sys_audio))
        ref = sys_audio[start_sample:available_end]

        if len(ref) < needed_len:
            ref = np.pad(ref, (0, needed_len - len(ref)), mode="constant")

        return ref

    def _trim_sys_audio_chunks(self, consumed_up_to: int):
        """Remove system audio chunks that are fully before *consumed_up_to*.

        This bounds memory growth during long recordings by discarding audio
        that has already been used for AEC.
        """
        chunks = self._sys_audio_chunks
        if chunks is None:
            return

        cursor = self._sys_audio_base_sample
        trim_count = 0
        for chunk in chunks:
            chunk_end = cursor + len(chunk)
            if chunk_end <= consumed_up_to:
                trim_count += 1
                cursor = chunk_end
            else:
                break

        if trim_count > 0:
            del chunks[:trim_count]
            self._sys_audio_base_sample = cursor

    def _get_sys_audio_window(
        self,
        start_sample: int,
        end_sample: int,
    ) -> Optional[np.ndarray]:
        """Return only the system-audio slice needed for one segment.

        This avoids full-history concatenation for every segment, which can
        otherwise cause large transient allocations over long sessions.
        """
        if self._sys_audio_chunks is None:
            return None

        chunks = list(self._sys_audio_chunks)  # Shallow copy for thread safety
        if not chunks:
            return None

        needed_len = end_sample - start_sample
        if needed_len <= 0:
            return None

        pieces: list[np.ndarray] = []
        cursor = self._sys_audio_base_sample

        try:
            for chunk in chunks:
                chunk_len = len(chunk)
                chunk_start = cursor
                chunk_end = cursor + chunk_len
                cursor = chunk_end

                if chunk_end <= start_sample:
                    continue
                if chunk_start >= end_sample:
                    break

                local_start = max(0, start_sample - chunk_start)
                local_end = min(chunk_len, end_sample - chunk_start)
                if local_end > local_start:
                    pieces.append(chunk[local_start:local_end].copy())
        except Exception:
            return None

        if not pieces:
            return None

        try:
            ref = pieces[0] if len(pieces) == 1 else np.concatenate(pieces, axis=0)
        except Exception:
            return None

        if len(ref) < needed_len:
            ref = np.pad(ref, (0, needed_len - len(ref)), mode="constant")

        return ref
