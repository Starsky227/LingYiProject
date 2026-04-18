"""
记忆记录工具 - 将事件信息委托给 memory_agent 写入知识图谱。
调用 MemoryWriter.full_memory_record 将内容交给专门的记忆 Agent 处理。
"""

import logging
from typing import Any

from brain.memory.record_memory import MemoryWriter
from brain.memory.search_memory import get_relevant_memories
from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from system.system_checker import is_neo4j_available

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    event = args.get("event", "").strip()
    description = args.get("description", "").strip()

    if not event:
        return "缺少必要参数: event"

    if not is_neo4j_available():
        return "Neo4j 未连接，无法记录记忆"

    try:
        kg_manager = get_knowledge_graph_manager()
        if not kg_manager.connected:
            return "Neo4j 连接不可用，无法记录记忆"

        # 先搜索与事件相关的已有记忆，供 memory_agent 决策参考
        related_memory = get_relevant_memories(
            keywords=[event],
            summary=event,
            max_results=50,
        )

        # 构建消息文本，委托给 memory_agent 处理
        message = f"事件：{event}"
        if description:
            message += f"\n详细描述：{description}"

        memory_writer = MemoryWriter(kg_manager=kg_manager)
        await memory_writer.full_memory_record(
            message=message,
            related_memory=related_memory,
        )

        return "记忆记录请求已提交给记忆系统处理"

    except Exception as e:
        logger.error(f"[record_memory] 记忆记录失败: {e}")
        return f"记忆记录失败: {e}"
