"""统一的 chat_logs 写入入口。

所有用户/AI 对话文本都应通过本模块写入，确保单一事实源。
- 文件路径：{config.system.log_dir}/chat_logs/chat_logs_YYYY_MM_DD.txt
- 行格式：[HH:MM:SS] text

调用方负责在 ``text`` 内自带 ``<发送者>`` 前缀（注入点已统一）。
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Optional

from system.config import config

logger = logging.getLogger(__name__)


def write_chat_log(text: str, timestamp: Optional[str] = None) -> None:
    """将单条对话追加到 chat_logs 目录。

    Args:
        text: 消息内容（多行会被压平为单行，避免破坏行格式）。
        timestamp: 可选的 ISO 时间戳；为空时使用当前时间。
    """
    try:
        logs_dir = os.path.join(str(config.system.log_dir), "chat_logs")
        os.makedirs(logs_dir, exist_ok=True)
        filename = datetime.datetime.now().strftime("chat_logs_%Y_%m_%d.txt")
        path = os.path.join(logs_dir, filename)

        if timestamp is None:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
        else:
            try:
                dt = datetime.datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
                ts = dt.strftime("%H:%M:%S")
            except (ValueError, AttributeError):
                ts = datetime.datetime.now().strftime("%H:%M:%S")

        safe_text = (text or "").replace("\r", " ").replace("\n", " ")
        line = f"[{ts}] {safe_text}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"[chat_log] 写入失败: {e}")
