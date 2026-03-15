"""
结束当前AI行为轮次。
AI在完成消息回复（send_message）后调用此工具，通知调用方本轮处理已结束。
返回特殊标记 __END__，由 lingyi_core 的工具循环识别并终止循环。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 工具循环终止标记
END_SIGNAL = "__END__"


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    summary = args.get("summary", "")
    logger.info(f"[end] AI结束发言: {summary}")
    return END_SIGNAL