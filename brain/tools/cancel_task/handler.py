"""
取消任务工具 — 终止一个正在运行的工具调用。

通过 context 中的 activity_tracker 查找并取消指定 task_id 对应的 asyncio.Task。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    call_id = args.get("call_id", "").strip()
    if not call_id:
        return "缺少必要参数: call_id"

    activity_tracker = context.get("activity_tracker")
    if not activity_tracker:
        return "当前环境不支持任务取消"

    return activity_tracker.cancel(call_id)
