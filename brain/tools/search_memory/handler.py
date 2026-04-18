"""
记忆搜索工具 - 通过关键词在知识图谱中检索相关记忆。
调用 get_relevant_memories 完成多轮扩展式记忆检索。
"""

import logging
from typing import Any

from brain.memory.search_memory import get_relevant_memories, get_formatted_memory_graph
from system.system_checker import is_neo4j_available

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    key_words = args.get("key_words", [])
    max_results = args.get("max_results", 50)

    if not key_words:
        return "缺少必要参数: key_words"

    if not isinstance(key_words, list):
        return "参数格式错误: key_words 应为字符串列表"

    # 限制 max_results 范围
    max_results = max(50, min(150, int(max_results)))

    if not is_neo4j_available():
        return "Neo4j 未连接，无法搜索记忆"

    try:
        result = get_relevant_memories(
            keywords=key_words,
            max_results=max_results,
        )

        nodes = result.get("nodes", [])
        relationships = result.get("relationships", [])

        if not nodes:
            return "未找到相关记忆"

        formatted = get_formatted_memory_graph(memory_data=result)
        return f"找到 {len(nodes)} 个节点、{len(relationships)} 条关系\n{formatted}"

    except Exception as e:
        logger.error(f"[search_memory] 记忆搜索失败: {e}")
        return f"记忆搜索失败: {e}"
