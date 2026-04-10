# meeting_recorder.py
"""Dual-stream audio capture for meeting transcription."""
import os
import threading
import wave

import numpy as np
import sounddevice as sd

from aec import StreamingAEC
from system_audio import SystemAudioCapture


class MeetingRecorder:
    """Captures system audio (always) and mic audio (full mode only) for meetings."""

    FSYNC_INTERVAL_FRAMES = 16000 * 5

    def __init__(self, mode: str = "listen", app_bundle_id: str | None = None,
                 sample_rate: int = 16000):
        self.mode = mode  # "listen" or "full"
        self.sample_rate = sample_rate
        self.is_recording = False
        self.is_paused = False

        self._app_bundle_id = app_bundle_id
        self._sys_capture = SystemAudioCapture(sample_rate=sample_rate)
        self._mic_recorder = None  # Only in full mode
        self._mic_stream = None

        self._system_wav_path = None
        self._mic_wav_path = None
        self._sys_wav_file = None
        self._mic_wav_file = None
        self._sys_total_frames = 0
        self._mic_total_frames = 0
        self._sys_frames_since_fsync = 0
        self._mic_frames_since_fsync = 0
        self._lock = threading.Lock()

        # Streaming AEC for full mode — removes speaker bleedthrough from mic
        self._aec = StreamingAEC() if mode == "full" else None

        # Callbacks for VAD pipelines
        self.on_system_audio = None  # fn(np.ndarray float32)
        self.on_mic_audio = None  # fn(np.ndarray float32)

    def start(self, system_wav_path: str, mic_wav_path: str | None = None):
        """Start recording. system_wav_path is always required.
        mic_wav_path is required in full mode."""
        if self.is_recording:
            return

        self._system_wav_path = system_wav_path
        os.makedirs(os.path.dirname(system_wav_path), exist_ok=True)

        # Wire real-time system audio callback for VAD
        self._sys_capture.on_audio_chunk = self._on_system_chunk

        # Start system audio capture
        self._sys_capture.start(app_bundle_id=self._app_bundle_id)

        # In full mode, start mic recording too
        if self.mode == "full" and mic_wav_path:
            self._mic_wav_path = mic_wav_path
            os.makedirs(os.path.dirname(mic_wav_path), exist_ok=True)
            self._mic_wav_file = wave.open(mic_wav_path, "wb")
            self._mic_wav_file.setnchannels(1)
            self._mic_wav_file.setsampwidth(2)
            self._mic_wav_file.setframerate(self.sample_rate)
            self._mic_total_frames = 0
            self._mic_frames_since_fsync = 0

            self._mic_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=512,
                callback=self._mic_callback,
            )
            self._mic_stream.start()
            self._mic_recorder = self._mic_stream

        self.is_recording = True
        self.is_paused = False

    def stop(self) -> dict:
        """Stop recording and return paths to audio files."""
        if not self.is_recording and not self.is_paused:
            return {"system_audio_path": None, "mic_audio_path": None}

        self.is_recording = False
        self.is_paused = False

        # Stop system audio and write to WAV
        sys_audio = self._sys_capture.stop()
        if len(sys_audio) > 0 and self._system_wav_path:
            self._write_wav(self._system_wav_path, sys_audio)

        # Stop mic
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None
            self._mic_recorder = None

        with self._lock:
            if self._mic_wav_file:
                try:
                    self._mic_wav_file._file.flush()
                    os.fsync(self._mic_wav_file._file.fileno())
                except OSError:
                    pass
                self._mic_wav_file.close()
                self._mic_wav_file = None

        return {
            "system_audio_path": self._system_wav_path,
            "mic_audio_path": self._mic_wav_path,
        }

    def reconnect_stream(self) -> bool:
        """Swap the mic input stream to the current OS default device.

        Only applies to full mode (where a mic stream exists). System audio
        via ScreenCaptureKit is unaffected by input device changes. No-op
        if not currently recording or not in full mode.
        """
        if not self.is_recording:
            return False
        if self.mode != "full":
            return False

        old_stream = self._mic_stream
        self._mic_stream = None
        self._mic_recorder = None

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
                callback=self._mic_callback,
            )
            new_stream.start()
        except Exception as e:
            print(f"MeetingRecorder.reconnect_stream failed: {e}")
            return False

        self._mic_stream = new_stream
        self._mic_recorder = new_stream
        return True

    def pause(self):
        """Pause recording."""
        if not self.is_recording:
            return
        self.is_recording = False
        self.is_paused = True

        # Pause mic stream if active
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None

    def resume(self):
        """Resume recording after pause."""
        if not self.is_paused:
            return
        self.is_recording = True
        self.is_paused = False

        # Resume mic stream in full mode
        if self.mode == "full" and self._mic_wav_file:
            self._mic_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=512,
                callback=self._mic_callback,
            )
            self._mic_stream.start()
            self._mic_recorder = self._mic_stream

    def _on_system_chunk(self, audio: np.ndarray):
        """Called when system audio chunk is available."""
        # Feed reference to AEC so mic echo can be cancelled
        if self._aec is not None:
            self._aec.feed_reference(audio)
        if self.on_system_audio:
            self.on_system_audio(audio)

    def _on_mic_chunk(self, audio: np.ndarray):
        """Called when mic audio chunk is available."""
        if self.on_mic_audio:
            self.on_mic_audio(audio)

    def _mic_callback(self, indata, frames, time_info, status):
        """sounddevice callback for mic audio."""
        audio = indata[:, 0].copy()

        # Write to WAV
        int16_data = (audio * 32767).astype(np.int16)
        with self._lock:
            if self._mic_wav_file:
                try:
                    self._mic_wav_file.writeframes(int16_data.tobytes())
                    self._mic_total_frames += len(int16_data)
                    self._mic_frames_since_fsync += len(int16_data)
                    if self._mic_frames_since_fsync >= self.FSYNC_INTERVAL_FRAMES:
                        self._mic_wav_file._file.flush()
                        os.fsync(self._mic_wav_file._file.fileno())
                        self._mic_frames_since_fsync = 0
                except OSError:
                    pass

        # Apply AEC to remove speaker bleedthrough before VAD
        cleaned = self._aec.process(audio) if self._aec is not None else audio
        self._on_mic_chunk(cleaned)

    @staticmethod
    def _write_wav(path: str, audio: np.ndarray):
        """Write float32 audio array to WAV file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        int16_data = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(int16_data.tobytes())
