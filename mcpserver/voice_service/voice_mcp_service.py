"""
Voice MCP Service — 语音交互自动组件

非 AI 工具，不参与模型 tool_call。
在用户启用"语音交互"后，自动挂载到 AI 每一条 message 输出上：
- stream_speak: 按句子分段流式播放 AI 的回复
- interrupt:    用户开口即停
- 用户说话检测: user_speaking 标记，供 LingYiCore 等待

底层调用 service/pcAssistant/voice_output (Qwen3TTS)：
  - 使用 LingYiVoice.wav 作为音源
  - 仅输入 text（不输入 prompt）
  - 临时文件暂存于 data/cache/voice_output，播完即删
"""

import logging
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class VoiceMCPService:
    """语音交互自动组件 — 单例，包装 Qwen3TTS 提供流式语音输出"""

    _instance: Optional["VoiceMCPService"] = None

    def __init__(self):
        self._tts_service = None
        self._voice_input_service = None  # VoiceInputVDLService 实例
        self._user_speaking = threading.Event()
        self._tts_playing = threading.Event()  # TTS 正在播放，用于信号隔离
        self._stream_sentence_count = 0  # 流式会话已发送句子数

    @classmethod
    def get_instance(cls) -> "VoiceMCPService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ #
    #  TTS 服务注入
    # ------------------------------------------------------------------ #

    def set_tts_service(self, tts_service) -> None:
        """注入 QwenTTSOutputService 实例"""
        self._tts_service = tts_service

    def set_voice_input_service(self, voice_input_service) -> None:
        """注入 VoiceInputVDLService 实例（用于信号隔离控制）"""
        self._voice_input_service = voice_input_service

    @property
    def has_tts(self) -> bool:
        return self._tts_service is not None

    # ------------------------------------------------------------------ #
    #  用户说话检测
    # ------------------------------------------------------------------ #

    @property
    def is_user_speaking(self) -> bool:
        return self._user_speaking.is_set()

    @property
    def is_tts_playing(self) -> bool:
        return self._tts_playing.is_set()

    def on_user_speech_start(self) -> None:
        """VAD 检测到用户开始说话 → 设置标记 + 打断 TTS"""
        logger.info("[VoiceMCP] 用户开始说话，打断 TTS")
        self._user_speaking.set()
        self.interrupt()

    def on_user_speech_end(self) -> None:
        """用户说话结束 → 清除标记"""
        self._user_speaking.clear()

    def wait_user_done(self, timeout: float = 30.0) -> bool:
        """阻塞等待用户说完话

        Returns:
            True 如果用户说完了，False 如果超时
        """
        if not self._user_speaking.is_set():
            return True
        deadline = time.monotonic() + timeout
        while self._user_speaking.is_set():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.1)
        return True

    # ------------------------------------------------------------------ #
    #  语音输出
    # ------------------------------------------------------------------ #

    def interrupt(self) -> str:
        """打断当前语音播放，恢复麦克风输入"""
        self._tts_playing.clear()
        self._unmute_mic()
        if self._tts_service is None:
            return "TTS 服务未启动"
        try:
            return self._tts_service.interrupt_playback(clear_pending=True)
        except Exception as e:
            logger.error(f"[VoiceMCP] interrupt failed: {e}")
            return f"打断语音失败: {e}"

    # ------------------------------------------------------------------ #
    #  麦克风信号隔离（借鉴 Xiao8 架构：播放期间完全屏蔽输入信号）
    # ------------------------------------------------------------------ #

    def _mute_mic(self) -> None:
        if self._voice_input_service is not None:
            try:
                self._voice_input_service.mute()
            except Exception as e:
                logger.debug(f"[VoiceMCP] mute mic failed: {e}")

    # ------------------------------------------------------------------ #
    #  流式 TTS 句子级接口（供 LingYiCore 流式回调使用）
    # ------------------------------------------------------------------ #

    def start_streaming(self) -> None:
        """开始流式 TTS 会话 — 静音麦克风，标记播放状态"""
        self._tts_playing.set()
        self._mute_mic()
        self._stream_sentence_count = 0

    def speak_sentence(self, sentence: str, is_first: bool = False) -> None:
        """流式播放单个句子（由 LingYiCore 回调驱动）"""
        if self._tts_service is None:
            return
        if self._user_speaking.is_set():
            return
        try:
            self._tts_service.speak_text(
                text=sentence,
                voice="",
                instructions="",
                append=(self._stream_sentence_count > 0),
            )
            self._stream_sentence_count += 1
        except Exception as e:
            logger.error(f"[VoiceMCP] speak_sentence failed: {e}")

    def finish_streaming(self) -> None:
        """结束流式 TTS 会话 — 启动后台监视线程等待播放完毕后恢复麦克风"""
        if self._stream_sentence_count > 0:
            threading.Thread(
                target=self._wait_playback_done,
                name="voice-mcp-tts-done",
                daemon=True,
            ).start()
        else:
            self._tts_playing.clear()
            self._unmute_mic()

    def _unmute_mic(self) -> None:
        if self._voice_input_service is not None:
            try:
                self._voice_input_service.unmute()
            except Exception as e:
                logger.debug(f"[VoiceMCP] unmute mic failed: {e}")

    def stream_speak(self, text: str, language: str = "", speed: float = 1.0) -> str:
        """流式语音输出 — 按句子分段逐句播放

        信号隔离策略：播放前静音麦克风，播放完毕后恢复。
        """
        if self._tts_service is None:
            return "TTS 服务未启动"

        sentences = self._split_sentences(text)
        if not sentences:
            return "无有效文本"

        self._tts_playing.set()
        self._mute_mic()  # 信号隔离：播放前静音麦克风
        queued = 0
        try:
            for sentence in sentences:
                if self._user_speaking.is_set():
                    logger.info("[VoiceMCP] 用户开始说话，停止流式播放")
                    break
                try:
                    self._tts_service.speak_text(
                        text=sentence,
                        voice="",
                        instructions="",
                        speed=speed,
                        language=language,
                        append=(queued > 0),
                    )
                    queued += 1
                except Exception as e:
                    logger.error(f"[VoiceMCP] stream_speak sentence failed: {e}")
        finally:
            # 启动后台监视线程，在播放结束后恢复麦克风
            threading.Thread(
                target=self._wait_playback_done,
                name="voice-mcp-tts-done",
                daemon=True,
            ).start()

        return f"已开始流式语音播放，共 {queued}/{len(sentences)} 段"

    def _wait_playback_done(self) -> None:
        """后台等待 TTS 播放队列清空后恢复麦克风输入"""
        try:
            tts = self._tts_service
            if tts is None:
                return
            while self._tts_playing.is_set():
                if not tts.is_running:
                    break
                if tts._request_queue.empty() and tts._playback_queue.empty():
                    # 额外等待一小段确保最后一个 chunk 播放完毕
                    time.sleep(0.5)
                    if tts._request_queue.empty() and tts._playback_queue.empty():
                        break
                time.sleep(0.1)
        except Exception as e:
            logger.debug(f"[VoiceMCP] _wait_playback_done: {e}")
        finally:
            self._tts_playing.clear()
            self._unmute_mic()  # 播放完毕：恢复麦克风

    # ------------------------------------------------------------------ #
    #  文本分句
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """将文本按句子边界分割"""
        parts = re.split(r'(?<=[。！？.!?\n；;])', text)
        sentences = []
        for p in parts:
            p = p.strip()
            if p:
                sentences.append(p)
        return sentences if sentences else ([text.strip()] if text.strip() else [])
