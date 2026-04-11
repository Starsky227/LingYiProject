"""
PCAssistant 主入口

启动 PyQt 聊天界面并将其连接到 LingYiCore，同时管理各后台服务
（QQ Bot、记忆云图等）。
"""

import os
import sys
import asyncio
import logging
import threading
from pathlib import Path
from concurrent.futures import Future

# ---- 将项目根目录加入模块搜索路径 ----
_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon

from system.config import config
from ui.pyqt_chat_ui import ChatWindow
from brain.lingyi_core.lingyi_core import LingYiCore
from service.pcAssistant.service_manager import PCServiceManager

logger = logging.getLogger(__name__)


SESSION_KEY = "pc_chat"
_PROMPT_PATH = Path(_PROJECT_ROOT) / "system" / "prompts" / "personality.txt"


def _load_main_prompt() -> str:
    if _PROMPT_PATH.exists():
        raw = _PROMPT_PATH.read_text(encoding="utf-8")
        return raw.format(
            AI_NAME=config.system.ai_name,
            USERNAME=config.system.user_name,
        )
    logger.warning(f"[PCAssistant] personality.txt not found: {_PROMPT_PATH}")
    return f"你是 {config.system.ai_name}，一位友善的 AI 助手。"


# ---------------------------------------------------------------------------- #
#  后台 asyncio 事件循环桥接
# ---------------------------------------------------------------------------- #

class _AsyncBridge:
    """在独立线程中持有 asyncio 事件循环，供同步代码提交协程"""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="async-bridge")
        self._thread.start()
        self._ready.wait()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def call_async(self, coro) -> Future:
        """从任意线程提交协程，返回 concurrent.futures.Future"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run_sync(self, coro):
        """阻塞调用协程并返回结果（在非 asyncio 线程中使用）"""
        return self.call_async(coro).result()

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)


# ---------------------------------------------------------------------------- #
#  lingyi_core 输出解析辅助
# ---------------------------------------------------------------------------- #

def _extract_reply_text(output_messages: list) -> str:
    """从 lingyi_core 的 process_message 返回值中提取文本"""
    parts = []
    for item in output_messages:
        if hasattr(item, "content"):
            c = item.content
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for block in c:
                    if hasattr(block, "text"):
                        parts.append(block.text)
    return "".join(parts).strip()


# ---------------------------------------------------------------------------- #
#  主聊天回调（UI 工作线程调用，阻塞直至 lingyi_core 返回）
# ---------------------------------------------------------------------------- #

def _make_chat_callback(lingyi: LingYiCore, bridge: _AsyncBridge, service_manager: PCServiceManager):
    def chat_with_model(messages: list, on_response) -> list:
        # 取最新一条用户消息
        user_msg = ""
        for msg in reversed(messages):
            role = msg.get("role", "")
            if role not in ("assistant", "system") and msg.get("content", "").strip():
                user_msg = msg["content"].strip()
                break

        if not user_msg:
            return []

        # 将消息放入 InputBuffer
        session_state = lingyi.get_session_state(SESSION_KEY)
        session_state.tool_context.update(
            {
                "voice_output_service": service_manager.get_voice_output_service(),
                "voice_output_enabled": service_manager.is_voice_output_running(),
                "source": "pcAssistant",
                "channel": "pc",
            }
        )

        async def _put_and_process():
            await session_state.input_buffer.put(
                message=user_msg,
                caller_message="来自PC客户端的消息",
                key_words=[],
            )
            return await lingyi.process_message(SESSION_KEY)

        try:
            output = bridge.run_sync(_put_and_process())
        except Exception as e:
            logger.exception(f"[PCAssistant] lingyi_core error: {e}")
            on_response(f"[处理出错] {e}")
            return [{"role": "assistant", "content": f"[处理出错] {e}"}]

        reply = _extract_reply_text(output)
        if reply:
            on_response(reply)

        return [{"role": "assistant", "content": reply}]

    return chat_with_model


# ---------------------------------------------------------------------------- #
#  主函数
# ---------------------------------------------------------------------------- #

def main():
    logging.basicConfig(
        level=getattr(logging, config.system.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # ---- 启动后台 asyncio 桥接 ----
    bridge = _AsyncBridge()
    bridge.start()

    # ---- 初始化 lingyi_core ----
    logger.info("[PCAssistant] 初始化 LingYiCore ...")
    main_prompt = _load_main_prompt()
    lingyi = LingYiCore(main_prompt=main_prompt)

    # ---- 服务管理器 ----
    service_manager = PCServiceManager()

    # ---- 启动 Qt 应用 ----
    app = QApplication.instance() or QApplication(sys.argv)

    icon_path = str(Path(_PROJECT_ROOT) / "ui" / "img" / "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = ChatWindow()

    # 注入服务管理器（图片区服务按钮 + 聊天区语音按钮）
    window.set_service_manager(service_manager)

    # 注入语音输入文本回调：语音转写后自动作为用户消息发送
    service_manager.set_voice_text_callback(window.submit_external_message)

    # 注入聊天回调
    window.set_chat_callback(_make_chat_callback(lingyi, bridge, service_manager))

    window.show()

    # ---- 退出时清理 ----
    app.aboutToQuit.connect(service_manager.cleanup)
    app.aboutToQuit.connect(bridge.stop)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
