"""
PCAssistant 服务管理器
负责启动 / 停止各后台服务子进程：
  - QQ Bot          (service/qqOneBot/qqbot_main.py)
  - 记忆云图         (brain/memory/memorygraph_visualizer.py)
  - 屏幕捕捉         (暂未实装)
    - 语音输入         (voice_input_VDL)
    - 语音输出         (Qwen3-TTS)
  - 助手模式         (暂未实装)
"""

import os
import sys
import logging
import subprocess
import time
import socket
from urllib.parse import urlparse
from typing import Optional, Callable

from system.config import config

# 延迟导入语音服务：依赖可能未安装，不应阻塞主程序
VoiceInputVDLService = None
TTSOutputService = None

def _lazy_import_voice_input():
    global VoiceInputVDLService
    if VoiceInputVDLService is None:
        try:
            from service.pcAssistant.voice_input_VDL import VoiceInputVDLService as _cls
            VoiceInputVDLService = _cls
        except Exception as e:
            logging.getLogger(__name__).warning(f"语音输入模块加载失败: {e}")
    return VoiceInputVDLService

def _lazy_import_voice_output():
    global TTSOutputService
    if TTSOutputService is None:
        try:
            from service.pcAssistant.voice_output import TTSOutputService as _cls
            TTSOutputService = _cls
        except Exception as e:
            logging.getLogger(__name__).warning(f"语音输出模块加载失败: {e}")
    return TTSOutputService

ScreenTextExtractor = None

def _lazy_import_screen_ocr():
    global ScreenTextExtractor
    if ScreenTextExtractor is None:
        try:
            from service.pcAssistant.screen_text_extract import ScreenTextExtractor as _cls
            ScreenTextExtractor = _cls
        except Exception as e:
            logging.getLogger(__name__).warning(f"屏幕文字提取模块加载失败: {e}")
    return ScreenTextExtractor

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROJECT_PARENT = os.path.dirname(_PROJECT_ROOT)
_PYTHON_EXE = sys.executable


# ----------------------------------------------------------------------- #
#  超时与轮询常量
# ----------------------------------------------------------------------- #
#
# 这些值原本以裸字面量散落在各处。集中到这里有两个好处：
#   1) 调参/排查时一目了然；
#   2) 注释解释每个值"为什么是这个数"，避免后人盲目调小/调大。
#
class _Timeouts:
    # 子进程启动后等多久再去 poll 退出码——给 Python 解释器加载时间，避免
    # 把"启动慢"误判成"立即崩溃"。
    SUBPROC_BOOT_GRACE_SEC = 0.8

    # cleanup / toggle 关闭子进程的优雅退出超时；超过则 kill。
    SUBPROC_TERMINATE_TIMEOUT_SEC = 5

    # cleanup 末尾的兜底关闭超时（更短，因为前面已经各自 wait 过一次）。
    SUBPROC_CLEANUP_TIMEOUT_SEC = 3


