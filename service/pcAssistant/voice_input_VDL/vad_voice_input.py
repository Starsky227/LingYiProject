import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - runtime environment dependent
    sd = None

try:
    import torch  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]

try:
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    WhisperModel = None  # type: ignore[assignment]


@dataclass
class _VadConfig:
    sample_rate: int = 16000
    channels: int = 1
    frame_ms: int = 32          # Silero VAD requires 512 samples = 32 ms @ 16 kHz
    pre_roll_ms: int = 320      # 10 frames × 32 ms
    end_silence_ms: int = 700
    min_speech_ms: int = 320    # at least 10 frames
    max_speech_ms: int = 15000
    vad_threshold: float = 0.5  # Silero speech-probability threshold


class VoiceInputVDLService:
    """Microphone VAD capture service with Silero VAD + faster-whisper transcription."""

    def __init__(
        self,
        text_callback: Callable[[str], None],
        whisper_model_size: str = "small",
        whisper_device: str = "cpu",
        whisper_compute_type: str = "int8",
        language: str = "zh",
        mic_device: Optional[int] = None,
    ):
        self._text_callback = text_callback
        self._whisper_model_size = whisper_model_size
        self._whisper_device = whisper_device
        self._whisper_compute_type = whisper_compute_type
        self._language = language
        self._mic_device = mic_device
        self._vad_cfg = _VadConfig()

        self._running = False
        self._stream = None
        self._silero: Optional[object] = None
        self._whisper: Optional[object] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._transcribe_thread: Optional[threading.Thread] = None
        self._audio_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue(maxsize=16)
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running

    def _load_models(self) -> bool:
        """Load Silero VAD and faster-whisper on first call; subsequent calls are no-ops."""
        if torch is None:
            logger.error("[VoiceVDL] torch is not available. Please install torch.")
            return False
        if WhisperModel is None:
            logger.error("[VoiceVDL] faster-whisper is not available. Please install faster-whisper.")
            return False

        if self._silero is None:
            logger.info("[VoiceVDL] loading Silero VAD model ...")
            try:
                self._silero, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    force_reload=False,
                    trust_repo=True,
                )
                self._silero.eval()
                logger.info("[VoiceVDL] Silero VAD ready.")
            except Exception as e:
                logger.error(f"[VoiceVDL] failed to load Silero VAD: {e}")
                return False

        if self._whisper is None:
            logger.info(f"[VoiceVDL] loading faster-whisper '{self._whisper_model_size}' ...")
            try:
                self._whisper = WhisperModel(
                    self._whisper_model_size,
                    device=self._whisper_device,
                    compute_type=self._whisper_compute_type,
                )
                logger.info("[VoiceVDL] faster-whisper ready.")
            except Exception as e:
                logger.error(f"[VoiceVDL] failed to load faster-whisper: {e}")
                return False

        return True

    def start(self) -> bool:
        if self._running:
            return True
        if sd is None:
            logger.error("[VoiceVDL] sounddevice is not available. Please install sounddevice.")
            return False
        if not self._load_models():
            return False

        self._stop_event.clear()
        self._audio_queue = queue.Queue(maxsize=16)

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="voice-vdl-capture",
            daemon=True,
        )
        self._transcribe_thread = threading.Thread(
            target=self._transcribe_loop,
            name="voice-vdl-transcribe",
            daemon=True,
        )
        self._capture_thread.start()
        self._transcribe_thread.start()
        self._running = True
        logger.info("[VoiceVDL] voice input started")
        return True

    def stop(self) -> bool:
        if not self._running:
            return True

        self._stop_event.set()
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            pass

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)
        if self._transcribe_thread and self._transcribe_thread.is_alive():
            self._transcribe_thread.join(timeout=3.0)

        self._capture_thread = None
        self._transcribe_thread = None
        self._stream = None
        self._running = False
        logger.info("[VoiceVDL] voice input stopped")
        return True

    def _capture_loop(self) -> None:
        cfg = self._vad_cfg
        frame_samples = int(cfg.sample_rate * cfg.frame_ms / 1000)  # 512 samples @ 16 kHz
        pre_roll_frames = max(1, int(cfg.pre_roll_ms / cfg.frame_ms))
        end_silence_frames = max(1, int(cfg.end_silence_ms / cfg.frame_ms))
        min_speech_frames = max(1, int(cfg.min_speech_ms / cfg.frame_ms))
        max_speech_frames = max(1, int(cfg.max_speech_ms / cfg.frame_ms))

        pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_frames)
        in_speech = False
        silence_frames = 0
        speech_frames = 0
        speech_segments: list[np.ndarray] = []

        self._silero.reset_states()  # clear LSTM state before stream starts

        def _audio_callback(indata, frames, _time_info, status):
            nonlocal in_speech, silence_frames, speech_frames, speech_segments
            if status:
                logger.debug(f"[VoiceVDL] input stream status: {status}")
            if frames <= 0 or self._stop_event.is_set():
                return

            mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
            frame = mono.astype(np.float32, copy=True)
            if len(frame) != frame_samples:
                return

            # Silero VAD neural inference
            frame_tensor = torch.from_numpy(frame).unsqueeze(0)
            with torch.no_grad():
                speech_prob = float(self._silero(frame_tensor, cfg.sample_rate))
            is_speech = speech_prob >= cfg.vad_threshold

            if not in_speech:
                pre_roll.append(frame)
                if is_speech:
                    in_speech = True
                    silence_frames = 0
                    speech_frames = 0
                    speech_segments = list(pre_roll)
            else:
                speech_segments.append(frame)
                speech_frames += 1
                if is_speech:
                    silence_frames = 0
                else:
                    silence_frames += 1

                should_flush = silence_frames >= end_silence_frames or speech_frames >= max_speech_frames
                if should_flush:
                    in_speech = False
                    if speech_frames >= min_speech_frames:
                        audio = np.concatenate(speech_segments, axis=0)
                        try:
                            self._audio_queue.put_nowait(audio)
                        except queue.Full:
                            logger.warning("[VoiceVDL] audio queue full, dropping segment")
                    speech_segments = []
                    speech_frames = 0
                    silence_frames = 0
                    self._silero.reset_states()  # reset LSTM state after each segment

        try:
            with sd.InputStream(
                samplerate=cfg.sample_rate,
                channels=cfg.channels,
                dtype="float32",
                blocksize=frame_samples,
                callback=_audio_callback,
                device=self._mic_device,
            ) as stream:
                self._stream = stream
                while not self._stop_event.is_set():
                    time.sleep(0.05)
        except Exception as e:
            logger.exception(f"[VoiceVDL] capture loop failed: {e}")
            self._stop_event.set()

    def _transcribe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if payload is None:
                break

            text = self._transcribe_segment(payload)
            if text:
                try:
                    self._text_callback(text)
                except Exception as e:
                    logger.error(f"[VoiceVDL] text callback failed: {e}")

    def _transcribe_segment(self, audio: np.ndarray) -> str:
        """Transcribe a float32 audio array using faster-whisper."""
        try:
            segments, _ = self._whisper.transcribe(
                audio,
                language=self._language,
                beam_size=5,
                vad_filter=False,  # VAD already applied upstream
            )
            text = "".join(s.text for s in segments).strip()
            logger.debug(f"[VoiceVDL] transcript: {text}")
            return text
        except Exception as e:
            logger.error(f"[VoiceVDL] transcription failed: {e}")
            return ""
