# lecture_recorder.py
"""Long-running microphone capture with incremental WAV file writing."""
import os
import struct
import threading
import wave

import numpy as np
import sounddevice as sd


class LectureRecorder:
    """Captures mic audio for ClassNote lectures, streaming to WAV file."""

    # fsync every 5 seconds of audio to guarantee durability
    FSYNC_INTERVAL_FRAMES = 16000 * 5  # 80,000 frames at 16kHz

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.is_recording = False
        self.is_paused = False
        self.on_vad_chunk = None  # Callback for VAD: fn(np.ndarray float32)
        self.on_write_error = None  # Callback for disk errors: fn(str)

        self._stream = None
        self._wav_file = None
        self._wav_path = None
        self._total_frames = 0
        self._frames_since_fsync = 0
        self._lock = threading.Lock()

    def start(self, wav_path: str):
        """Start recording to the given WAV file path."""
        if self.is_recording:
            return
        os.makedirs(os.path.dirname(wav_path), exist_ok=True)
        self._wav_path = wav_path
        self._wav_file = wave.open(wav_path, "wb")
        self._wav_file.setnchannels(1)
        self._wav_file.setsampwidth(2)  # 16-bit
        self._wav_file.setframerate(self.sample_rate)
        self._total_frames = 0

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=512,
            callback=self._audio_callback,
        )
        self._stream.start()
        self.is_recording = True
        self.is_paused = False

    def stop(self):
        """Stop recording and finalize the WAV file."""
        if not self.is_recording and not self.is_paused:
            return
        self.is_recording = False
        self.is_paused = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if self._wav_file:
                try:
                    self._wav_file._file.flush()
                    os.fsync(self._wav_file._file.fileno())
                except OSError:
                    pass
                self._wav_file.close()
                self._wav_file = None

    def pause(self):
        """Pause recording (stop mic stream, keep WAV open)."""
        if not self.is_recording:
            return
        self.is_recording = False
        self.is_paused = True
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def resume(self):
        """Resume recording after pause."""
        if not self.is_paused:
            return
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=512,
            callback=self._audio_callback,
        )
        self._stream.start()
        self.is_recording = True
        self.is_paused = False

    def reconnect_stream(self) -> bool:
        """Swap the input stream to the current OS default device.

        Called by DeviceMonitor when the default input device changes.
        The WAV file stays open across the reconnect. No-op if not recording.
        """
        if not self.is_recording:
            return False

        old_stream = self._stream
        self._stream = None

        if old_stream is not None:
            try:
                old_stream.stop()
            except Exception:
                pass
            try:
                old_stream.close()
            except Exception:
                pass

        try:
            new_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=512,
                callback=self._audio_callback,
            )
            new_stream.start()
        except Exception as e:
            print(f"LectureRecorder.reconnect_stream failed: {e}")
            self.is_recording = False
            return False

        self._stream = new_stream
        return True

    @property
    def elapsed_seconds(self) -> float:
        return self._total_frames / self.sample_rate

    @property
    def wav_path(self) -> str | None:
        return self._wav_path

    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice callback -- write to WAV and forward to VAD."""
        if status and self.on_write_error:
            status_str = str(status)
            if "input" in status_str.lower():
                self.on_write_error("Microphone issue detected — audio may have gaps")

        audio = indata[:, 0].copy()  # mono float32

        # Write to WAV as int16
        int16_data = (audio * 32767).astype(np.int16)
        with self._lock:
            if self._wav_file:
                try:
                    self._wav_file.writeframes(int16_data.tobytes())
                    self._total_frames += len(int16_data)
                    self._frames_since_fsync += len(int16_data)
                    # Periodic fsync to guarantee data reaches disk
                    if self._frames_since_fsync >= self.FSYNC_INTERVAL_FRAMES:
                        self._wav_file._file.flush()
                        os.fsync(self._wav_file._file.fileno())
                        self._frames_since_fsync = 0
                except OSError as e:
                    if self.on_write_error:
                        self.on_write_error(f"Audio write failed: {e}")

        # Forward to VAD
        if self.on_vad_chunk:
            self.on_vad_chunk(audio)

    @staticmethod
    def recover_wav(wav_path: str):
        """Fix WAV header for a file with broken/incomplete header but valid PCM data."""
        with open(wav_path, "rb") as f:
            data = f.read()

        # Find the data chunk
        data_pos = data.find(b"data")
        if data_pos < 0:
            return
        pcm_start = data_pos + 8  # skip "data" + 4-byte size
        pcm_data = data[pcm_start:]
        data_size = len(pcm_data)
        file_size = 36 + data_size  # RIFF header(12) + fmt(24) + data header(8) + pcm

        with open(wav_path, "wb") as f:
            # RIFF header
            f.write(b"RIFF")
            f.write(struct.pack("<I", file_size - 8))
            f.write(b"WAVE")
            # fmt chunk (PCM, mono, 16kHz, 16-bit)
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16))
            # data chunk
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(pcm_data)