class PCServiceManager:
    """管理 pcAssistant 周边后台服务的生命周期"""

    def __init__(self):
        self._qq_process: Optional[subprocess.Popen] = None
        self._memoryviz_process: Optional[subprocess.Popen] = None
        self._napcat_process: Optional[subprocess.Popen] = None
        self._voice_service = None
        self._voice_text_callback: Optional[Callable[[str], None]] = None
        self._screen_ocr_callback: Optional[Callable[[str], None]] = None
        self._voice_output_service = None
        self._voice_interaction_active = False
        self._speech_start_callback: Optional[Callable[[], None]] = None
        self._screen_ocr_service = None
        self._screen_capture_enabled = False  # 屏幕捕捉权限（view_screen 工具依赖此标志）

    def set_voice_text_callback(self, callback: Callable[[str], None]) -> None:
        self._voice_text_callback = callback

    def set_screen_ocr_callback(self, callback: Callable[[str], None]) -> None:
        """设置屏幕 OCR 文字回调（独立于语音回调）"""
        self._screen_ocr_callback = callback

    def set_speech_start_callback(self, callback: Callable[[], None]) -> None:
        """注入"用户开始说话"回调，语音交互模式下 VAD 检测到语音即调用"""
        self._speech_start_callback = callback

    # ------------------------------------------------------------------ #
    #  语音输入 (voice_input_VDL)
    # ------------------------------------------------------------------ #

    def is_voice_input_running(self) -> bool:
        return self._voice_service is not None and self._voice_service.is_running

    def start_voice_input(self) -> bool:
        if self.is_voice_input_running():
            logger.info("[PCService] Voice input already running")
            return True
        if self._voice_text_callback is None:
            logger.error("[PCService] Voice text callback is not set")
            return False
        cls = _lazy_import_voice_input()
        if cls is None:
            logger.error("[PCService] Voice input module not available")
            return False
        try:
            stt_cfg = config.stt
            # 一次性把所有 STT/VAD 字段从 pydantic 模型拷到本地 dataclass，
            # 让 VoiceInputVDLService 不再持有 config 引用 → 运行期改 config.json 不会
            # 中途生效（避免半新半旧的诡异行为）。
            from service.pcAssistant.voice_input_VDL import VadConfig
            vad_cfg = VadConfig(
                pre_roll_ms=stt_cfg.vad_pre_roll_ms,
                end_silence_ms=stt_cfg.vad_end_silence_ms,
                min_speech_ms=stt_cfg.vad_min_speech_ms,
                speech_confirm_ms=stt_cfg.vad_speech_confirm_ms,
                max_speech_ms=stt_cfg.vad_max_speech_ms,
                vad_threshold=stt_cfg.vad_threshold,
            )
            self._voice_service = cls(
                text_callback=self._voice_text_callback,
                speech_start_callback=self._speech_start_callback,
                whisper_model_size=stt_cfg.whisper_model_size,
                whisper_device=stt_cfg.whisper_device,
                whisper_compute_type=stt_cfg.whisper_compute_type,
                language=stt_cfg.language,
                mic_device=stt_cfg.mic_device,
                vad_config=vad_cfg,
            )
            started = self._voice_service.start()
            if started:
                logger.info("[PCService] Voice input started")
            return started
        except Exception as e:
            logger.error(f"[PCService] Failed to start voice input: {e}")
            self._voice_service = None
            return False

    def stop_voice_input(self) -> bool:
        if self._voice_service is None:
            return True
        try:
            self._voice_service.stop()
            logger.info("[PCService] Voice input stopped")
            return True
        except Exception as e:
            logger.error(f"[PCService] Failed to stop voice input: {e}")
            return False
        finally:
            self._voice_service = None

    def toggle_voice_input(self) -> bool:
        if self.is_voice_input_running():
            self.stop_voice_input()
            return False
        return self.start_voice_input()

    # ------------------------------------------------------------------ #
    #  语音输出 (voice_output / Qwen3-TTS)
    # ------------------------------------------------------------------ #

    def is_voice_output_running(self) -> bool:
        return self._voice_output_service is not None and self._voice_output_service.is_running

    def start_voice_output(self) -> bool:
        if self.is_voice_output_running():
            logger.info("[PCService] Voice output already running")
            return True
        cls = _lazy_import_voice_output()
        if cls is None:
            logger.error("[PCService] Voice output module not available")
            return False
        try:
            self._voice_output_service = cls()
            # \u9884\u52a0\u8f7d\u6a21\u578b\uff08\u9996\u6b21\u4e0b\u8f7d\u53ef\u80fd\u8017\u65f6\u8f83\u957f\uff09\u2014\u2014\u63d0\u524d\u8f93\u51fa\u8fdb\u5ea6\u65e5\u5fd7\uff0c\n            # \u907f\u514d UI \u201c\u70b9\u4e86\u542f\u52a8\u5374\u8fdf\u8fdf\u4e0d\u54cd\u5e94\u201d\u7684\u9ed1\u76d2\u4f53\u9a8c\u3002\n            def _progress(stage: str, message: str) -> None:\n                logger.info(f\"[PCService][TTS \u51c6\u5907] {stage}: {message}\")\n            if hasattr(self._voice_output_service, \"prepare\"):\n                self._voice_output_service.prepare(progress_callback=_progress)\n            started = self._voice_output_service.start()
            if started:
                logger.info("[PCService] Voice output started")
            return started
        except Exception as e:
            logger.error(f"[PCService] Failed to start voice output: {e}")
            self._voice_output_service = None
            return False

    def stop_voice_output(self) -> bool:
        if self._voice_output_service is None:
            return True
        try:
            self._voice_output_service.stop()
            logger.info("[PCService] Voice output stopped")
            return True
        except Exception as e:
            logger.error(f"[PCService] Failed to stop voice output: {e}")
            return False
        finally:
            self._voice_output_service = None

    def toggle_voice_output(self) -> bool:
        if self.is_voice_output_running():
            self.stop_voice_output()
            return False
        return self.start_voice_output()

    def interrupt_voice_output(self) -> str:
        if not self.is_voice_output_running() or self._voice_output_service is None:
            return "语音输出未开启"
        return self._voice_output_service.interrupt_playback(clear_pending=True)

    def get_voice_output_service(self):
        return self._voice_output_service

    # ------------------------------------------------------------------ #
    #  语音交互 (voice_input + voice_output 联动)
    # ------------------------------------------------------------------ #

    def is_voice_interaction_running(self) -> bool:
        return self._voice_interaction_active

    def start_voice_interaction(self) -> bool:
        """启动语音交互：同时开启语音输入和语音输出，并注入 VoiceMCPService。

        返回 True/False 表示语音交互整体是否进入"可用"状态：
          * 至少 VAD 输入跑起来 → True；
          * VAD 输入失败 → False（用户无法对话，没有意义保留半残状态）。

        即便 TTS 启动失败，也只降级为"用户能说、AI 不能开口"，并依然返回 True，
        以便让 UI 把开关状态置为 ON 并显示警告横幅而不是直接弹失败回滚。
        """
        if self._voice_interaction_active:
            return True

        tts_ok = True
        if not self.is_voice_output_running():
            if not self.start_voice_output():
                tts_ok = False
                logger.warning(
                    "[PCService] 语音交互：TTS 启动失败 — 已降级为只输入模式。"
                    "可能原因：模型尚未下载完毕 / 显存不足 / 音频设备占用。"
                )

        # 将 TTS 服务注入 VoiceMCPService 单例。
        # 这一步即使 TTS 不在线也要执行，把 None 显式打进去能保证旧的残留实例不会被复用。
        try:
            from mcpserver.voice_service.voice_mcp_service import VoiceMCPService
            voice_mcp = VoiceMCPService.get_instance()
            voice_mcp.set_tts_service(self._voice_output_service if tts_ok else None)
        except ImportError as e:
            logger.error(f"[PCService] VoiceMCPService 模块缺失：{e}（语音中断协调将不可用）")
        except Exception as e:
            logger.error(
                f"[PCService] VoiceMCPService TTS 注入失败：{type(e).__name__}: {e}；"
                f"后果：TTS 仍能播报，但 view_screen / 模型主动打断 / 语音被打断后清队列等"
                f"协调能力会失效。"
            )

        if not self.is_voice_input_running():
            if not self.start_voice_input():
                logger.error("[PCService] 语音交互：语音输入启动失败，整体回滚")
                # 回滚：避免半残状态
                if tts_ok:
                    self.stop_voice_output()
                return False

        # 将语音输入服务注入 VoiceMCPService（信号隔离：TTS 播放时静音麦克风）
        try:
            from mcpserver.voice_service.voice_mcp_service import VoiceMCPService
            voice_mcp = VoiceMCPService.get_instance()
            voice_mcp.set_voice_input_service(self._voice_service)
        except ImportError as e:
            logger.error(f"[PCService] VoiceMCPService 模块缺失：{e}（无法做麦克风静音协调）")
        except Exception as e:
            logger.error(
                f"[PCService] VoiceMCPService 语音输入注入失败：{type(e).__name__}: {e}；"
                f"后果：TTS 播放期间麦克风不会被静音，可能产生回环（AI 听见自己说话）。"
            )

        self._voice_interaction_active = True
        logger.info(
            "[PCService] 语音交互已开启"
            + ("" if tts_ok else "（TTS 不可用，仅输入）")
        )
        return True

    def stop_voice_interaction(self) -> bool:
        """停止语音交互：同时关闭语音输入和语音输出"""
        self.stop_voice_input()
        self.stop_voice_output()

        try:
            from mcpserver.voice_service.voice_mcp_service import VoiceMCPService
            voice_mcp = VoiceMCPService.get_instance()
            voice_mcp.set_tts_service(None)
            voice_mcp.set_voice_input_service(None)
        except Exception:
            pass

        self._voice_interaction_active = False
        logger.info("[PCService] 语音交互已关闭")
        return True

    def toggle_voice_interaction(self) -> bool:
        """切换语音交互状态，返回新状态"""
        if self._voice_interaction_active:
            self.stop_voice_interaction()
            return False
        return self.start_voice_interaction()

    # ------------------------------------------------------------------ #
    #  屏幕文字提取 (screen_text_extract / PaddleOCR)
    # ------------------------------------------------------------------ #

    def is_screen_capture_enabled(self) -> bool:
        return self._screen_capture_enabled

    def toggle_screen_capture(self) -> bool:
        """切换屏幕文字提取（OCR 服务 + 权限标志），返回新状态"""
        if self.is_screen_ocr_running():
            # 当前正在运行 → 停止
            self.stop_screen_ocr()
            self._screen_capture_enabled = False
            logger.info("[PCService] 屏幕文字提取已关闭")
            return False
        else:
            # 当前未运行 → 使用配置参数启动
            from system.config import config
            ocr_cfg = config.screen_ocr
            region = tuple(ocr_cfg.region)
            started = self.start_screen_ocr(
                region=region,
                interval=ocr_cfg.interval,
                hash_threshold=ocr_cfg.hash_threshold,
                stable_count=ocr_cfg.stable_count,
            )
            self._screen_capture_enabled = started
            logger.info(f"[PCService] 屏幕文字提取启动{'成功' if started else '失败'}, region={region}")
            return started

    def is_screen_ocr_running(self) -> bool:
        return self._screen_ocr_service is not None and self._screen_ocr_service.is_running

    def start_screen_ocr(
        self,
        region: tuple[int, int, int, int],
        interval: float = 0.5,
        hash_threshold: int = 5,
        stable_count: int = 2,
    ) -> bool:
        """启动屏幕文字提取，region = (left, top, width, height)"""
        if self.is_screen_ocr_running():
            logger.info("[PCService] Screen OCR already running")
            return True
        ocr_cb = self._screen_ocr_callback or self._voice_text_callback
        if ocr_cb is None:
            logger.error("[PCService] No callback set for screen OCR")
            return False
        cls = _lazy_import_screen_ocr()
        if cls is None:
            logger.error("[PCService] Screen OCR module not available")
            return False
        try:
            self._screen_ocr_service = cls(
                text_callback=ocr_cb,
                region=region,
                interval=interval,
                hash_threshold=hash_threshold,
                stable_count=stable_count,
            )
            started = self._screen_ocr_service.start()
            if started:
                logger.info(f"[PCService] Screen OCR started, region={region}")
            return started
        except Exception as e:
            logger.error(f"[PCService] Failed to start screen OCR: {e}")
            self._screen_ocr_service = None
            return False

    def stop_screen_ocr(self) -> bool:
        if self._screen_ocr_service is None:
            return True
        try:
            self._screen_ocr_service.stop()
            logger.info("[PCService] Screen OCR stopped")
            return True
        except Exception as e:
            logger.error(f"[PCService] Failed to stop screen OCR: {e}")
            return False
        finally:
            self._screen_ocr_service = None

    def set_screen_ocr_region(self, left: int, top: int, width: int, height: int) -> None:
        """动态更新截屏区域（运行中也可调整）"""
        if self._screen_ocr_service is not None:
            self._screen_ocr_service.set_region(left, top, width, height)

    @staticmethod
    def select_screen_region():
        """弹出全屏遮罩让用户框选区域，返回 (left, top, width, height) 或 None"""
        from service.pcAssistant.screen_text_extract.region_selector import select_screen_region
        return select_screen_region()

    def _build_child_env(self) -> dict:
        """为子进程统一设置 UTF-8，避免 Windows 控制台编码导致崩溃。"""
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    def _spawn_service(self, script: str, service_name: str) -> Optional[subprocess.Popen]:
        """启动子进程并做短暂健康检查，启动失败返回 None。

        ``stdout=PIPE`` 是有意为之：调用者会在外层启动一个 pump 线程
        （main.py ``_ensure_pc_log_stream``）把子进程输出转到 logger。
        切换 DEVNULL 会丢失关键调试信息（QQ/NapCat 启动失败原因等）。
        子进程秒崩或 cleanup() 路径上的 fd 释放由本方法 / cleanup() 显式负责。
        """
        proc = subprocess.Popen(
            [_PYTHON_EXE, script],
            cwd=_PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._build_child_env(),
        )

        # 短暂观察是否秒退，避免返回“假成功”。
        time.sleep(_Timeouts.SUBPROC_BOOT_GRACE_SEC)
        if proc.poll() is not None:
            output = ""
            try:
                if proc.stdout:
                    output = proc.stdout.read()[-3000:]
            except Exception:
                output = ""
            logger.error(
                f"[PCService] {service_name} exited early with code={proc.returncode}. "
                f"Output: {output}"
            )
            # 子进程已退出但 PIPE fd 仍然打开，必须显式 close 否则 Windows 下
            # 长期运行会留下僵尸 fd（ResourceWarning），并且若用户再次 start_qq()
            # 重新 spawn，旧 fd 在 GC 前一直占用。
            try:
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
            except Exception:
                pass
            return None
        return proc

    def _parse_ws_host_port(self) -> tuple[Optional[str], Optional[int]]:
        ws_url = (config.qq_config.onebot_ws_url or "").strip()
        if not ws_url:
            return None, None
        parsed = urlparse(ws_url)
        return parsed.hostname, parsed.port

    def _is_onebot_available(self, timeout: float = 0.5) -> bool:
        host, port = self._parse_ws_host_port()
        if not host or not port:
            return False
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _wait_onebot_available(self, timeout_sec: float = 20.0, interval_sec: float = 0.5) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._is_onebot_available(timeout=interval_sec):
                return True
            time.sleep(interval_sec)
        return self._is_onebot_available(timeout=interval_sec)

    def _find_napcat_launcher(self) -> Optional[str]:
        """在 LingYiProject 同级目录下查找 NapCat 的 launcher.bat。"""
        try:
            for name in os.listdir(_PROJECT_PARENT):
                folder = os.path.join(_PROJECT_PARENT, name)
                if not os.path.isdir(folder):
                    continue
                launcher = os.path.join(folder, "launcher.bat")
                if not os.path.exists(launcher):
                    continue
                if "napcat" in name.lower():
                    return launcher

            # 回退：若目录名不包含 napcat，但存在 launcher.bat，也作为候选。
            for name in os.listdir(_PROJECT_PARENT):
                folder = os.path.join(_PROJECT_PARENT, name)
                if not os.path.isdir(folder):
                    continue
                launcher = os.path.join(folder, "launcher.bat")
                if os.path.exists(launcher):
                    return launcher
        except Exception as e:
            logger.error(f"[PCService] Failed to search NapCat launcher: {e}")
        return None

    def is_napcat_running(self) -> bool:
        # 以 OneBot 端口可用为准，更可靠地反映 NapCat 是否已就绪。
        return self._is_onebot_available()

    def start_napcat(self) -> bool:
        if self.is_napcat_running():
            logger.info("[PCService] NapCat already available")
            return True

        launcher = self._find_napcat_launcher()
        if not launcher:
            logger.error("[PCService] NapCat launcher.bat not found in sibling folders")
            return False

        try:
            self._napcat_process = subprocess.Popen(
                ["cmd", "/c", launcher],
                cwd=os.path.dirname(launcher),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._build_child_env(),
            )
            logger.info(f"[PCService] NapCat launcher started (pid={self._napcat_process.pid})")
        except Exception as e:
            logger.error(f"[PCService] Failed to start NapCat: {e}")
            return False

        if not self._wait_onebot_available(timeout_sec=20.0, interval_sec=0.5):
            # 此处 NapCat 进程很可能仍存活（只是 OneBot 端口没就绪）。
            # 直接 stdout.read() 会阻塞到 NapCat 退出（管道 EOF）—— 历史 bug。
            # 正确做法：先 terminate，再读残余输出，最后关 fd。
            output = ""
            try:
                if self._napcat_process:
                    if self._napcat_process.poll() is None:
                        try:
                            self._napcat_process.terminate()
                            self._napcat_process.wait(
                                timeout=_Timeouts.SUBPROC_TERMINATE_TIMEOUT_SEC
                            )
                        except Exception:
                            self._napcat_process.kill()
                    if self._napcat_process.stdout:
                        try:
                            output = self._napcat_process.stdout.read()[-2000:]
                        except Exception:
                            output = ""
                        try:
                            self._napcat_process.stdout.close()
                        except Exception:
                            pass
            except Exception:
                output = ""
            logger.error(f"[PCService] NapCat started but OneBot endpoint not ready. Output: {output}")
            self._napcat_process = None
            return False

        logger.info("[PCService] NapCat is ready (OneBot endpoint reachable)")
        return True

    def stop_napcat(self) -> bool:
        # 只终止由本管理器启动的 launcher 进程；不主动杀系统中其他同名进程。
        if self._napcat_process and self._napcat_process.poll() is None:
            try:
                self._napcat_process.terminate()
                self._napcat_process.wait(timeout=_Timeouts.SUBPROC_TERMINATE_TIMEOUT_SEC)
            except Exception:
                self._napcat_process.kill()
        # 即使进程已自然退出，PIPE fd 仍要显式关掉，避免长期 toggle 累积 fd 泄漏。
        if self._napcat_process is not None:
            try:
                if self._napcat_process.stdout and not self._napcat_process.stdout.closed:
                    self._napcat_process.stdout.close()
            except Exception:
                pass
        self._napcat_process = None
        return True

    # ------------------------------------------------------------------ #
    #  QQ Bot
    # ------------------------------------------------------------------ #

    def is_qq_running(self) -> bool:
        return self._qq_process is not None and self._qq_process.poll() is None

    def start_qq(self) -> bool:
        if self.is_qq_running():
            logger.info("[PCService] QQ already running")
            return True

        # QQBot 依赖 NapCat 提供 OneBot 链接：先确保 OneBot 端点可用。
        if not self.is_napcat_running():
            if not self.start_napcat():
                logger.error("[PCService] Cannot start QQ because NapCat is unavailable")
                return False

        script = os.path.join(_PROJECT_ROOT, "service", "qqOneBot", "qqbot_main.py")
        if not os.path.exists(script):
            logger.error(f"[PCService] QQ script not found: {script}")
            return False
        try:
            self._qq_process = self._spawn_service(script, "QQ")
            if self._qq_process is None:
                return False
            logger.info(f"[PCService] QQ started (pid={self._qq_process.pid})")
            return True
        except Exception as e:
            logger.error(f"[PCService] Failed to start QQ: {e}")
            return False

    def stop_qq(self) -> bool:
        if not self.is_qq_running():
            # QQ 未运行时也尝试收尾 NapCat（仅限本管理器拉起的 launcher）。
            self.stop_napcat()
            return True
        try:
            self._qq_process.terminate()
            self._qq_process.wait(timeout=_Timeouts.SUBPROC_TERMINATE_TIMEOUT_SEC)
        except Exception:
            self._qq_process.kill()
        logger.info("[PCService] QQ stopped")
        # 显式关闭 PIPE fd，避免 toggle 反复累积 fd 泄漏。
        try:
            if self._qq_process.stdout and not self._qq_process.stdout.closed:
                self._qq_process.stdout.close()
        except Exception:
            pass
        self._qq_process = None
        self.stop_napcat()
        return True

    def toggle_qq(self) -> bool:
        """切换 QQ 运行状态，返回操作后的运行状态"""
        if self.is_qq_running():
            self.stop_qq()
            return False
        else:
            return self.start_qq()

    # ------------------------------------------------------------------ #
    #  记忆云图 (memorygraph_visualizer.py)
    # ------------------------------------------------------------------ #

    def is_memory_viz_running(self) -> bool:
        return self._memoryviz_process is not None and self._memoryviz_process.poll() is None

    def open_memory_visualizer(self) -> bool:
        if self.is_memory_viz_running():
            logger.info("[PCService] Memory visualizer already running")
            return True
        script = os.path.join(_PROJECT_ROOT, "brain", "memory", "memorygraph_visualizer.py")
        if not os.path.exists(script):
            logger.error(f"[PCService] Memory visualizer script not found: {script}")
            return False
        try:
            self._memoryviz_process = self._spawn_service(script, "Memory visualizer")
            if self._memoryviz_process is None:
                return False
            logger.info(f"[PCService] Memory visualizer started (pid={self._memoryviz_process.pid})")
            return True
        except Exception as e:
            logger.error(f"[PCService] Failed to open memory visualizer: {e}")
            return False

    def close_memory_visualizer(self) -> bool:
        if not self.is_memory_viz_running():
            return True
        try:
            self._memoryviz_process.terminate()
            self._memoryviz_process.wait(timeout=_Timeouts.SUBPROC_TERMINATE_TIMEOUT_SEC)
        except Exception:
            self._memoryviz_process.kill()
        logger.info("[PCService] Memory visualizer stopped")
        try:
            if self._memoryviz_process.stdout and not self._memoryviz_process.stdout.closed:
                self._memoryviz_process.stdout.close()
        except Exception:
            pass
        self._memoryviz_process = None
        return True

    def toggle_memory_visualizer(self) -> bool:
        """切换记忆云图，返回操作后的运行状态"""
        if self.is_memory_viz_running():
            self.close_memory_visualizer()
            return False
        else:
            return self.open_memory_visualizer()

    # ------------------------------------------------------------------ #
    #  清理（程序退出时调用）
    # ------------------------------------------------------------------ #

    def cleanup(self):
        for name, proc in [
            ("QQ", self._qq_process),
            ("MemoryViz", self._memoryviz_process),
            ("NapCatLauncher", self._napcat_process),
        ]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=_Timeouts.SUBPROC_CLEANUP_TIMEOUT_SEC)
                except Exception:
                    proc.kill()
                logger.info(f"[PCService] {name} terminated on cleanup")
            # 即使进程已经自然退出，stdout PIPE 也需要显式关闭，否则
            # 在主进程里悬着的 fd 会让 GC 在程序退出时报 ResourceWarning，
            # Windows 下偶发会让 main.py 的 _pump_stdout 线程卡在 readline 上无法退出。
            if proc is not None:
                try:
                    if proc.stdout and not proc.stdout.closed:
                        proc.stdout.close()
                    if proc.stderr and not proc.stderr.closed:
                        proc.stderr.close()
                except Exception as e:
                    logger.warning(f"[PCService] {name} stdio close failed: {e}")
        self.stop_voice_input()
        self.stop_voice_output()
        self.stop_screen_ocr()
        self._voice_interaction_active = False
