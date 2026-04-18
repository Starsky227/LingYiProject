"""
临时便签工具 - 读写 session 级临时笔记。
"""

from typing import Any

MAX_LENGTH = 500


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    session_state = context.get("_session_state")
    if session_state is None:
        return "内部错误：无法访问 session 状态"

    action = args.get("action", "read")

    if action == "read":
        text = session_state.scratchpad
        return text if text else "(便签为空)"

    if action == "write":
        content = str(args.get("content", ""))
        if len(content) > MAX_LENGTH:
            content = content[:MAX_LENGTH]
        session_state.scratchpad = content
        return f"便签已更新（{len(content)}字）"

    return f"未知操作: {action}"
