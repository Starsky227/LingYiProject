import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
os.chdir(_project_root)

from system.config import config
from system.paths import CACHE_DIR, ensure_dir

logger = logging.getLogger(__name__)

_TARGET_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
_TARGET_MODEL_DIR_NAME = "Qwen3-TTS-12Hz-0.6B-Base"
_LEGACY_MODEL_DIR_NAMES = (
    "Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "Qwen3-TTS-12Hz-0.6B-CustomVoice-Int4",
    "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
)

try:
    import sounddevice as sd  # type: ignore[import-not-found]
except Exception as _e:  # pragma: no cover
    logging.getLogger(__name__).warning(f"[VoiceOutput] failed to import sounddevice: {_e}")
    sd = None

try:
    import torch  # type: ignore[import-not-found]
    # 双重保证：若 main.py 的早期修复未覆盖，在此补充注册 torch DLL 目录
    if sys.platform == "win32":
        _torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(_torch_lib):
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(_torch_lib)
            if _torch_lib not in os.environ.get("PATH", ""):
                os.environ["PATH"] = _torch_lib + os.pathsep + os.environ.get("PATH", "")
except Exception as _e:  # pragma: no cover
    logging.getLogger(__name__).warning(f"[VoiceOutput] failed to import torch: {_e}")
    torch = None  # type: ignore[assignment]

try:
    from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]
except Exception as _e:  # pragma: no cover
    logging.getLogger(__name__).warning(f"[VoiceOutput] failed to import qwen_tts: {_e}")
    Qwen3TTSModel = None  # type: ignore[assignment]


_SEGMENT_END = object()

_LANGUAGE_MAP = {
    "auto": "Auto",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
    "en": "English",
    "en-us": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}


@dataclass
class _SpeakRequest:
    speech_id: str
    text: str
    voice: str
    instructions: str
    speed: float
    language: str


@dataclass
class _PlaybackItem:
    speech_id: str
    wav: np.ndarray
    sample_rate: int
    file_path: Path


