from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentserver.runner import run_agent_with_tools

logger = logging.getLogger(__name__)


async def _download_file(file_source: str, context: dict[str, Any]) -> str:
    """在进入 agent 循环前，先硬性下载文件并返回本地路径。"""
    from agentserver.file_analysis_agent.tools.download_file.handler import execute as download_execute
    return await download_execute({"file_source": file_source}, context)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    """执行 file_analysis_agent。"""

    file_source = str(args.get("file_source", "")).strip()
    user_prompt = str(args.get("prompt", "")).strip()

    if not file_source:
        return "请提供文件 URL 或 file_id"

    # 硬性下载：在交给 agent 循环前先确保文件已在本地
    local_path = await _download_file(file_source, context)
    if local_path.startswith("错误"):
        return f"文件下载失败：{local_path}"

    if user_prompt:
        user_content = f"文件已下载到本地路径：{local_path}\n\n用户需求：{user_prompt}"
    else:
        user_content = f"文件已下载到本地路径：{local_path}\n\n请分析这个文件。"

    result = await run_agent_with_tools(
        agent_name="file_analysis_agent",
        user_content=user_content,
        empty_user_content_message="请提供文件 URL 或 file_id",
        default_prompt="你是一个专业的文件分析助手...",
        context=context,
        agent_dir=Path(__file__).parent,
        logger=logger,
        max_iterations=30,
        tool_error_prefix="错误",
    )
    return f"{{valuable}}{result}"
