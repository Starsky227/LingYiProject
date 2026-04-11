from __future__ import annotations

from typing import Any

from brain.memory.tools._common import format_json


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    del context

    summary = str(args.get("summary", "")).strip()
    if not summary:
        summary = "memory_agent 会话已结束"

    return format_json(
        {
            "success": True,
            "ended": True,
            "summary": summary,
        }
    )
