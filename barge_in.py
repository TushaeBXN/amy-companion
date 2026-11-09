"""Barge-in detection for voice mode.

While Amy is speaking (voice.speak_streamed with interruptible=True), a
MicMonitor runs VAD on a live mic stream in the background. The moment it
hears the user start talking, playback is killed so she stops immediately
instead of finishing her sentence.
"""
import threading

import pyaudio
import webrtcvad


class MicMonitor:
    def __init__(self, sample_rate=16000, chunk_ms=30, aggressiveness=2):
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_ms / 1000)
        self._vad = webrtcvad.Vad(aggressiveness)
        self._stop = threading.Event()
        self._speech = threading.Event()
        self._thread = None
        self._audio = None
        self._stream = None

    def start(self):
        self._stop.clear()
        self._speech.clear()
        try:
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
                input=True, frames_per_buffer=self.chunk_size,
            )
        except Exception as e:
            print(f"  [barge-in] mic unavailable: {e}")
            self._audio = None
            self._stream = None
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while not self._stop.is_set():
                chunk = self._stream.read(self.chunk_size, exception_on_overflow=False)
                if self._vad.is_speech(chunk, self.sample_rate):
                    self._speech.set()
                    return
        except Exception:
            return

    def interrupted(self):
        return self._speech.is_set()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass
