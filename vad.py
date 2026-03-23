# vad.py
"""Voice Activity Detection using Silero VAD (ONNX Runtime, no PyTorch)."""
import os
import queue
from dataclasses import dataclass
from typing import Optional

import numpy as np

SILERO_MODEL_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)
MODEL_CACHE_DIR = os.path.expanduser("~/.dashscribe")
MODEL_FILENAME = "silero_vad.onnx"

# Silero VAD operates on 512-sample windows at 16kHz (32ms each)
VAD_WINDOW_SAMPLES = 512
VAD_SAMPLE_RATE = 16000


class SileroVAD:
    """Silero VAD wrapper using ONNX Runtime. No PyTorch dependency."""

    CONTEXT_SIZE_16K = 64  # Silero v5 requires 64-sample context at 16kHz

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self._session = None
        self._state: Optional[np.ndarray] = None
        self._context: Optional[np.ndarray] = None
        self._available = False

    def load(self) -> bool:
        """Load the ONNX model. Returns True if successful.

        Downloads the model on first use, caches at ~/.dashscribe/silero_vad.onnx.
        """
        model_path = os.path.join(MODEL_CACHE_DIR, MODEL_FILENAME)
        if not os.path.exists(model_path):
            try:
                import urllib.request

                os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
                urllib.request.urlretrieve(SILERO_MODEL_URL, model_path)
            except Exception as e:
                print(f"VAD model download failed: {e}")
                return False

        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = ort.InferenceSession(model_path, sess_options=opts)
            self._reset_state()
            self._available = True
            return True
        except Exception as e:
            print(f"VAD initialization failed: {e}")
            return False

    def _reset_state(self):
        """Reset LSTM state and context for a new audio stream."""
        # Silero VAD ONNX state: (2, batch=1, hidden=128)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT_SIZE_16K), dtype=np.float32)

    def reset(self):
        """Reset state for a new recording session."""
        if self._available:
            self._reset_state()

    def __call__(self, audio_chunk: np.ndarray) -> float:
        """Run VAD on a 512-sample chunk. Returns speech probability [0, 1].

        Args:
            audio_chunk: float32 numpy array, exactly 512 samples at 16kHz.

        Returns:
            Speech probability. > self.threshold means speech detected.
        """
        if not self._available:
            return 0.0

        chunk = audio_chunk.reshape(1, -1).astype(np.float32)
        # Silero v5: prepend context from previous chunk
        input_data = np.concatenate([self._context, chunk], axis=1)
        self._context = chunk[:, -self.CONTEXT_SIZE_16K:]

        sr = np.array(VAD_SAMPLE_RATE, dtype=np.int64)

        ort_inputs = {
            "input": input_data,
            "state": self._state,
            "sr": sr,
        }

        out, self._state = self._session.run(None, ort_inputs)
        return float(out.squeeze())

    @property
    def is_available(self) -> bool:
        return self._available


@dataclass
class SealedSegment:
    """A completed speech segment ready for AEC + transcription."""

    segment_index: int
    mic_audio: np.ndarray  # float32, 16kHz mono
    start_sample: int  # Global sample offset (for system audio alignment)
    end_sample: int  # Global sample offset


