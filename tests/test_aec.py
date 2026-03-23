# tests/test_aec.py
import numpy as np
from aec import nlms_echo_cancel, noise_gate


def test_silent_ref_passes_mic_through():
    """When system audio is silent, mic signal passes through unchanged."""
    mic = np.random.randn(8000).astype(np.float32) * 0.1
    ref = np.zeros(8000, dtype=np.float32)
    out = nlms_echo_cancel(mic, ref)
    # Output should be very close to mic (no echo to remove)
    np.testing.assert_allclose(out, mic, atol=1e-4)


def test_cancels_echo_of_known_signal():
    """Echo of a known signal should be significantly reduced."""
    np.random.seed(42)
    n = 16000  # 1 second at 16kHz

    # System audio: a tone
    ref = (np.sin(2 * np.pi * 440 * np.arange(n) / 16000) * 0.3).astype(np.float32)

    # Simulate echo: delayed and attenuated copy of ref
    echo = np.zeros(n, dtype=np.float32)
    delay = 100  # samples
    echo[delay:] = ref[:-delay] * 0.5

    # Voice: different frequency
    voice = (np.sin(2 * np.pi * 200 * np.arange(n) / 16000) * 0.2).astype(np.float32)

    # Mic = voice + echo
    mic = voice + echo

    out = nlms_echo_cancel(mic, ref, filter_len=800, step_size=0.5)

    # After convergence (skip first 2000 samples), echo should be reduced
    converged = out[2000:]
    mic_converged = mic[2000:]
    voice_converged = voice[2000:]

    # Output should be closer to voice than the raw mic was
    error_before = np.mean((mic_converged - voice_converged) ** 2)
    error_after = np.mean((converged - voice_converged) ** 2)
    assert error_after < error_before * 0.5  # at least 50% reduction


def test_short_signal_returns_copy():
    """Signals shorter than filter length just return a copy of mic."""
    mic = np.ones(100, dtype=np.float32)
    ref = np.ones(100, dtype=np.float32)
    out = nlms_echo_cancel(mic, ref, filter_len=200)
    np.testing.assert_array_equal(out, mic)


def test_different_length_signals():
    """Handles mic and ref of different lengths (uses shorter)."""
    mic = np.random.randn(10000).astype(np.float32) * 0.1
    ref = np.random.randn(8000).astype(np.float32) * 0.1
    out = nlms_echo_cancel(mic, ref)
    assert len(out) == 8000  # min of both lengths


def test_noise_gate_suppresses_quiet_frames():
    """Quiet frames next to loud frames should be attenuated."""
    sr = 16000
    n = sr  # 1 second
    signal = np.zeros(n, dtype=np.float32)
    # First half: quiet residual noise
    signal[:n // 2] = np.random.randn(n // 2).astype(np.float32) * 0.003
    # Second half: loud speech
    signal[n // 2:] = (np.sin(2 * np.pi * 300 * np.arange(n // 2) / sr) * 0.3).astype(np.float32)
    gated = noise_gate(signal, sample_rate=sr)
    # Quiet half should be suppressed
    quiet_energy_before = np.sum(signal[:n // 2] ** 2)
    quiet_energy_after = np.sum(gated[:n // 2] ** 2)
    assert quiet_energy_after < quiet_energy_before * 0.5


def test_noise_gate_preserves_loud_frames():
    """Loud speech frames should pass through unchanged."""
    n = 16000
    # Loud signal well above any noise floor
    loud = (np.sin(2 * np.pi * 300 * np.arange(n) / 16000) * 0.5).astype(np.float32)
    gated = noise_gate(loud)
    np.testing.assert_allclose(gated, loud, atol=1e-6)


def test_noise_gate_mixed_signal():
    """Speech burst in quiet signal: speech preserved, silence suppressed."""
    sr = 16000
    n = sr * 2  # 2 seconds
    signal = np.zeros(n, dtype=np.float32)
    # 0.5s speech burst in the middle
    burst_start = int(0.75 * sr)
    burst_end = int(1.25 * sr)
    signal[burst_start:burst_end] = (
        np.sin(2 * np.pi * 300 * np.arange(burst_end - burst_start) / sr) * 0.3
    ).astype(np.float32)
    # Add low-level noise everywhere
    signal += np.random.randn(n).astype(np.float32) * 0.002

    gated = noise_gate(signal, sample_rate=sr)
    # Speech region should be mostly preserved
    speech_energy = np.sum(gated[burst_start:burst_end] ** 2)
    assert speech_energy > 0.1
    # Quiet regions should be suppressed
    quiet_energy = np.sum(gated[:burst_start] ** 2)
    original_quiet_energy = np.sum(signal[:burst_start] ** 2)
    assert quiet_energy < original_quiet_energy * 0.5
