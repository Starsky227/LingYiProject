"""speak 工具 — 模型唯一的发言通道。

该工具把模型本轮的发言通过 context 注入的回调送到 UI 气泡 + TTS，
同时把文本写入会话历史，作为该轮 assistant 的"已发言"记录。

设计：
- 真正的副作用（UI 气泡推送、TTS 播报）由 context['_on_text_output'] 完成；
  这是 lingyi_core 把外部传入的 on_text_output 包装后注入的回调，会顺带写 chat_logs。
- 历史与记忆批次写入直接通过 context['_session_state']，与原先文本输出路径一致。
- 返回值供模型获悉发言成功（function_call_output），尽量简短，避免增加上下文负担。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from system.config import config

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    text = str(args.get("text", "") or "").strip()
    if not text:
        return "speak 调用被忽略：text 为空"

    on_text_output = context.get("_on_text_output")
    session_state = context.get("_session_state")

    # 写入会话历史（assistant role）+ 长期记忆批次
    if session_state is not None:
        try:
            session_state.conversation_context.add_message(text, role="assistant")
            ai_name = config.system.ai_name
            time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            formatted = f"{time_str} <{ai_name}> {text}"
            session_state.memory_batch.add_entry(text=formatted, fixed_keywords=[])
        except Exception as e:
            logger.warning(f"[speak] 写入历史/记忆失败: {e}")

    # 推送到 UI / TTS（包装层会顺便写 chat_logs）
    if callable(on_text_output):
        try:
            on_text_output(text)
        except Exception as e:
            logger.warning(f"[speak] on_text_output 回调异常: {e}")
    else:
        logger.warning("[speak] context 中未注入 _on_text_output，发言未推送到 UI/TTS")

    # 返回简短确认；不复述 text，避免膨胀模型上下文
    return "已发送"