class VADSegmenter:
    """Analyzes audio chunks from the recorder callback, detects speech
    boundaries, and produces SealedSegments.

    Thread safety: feed() is called from the sounddevice callback thread.
    The segment_queue is consumed by the pipeline worker thread.
    """

    def __init__(
        self,
        vad: SileroVAD,
        sample_rate: int = 16000,
        max_segment_duration_s: float = 20.0,
        silence_threshold_ms: int = 600,
        min_segment_duration_s: float = 1.0,
    ):
        self.vad = vad
        self.sample_rate = sample_rate
        self.segment_queue: queue.Queue[Optional[SealedSegment]] = queue.Queue()

        self._silence_threshold_samples = int(
            silence_threshold_ms * sample_rate / 1000
        )
        self._min_segment_samples = int(min_segment_duration_s * sample_rate)
        self._max_segment_samples = int(max_segment_duration_s * sample_rate)
        self._reset_state()

    def _reset_state(self):
        self._current_chunks: list[np.ndarray] = []
        self._segment_start_sample: int = 0
        self._global_sample_count: int = 0
        self._segment_index: int = 0
        self._silence_samples: int = 0
        self._in_speech: bool = False
        self._vad_buffer: np.ndarray = np.array([], dtype=np.float32)
        self._current_samples: int = 0
        self._pre_speech_buffer: list[np.ndarray] = []
        self._pre_speech_buffer_samples: int = 0

    def reset(self):
        """Reset state for a new recording session."""
        self._reset_state()
        self.vad.reset()
        while not self.segment_queue.empty():
            try:
                self.segment_queue.get_nowait()
            except queue.Empty:
                break

    def feed(self, chunk: np.ndarray):
        """Process an audio chunk from the sounddevice callback.

        Args:
            chunk: float32 array, shape (N, 1) or (N,), 16kHz mono.

        Must be fast (< 2ms) to avoid blocking the audio callback.
        """
        flat = chunk.flatten()

        # Only accumulate audio when speech is active.
        # When not in speech, buffer recent audio as lookback so we don't
        # clip the start of the next utterance (keep ~100ms).
        if self._in_speech:
            self._current_chunks.append(flat.copy())
            self._current_samples += len(flat)
        else:
            self._pre_speech_buffer.append(flat.copy())
            self._pre_speech_buffer_samples += len(flat)
            # Keep only last ~500ms of pre-speech audio
            lookback_limit = int(self.sample_rate * 0.5)
            while self._pre_speech_buffer_samples > lookback_limit and len(self._pre_speech_buffer) > 1:
                removed = self._pre_speech_buffer.pop(0)
                self._pre_speech_buffer_samples -= len(removed)

        # Accumulate into VAD buffer and process in 512-sample windows
        self._vad_buffer = np.concatenate([self._vad_buffer, flat])

        while len(self._vad_buffer) >= VAD_WINDOW_SAMPLES:
            window = self._vad_buffer[:VAD_WINDOW_SAMPLES]
            self._vad_buffer = self._vad_buffer[VAD_WINDOW_SAMPLES:]

            prob = self.vad(window)
            is_speech = prob > self.vad.threshold

            if is_speech:
                if not self._in_speech:
                    # Speech just started — move lookback buffer into segment chunks.
                    # The current chunk is already in _pre_speech_buffer (added at top of feed).
                    self._current_chunks = self._pre_speech_buffer
                    self._current_samples = self._pre_speech_buffer_samples
                    self._pre_speech_buffer = []
                    self._pre_speech_buffer_samples = 0
                    self._segment_start_sample = max(0, self._global_sample_count - self._current_samples)
                self._in_speech = True
                self._silence_samples = 0
            else:
                self._silence_samples += VAD_WINDOW_SAMPLES

            self._global_sample_count += VAD_WINDOW_SAMPLES

        # Seal segment when speech was detected and silence exceeds threshold
        if (
            self._in_speech
            and self._silence_samples >= self._silence_threshold_samples
        ):
            segment_audio = np.concatenate(self._current_chunks)
            if len(segment_audio) >= self._min_segment_samples:
                segment = SealedSegment(
                    segment_index=self._segment_index,
                    mic_audio=segment_audio,
                    start_sample=self._segment_start_sample,
                    end_sample=self._segment_start_sample + len(segment_audio),
                )
                self.segment_queue.put(segment)
                self._segment_index += 1
                self._segment_start_sample += len(segment_audio)
                self._current_chunks = []
                self._current_samples = 0
            self._in_speech = False
            self._silence_samples = 0

        # Force periodic sealing for long continuous speech without pauses.
        if self._in_speech and self._current_samples >= self._max_segment_samples:
            segment_audio = np.concatenate(self._current_chunks)
            if len(segment_audio) >= self._min_segment_samples:
                segment = SealedSegment(
                    segment_index=self._segment_index,
                    mic_audio=segment_audio,
                    start_sample=self._segment_start_sample,
                    end_sample=self._segment_start_sample + len(segment_audio),
                )
                self.segment_queue.put(segment)
                self._segment_index += 1
                self._segment_start_sample += len(segment_audio)
            self._current_chunks = []
            self._current_samples = 0
            self._silence_samples = 0
            self._in_speech = False

    def seal_final(self) -> Optional[SealedSegment]:
        """Seal whatever audio remains as the final segment.

        Called when recording stops. Returns the segment directly
        (not via queue) so the caller can handle it synchronously.
        """
        if not self._current_chunks:
            return None

        segment_audio = np.concatenate(self._current_chunks)
        if len(segment_audio) < int(self.sample_rate * 0.1):  # < 100ms, skip
            return None

        segment = SealedSegment(
            segment_index=self._segment_index,
            mic_audio=segment_audio,
            start_sample=self._segment_start_sample,
            end_sample=self._segment_start_sample + len(segment_audio),
        )
        self._current_chunks = []
        self._current_samples = 0
        return segment

    def signal_done(self):
        """Signal the worker thread that no more segments are coming."""
        self.segment_queue.put(None)
