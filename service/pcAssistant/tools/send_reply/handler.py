"""
助手模式回复工具 — AI 通过此工具主动向用户发送消息。

在助手模式下，AI 不会自动回复每条消息。
当 AI 判断需要发言时，调用此工具将回复内容推送到聊天窗口。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    message = args.get("message", "").strip()
    if not message:
        return "错误: 消息内容不能为空"

    reply_callback = context.get("assistant_reply_callback")
    if reply_callback:
        try:
            reply_callback(message)
        except Exception as e:
            logger.warning(f"[send_reply] 回调执行失败: {e}")
            return f"消息发送失败: {e}"
    else:
        logger.warning("[send_reply] 未找到 assistant_reply_callback，消息无法推送到 UI")

    return f"{{valuable}}[AI回复] {message}"
