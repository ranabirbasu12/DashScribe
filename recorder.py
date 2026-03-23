# recorder.py
import tempfile
import wave
import threading
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 16000


def get_wav_duration(path: str) -> float:
    """Return duration of a WAV file in seconds."""
    with wave.open(path, "r") as wf:
        return wf.getnframes() / wf.getframerate()


class AudioRecorder:
    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.channels = 1
        self.is_recording = False
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._sys_capture = None
        self._lock = threading.Lock()
        self.on_amplitude = None
        self.on_vad_chunk = None

    def _audio_callback(self, indata, frames, time, status):
        if status:
            print(f"Audio status: {status}")
        chunk_copy = None
        with self._lock:
            if self.is_recording:
                chunk_copy = indata.copy()
                self._chunks.append(chunk_copy)
        if chunk_copy is not None:
            if self.on_amplitude is not None:
                rms = float(np.sqrt(np.mean(chunk_copy ** 2)))
                self.on_amplitude(rms)
            if self.on_vad_chunk is not None:
                self.on_vad_chunk(chunk_copy)

    def start(self):
        with self._lock:
            if self.is_recording:
                return
            self._chunks = []
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.float32,
                callback=self._audio_callback,
            )

        # Start system audio capture for echo cancellation
        try:
            from system_audio import SystemAudioCapture
            self._sys_capture = SystemAudioCapture(sample_rate=self.sample_rate)
            self._sys_capture.start()
        except Exception as e:
            print(f"System audio capture unavailable: {e}")
            self._sys_capture = None

        try:
            self._stream.start()
        except Exception:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
            self._stream = None
            if self._sys_capture is not None:
                try:
                    self._sys_capture.stop()
                except Exception:
                    pass
            self._sys_capture = None
            raise

        with self._lock:
            self.is_recording = True

    def stop(self) -> str:
        with self._lock:
            self.is_recording = False
            stream = self._stream
            self._stream = None
            sys_capture = self._sys_capture
            self._sys_capture = None
            self.on_vad_chunk = None
            chunks = self._chunks
            self._chunks = []

        if stream is not None:
            stream.stop()
            stream.close()

        # Get system audio reference
        sys_audio = None
        if sys_capture is not None:
            try:
                sys_audio = sys_capture.stop()
            except Exception:
                pass

        if not chunks:
            return ""

        audio = np.concatenate(chunks, axis=0).flatten()
        del chunks

        # Apply echo cancellation if we have system audio
        if sys_audio is not None and len(sys_audio) > 0:
            try:
                from aec import nlms_echo_cancel, noise_gate
                audio = nlms_echo_cancel(audio, sys_audio)
                audio = noise_gate(audio, sample_rate=self.sample_rate)
            except Exception as e:
                print(f"AEC failed, using raw audio: {e}")
        del sys_audio

        audio_int16 = np.int16(np.clip(audio, -1.0, 1.0) * 32767)
        del audio

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wavfile.write(tmp.name, self.sample_rate, audio_int16)
        tmp.close()
        del audio_int16
        return tmp.name

    def stop_raw(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Stop recording and return raw mic + system audio without AEC.

        Used by the streaming pipeline, which applies AEC per-segment.
        """
        with self._lock:
            self.is_recording = False
            stream = self._stream
            self._stream = None
            sys_capture = self._sys_capture
            self._sys_capture = None
            self.on_vad_chunk = None
            chunks = self._chunks
            self._chunks = []

        if stream is not None:
            stream.stop()
            stream.close()

        sys_audio = None
        if sys_capture is not None:
            try:
                sys_audio = sys_capture.stop()
            except Exception:
                pass

        if not chunks:
            return None, None

        mic_audio = np.concatenate(chunks, axis=0).flatten()
        del chunks

        return mic_audio, sys_audio

    def get_sys_audio_chunks(self) -> list | None:
        """Return reference to system audio chunk list for live AEC alignment."""
        if self._sys_capture is not None:
            return self._sys_capture._chunks
        return None
