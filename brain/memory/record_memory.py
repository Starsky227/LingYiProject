#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆写入模块 — memory_agent 核心

调用方式：
    memory_writer = MemoryWriter()
    await memory_writer.full_memory_record(message, related_memory)

full_memory_record 会调用 memory_agent 工具集，将消息中有价值的信息
以节点/关系的形式写入 Neo4j 图谱。
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from brain.memory._agent_runner import run_memory_agent

logger = logging.getLogger(__name__)

_AGENT_DIR = Path(__file__).parent  # brain/memory/
_PROMPT_PATH = _AGENT_DIR / "prompt" / "memory_record_prompt.xml"


def _load_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    logger.error(f"memory_record_prompt.xml 不存在: {_PROMPT_PATH}")
    return ""


_MEMORY_RECORD_PROMPT = _load_prompt()


class MemoryWriter:
    """记忆写入器 — 基于 memory_agent 工具集"""

    def __init__(self, kg_manager=None):
        # kg_manager 保留以兼容旧接口，实际由工具层通过单例获取
        self.kg_manager = kg_manager

    async def full_memory_record(
        self,
        message: str,
        related_memory: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        完整的记忆记录流程。
        输入消息文本，调用 memory_agent 工具集判断并写入记忆图谱。

        Args:
            message: 需要处理的消息内容
            related_memory: 已检索的关联记忆节点，格式 {"nodes": [...], "relations": [...]}
        """
        if not message or not message.strip():
            return

        # 构建用户内容
        parts = [f"请处理以下消息内容，判断是否有需要写入记忆图谱的信息：\n{message}"]
        if related_memory:
            parts.append(f"\n关联记忆（供参考，勿重复写入）：\n{related_memory}")
        user_content = "\n".join(parts)

        await run_memory_agent(
            agent_name="memory_agent",
            user_content=user_content,
            system_prompt=_MEMORY_RECORD_PROMPT,
            tools_dir=_AGENT_DIR / "tools",
            max_iterations=20,
            tool_error_prefix="错误",
        )