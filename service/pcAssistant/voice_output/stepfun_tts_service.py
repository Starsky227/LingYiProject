"""
StepFun 实时 TTS 语音合成服务（备份实现 / 未接入主链路）

⚠️  当前 PCServiceManager 仅使用 ``qwen_tts_service.QwenTTSOutputService``。
本文件保留为可切换的备选实现：通过 StepFun WebSocket API（step-tts-2）做实时
语音合成，参考 xiao8-main 的 step_realtime_tts_worker。如需启用，需要在
``service_manager._lazy_import_voice_output`` 里手动切换导入目标。

接口与 QwenTTSOutputService 保持一致，可在 service_manager 中无缝切换。
"""

import asyncio
import base64
import io
import json
import logging
import os
import queue
import sys
import threading
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
os.chdir(_project_root)

from system.config import config
from system.paths import CACHE_DIR, ensure_dir

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd  # type: ignore[import-not-found]
except Exception as _e:
    logger.warning(f"[StepFunTTS] failed to import sounddevice: {_e}")
    sd = None

try:
    import websockets  # type: ignore[import-not-found]
    import websockets.exceptions  # type: ignore[import-not-found]
except Exception as _e:
    logger.warning(f"[StepFunTTS] failed to import websockets: {_e}")
    websockets = None

_TTS_URL = "wss://api.stepfun.com/v1/realtime/audio?model=step-tts-2"
_SAMPLE_RATE = 24000  # StepFun 返回 24kHz WAV


@dataclass
class _SpeakRequest:
    speech_id: str
    text: str
    speed: float
    language: str


@dataclass
class _PlaybackItem:
    speech_id: str
    wav: np.ndarray
    sample_rate: int
    file_path: Path


