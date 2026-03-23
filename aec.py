# aec.py
"""Acoustic Echo Cancellation using NLMS adaptive filter."""
import threading

import numpy as np


def nlms_echo_cancel(
    mic: np.ndarray,
    ref: np.ndarray,
    filter_len: int = 1600,
    step_size: float = 0.5,
    block_size: int = 256,
) -> np.ndarray:
    """Remove echo of `ref` (system audio) from `mic` (microphone) signal.

    Uses block NLMS (Normalized Least Mean Squares) adaptive filtering.
    The filter learns how the system audio leaks into the mic (through
    speakers → room → mic) and subtracts that estimated echo.

    Args:
        mic: Microphone signal (voice + echo), float32.
        ref: Reference signal (system audio output), float32, same sample rate.
        filter_len: Adaptive filter length in samples. At 16kHz, 1600 = 100ms
                     of echo tail. Covers room reflections + speaker-to-mic delay.
        step_size: NLMS step size (0 < mu <= 1). Higher = faster adaptation,
                    more noise. 0.5 is a good default.
        block_size: Process this many samples at a time for efficiency.

    Returns:
        Estimated voice signal (mic with echo removed), float32.
    """
    n = min(len(mic), len(ref))
    if n < filter_len:
        return mic[:n].copy()

    output = np.zeros(n, dtype=np.float32)
    output[:filter_len] = mic[:filter_len]
    w = np.zeros(filter_len, dtype=np.float64)
    eps = 1e-8

    # Pre-allocate working buffer ONCE (reused every block iteration)
    ref_matrix = np.zeros((block_size, filter_len), dtype=np.float64)

    for start in range(filter_len, n, block_size):
        end = min(start + block_size, n)
        blen = end - start

        # Build reference matrix: each row is ref[i-L:i] reversed (convolution)
        # Use a slice for the final (possibly short) block — zero-cost view
        rm = ref_matrix[:blen]
        for j in range(blen):
            idx = start + j
            rm[j] = ref[idx - filter_len:idx][::-1]

        # Filter output = estimated echo for this block
        echo_est = rm @ w

        # Error = mic - echo estimate ≈ voice
        mic_block = mic[start:end].astype(np.float64)
        error = mic_block - echo_est
        output[start:end] = error.astype(np.float32)

        # NLMS weight update using block mean gradient
        norms = np.sum(rm ** 2, axis=1, keepdims=True) + eps
        gradients = rm * (error[:, np.newaxis] / norms)
        w += step_size * np.mean(gradients, axis=0)

    return output


class StreamingAEC:
    """Real-time NLMS echo canceller for meeting pipelines.

    Maintains adaptive filter weights across calls so echo cancellation
    improves over time. System audio is fed as reference; mic audio is
    cleaned of speaker bleedthrough before going to VAD.
    """

    def __init__(self, filter_len: int = 1600, step_size: float = 0.3,
                 block_size: int = 64):
        self._L = filter_len
        self._mu = step_size
        self._block_size = block_size
        self._w = np.zeros(filter_len, dtype=np.float64)
        self._eps = 1e-8

        # Reference buffer: keeps recent system audio for alignment
        self._ref_buf = np.zeros(filter_len, dtype=np.float32)
        self._lock = threading.Lock()

    def feed_reference(self, chunk: np.ndarray):
        """Buffer system audio as echo reference. Called from SCK thread."""
        flat = chunk.flatten().astype(np.float32)
        with self._lock:
            self._ref_buf = np.concatenate([self._ref_buf, flat])
            # Keep at most 2s + filter_len to bound memory
            max_keep = 32000 + self._L
            if len(self._ref_buf) > max_keep:
                self._ref_buf = self._ref_buf[-max_keep:]

    def process(self, mic_chunk: np.ndarray) -> np.ndarray:
        """Remove echo from mic chunk. Called from sounddevice callback thread.

        Returns cleaned audio, same length as mic_chunk.
        """
        n = len(mic_chunk)
        L = self._L
        bs = self._block_size

        with self._lock:
            # We need L + n reference samples aligned with this mic block.
            # Use the most recent reference audio available.
            needed = L + n
            if len(self._ref_buf) < needed:
                ref = np.zeros(needed, dtype=np.float32)
                avail = len(self._ref_buf)
                ref[-avail:] = self._ref_buf
            else:
                ref = self._ref_buf[-needed:]

        # Block NLMS — same algorithm as batch nlms_echo_cancel but
        # filter weights persist across calls.
        output = np.zeros(n, dtype=np.float32)
        ref_matrix = np.zeros((bs, L), dtype=np.float64)

        for start in range(0, n, bs):
            end = min(start + bs, n)
            blen = end - start

            rm = ref_matrix[:blen]
            for j in range(blen):
                idx = L + start + j
                rm[j] = ref[idx - L:idx][::-1]

            echo_est = rm @ self._w
            mic_block = mic_chunk[start:end].astype(np.float64)
            error = mic_block - echo_est
            output[start:end] = error.astype(np.float32)

            norms = np.sum(rm ** 2, axis=1, keepdims=True) + self._eps
            gradients = rm * (error[:, np.newaxis] / norms)
            self._w += self._mu * np.mean(gradients, axis=0)

        return output

    def reset(self):
        """Reset filter weights and reference buffer."""
        self._w[:] = 0.0
        with self._lock:
            self._ref_buf = np.zeros(self._L, dtype=np.float32)


def noise_gate(
    signal: np.ndarray,
    sample_rate: int = 16000,
    frame_ms: int = 20,
    percentile: float = 25.0,
    threshold_factor: float = 3.0,
) -> np.ndarray:
    """Suppress low-amplitude frames that are likely residual echo, not speech.

    Estimates the noise floor from the quietest frames, then smoothly
    attenuates anything below `noise_floor * threshold_factor`.
    Speech frames (louder) pass through unchanged.

    Args:
        signal: Audio signal, float32.
        sample_rate: Sample rate in Hz.
        frame_ms: Frame length in milliseconds for RMS analysis.
        percentile: Percentile of frame energies used as noise floor estimate.
            25th percentile captures the quieter (non-speech) frames.
        threshold_factor: Multiply noise floor by this to get the gate threshold.
            Higher = more aggressive gating. 3.0 is a safe default.

    Returns:
        Gated signal, float32, same length as input.
    """
    frame_len = int(sample_rate * frame_ms / 1000)
    n_frames = len(signal) // frame_len
    if n_frames == 0:
        return signal.copy()

    # Compute RMS energy per frame
    frames = signal[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))

    # Adaptive threshold from noise floor
    noise_floor = np.percentile(rms, percentile)
    loud_level = np.percentile(rms, 75)
    threshold = noise_floor * threshold_factor

    # If signal has uniform amplitude (no clear quiet vs loud distinction),
    # there's nothing to gate — it's all speech or all silence.
    if threshold < 1e-8 or noise_floor > loud_level * 0.5:
        return signal.copy()

    # Apply soft gain: quadratic curve from 0 (silence) to 1 (at threshold)
    output = signal.copy()
    for i in range(n_frames):
        if rms[i] < threshold:
            gain = (rms[i] / threshold) ** 2
            start = i * frame_len
            end = start + frame_len
            output[start:end] *= gain

    return output
