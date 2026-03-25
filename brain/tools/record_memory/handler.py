"""
记忆记录工具 - 将事件信息写入知识图谱。
调用 MemoryWriter.passive_memory_record 完成结构化记忆存储。
"""

import json
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

        memory_to_record = {
            "event": event,
            "description": description,
        }

        # 先搜索与事件相关的已有记忆，供 LLM 决策参考
        related_memory = get_relevant_memories(
            keywords=[event],
            summary=event,
            max_results=50,
        )

        memory_writer = MemoryWriter(kg_manager=kg_manager)
        result = memory_writer.passive_memory_record(
            memory_to_record=memory_to_record,
            related_memory=related_memory,
        )

        if result and isinstance(result, dict):
            nodes_count = len(result.get("nodes", []))
            relations_count = len(result.get("relations", []))
            return f"记忆记录成功，创建/更新了 {nodes_count} 个节点、{relations_count} 条关系"

        return "记忆记录完成"

    except Exception as e:
        logger.error(f"[record_memory] 记忆记录失败: {e}")
        return f"记忆记录失败: {e}"
