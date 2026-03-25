import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI
from system.config import config
from brain.memory.search_memory import full_memory_search
from brain.memory.record_memory import MemoryWriter
from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from system.system_checker import is_neo4j_available
from brain.lingyi_core.tool_manager import ToolManager, LocalToolRegistry, AgentSubRegistry
from brain.lingyi_core.session_state import SessionState

logger = logging.getLogger(__name__)

# 初始化 OpenAI 客户端
client = OpenAI(
    api_key=config.api.api_key,
    base_url=config.api.base_url,
)


# 最大工具调用轮次，防止无限循环
MAX_TOOL_ROUNDS = 10

# 无工具调用时等待 buffer 新消息的超时（秒）
BUFFER_WAIT_TIMEOUT = 1.0

# 工具调用记录在 input_items 中的存活轮次（超过后清理已完成的 tool_call + result）
TOOL_CALL_TTL = 5

# 消息批量收集窗口
BATCH_MIN_WAIT = 2.0  # 首条消息后至少等待秒数
BATCH_MAX_WAIT = 5.0  # 首条消息后最多等待秒数


class LingYiCore:
    """AI 模型客户端 — 自主协调器"""

    def __init__(self, main_prompt: str) -> None:
        self.main_prompt = main_prompt
        self._kg_manager = get_knowledge_graph_manager()
        self._memory_writers: dict[str, MemoryWriter] = {}

        # 工具管理器 — 多来源聚合
        self.tool_manager = ToolManager()

        # brain/tools sub-registry (prefix: main-)
        brain_tools_dir = Path(__file__).parent.parent / "tools"
        brain_registry = LocalToolRegistry(brain_tools_dir)
        brain_registry.load_items()
        self.tool_manager.register_sub_registry("main-", brain_registry)

        # agentserver agents sub-registry (prefix: main-)
        agent_registry = AgentSubRegistry()
        agent_registry.discover()
        self.tool_manager.register_sub_registry("main-", agent_registry)

        # Per-session 状态（memory_cache / activity_tracker / input_buffer）
        self._session_states: dict[str, SessionState] = {}

    def get_session_state(self, session_key: str) -> SessionState:
        """获取指定 session 的状态（外部可用于 input_buffer 等交互）"""
        if session_key not in self._session_states:
            self._session_states[session_key] = SessionState(session_key)
        return self._session_states[session_key]

    def _get_memory_writer(self, session_key: str) -> MemoryWriter:
        """获取指定会话的 MemoryWriter（按 session_key 隔离已提取事件记录）"""
        if session_key not in self._memory_writers:
            self._memory_writers[session_key] = MemoryWriter(kg_manager=self._kg_manager)
        return self._memory_writers[session_key]

    def _parse_response(self, resp):
        messages = []
        toolcalls = []

        for item in resp.output:
            if item.type == "message":
                messages.append(item)
            elif item.type == "tool_call":
                toolcalls.append(item)

        return messages, toolcalls

    async def process_message(
        self,
        session_key: str = "default",
    ) -> list:
        """处理输入信息，通过工具调用循环生成回复

        从 InputBuffer 收集消息（等待 2-5s 批量窗口），然后进入工具调用循环。
        所有工具统一通过 tool_manager 执行。

        Args:
            session_key: 会话标识（用于隔离 MemoryWriter / SessionState）

        Returns:
            模型输出的消息列表
        """
        logger.info(f"[process_message] session={session_key} 开始处理")

        session_state = self.get_session_state(session_key)
        session_state.input_buffer.is_processing = True
        session_state.activity_tracker.clear()

        try:
            return await self._process_message_inner(
                session_key, session_state,
            )
        finally:
            session_state.input_buffer.is_processing = False
            session_state.activity_tracker.clear()

    async def _collect_batch(self, session_state: "SessionState") -> None:
        """等待 2-5s 收集初始消息批次

        首条消息通常已在 buffer 中（触发 process_message 前就已 put）。
        等待至少 BATCH_MIN_WAIT 秒，最多 BATCH_MAX_WAIT 秒，给连续发言的消息窗口。
        """
        start = time.monotonic()
        # 等待首条消息到达
        while not session_state.input_buffer.has_pending():
            if time.monotonic() - start > BATCH_MAX_WAIT:
                return
            await asyncio.sleep(0.1)

        # 首条消息到达后，继续等待更多消息
        first_msg_time = time.monotonic()
        while True:
            elapsed = time.monotonic() - first_msg_time
            total = time.monotonic() - start
            if elapsed >= BATCH_MIN_WAIT or total >= BATCH_MAX_WAIT:
                break
            await asyncio.sleep(0.2)

    async def _process_message_inner(
        self,
        session_key: str,
        session_state: "SessionState",
    ) -> list:
        """核心处理逻辑 — 非阻塞后台工具执行

        工作流程：
        0. 等待 2-5s 收集初始消息批次
        1. 汇总所有信息交给大模型
        2. 大模型思考期间，新消息进入 buffer 但不输入给模型
        3. 大模型输出 message 和 tool_call；工具作为后台 Task 启动，不阻塞循环
        4. 每轮收割已完成的工具结果 + buffer 新消息，一起交给模型
        5. 无新上下文（无完成的工具、无 buffer）时等待至多 1s
        6. 后台任务 & buffer 都空则结束，否则重复
        """
        memory_writer = self._get_memory_writer(session_key)

        # 0. 等待收集初始消息批次
        await self._collect_batch(session_state)
        initial_messages = session_state.input_buffer.drain_all()
        if not initial_messages:
            logger.info(f"[process_message] session={session_key} 无消息可处理")
            return []

        # 合并初始消息
        input_message = "\n".join(msg["message"] for msg in initial_messages)
        merged_keywords: list[str] = []
        for msg in initial_messages:
            for kw in msg.get("key_words", []):
                if kw and kw not in merged_keywords:
                    merged_keywords.append(kw)
        caller_message = initial_messages[-1].get("caller_message", "")

        if len(initial_messages) > 1:
            logger.info(f"[消息收集] session={session_key} 收集了 {len(initial_messages)} 条消息")
        logger.info(f"[收到输入信息] \n{input_message}")

        # 1. 搜索相关记忆并缓存
        related_memory = None
        if is_neo4j_available():
            related_memory = full_memory_search(input_message, save_temp_memory=False, add_keywords=merged_keywords)
            session_state.memory_cache.add(related_memory)
            logger.debug(f"[检索到相关记忆] {len(related_memory.get('nodes', []))} 个相关记忆节点")
        else:
            logger.info("Neo4j未连接，跳过记忆搜索")

        # 2. 准备主模型输入 — 使用去重后的缓存记忆（最近 N 轮合并）
        input_items: list[Any] = [{"role": "system", "content": self.main_prompt}]

        cached_memory = session_state.memory_cache.get_deduplicated()
        if cached_memory.get("nodes"):
            input_items.append({"role": "system", "content": f"[相关记忆] 请参考以下记忆信息进行回复\n{cached_memory}"})

        history_context = session_state.conversation_context.get_formatted_context()
        if history_context:
            input_items.append({"role": "user", "content": f"[历史消息] 请注意鉴别可能包含你自己(qq{config.qq_config.bot_qq})的信息\n{history_context}"})

        input_items.append({"role": "user", "content": f"{caller_message}\n{input_message}"})

        # 3. 工具调用循环（非阻塞后台工具执行）
        all_output_messages = []
        tools_param = self.tool_manager.get_tools_schema() or None

        call_round_map: dict[str, int] = {}   # call_id → 结果写入 input_items 时的 absolute_round
        absolute_round = 0
        rounds_since_input = 0
        background_tasks: dict[str, tuple[Any, asyncio.Task]] = {}  # call_id → (tc_obj, task)
        PROGRESS_MARKER = "[正在执行的工具]"

        try:
            while True:
                # Step 1: 收割已完成的后台工具任务
                newly_completed: list[str] = []
                for call_id in list(background_tasks.keys()):
                    _, task = background_tasks[call_id]
                    if task.done():
                        newly_completed.append(call_id)

                for call_id in newly_completed:
                    tc_obj, task = background_tasks.pop(call_id)
                    try:
                        result = task.result()
                    except asyncio.CancelledError:
                        result = "任务已被取消"
                    except Exception as e:
                        result = f"工具执行出错: {e}"
                    finally:
                        session_state.activity_tracker.complete(call_id)

                    logger.info(f"[工具结果] {tc_obj.name} -> {str(result)[:200]}")
                    input_items.append(tc_obj)
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": str(result),
                    })
                    call_round_map[call_id] = absolute_round

                # Step 2: 释放缓冲消息
                buffered = session_state.input_buffer.drain_all()
                if buffered:
                    rounds_since_input = 0  # 外部输入刷新计数器
                    for msg in buffered:
                        input_items.append({
                            "role": "user",
                            "content": f"[新消息] {msg.get('caller_message', '')}\n{msg['message']}",
                        })
                    logger.info(f"[InputBuffer] 释放了 {len(buffered)} 条缓冲消息")

                # 清理过期的 tool_call 及其 function_call_output（已完成且超过 TTL 轮次）
                if call_round_map:
                    completed_call_ids_in_items = {
                        item.get("call_id")
                        for item in input_items
                        if isinstance(item, dict) and item.get("type") == "function_call_output"
                    }
                    stale_ids = {
                        cid for cid, r in call_round_map.items()
                        if absolute_round - r >= TOOL_CALL_TTL and cid in completed_call_ids_in_items
                    }
                    if stale_ids:
                        input_items = [
                            item for item in input_items
                            if not (
                                (hasattr(item, "call_id") and getattr(item, "type", None) == "tool_call"
                                 and item.call_id in stale_ids)
                                or (isinstance(item, dict) and item.get("type") == "function_call_output"
                                    and item.get("call_id") in stale_ids)
                            )
                        ]
                        for cid in stale_ids:
                            del call_round_map[cid]
                        logger.info(f"[ToolCall清理] 移除了 {len(stale_ids)} 组过期工具调用记录")

                # Step 3: 更新工具进度状态（替换旧的，注入最新的）
                input_items = [
                    item for item in input_items
                    if not (isinstance(item, dict) and item.get("role") == "system"
                            and PROGRESS_MARKER in item.get("content", ""))
                ]
                progress_text = session_state.activity_tracker.get_status_text()
                if progress_text:
                    input_items.append({"role": "system", "content": progress_text})

                # Step 4: 有新上下文时调用模型（首轮 / 工具返回结果 / buffer 新消息）
                has_new_context = (absolute_round == 0 or bool(newly_completed) or bool(buffered))

                if has_new_context:
                    if rounds_since_input >= MAX_TOOL_ROUNDS:
                        logger.warning(f"已达最大模型调用轮次 {MAX_TOOL_ROUNDS}，停止处理")
                        break

                    try:
                        create_kwargs: dict[str, Any] = {
                            "model": config.api.model,
                            "input": input_items,
                            "temperature": config.api.temperature,
                            "max_output_tokens": config.api.max_tokens,
                        }
                        if tools_param:
                            create_kwargs["tools"] = tools_param

                        print(f"模型输入：\n{input_items}\n")

                        response = client.responses.create(**create_kwargs)
                        output_messages, tool_calls = self._parse_response(response)
                    except Exception as e:
                        logger.error(f"主模型调用失败 (第{absolute_round + 1}轮): {e}")
                        break

                    all_output_messages.extend(output_messages)
                    rounds_since_input += 1

                    # 模型的文本消息加入 input_items 保持上下文连续性
                    if output_messages:
                        input_items.extend(output_messages)

                    # 启动新的工具调用为后台异步任务（不阻塞循环）
                    if tool_calls:
                        for tc in tool_calls:
                            try:
                                tc_args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                            except (json.JSONDecodeError, TypeError):
                                tc_args = {}
                            coro = self._execute_tool_call(tc, tc_args, session_state)
                            task = asyncio.create_task(coro)
                            session_state.activity_tracker.start(tc.call_id, tc.name, tc_args, task=task)
                            background_tasks[tc.call_id] = (tc, task)

                # Step 5: 等待任务完成或 buffer 新消息（至多 BUFFER_WAIT_TIMEOUT 秒）
                if background_tasks:
                    pending = [t for _, t in background_tasks.values()]
                    await asyncio.wait(pending, timeout=BUFFER_WAIT_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
                elif not session_state.input_buffer.has_pending():
                    await asyncio.sleep(BUFFER_WAIT_TIMEOUT)

                absolute_round += 1

                # Step 6: 终止判断 — 后台任务 & buffer 都空 → 无需继续
                if not background_tasks and not session_state.input_buffer.has_pending():
                    break
        finally:
            # 清理残留的后台任务
            for call_id, (_, task) in list(background_tasks.items()):
                if not task.done():
                    task.cancel()
                session_state.activity_tracker.complete(call_id)
            background_tasks.clear()

        # 4. 记录记忆
        if is_neo4j_available():
            memory_writer.full_memory_record(input_message, related_memory)

        return all_output_messages

    async def _execute_tool_call(
        self,
        tc,
        tc_args: dict,
        session_state: "SessionState",
    ) -> str:
        """执行单个工具调用（由 asyncio.Task 驱动，支持被 cancel）

        注意：start/complete 由调用方管理，此方法只负责执行。
        统一通过 tool_manager 执行，上下文合并 session_state.tool_context 和 activity_tracker。
        """
        logger.info(f"[工具调用] {tc.name} 参数={tc_args}")
        context = {
            **session_state.tool_context,
            "activity_tracker": session_state.activity_tracker,
            "session_key": session_state.session_key,
        }
        if self.tool_manager.has_tool(tc.name):
            result = await self.tool_manager.execute_tool(tc.name, tc_args, context)
        else:
            result = f"未找到工具: {tc.name}"
        return str(result)