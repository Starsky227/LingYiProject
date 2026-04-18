"""
Per-session 状态管理

提供:
- MemoryCache: 储存最近 N 次长期记忆检索结果，去重后提供给模型
- ToolProgress: 追踪当前正在执行的工具，供模型感知
- InputBuffer: 模型处理期间新到达的消息暂存，在工具等待间隙释放给模型
- MemoryBatchBuffer: 按固定条数累计条目，到达阈值后触发记忆搜索+记录
- SessionState: 聚合以上各组件的 session 级容器
"""

import asyncio
import logging
import time
from collections import deque
from typing import Any

from system.system_checker import is_neo4j_available

logger = logging.getLogger(__name__)


# 空闲超时刷新记忆（秒）—— 当对话结束后超过该时间没有新消息，强制刷新未保存的记忆
MEMORY_IDLE_FLUSH_TIMEOUT = 600  # 10分钟


class MemoryCache:
    """记忆缓存 — 储存最近 N 次格式化后的记忆文本

    每次消息到达时搜索记忆，格式化后存入缓存，
    通过 get_merged() 获取最近几轮合并去重后的记忆上下文。
    """

    def __init__(self, max_entries: int = 3):
        self._cache: deque[str] = deque(maxlen=max_entries)

    def add(self, formatted_text: str):
        """添加一次格式化后的记忆搜索结果"""
        if formatted_text and formatted_text.strip():
            self._cache.append(formatted_text)

    def get_merged(self) -> str:
        """合并所有缓存记忆，按行去重"""
        seen: set[str] = set()
        lines: list[str] = []
        for text in self._cache:
            for line in text.strip().split("\n"):
                stripped = line.strip()
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    lines.append(stripped)
        return "\n".join(lines)

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

    def start(self, call_id: str, name: str, args: dict = None, task: asyncio.Task = None, tool_call=None):
        """记录一个工具调用开始

        Args:
            call_id: 工具调用唯一标识（即模型生成的 call_id，作为 call_id 暴露给模型）
            name: 工具名称
            args: 调用参数
            task: asyncio.Task 引用，用于支持 cancel
            tool_call: 原始 tool_call 对象，用于在进度上下文中保留调用意图
        """
        self._in_flight[call_id] = {
            "name": name,
            "args_summary": str(args)[:100] if args else "",
            "start_time": time.monotonic(),
            "task": task,
            "tool_call": tool_call,
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
        """返回可读的工具进度文本（包含 call_id 供 cancel_task 使用，以及调用参数摘要）"""
        if not self._in_flight:
            return ""
        now = time.monotonic()
        lines = []
        for call_id, info in self._in_flight.items():
            elapsed = round(now - info["start_time"], 1)
            line = f"- {info['name']} [call_id: {call_id}] (已运行 {elapsed}s)"
            if info.get("args_summary"):
                line += f" 参数: {info['args_summary']}"
            lines.append(line)
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

    def __init__(self, max_messages: int = 25):
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


class MemoryBatchBuffer:
    """长期记忆批次缓冲

    按固定条数累计“消息/工具调用结果”条目，到达阈值后一次性触发：
    1) search_memory
    2) memory_record
    """

    def __init__(self, trigger_count: int = 10):
        self.trigger_count = max(1, trigger_count)
        self._entries: list[dict[str, Any]] = []

    @staticmethod
    def _dedupe_keywords(keywords: list[str]) -> list[str]:
        ordered: list[str] = []
        for kw in keywords:
            token = str(kw or "").strip()
            if token and token not in ordered:
                ordered.append(token)
        return ordered

    def add_entry(self, text: str, fixed_keywords: list[str]):
        if not text or not text.strip():
            return
        self._entries.append({
            "text": text.strip(),
            "fixed_keywords": [kw for kw in (fixed_keywords or []) if kw],
        })

    def build_batch_payload(self, batch: list[dict[str, Any]]) -> tuple[str, list[str]]:
        """将一组条目构建为记忆搜索/记录所需的文本和关键词"""
        lines: list[str] = []
        merged_keywords: list[str] = []

        for idx, item in enumerate(batch, start=1):
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            lines.append(f"{idx}. {text}")
            merged_keywords.extend(item.get("fixed_keywords", []))

        payload = "[批次消息]\n" + "\n".join(lines)
        return payload, self._dedupe_keywords(merged_keywords)

    def has_ready_batch(self) -> bool:
        return len(self._entries) >= self.trigger_count

    def pop_ready_batch(self) -> list[dict[str, Any]]:
        if not self.has_ready_batch():
            return []
        batch = self._entries[:self.trigger_count]
        self._entries = self._entries[self.trigger_count:]
        return batch

    def pending_count(self) -> int:
        return len(self._entries)

    def flush_all(self) -> list[dict[str, Any]]:
        """强制弹出所有剩余条目（不足 trigger_count 也弹出），用于关闭或超时场景"""
        if not self._entries:
            return []
        batch = list(self._entries)
        self._entries.clear()
        return batch


class SessionState:
    """单个 session 的完整运行状态

    按 session_key 隔离，确保不同频道/对话的状态互不干扰。
    """

    def __init__(self, session_key: str):
        self.session_key = session_key
        self.memory_cache = MemoryCache()
        self.memory_batch = MemoryBatchBuffer(trigger_count=20)
        self.activity_tracker = ActiveToolTracker()
        self.input_buffer = InputBuffer()
        self.conversation_context = ConversationContext()
        self.tool_context: dict[str, Any] = {}  # 外部上下文，由调用方设置（如 QQ 的 onebot/session/event）
        self.external_pending_images: list[dict] = []  # 外部注入的待处理图片 [{data_url, description}]
        self.screen_context: str = ""  # 屏幕 OCR 最新文字（被动环境信息）
        self.scratchpad: str = ""  # AI 可读写的临时便签（session 级）
        self._memory_writer = None  # 由外部设置，用于空闲刷新
        self._idle_flush_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    #  记忆批处理：搜索 + 记录
    # ------------------------------------------------------------------ #

    async def process_ready_batches(self, memory_writer) -> None:
        """处理所有就绪的记忆批次（每个批次作为后台非阻塞任务）"""
        if not is_neo4j_available():
            return
        while self.memory_batch.has_ready_batch():
            ready_batch = self.memory_batch.pop_ready_batch()
            memory_text, fixed_keywords = self.memory_batch.build_batch_payload(ready_batch)
            if not memory_text.strip():
                continue
            asyncio.create_task(
                self._memory_search_and_record(
                    memory_writer, memory_text, fixed_keywords, len(ready_batch),
                )
            )

    async def flush_pending_memory(self, memory_writer) -> None:
        """强制刷新所有未保存的记忆条目（不足阈值也刷新）"""
        if not is_neo4j_available():
            return
        remaining = self.memory_batch.flush_all()
        if not remaining:
            return
        memory_text, fixed_keywords = self.memory_batch.build_batch_payload(remaining)
        if not memory_text.strip():
            return
        await self._memory_search_and_record(
            memory_writer, memory_text, fixed_keywords, len(remaining),
        )

    async def _memory_search_and_record(
        self,
        memory_writer,
        memory_text: str,
        fixed_keywords: list[str],
        batch_count: int,
    ) -> None:
        """后台执行：同步的 full_memory_search 放入线程池，完成后异步写入记忆"""
        from brain.memory.search_memory import full_memory_search

        try:
            related_memory = await asyncio.to_thread(
                full_memory_search,
                memory_text,
                False,          # save_temp_memory
                fixed_keywords,  # add_keywords
            )
            logger.info(
                f"[记忆批处理] session={self.session_key} 触发一次，条数={batch_count}，"
                f"关键词数={len(fixed_keywords)}，关联节点={len(related_memory.get('nodes', []))}"
            )
            record_payload = f"{memory_text}\n\n[固定关键词]\n{fixed_keywords}"
            await memory_writer.full_memory_record(record_payload, related_memory)
        except Exception as e:
            logger.error(f"[记忆批处理] session={self.session_key} 失败: {e}")

    # ------------------------------------------------------------------ #
    #  记忆刷新：空闲超时
    # ------------------------------------------------------------------ #

    def schedule_idle_flush(self, memory_writer) -> None:
        """启动空闲刷新计时器：如果 MEMORY_IDLE_FLUSH_TIMEOUT 秒内无新对话，强制刷新未保存的记忆

        每次对话结束时调用，会取消已有计时器并重新计时。
        计时器到期后只触发一次刷新，不会反复触发。
        """
        self._memory_writer = memory_writer
        self.cancel_idle_flush()

        async def _idle_wait():
            await asyncio.sleep(MEMORY_IDLE_FLUSH_TIMEOUT)
            logger.info(f"[记忆刷新] session={self.session_key} 空闲超时，强制刷新未保存的记忆")
            await self.flush_pending_memory(self._memory_writer)

        try:
            self._idle_flush_task = asyncio.create_task(_idle_wait())
        except RuntimeError:
            # 没有运行中的事件循环，忽略
            pass

    def cancel_idle_flush(self) -> None:
        """取消已有的空闲刷新计时器"""
        if self._idle_flush_task and not self._idle_flush_task.done():
            self._idle_flush_task.cancel()
        self._idle_flush_task = None
