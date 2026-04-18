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
    speech_confirm_ms: int = 1000  # 持续语音超过此时长才确认为真正发言（触发打断）
    max_speech_ms: int = 15000
    vad_threshold: float = 0.65  # Silero speech-probability threshold


class VoiceInputVDLService:
    """Microphone VAD capture service with Silero VAD + faster-whisper transcription."""

    def __init__(
        self,
        text_callback: Callable[[str], None],
        speech_start_callback: Optional[Callable[[], None]] = None,
        whisper_model_size: str = "small",
        whisper_device: str = "cpu",
        whisper_compute_type: str = "int8",
        language: str = "zh",
        mic_device: Optional[int] = None,
    ):
        self._text_callback = text_callback
        self._speech_start_callback = speech_start_callback
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
        self._vad_thread: Optional[threading.Thread] = None
        self._transcribe_thread: Optional[threading.Thread] = None
        self._raw_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue(maxsize=512)
        self._audio_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue(maxsize=16)
        self._stop_event = threading.Event()
        self._muted = threading.Event()  # TTS 播放期间静音麦克风输入

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

    @staticmethod
    def _find_physical_mic() -> Optional[int]:
        """自动检测物理麦克风设备，排除虚拟/回环/桌面音频设备。

        优先选择支持 16kHz 采样率的物理麦克风；
        同等条件下优先低延迟 API（DirectSound > MME）。
        WASAPI 通常不支持 16kHz 重采样，优先级较低。
        """
        if sd is None:
            return None

        # 虚拟/回环设备的关键词黑名单（小写匹配）
        _VIRTUAL_KEYWORDS = (
            "stereo mix", "立体声混音",
            "what u hear", "what you hear",
            "loopback", "回环",
            "virtual", "虚拟",
            "cable", "voicemeeter",
            "sound mapper", "声音映射",
            "主声音捕获", "primary sound capture",
            "wave out",
        )

        devices = sd.query_devices()
        host_apis = sd.query_hostapis()

        candidates = []
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] < 1:
                continue
            name_lower = dev["name"].lower()
            # 跳过虚拟/回环设备
            if any(kw in name_lower for kw in _VIRTUAL_KEYWORDS):
                continue
            api_name = host_apis[dev["hostapi"]]["name"]
            # 必须包含 "mic" / "麦克风" 关键词，确认是麦克风
            is_mic = ("mic" in name_lower or "麦克风" in name_lower)
            if not is_mic:
                continue
            # 检测是否支持 16kHz
            supports_16k = True
            try:
                sd.check_input_settings(device=idx, samplerate=16000, channels=1)
            except Exception:
                supports_16k = False
            # 排序优先级：支持16kHz(0) > 不支持(1)；同级内 DirectSound(0) > MME(1) > 其他(2)
            api_lower = api_name.lower()
            if "directsound" in api_lower:
                api_priority = 0
            elif "mme" in api_lower:
                api_priority = 1
            else:
                api_priority = 2
            sr_priority = 0 if supports_16k else 1
            candidates.append((sr_priority, api_priority, idx, dev["name"], api_name))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[0], x[1]))
        chosen = candidates[0]
        logger.info(f"[VoiceVDL] 自动选择麦克风: [{chosen[2]}] {chosen[3]} (api={chosen[4]})")
        return chosen[2]

    def _negotiate_sample_rate(self, desired: int) -> int:
        """检测设备是否支持目标采样率，不支持则回退到设备默认采样率。"""
        dev = self._mic_device
        try:
            sd.check_input_settings(device=dev, samplerate=desired, channels=self._vad_cfg.channels)
            return desired
        except Exception:
            pass
        # 回退到设备默认采样率
        try:
            info = sd.query_devices(dev) if dev is not None else sd.query_devices(sd.default.device[0])
            native = int(info["default_samplerate"])
            sd.check_input_settings(device=dev, samplerate=native, channels=self._vad_cfg.channels)
            return native
        except Exception:
            pass
        # 最后兜底：尝试常见采样率
        for sr in [44100, 48000, 22050, 32000]:
            try:
                sd.check_input_settings(device=dev, samplerate=sr, channels=self._vad_cfg.channels)
                return sr
            except Exception:
                continue
        return desired  # 均失败，让 InputStream 自行报错

    def start(self) -> bool:
        if self._running:
            return True
        if sd is None:
            logger.error("[VoiceVDL] sounddevice is not available. Please install sounddevice.")
            return False
        if not self._load_models():
            return False

        # 自动检测物理麦克风
        if self._mic_device is None:
            detected = self._find_physical_mic()
            if detected is not None:
                self._mic_device = detected
            else:
                logger.warning("[VoiceVDL] 未自动检测到物理麦克风，将使用系统默认输入设备")

        self._stop_event.clear()
        self._raw_queue = queue.Queue(maxsize=512)
        self._audio_queue = queue.Queue(maxsize=16)

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="voice-vdl-capture",
            daemon=True,
        )
        self._vad_thread = threading.Thread(
            target=self._vad_loop,
            name="voice-vdl-vad",
            daemon=True,
        )
        self._transcribe_thread = threading.Thread(
            target=self._transcribe_loop,
            name="voice-vdl-transcribe",
            daemon=True,
        )
        self._capture_thread.start()
        self._vad_thread.start()
        self._transcribe_thread.start()
        self._running = True
        logger.info("[VoiceVDL] voice input started")
        return True

    def stop(self) -> bool:
        if not self._running:
            return True

        self._stop_event.set()
        try:
            self._raw_queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            pass

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)
        if self._vad_thread and self._vad_thread.is_alive():
            self._vad_thread.join(timeout=2.0)
        if self._transcribe_thread and self._transcribe_thread.is_alive():
            self._transcribe_thread.join(timeout=3.0)

        self._capture_thread = None
        self._vad_thread = None
        self._transcribe_thread = None
        self._stream = None
        self._running = False
        logger.info("[VoiceVDL] voice input stopped")
        return True

    # ------------------------------------------------------------------ #
    #  静音控制 — TTS 播放期间暂停麦克风采集（信号隔离）
    # ------------------------------------------------------------------ #

    def mute(self) -> None:
        """静音麦克风输入，丢弃所有音频帧（TTS 播放时调用）"""
        if not self._muted.is_set():
            self._muted.set()
            # 清空已采集的帧，避免残留帧触发 VAD
            self._drain_queue(self._raw_queue)
            logger.debug("[VoiceVDL] mic muted")

    def unmute(self) -> None:
        """恢复麦克风输入"""
        if self._muted.is_set():
            self._muted.clear()
            logger.debug("[VoiceVDL] mic unmuted")

    @property
    def is_muted(self) -> bool:
        return self._muted.is_set()

    @staticmethod
    def _drain_queue(q: queue.Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _capture_loop(self) -> None:
        """Audio callback only enqueues raw frames — zero heavy computation."""
        cfg = self._vad_cfg
        target_sr = cfg.sample_rate          # 16000 — VAD/Whisper 需要的采样率
        target_frame = int(target_sr * cfg.frame_ms / 1000)  # 512 @ 16 kHz

        # 检测设备实际支持的采样率
        hw_sr = self._negotiate_sample_rate(target_sr)
        need_resample = (hw_sr != target_sr)
        hw_frame = int(hw_sr * cfg.frame_ms / 1000) if need_resample else target_frame

        if need_resample:
            logger.info(f"[VoiceVDL] 设备原生采样率 {hw_sr}Hz，将重采样到 {target_sr}Hz")

        _status_error_count = [0]  # mutable counter in closure

        def _audio_callback(indata, frames, _time_info, status):
            if status:
                logger.warning(f"[VoiceVDL] input stream status: {status}")
                _status_error_count[0] += 1
            if frames <= 0 or self._stop_event.is_set():
                return
            # 静音期间丢弃所有帧（信号隔离：TTS 播放时不采集）
            if self._muted.is_set():
                return
            mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
            chunk = mono.astype(np.float32, copy=True)
            if need_resample:
                # 线性插值重采样到 16kHz
                src_len = len(chunk)
                dst_len = int(round(src_len * target_sr / hw_sr))
                if dst_len < 1:
                    return
                chunk = np.interp(
                    np.linspace(0, src_len - 1, dst_len, dtype=np.float32),
                    np.arange(src_len, dtype=np.float32),
                    chunk,
                ).astype(np.float32)
            if len(chunk) != target_frame:
                return
            try:
                self._raw_queue.put_nowait(chunk)
            except queue.Full:
                pass  # drop oldest-ish frame rather than block audio thread

        _max_retries = 3
        for _attempt in range(_max_retries):
            if self._stop_event.is_set():
                break
            try:
                logger.info(f"[VoiceVDL] opening InputStream (attempt {_attempt + 1}/{_max_retries}), device={self._mic_device}, sr={hw_sr}")
                _status_error_count[0] = 0
                with sd.InputStream(
                    samplerate=hw_sr,
                    channels=cfg.channels,
                    dtype="float32",
                    blocksize=hw_frame,
                    callback=_audio_callback,
                    device=self._mic_device,
                ) as stream:
                    self._stream = stream
                    _health_interval = 0
                    while not self._stop_event.is_set():
                        time.sleep(0.05)
                        _health_interval += 1
                        if _health_interval >= 200:  # ~10 seconds
                            _health_interval = 0
                            if not stream.active:
                                logger.error("[VoiceVDL] InputStream is no longer active! Restarting...")
                                break
                            if _status_error_count[0] > 50:
                                logger.warning(f"[VoiceVDL] excessive stream errors ({_status_error_count[0]}), restarting stream...")
                                break
                            _status_error_count[0] = 0
            except Exception as e:
                logger.exception(f"[VoiceVDL] capture loop failed (attempt {_attempt + 1}): {e}")
            if _attempt < _max_retries - 1 and not self._stop_event.is_set():
                logger.info("[VoiceVDL] retrying stream in 1s ...")
                time.sleep(1.0)
                continue
            if not self._stop_event.is_set():
                logger.error("[VoiceVDL] all retry attempts exhausted, stopping capture")
                self._stop_event.set()

    def _vad_loop(self) -> None:
        """Dedicated thread for Silero VAD inference — keeps audio callback fast."""
        cfg = self._vad_cfg
        pre_roll_frames = max(1, int(cfg.pre_roll_ms / cfg.frame_ms))
        end_silence_frames = max(1, int(cfg.end_silence_ms / cfg.frame_ms))
        min_speech_frames = max(1, int(cfg.min_speech_ms / cfg.frame_ms))
        speech_confirm_frames = max(1, int(cfg.speech_confirm_ms / cfg.frame_ms))
        max_speech_frames = max(1, int(cfg.max_speech_ms / cfg.frame_ms))

        pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_frames)
        in_speech = False
        speech_confirmed = False  # 是否已确认为真正发言（已触发打断）
        silence_frames = 0
        speech_frames = 0
        speech_segments: list[np.ndarray] = []

        self._silero.reset_states()

        while not self._stop_event.is_set():
            try:
                frame = self._raw_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if frame is None:
                break

            # Silero VAD inference (safe in dedicated thread)
            frame_tensor = torch.from_numpy(frame).unsqueeze(0)
            with torch.no_grad():
                speech_prob = float(self._silero(frame_tensor, cfg.sample_rate))
            is_speech = speech_prob >= cfg.vad_threshold

            if not in_speech:
                pre_roll.append(frame)
                if is_speech:
                    in_speech = True
                    speech_confirmed = False
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

                # 语音持续超过确认阈值，才触发打断回调（避免短暂噪音误触发）
                if not speech_confirmed and speech_frames >= speech_confirm_frames:
                    speech_confirmed = True
                    if self._speech_start_callback:
                        try:
                            self._speech_start_callback()
                        except Exception as e:
                            logger.error(f"[VoiceVDL] speech_start_callback failed: {e}")

                should_flush = silence_frames >= end_silence_frames or speech_frames >= max_speech_frames
                if should_flush:
                    in_speech = False
                    # 只有确认为真正发言的片段才提交转写，短暂噪音直接丢弃
                    if speech_confirmed and speech_frames >= min_speech_frames:
                        audio = np.concatenate(speech_segments, axis=0)
                        try:
                            self._audio_queue.put_nowait(audio)
                        except queue.Full:
                            logger.warning("[VoiceVDL] audio queue full, dropping segment")
                    elif not speech_confirmed:
                        logger.debug(f"[VoiceVDL] 丢弃短暂噪音片段 ({speech_frames * cfg.frame_ms}ms < {cfg.speech_confirm_ms}ms)")
                    speech_confirmed = False
                    speech_segments = []
                    speech_frames = 0
                    silence_frames = 0
                    self._silero.reset_states()

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    def on_text(text: str) -> None:
        print(f"[语音识别] >>> {text}\n")

    svc = VoiceInputVDLService(
        text_callback=on_text,
        whisper_model_size="small",
        whisper_device="cpu",
        whisper_compute_type="int8",
        language="zh",
    )

    if not svc.start():
        print("[TEST] 语音输入启动失败，请检查依赖和麦克风")
        raise SystemExit(1)

    print("[TEST] 语音输入已启动，请对麦克风说话（Ctrl+C 退出）")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[TEST] 正在停止...")
    finally:
        svc.stop()
        print("[TEST] 已退出")