class QwenTTSOutputService:
    """Local Qwen3-TTS 0.6B-Base playback service for pcAssistant.

    Note:
    - Uses Qwen3-TTS-12Hz-0.6B-Base with a reference voice file (LingYiVoice.wav) for voice cloning.
    - 12Hz tokenizer does NOT depend on SoX; no external SoX binary required.
    - The official local package does not expose true streaming generation; playback here is chunked after synthesis.
    """

    def __init__(self):
        tts_cfg = config.tts
        self._sample_rate = 24000
        self._model_path = str(tts_cfg.model_path).strip() or _TARGET_MODEL_ID
        default_local_dir = str(Path("data") / "cache" / "models" / _TARGET_MODEL_DIR_NAME)
        self._model_local_dir = Path(str(tts_cfg.model_local_dir).strip() or default_local_dir)
        self._device = str(tts_cfg.device or "cpu").strip() or "cpu"
        self._dtype_name = str(tts_cfg.dtype or "float32").strip() or "float32"
        self._default_voice = str(tts_cfg.default_voice or "").strip()
        self._default_language = self._normalize_language(tts_cfg.default_language)
        self._default_speed = float(tts_cfg.default_speed)
        self._max_new_tokens = int(tts_cfg.max_new_tokens)
        self._local_files_only = bool(tts_cfg.local_files_only)
        self._playback_chunk_ms = int(tts_cfg.playback_chunk_ms)
        self._tts_model = None

        # 声源文件：voice_output/LingYiVoice.wav
        self._voice_source: Path = Path(__file__).resolve().parent / "LingYiVoice.wav"

        self._cache_dir: Path = ensure_dir(CACHE_DIR / "voice_output")

        self._running = False
        self._stop_event = threading.Event()
        self._interrupt_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._player_thread: Optional[threading.Thread] = None
        self._request_queue: "queue.Queue[Optional[_SpeakRequest]]" = queue.Queue(maxsize=16)
        self._playback_queue: "queue.Queue[object]" = queue.Queue(maxsize=128)

    @staticmethod
    def _normalize_language(language: str) -> str:
        token = str(language or "Auto").strip()
        if not token:
            return "Auto"
        return _LANGUAGE_MAP.get(token.lower(), token)

    @staticmethod
    def _resolve_torch_dtype(dtype_name: str):
        token = str(dtype_name or "float32").strip().lower()
        if torch is None:
            return None
        mapping = {
            "float32": torch.float32,
            "fp32": torch.float32,
            "float16": torch.float16,
            "fp16": torch.float16,
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
        }
        return mapping.get(token, torch.float32)

    def _resolve_model_source(self) -> str:
        if self._model_local_dir.exists():
            return str(self._model_local_dir)
        return self._model_path

    def _cleanup_legacy_models(self) -> None:
        # 删除旧 0.6B 模型目录，避免误加载旧模型。
        parent = self._model_local_dir.parent
        candidates = [parent / name for name in _LEGACY_MODEL_DIR_NAMES]
        for old_dir in candidates:
            if old_dir.resolve() == self._model_local_dir.resolve():
                continue
            if old_dir.exists():
                try:
                    shutil.rmtree(old_dir)
                    logger.info(f"[VoiceOutput] removed legacy model directory: {old_dir}")
                except Exception as e:
                    logger.warning(f"[VoiceOutput] failed to remove legacy model directory {old_dir}: {e}")

    @staticmethod
    def _run_cmd(cmd: list[str]) -> tuple[bool, str]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as e:
            return False, str(e)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            return False, err
        return True, (proc.stdout or "").strip()

    def _ensure_runtime_model(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> bool:
        self._cleanup_legacy_models()
        if self._model_local_dir.exists() and any(self._model_local_dir.iterdir()):
            return True
        self._model_local_dir.mkdir(parents=True, exist_ok=True)

        msg = f"首次使用语音输出，正在下载模型 {self._model_path}，首次可能耗时 1–5 分钟。"
        logger.info(f"[VoiceOutput] {msg}")
        if progress_callback:
            try:
                progress_callback("download_start", msg)
            except Exception:
                pass
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=self._model_path,
                local_dir=str(self._model_local_dir),
            )
        except Exception as e:
            err_msg = f"语音模型下载失败: {e}"
            logger.error(f"[VoiceOutput] {err_msg}")
            if progress_callback:
                try:
                    progress_callback("error", err_msg)
                except Exception:
                    pass
            return False
        done_msg = "语音模型下载完成。"
        logger.info(f"[VoiceOutput] {done_msg}")
        if progress_callback:
            try:
                progress_callback("download_complete", done_msg)
            except Exception:
                pass
        return True

    def prepare(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> bool:
        """预加载语音模型。

        预期在任意“可能耗时”的时机调用（如 UI 启动后后台线程、启用语音交互之前）。
        ``start()`` 内部会再调用一次，但本方法幂等，已加载不会重复工作。

        progress_callback 签名：``(stage, message)``，stage 取值:
            ``"download_start"`` / ``"download_complete"`` / ``"load_start"`` /
            ``"load_complete"`` / ``"error"``。
        仅用于 UI 提示，不影响实际业务逻辑。
        """
        if not self._ensure_runtime_model(progress_callback):
            return False
        if self._tts_model is None:
            if progress_callback:
                try:
                    progress_callback("load_start", "正在加载语音模型到内存…")
                except Exception:
                    pass
            ok = self._load_model_inner()
            if progress_callback:
                try:
                    progress_callback(
                        "load_complete" if ok else "error",
                        "语音模型已就绪" if ok else "语音模型加载失败",
                    )
                except Exception:
                    pass
            return ok
        return True

    def _load_model(self) -> bool:
        if self._tts_model is not None:
            return True
        if not self._ensure_runtime_model():
            return False
        return self._load_model_inner()

    def _load_model_inner(self) -> bool:
        if self._tts_model is not None:
            return True
        if torch is None:
            logger.error("[VoiceOutput] torch is not available. Please install torch CPU version.")
            return False
        if Qwen3TTSModel is None:
            logger.error("[VoiceOutput] qwen-tts is not available. Please install qwen-tts.")
            return False

        model_source = self._resolve_model_source()
        dtype = self._resolve_torch_dtype(self._dtype_name)
        logger.info(f"[VoiceOutput] loading local Qwen3-TTS model from {model_source} on {self._device} ...")
        try:
            kwargs = {
                "device_map": self._device,
                "dtype": dtype,
                "local_files_only": self._local_files_only,
            }
            self._tts_model = Qwen3TTSModel.from_pretrained(model_source, **kwargs)
            logger.info("[VoiceOutput] local Qwen3-TTS model ready")
            return True
        except Exception as e:
            logger.error(f"[VoiceOutput] failed to load local Qwen3-TTS model: {e}")
            return False

    @staticmethod
    def _apply_speed(wav: np.ndarray, speed: float) -> np.ndarray:
        safe_speed = float(max(0.5, min(2.0, speed)))
        if abs(safe_speed - 1.0) < 1e-6 or wav.size <= 1:
            return wav
        new_len = max(1, int(round(len(wav) / safe_speed)))
        src_idx = np.linspace(0, len(wav) - 1, num=len(wav), dtype=np.float32)
        dst_idx = np.linspace(0, len(wav) - 1, num=new_len, dtype=np.float32)
        return np.interp(dst_idx, src_idx, wav).astype(np.float32)

    def _write_temp_wav(self, file_path: Path, wav: np.ndarray, sample_rate: int) -> None:
        audio = np.clip(wav, -1.0, 1.0)
        audio_i16 = (audio * 32767.0).astype(np.int16)
        with wave.open(str(file_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_i16.tobytes())

    def _cleanup_cache_dir(self) -> None:
        for path in self._cache_dir.glob("*.wav"):
            try:
                path.unlink()
            except Exception:
                continue

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._running:
            return True
        if getattr(self, "_zombie", False):
            logger.error(
                "[VoiceOutput] 上次 stop() 后 worker/player 线程未退出（zombie 状态），"
                "为避免重复占用音频设备/显存，拒绝再次 start()。请重启进程。"
            )
            return False
        if sd is None:
            logger.error("[VoiceOutput] sounddevice is not available. Please install sounddevice.")
            return False
        if not bool(config.tts.enabled):
            logger.error("[VoiceOutput] tts.enabled is false in config.json")
            return False
        if not self._load_model():
            return False

        self._stop_event.clear()
        self._interrupt_event.clear()
        self._cleanup_cache_dir()
        self._request_queue = queue.Queue(maxsize=16)
        self._playback_queue = queue.Queue(maxsize=128)

        self._worker_thread = threading.Thread(target=self._worker_loop, name="voice-tts-worker", daemon=True)
        self._player_thread = threading.Thread(target=self._player_loop, name="voice-tts-player", daemon=True)
        self._worker_thread.start()
        self._player_thread.start()
        self._running = True
        logger.info("[VoiceOutput] Qwen3-TTS service started")
        return True

    def stop(self) -> bool:
        if not self._running:
            return True

        self._stop_event.set()
        self._interrupt_event.set()
        self._drain_queue(self._request_queue)
        self._drain_queue(self._playback_queue)
        self._request_queue.put_nowait(None)
        self._playback_queue.put_nowait(None)

        zombies: list[str] = []
        for label, t in (("worker", self._worker_thread), ("player", self._player_thread)):
            if t and t.is_alive():
                t.join(timeout=3.0)
                if t.is_alive():
                    zombies.append(label)

        if zombies:
            self._zombie = True
            logger.error(
                f"[VoiceOutput] 以下 TTS 线程未能在超时内退出: {zombies}; "
                f"QwenTTSService 进入 zombie 状态，下次 start() 会被拒绝。"
            )
        else:
            self._worker_thread = None
            self._player_thread = None

        self._running = False
        self._cleanup_cache_dir()
        logger.info(
            "[VoiceOutput] Qwen3-TTS service stopped"
            + (f" (zombie threads: {zombies})" if zombies else "")
        )
        return not zombies

    def speak_text(
        self,
        text: str,
        voice: str = "",
        instructions: str = "",
        speed: float = 1.0,
        language: str = "",
        append: bool = False,
    ) -> str:
        if not self._running:
            return "语音输出未开启"

        clean_text = (text or "").strip()
        if not clean_text:
            return "参数错误: text 不能为空"

        safe_speed = float(max(0.5, min(2.0, speed)))
        normalized_language = self._normalize_language(language or self._default_language)
        req = _SpeakRequest(
            speech_id=uuid.uuid4().hex,
            text=clean_text,
            voice="",
            instructions=(instructions or "").strip(),
            speed=safe_speed,
            language=normalized_language,
        )

        if not append:
            # 非追加模式：新语音打断当前播放，替换队列
            self.interrupt_playback(clear_pending=True)
        self._interrupt_event.clear()

        try:
            self._request_queue.put_nowait(req)
        except queue.Full:
            return "语音输出繁忙，请稍后再试"
        return f"已开始语音播放 (speech_id={req.speech_id})"

    def interrupt_playback(self, clear_pending: bool = True) -> str:
        if not self._running:
            return "语音输出未开启"

        self._interrupt_event.set()
        if clear_pending:
            self._drain_queue(self._request_queue)
            self._drain_queue(self._playback_queue)
            self._cleanup_cache_dir()
        return "已打断当前语音播放"

    @staticmethod
    def _drain_queue(q: queue.Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                req = self._request_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if req is None:
                break

            file_path = self._cache_dir / f"{req.speech_id}.wav"
            try:
                wavs, sr = self._tts_model.generate_voice_clone(
                    text=req.text,
                    language=req.language,
                    ref_audio=str(self._voice_source),
                    x_vector_only_mode=True,
                    non_streaming_mode=True,
                    max_new_tokens=self._max_new_tokens,
                )
                if self._stop_event.is_set() or self._interrupt_event.is_set():
                    continue
                if not wavs:
                    logger.warning("[VoiceOutput] local TTS returned empty audio")
                    continue

                wav = np.asarray(wavs[0], dtype=np.float32)
                wav = self._apply_speed(wav, req.speed)
                self._write_temp_wav(file_path, wav, sr)
                try:
                    self._playback_queue.put_nowait(
                        _PlaybackItem(
                            speech_id=req.speech_id,
                            wav=wav,
                            sample_rate=sr,
                            file_path=file_path,
                        )
                    )
                except queue.Full:
                    logger.warning("[VoiceOutput] playback queue full, dropping generated audio")
            except Exception as e:
                logger.error(f"[VoiceOutput] local Qwen3-TTS synthesis failed: {e}")
            finally:
                if file_path.exists() and self._interrupt_event.is_set():
                    try:
                        file_path.unlink()
                    except Exception:
                        pass

    def _player_loop(self) -> None:
        try:
            chunk_frames = max(1, int(self._sample_rate * self._playback_chunk_ms / 1000))
            with sd.OutputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                blocksize=0,
                latency="low",
            ) as stream:
                while not self._stop_event.is_set():
                    try:
                        item = self._playback_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    if item is None:
                        break
                    if not isinstance(item, _PlaybackItem):
                        continue

                    try:
                        wav = np.asarray(item.wav, dtype=np.float32)
                        if item.sample_rate != self._sample_rate and wav.size > 1:
                            src_idx = np.linspace(0, len(wav) - 1, num=len(wav), dtype=np.float32)
                            dst_len = max(1, int(round(len(wav) * self._sample_rate / item.sample_rate)))
                            dst_idx = np.linspace(0, len(wav) - 1, num=dst_len, dtype=np.float32)
                            wav = np.interp(dst_idx, src_idx, wav).astype(np.float32)

                        if self._interrupt_event.is_set():
                            continue

                        for start in range(0, len(wav), chunk_frames):
                            if self._stop_event.is_set() or self._interrupt_event.is_set():
                                break
                            chunk = wav[start:start + chunk_frames]
                            if chunk.size:
                                stream.write(chunk.reshape(-1, 1))
                    finally:
                        try:
                            if item.file_path.exists():
                                item.file_path.unlink()
                        except Exception as e:
                            logger.warning(f"[VoiceOutput] failed to delete temp audio file {item.file_path}: {e}")
                        self._interrupt_event.clear()
        except Exception as e:
            logger.error(f"[VoiceOutput] playback loop failed: {e}")
            self._stop_event.set()


if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    _TEXT = "这句太重了……像是把最后一点执念也说出口了。阿达希尔这一声“王啊”，听着真的有种要结束了的味道。"
    _OUTPUT = Path(__file__).resolve().parent / "voice.wav"

    print(f"[TEST] 正在加载模型...")
    svc = QwenTTSOutputService()
    if not svc._load_model():
        print("[TEST] 模型加载失败，请检查依赖和配置")
        sys.exit(1)

    print(f"[TEST] 正在合成: {_TEXT}")
    wavs, sr = svc._tts_model.generate_voice_clone(
        prompt="sad, poetic, very slow",
        text=_TEXT,
        language=svc._default_language or "Chinese",
        ref_audio=str(svc._voice_source),
        x_vector_only_mode=True,
        non_streaming_mode=True,
        max_new_tokens=svc._max_new_tokens,
    )

    if not wavs:
        print("[TEST] TTS 返回空音频")
        sys.exit(1)

    wav = np.asarray(wavs[0], dtype=np.float32)
    svc._write_temp_wav(_OUTPUT, wav, sr)
    print(f"[TEST] 已生成: {_OUTPUT.resolve()}  (采样率={sr}, 时长={len(wav)/sr:.2f}s)")