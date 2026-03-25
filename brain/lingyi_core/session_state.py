"""
Per-session 状态管理

提供:
- MemoryCache: 储存最近 N 次长期记忆检索结果，去重后提供给模型
- ToolProgress: 追踪当前正在执行的工具，供模型感知
- InputBuffer: 模型处理期间新到达的消息暂存，在工具等待间隙释放给模型
- SessionState: 聚合以上三者的 session 级容器
"""

import asyncio
import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class MemoryCache:
    """记忆缓存 — 储存最近 N 次提取到的记忆节点图，去重后提供给模型

    每次 process_message 搜索到的记忆结果(nodes + relationships)会存入缓存，
    通过 get_deduplicated() 获取最近几轮去重合并后的完整记忆上下文。
    """

    def __init__(self, max_entries: int = 3):
        self._cache: deque[dict] = deque(maxlen=max_entries)

    def add(self, memory_result: dict):
        """添加一次记忆搜索结果到缓存"""
        if memory_result and memory_result.get("nodes"):
            self._cache.append(memory_result)

    def get_deduplicated(self) -> dict:
        """返回去重合并后的所有缓存记忆"""
        all_nodes: list[dict] = []
        all_rels: list[dict] = []
        seen_node_ids: set = set()
        seen_rel_keys: set = set()

        for result in self._cache:
            for node in result.get("nodes", []):
                nid = node.get("id") or node.get("name", "")
                if nid and nid not in seen_node_ids:
                    seen_node_ids.add(nid)
                    all_nodes.append(node)
            for rel in result.get("relationships", []):
                rkey = (
                    rel.get("source", ""),
                    rel.get("type", ""),
                    rel.get("target", ""),
                )
                if rkey not in seen_rel_keys:
                    seen_rel_keys.add(rkey)
                    all_rels.append(rel)

        return {"nodes": all_nodes, "relationships": all_rels}

    def has_content(self) -> bool:
        return bool(self._cache)


class ActiveToolTracker:
    """工具执行状态追踪 — 让模型知道哪些工具正在运行

    在工具调用开始时 start()，完成时 complete()。
    get_status_text() 返回可注入模型上下文的文本描述（包含 call_id 供 cancel 使用）。
    cancel() 可取消正在运行的工具。
    """

    def __init__(self):
        self._in_flight: dict[str, dict] = {}

    def start(self, call_id: str, name: str, args: dict = None, task: asyncio.Task = None):
        """记录一个工具调用开始

        Args:
            call_id: 工具调用唯一标识（即模型生成的 call_id，作为 call_id 暴露给模型）
            name: 工具名称
            args: 调用参数
            task: asyncio.Task 引用，用于支持 cancel
        """
        self._in_flight[call_id] = {
            "name": name,
            "args_summary": str(args)[:100] if args else "",
            "start_time": time.monotonic(),
            "task": task,
        }

    def complete(self, call_id: str):
        """标记一个工具调用完成"""
        self._in_flight.pop(call_id, None)

    def cancel(self, call_id: str) -> str:
        """取消一个正在运行的工具

        Args:
            call_id: 工具调用唯一标识

        Returns:
            操作结果的描述文本
        """
        info = self._in_flight.get(call_id)
        if not info:
            return f"未找到正在运行的任务: {call_id}"
        task: asyncio.Task | None = info.get("task")
        if task and not task.done():
            task.cancel()
            name = info["name"]
            logger.info(f"[ToolProgress] 取消任务: {name} ({call_id})")
            return f"已发送取消信号: {name} ({call_id})"
        return f"任务已完成或无法取消: {call_id}"

    def get_status_text(self) -> str:
        """返回可读的工具进度文本（包含 call_id 供 cancel_task 使用）"""
        if not self._in_flight:
            return ""
        now = time.monotonic()
        lines = []
        for call_id, info in self._in_flight.items():
            elapsed = round(now - info["start_time"], 1)
            lines.append(f"- {info['name']} [call_id: {call_id}] (已运行 {elapsed}s)")
        return "[正在执行的工具]\n" + "\n".join(lines)

    def has_pending(self) -> bool:
        return bool(self._in_flight)

    def clear(self):
        self._in_flight.clear()


class InputBuffer:
    """消息缓冲 — session 正在处理时，新到达的消息暂存于此

    当 is_processing 为 True 时，外部调用者应通过 put() 将消息放入缓冲。
    在工具调用间隙，lingyi_core 会调用 drain_all() 释放缓冲消息给模型，
    供模型决定是否要继续等待工具执行或立即终止。
    """

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._is_processing: bool = False

    @property
    def is_processing(self) -> bool:
        """当前 session 是否正在处理消息（工具循环进行中）"""
        return self._is_processing

    @is_processing.setter
    def is_processing(self, value: bool):
        self._is_processing = value

    async def put(self, message: str, caller_message: str = "", key_words: list = None):
        """将消息放入缓冲"""
        await self._queue.put({
            "message": message,
            "caller_message": caller_message,
            "key_words": key_words or [],
            "time": time.monotonic(),
        })

    def drain_all(self) -> list[dict]:
        """取出所有缓冲消息（非阻塞）"""
        items = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def has_pending(self) -> bool:
        return not self._queue.empty()


class ConversationContext:
    """会话上下文管理 — 储存格式化的对话历史消息

    由外部调用 add_message() 添加格式化后的消息文本，
    lingyi_core 通过 get_formatted_context() 获取完整对话历史。
    """

    def __init__(self, max_messages: int = 15):
        self._messages: list[str] = []
        self._max_messages = max_messages

    def add_message(self, formatted_message: str):
        """添加一条格式化后的消息"""
        self._messages.append(formatted_message)
        if len(self._messages) > self._max_messages:
            self._messages = self._messages[-self._max_messages:]

    def initialize(self, messages: list[str]):
        """初始化历史消息（首次会话创建时从日志读取）"""
        self._messages = list(messages[-self._max_messages:])

    def get_formatted_context(self) -> str:
        """返回所有消息的格式化文本"""
        if not self._messages:
            return ""
        return "".join(self._messages)


class SessionState:
    """单个 session 的完整运行状态

    按 session_key 隔离，确保不同频道/对话的状态互不干扰。
    """

    def __init__(self, session_key: str):
        self.session_key = session_key
        self.memory_cache = MemoryCache()
        self.activity_tracker = ActiveToolTracker()
        self.input_buffer = InputBuffer()
        self.conversation_context = ConversationContext()
        self.tool_context: dict[str, Any] = {}  # 外部上下文，由调用方设置（如 QQ 的 onebot/session/event）
