# -*- coding: utf-8 -*-
"""五元组提取器模块 - 将消息转换为记忆五元组并存储"""

from typing import List, Dict, Any
import json
import os
from datetime import datetime

from brain.memory.knowledge_graph_manager import write_memories_to_graph


def extract_quintuples_from_messages(messages: List[Dict], username: str) -> List[Dict]:
    """从消息列表中提取五元组

    Args:
        messages: 格式化后的消息列表 [{"role": "...", "content": "..."}]
        username: 当前用户名

    Returns:
        五元组列表
    """
    quintuples = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if not content:
            continue

        if role == "user" or role == username:
            quintuple = {
                "subject": username,
                "predicate": "说",
                "object": content,
                "time": "unknown",
                "context": "chat_history",
            }
            quintuples.append(quintuple)
        elif role == "assistant":
            timestamp = msg.get("timestamp", "")
            quintuple = {
                "subject": "AI助手",
                "predicate": "回复",
                "object": content,
                "time": timestamp,
                "context": "chat_history",
            }
            quintuples.append(quintuple)

    return quintuples


def record_messages_to_memories(messages: List[Dict], username: str) -> str:
    """将消息记录到记忆系统

    Args:
        messages: 格式化后的消息列表 [{"role": "...", "content": "..."}]
        username: 当前用户名

    Returns:
        记忆ID
    """
    try:
        quintuples = extract_quintuples_from_messages(messages, username)

        if not quintuples:
            return ""

        result = write_memories_to_graph(triples=[], quintuples=quintuples)

        if result.get("success"):
            return f"memory_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        else:
            return ""

    except Exception as e:
        print(f"[错误] 记录消息到记忆失败: {e}")
        return ""
