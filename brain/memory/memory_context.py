# -*- coding: utf-8 -*-
"""
会话上下文管理模块，用于记录对话历史和事件提取结果。
"""

from typing import Dict, Any, List
from collections import deque
from datetime import datetime


class MemoryContext:
    """会话上下文管理器，用于记录对话历史和事件提取结果"""
    
    def __init__(self, session_id: str, max_messages: int = 25):
        self.session_id = session_id
        self.messages = deque(maxlen=max_messages)
        self.extracted_events = deque(maxlen=10)  # 记录提取的事件
        self.last_updated = None

    def add_message(self, role: str, content: str):
        """添加对话消息"""
        self.messages.append({
            "role": role,
            "content": content,
            "time": datetime.now().isoformat(),
        })
        self.last_updated = datetime.now()

    def add_extracted_event(self, event_data: Dict[str, Any]):
        """记录提取的事件数据"""
        self.extracted_events.append({
            "event_data": event_data,
            "session_id": self.session_id,
            "time": datetime.now().isoformat(),
        })
        self.last_updated = datetime.now()

    def get_context(self) -> List[Dict[str, Any]]:
        """获取对话上下文"""
        return list(self.messages)
    
    def get_extracted_events(self) -> List[Dict[str, Any]]:
        """获取提取的事件列表"""
        return list(self.extracted_events)

    def clear(self):
        """清空上下文"""
        self.messages.clear()
        self.extracted_events.clear()
        self.last_updated = None