class StepFunTTSService:
    """StepFun 实时 WebSocket TTS 服务，接口兼容 QwenTTSOutputService。"""

    def __init__(self):
        tts_cfg = config.tts
        self._api_key: str = str(tts_cfg.tts_api_key).strip()
        self._voice_id: str = str(tts_cfg.tts_voice_id).strip() or "qingchunshaonv"
        self._playback_sample_rate = _SAMPLE_RATE
        self._playback_chunk_ms = int(tts_cfg.playback_chunk_ms)

        self._cache_dir: Path = ensure_dir(CACHE_DIR / "voice_output")

        self._running = False
        self._stop_event = threading.Event()
        self._interrupt_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._player_thread: Optional[threading.Thread] = None
        self._request_queue: "queue.Queue[Optional[_SpeakRequest]]" = queue.Queue(maxsize=16)
        self._playback_queue: "queue.Queue[object]" = queue.Queue(maxsize=128)

    # ------------------------------------------------------------------ #
    #  WebSocket 异步工作核心
    # ------------------------------------------------------------------ #

    def _run_async_worker(self) -> None:
        """在独立线程中运行异步 WebSocket worker。"""
        try:
            asyncio.run(self._async_worker())
        except Exception as e:
            logger.error(f"[StepFunTTS] async worker 启动失败: {e}")

    async def _async_worker(self) -> None:
        """异步 TTS worker：管理 WebSocket 连接，发送文本，接收音频。"""
        ws = None
        session_id: Optional[str] = None
        receive_task: Optional[asyncio.Task] = None
        current_speech_id: Optional[str] = None
        session_ready = asyncio.Event()
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async def _connect_and_create_session():
            """建立 WebSocket 连接并创建 TTS 会话，返回 (ws, session_id)。"""
            nonlocal session_ready
            session_ready.clear()
            _ws = await websockets.connect(_TTS_URL, additional_headers=headers)

            # 等待 connection.done
            _sid = None
            try:
                async def _wait_conn():
                    nonlocal _sid
                    async for message in _ws:
                        event = json.loads(message)
                        if event.get("type") == "tts.connection.done":
                            _sid = event.get("data", {}).get("session_id")
                            session_ready.set()
                            break
                        elif event.get("type") == "tts.response.error":
                            logger.error(f"[StepFunTTS] 连接错误: {event}")
                            break

                await asyncio.wait_for(_wait_conn(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error("[StepFunTTS] 等待连接超时")
                await _ws.close()
                return None, None

            if not _sid:
                await _ws.close()
                return None, None

            # 创建会话
            create_event = {
                "type": "tts.create",
                "data": {
                    "session_id": _sid,
                    "voice_id": self._voice_id,
                    "response_format": "wav",
                    "sample_rate": _SAMPLE_RATE,
                }
            }
            await _ws.send(json.dumps(create_event))

            # 等待会话创建成功
            try:
                async def _wait_created():
                    async for message in _ws:
                        event = json.loads(message)
                        if event.get("type") == "tts.response.created":
                            break
                        elif event.get("type") == "tts.response.error":
                            logger.error(f"[StepFunTTS] 创建会话错误: {event}")
                            break

                await asyncio.wait_for(_wait_created(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("[StepFunTTS] 会话创建超时，继续...")

            return _ws, _sid

        def _make_receive_task(_ws_ref):
            """创建接收音频的异步任务。"""
            async def _receive():
                try:
                    async for message in _ws_ref:
                        if self._stop_event.is_set():
                            break
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "tts.response.audio.delta":
                            try:
                                audio_b64 = event.get("data", {}).get("audio", "")
                                if not audio_b64:
                                    continue
                                audio_bytes = base64.b64decode(audio_b64)
                                wav_arr, sr = self._wav_bytes_to_numpy(audio_bytes)
                                if wav_arr.size == 0:
                                    continue

                                # 写入临时文件
                                fid = uuid.uuid4().hex[:8]
                                file_path = self._cache_dir / f"stepfun_{fid}.wav"
                                self._write_temp_wav(file_path, wav_arr, sr)

                                try:
                                    self._playback_queue.put_nowait(
                                        _PlaybackItem(
                                            speech_id=current_speech_id or "",
                                            wav=wav_arr,
                                            sample_rate=sr,
                                            file_path=file_path,
                                        )
                                    )
                                except queue.Full:
                                    logger.warning("[StepFunTTS] 播放队列已满，丢弃音频块")
                            except Exception as e:
                                logger.error(f"[StepFunTTS] 处理音频数据出错: {e}")

                        elif event_type == "tts.response.error":
                            logger.error(f"[StepFunTTS] 服务端错误: {event}")

                        elif event_type in ("tts.response.done", "tts.response.audio.done"):
                            logger.debug(f"[StepFunTTS] 响应完成: {event_type}")

                except websockets.exceptions.ConnectionClosed:
                    logger.debug("[StepFunTTS] WebSocket 连接已关闭")
                except Exception as e:
                    if not self._stop_event.is_set():
                        logger.error(f"[StepFunTTS] 接收消息出错: {e}")

            return asyncio.create_task(_receive())

        # ---- 主循环 ----
        try:
            # 首次连接
            ws, session_id = await _connect_and_create_session()
            if ws is None:
                logger.error("[StepFunTTS] 首次连接失败")
                return
            logger.info("[StepFunTTS] StepFun TTS 已就绪")
            receive_task = _make_receive_task(ws)

            loop = asyncio.get_running_loop()
            while not self._stop_event.is_set():
                try:
                    req = await loop.run_in_executor(None, self._request_queue_get)
                except Exception:
                    break

                if req is None:
                    # 停止信号
                    break

                if isinstance(req, str) and req == "__interrupt__":
                    # 打断：关闭连接
                    if ws:
                        try:
                            await ws.close()
                        except Exception:
                            pass
                        ws = None
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                        receive_task = None
                    session_id = None
                    current_speech_id = None
                    continue

                # 新的语音请求
                if current_speech_id != req.speech_id:
                    current_speech_id = req.speech_id
                    # 重新建立连接以保证干净的会话
                    if ws:
                        try:
                            # 发送 text.done 结束上一轮
                            if session_id:
                                done_ev = {"type": "tts.text.done", "data": {"session_id": session_id}}
                                await ws.send(json.dumps(done_ev))
                            await ws.close()
                        except Exception:
                            pass
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass

                    ws, session_id = await _connect_and_create_session()
                    if ws is None:
                        logger.warning("[StepFunTTS] 重连失败，跳过该请求")
                        continue
                    receive_task = _make_receive_task(ws)

                # 发送文本
                if not req.text or not req.text.strip():
                    continue
                if not ws or not session_id:
                    continue

                try:
                    text_event = {
                        "type": "tts.text.delta",
                        "data": {
                            "session_id": session_id,
                            "text": req.text,
                        }
                    }
                    await ws.send(json.dumps(text_event))
                    # 立即发送 text.done，让服务器开始合成
                    done_event = {
                        "type": "tts.text.done",
                        "data": {"session_id": session_id}
                    }
                    await ws.send(json.dumps(done_event))
                except Exception as e:
                    logger.error(f"[StepFunTTS] 发送文本失败: {e}")
                    ws = None
                    session_id = None
                    current_speech_id = None

        except Exception as e:
            logger.error(f"[StepFunTTS] Worker 错误: {e}")
        finally:
            if receive_task and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass

    def _request_queue_get(self):
        """阻塞获取请求（供 run_in_executor 使用），支持停止检测。"""
        while not self._stop_event.is_set():
            try:
                return self._request_queue.get(timeout=0.2)
            except queue.Empty:
                continue
        return None

    # ------------------------------------------------------------------ #
    #  音频工具
    # ------------------------------------------------------------------ #

    @staticmethod
    def _wav_bytes_to_numpy(wav_bytes: bytes) -> tuple[np.ndarray, int]:
        """将 WAV 字节解码为 float32 numpy 数组和采样率。"""
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                sr = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                sw = wf.getsampwidth()
            if sw == 2:
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
            elif sw == 4:
                arr = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483647.0
            else:
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
            return arr, sr
        except Exception as e:
            logger.warning(f"[StepFunTTS] WAV 解码失败: {e}")
            return np.array([], dtype=np.float32), _SAMPLE_RATE

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

    # ------------------------------------------------------------------ #
    #  生命周期
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._running:
            return True
        if sd is None:
            logger.error("[StepFunTTS] sounddevice 不可用，请安装 sounddevice")
            return False
        if websockets is None:
            logger.error("[StepFunTTS] websockets 不可用，请安装: pip install websockets")
            return False
        if not bool(config.tts.enabled):
            logger.error("[StepFunTTS] tts.enabled 为 false")
            return False
        if not self._api_key:
            logger.error("[StepFunTTS] tts_api_key 未配置")
            return False

        self._stop_event.clear()
        self._interrupt_event.clear()
        self._cleanup_cache_dir()
        self._request_queue = queue.Queue(maxsize=16)
        self._playback_queue = queue.Queue(maxsize=128)

        self._worker_thread = threading.Thread(
            target=self._run_async_worker, name="stepfun-tts-worker", daemon=True
        )
        self._player_thread = threading.Thread(
            target=self._player_loop, name="stepfun-tts-player", daemon=True
        )
        self._worker_thread.start()
        self._player_thread.start()
        self._running = True
        logger.info("[StepFunTTS] StepFun TTS service started")
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

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
        if self._player_thread and self._player_thread.is_alive():
            self._player_thread.join(timeout=3.0)

        self._worker_thread = None
        self._player_thread = None
        self._running = False
        self._cleanup_cache_dir()
        logger.info("[StepFunTTS] StepFun TTS service stopped")
        return True

    # ------------------------------------------------------------------ #
    #  公共接口（与 QwenTTSOutputService 保持一致）
    # ------------------------------------------------------------------ #

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

        req = _SpeakRequest(
            speech_id=uuid.uuid4().hex,
            text=clean_text,
            speed=float(max(0.5, min(2.0, speed))),
            language=language,
        )

        if not append:
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
            # 向 worker 发送打断信号
            try:
                self._request_queue.put_nowait("__interrupt__")
            except queue.Full:
                pass
            self._cleanup_cache_dir()
        return "已打断当前语音播放"

    # ------------------------------------------------------------------ #
    #  播放线程
    # ------------------------------------------------------------------ #

    @staticmethod
    def _drain_queue(q: queue.Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _player_loop(self) -> None:
        """播放线程：从播放队列取音频，通过 sounddevice 播放。"""
        try:
            chunk_frames = max(1, int(self._playback_sample_rate * self._playback_chunk_ms / 1000))
            with sd.OutputStream(
                samplerate=self._playback_sample_rate,
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
                        # 重采样到播放采样率
                        if item.sample_rate != self._playback_sample_rate and wav.size > 1:
                            src_idx = np.linspace(0, len(wav) - 1, num=len(wav), dtype=np.float32)
                            dst_len = max(1, int(round(len(wav) * self._playback_sample_rate / item.sample_rate)))
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
                            logger.warning(f"[StepFunTTS] 删除临时文件失败 {item.file_path}: {e}")
                        self._interrupt_event.clear()
        except Exception as e:
            logger.error(f"[StepFunTTS] 播放循环异常: {e}")
            self._stop_event.set()


# ====================================================================== #
#  独立测试入口
# ====================================================================== #

if __name__ == "__main__":
    import time

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    svc = StepFunTTSService()
    print(f"[TEST] voice_id = {svc._voice_id}")
    print(f"[TEST] 正在启动 StepFun TTS ...")

    if not svc.start():
        print("[TEST] 启动失败，请检查日志")
        sys.exit(1)

    # 等待 WebSocket 连接建立
    time.sleep(2)
    print("[TEST] 服务已启动，输入文本回车播放，输入 q 退出：")

    try:
        while True:
            text = input("> ").strip()
            if not text:
                continue
            if text.lower() == "q":
                break
            result = svc.speak_text(text)
            print(f"  → {result}")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        print("[TEST] 正在停止 ...")
        svc.stop()
        print("[TEST] 已停止")
