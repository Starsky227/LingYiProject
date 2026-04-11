import asyncio
import json
import logging
import time
from datetime import datetime
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


# 最大工具调用轮次，防止无限循环
MAX_TOOL_ROUNDS = 10

# 工具返回值中的 valuable 标记前缀
VALUABLE_TAG = "{valuable}"

# 无工具调用时等待 buffer 新消息的超时（秒）
BUFFER_WAIT_TIMEOUT = 1.0

# 工具调用记录在 input_items 中的存活轮次（超过后清理已完成的 tool_call + result）
TOOL_CALL_TTL = 3

# 消息批量收集窗口
BATCH_MIN_WAIT = 2.0  # 首条消息后至少等待秒数
BATCH_MAX_WAIT = 5.0  # 首条消息后最多等待秒数


class LingYiCore:
    """AI 模型客户端 — 自主协调器"""

    def __init__(self, main_prompt: str) -> None:
        self.main_prompt = self._compose_main_prompt(main_prompt)
        self._kg_manager = get_knowledge_graph_manager()
        self._memory_writers: dict[str, MemoryWriter] = {}

        # OpenAI 客户端（延迟到实例化时创建，而非模块级别）
        self.client = OpenAI(
            api_key=config.main_api.api_key,
            base_url=config.main_api.base_url,
        )

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

    def _load_core_prompt(self) -> str:
        """加载 LingYiCore 的全局人格提示词。"""
        prompt_path = Path(__file__).parent / "LingYi_prompt.xml"
        if not prompt_path.exists():
            logger.warning(f"[LingYiCore] 未找到核心提示词文件: {prompt_path}")
            return ""
        try:
            return prompt_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"[LingYiCore] 读取核心提示词失败: {e}")
            return ""

    def _compose_main_prompt(self, module_prompt: str) -> str:
        """拼接核心人格提示词与调用方模块提示词。"""
        core_prompt = self._load_core_prompt()
        extra_prompt = (module_prompt or "").strip()

        if core_prompt and extra_prompt:
            return (
                f"{core_prompt}\n\n"
                "<!-- 子模块提示词 -->\n"
                f"{extra_prompt}"
            )
        return core_prompt or extra_prompt

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

    @staticmethod
    def _dedupe_keywords(keywords: list[str]) -> list[str]:
        ordered: list[str] = []
        for kw in keywords:
            token = str(kw or "").strip()
            if token and token not in ordered:
                ordered.append(token)
        return ordered

    def _fixed_keywords_for_message(self, session_key: str, key_words: list[str]) -> list[str]:
        base = [
            "会话消息",
            "对话",
            config.system.ai_name,
            session_key,
        ]
        return self._dedupe_keywords(base + list(key_words or []))

    def _fixed_keywords_for_tool(self, session_key: str, tool_name: str) -> list[str]:
        return self._dedupe_keywords([
            "工具调用",
            "工具结果",
            config.system.ai_name,
            session_key,
            tool_name,
        ])

    def _fixed_keywords_for_assistant_message(self, session_key: str) -> list[str]:
        return self._dedupe_keywords([
            "AI回复",
            "对话",
            config.system.ai_name,
            session_key,
        ])

    def _build_memory_batch_payload(self, batch: list[dict[str, Any]]) -> tuple[str, list[str]]:
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

    def _parse_response(self, resp):
        messages = []
        toolcalls = []

        for item in resp.output:
            if item.type == "message":
                messages.append(item)
            elif item.type == "function_call":
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
        """
        核心处理逻辑 — 非阻塞后台工具执行

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

        # 1. 初始化消息状态
        caller_message = initial_messages[-1].get("caller_message", "")
        pending_messages = initial_messages  # 首轮循环消费
        message_chunk = ""

        # 2. 准备prompt
        prompt_items: list[Any] = [{"role": "system", "content": self.main_prompt}]

        # 3. 工具调用（非阻塞后台工具执行）
        all_output_messages = []
        tools_param = self.tool_manager.get_tools_schema() or None

        tool_items: list[Any] = []  # 工具调用记录（reasoning + function_call + function_call_output）
        call_round_map: dict[str, int] = {}   # call_id → 结果写入 tool_items 时的 absolute_round
        reasoning_for_calls: dict[str, str] = {}  # call_id → reasoning item id
        absolute_round = 0
        rounds_since_input = 0
        background_tasks: dict[str, tuple[Any, asyncio.Task]] = {}  # call_id → (tc_obj, task)
        valuable_results: list[tuple[str, str]] = []  # (tool_name, result) — 本轮待写入 conversation_context

        # 4. 进入循环
        try:
            while True:
                # Step 1: 收割已完成的后台工具任务 → 追加到 tool_items
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

                    # 检查 {valuable} 标记
                    result_str = str(result)
                    if result_str.startswith(VALUABLE_TAG):
                        result_str = result_str[len(VALUABLE_TAG):]
                        valuable_results.append((tc_obj.name, result_str))
                        # 长期记忆批次：仅 valuable 工具结果计入触发计数
                        session_state.memory_batch.add_entry(
                            text=f"[工具调用-{tc_obj.name}] {result_str}",
                            fixed_keywords=self._fixed_keywords_for_tool(session_key, tc_obj.name),
                        )

                    # tc_obj (function_call) 已在模型响应时加入 tool_items，此处只追加结果
                    tool_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result_str,
                    })
                    call_round_map[call_id] = absolute_round

                # Step 2: 收集新消息（首轮消费 pending_messages，后续消费 buffer）
                buffered = session_state.input_buffer.drain_all()
                incoming = pending_messages + buffered
                pending_messages = []

                history_context = session_state.conversation_context.get_formatted_context()

                if incoming:
                    rounds_since_input = 0
                    new_messages = [msg["message"] for msg in incoming]

                    # 长期记忆批次：消息计入触发计数
                    for msg in incoming:
                        msg_text = str(msg.get("message", "")).strip()
                        msg_keywords = self._fixed_keywords_for_message(
                            session_key,
                            msg.get("key_words", []) or [],
                        )
                        session_state.memory_batch.add_entry(
                            text=msg_text,
                            fixed_keywords=msg_keywords,
                        )

                    # 将本轮新消息追加到 conversation_context
                    for msg_text in new_messages:
                        session_state.conversation_context.add_message(msg_text)
                    caller_message = incoming[-1].get("caller_message", caller_message)
                    logger.info(f"[收到消息] {len(incoming)} 条")

                    message_chunk = "[消息来源] " + caller_message + "\n[历史消息]\n" + history_context + "\n[新消息]\n" + "\n".join(new_messages)
                else:
                    message_chunk = "[消息来源] " + caller_message + "\n[历史消息]\n" + history_context + "\n[新消息]没有新消息，请检查工具返回结果，"

                # Step 3: 清理 tool_items 中过期的 reasoning + function_call + function_call_output
                if call_round_map:
                    stale_ids = {
                        cid for cid, r in call_round_map.items()
                        if absolute_round - r >= TOOL_CALL_TTL
                    }
                    if stale_ids:
                        # 找出要清理的 reasoning id，但保留仍被非过期 call 引用的
                        stale_reasoning_ids = {reasoning_for_calls.get(cid) for cid in stale_ids} - {None}
                        for cid, rid in reasoning_for_calls.items():
                            if cid not in stale_ids and rid in stale_reasoning_ids:
                                stale_reasoning_ids.discard(rid)
                        tool_items = [
                            item for item in tool_items
                            if not (
                                (hasattr(item, "call_id") and getattr(item, "type", None) == "function_call"
                                 and item.call_id in stale_ids)
                                or (isinstance(item, dict) and item.get("type") == "function_call_output"
                                    and item.get("call_id") in stale_ids)
                                or (getattr(item, "type", None) == "reasoning"
                                    and getattr(item, "id", None) in stale_reasoning_ids)
                            )
                        ]
                        for cid in stale_ids:
                            del call_round_map[cid]
                            reasoning_for_calls.pop(cid, None)
                        logger.info(f"[ToolCall清理] 移除了 {len(stale_ids)} 组过期工具调用记录")

                # Step 4: 有新上下文时组装 input_items 并调用模型
                has_new_context = bool(incoming) or bool(newly_completed)

                if has_new_context:
                    if rounds_since_input >= MAX_TOOL_ROUNDS:
                        logger.warning(f"已达最大模型调用轮次 {MAX_TOOL_ROUNDS}，停止处理")
                        break

                    # 组装模型输入：prompt + 记忆 + 消息上下文 + 工具进度
                    input_items = list(prompt_items) #prompt
                    cached_memory = session_state.memory_cache.get_deduplicated() #记忆
                    if cached_memory.get("nodes"):
                        input_items.append({"role": "system", "content": f"[相关记忆] 请参考以下记忆信息进行回复\n{cached_memory}"})
                    input_items.append({"role": "user", "content": message_chunk}) #消息上下文
                    input_items.extend(tool_items) #工具信息
                    progress_text = session_state.activity_tracker.get_status_text()
                    if progress_text:
                        input_items.append({"role": "system", "content": progress_text})

                    # 打印当前轮次上下文
                    print(f"\n{'='*60}")
                    print(f"  第 {absolute_round + 1} 轮模型调用")
                    print(f"{'='*60}")
                    print(prompt_items[0]["content"][:50] + "\n...")
                    print(f"📨 消息上下文:\n{message_chunk}")
                    if tool_items:
                        print(f"\n🔧 工具信息 ({len(tool_items) // 2} 组):")
                        for item in tool_items:
                            if isinstance(item, dict) and item.get("type") == "function_call_output":
                                output_preview = item["output"][:200] + ("..." if len(item["output"]) > 200 else "")
                                print(f"  [{item['call_id']}] → {output_preview}")
                            elif hasattr(item, "name"):
                                print(f"  📞 {item.name}({getattr(item, 'arguments', '')[:100]})")
                    if progress_text:
                        print(f"\n⏳ {progress_text}")
                    print(f"{'='*60}\n")

                    try:
                        create_kwargs: dict[str, Any] = {
                            "model": config.main_api.model,
                            "input": input_items,
                            "reasoning": {"effort": "medium"},
                            "max_output_tokens": config.main_api.max_tokens,
                        }
                        if tools_param:
                            create_kwargs["tools"] = tools_param

                        response = self.client.responses.create(**create_kwargs)
                        output_messages, tool_calls = self._parse_response(response)
                    except Exception as e:
                        logger.error(f"主模型调用失败 (第{absolute_round + 1}轮): {e}")
                        break

                    # 打印模型输出
                    print(f"\n📨 模型返回:")
                    for item in response.output:
                        if item.type == "reasoning":
                            summaries = getattr(item, 'summary', None)
                            if summaries:
                                for s in summaries:
                                    print(f"  💭 {getattr(s, 'text', '')[:300]}")
                        elif item.type == "message":
                            for c in getattr(item, 'content', []):
                                if getattr(c, 'type', '') == 'output_text':
                                    print(f"  💬 {c.text[:500]}")
                        elif item.type == "function_call":
                            print(f"  🔧 调用: {item.name}({item.arguments[:200]})")

                    # 将 reasoning + function_call 立即加入 tool_items（API 要求 function_call 必须携带其 reasoning）
                    last_reasoning_id = None
                    for item in response.output:
                        if item.type == "reasoning":
                            last_reasoning_id = item.id
                            tool_items.append(item)
                        elif item.type == "function_call":
                            tool_items.append(item)
                            if last_reasoning_id:
                                reasoning_for_calls[item.call_id] = last_reasoning_id

                    all_output_messages.extend(output_messages)
                    rounds_since_input += 1

                    # 将本轮 valuable 工具结果写入 conversation_context（模型已在 tool_items 中看过）
                    if valuable_results:
                        for tool_name, v_result in valuable_results:
                            time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            formatted = f"{time_str} [工具结果-{tool_name}] {v_result}\n"
                            session_state.conversation_context.add_message(formatted)
                        valuable_results.clear()

                    # 模型的文本消息记录到会话历史（history_context 每轮刷新，无需再加入 tool_items）
                    if output_messages:
                        for om in output_messages:
                            text_parts = [c.text for c in getattr(om, 'content', []) if getattr(c, 'type', '') == 'output_text' and getattr(c, 'text', '')]
                            if text_parts:
                                time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                formatted = f"{time_str} <{config.system.ai_name}> {''.join(text_parts)}\n"
                                session_state.conversation_context.add_message(formatted)
                                session_state.memory_batch.add_entry(
                                    text=formatted.rstrip(),
                                    fixed_keywords=self._fixed_keywords_for_assistant_message(session_key),
                                )

                    # 启动新的工具调用为后台异步任务（不阻塞循环）
                    if tool_calls:
                        for tc in tool_calls:
                            try:
                                tc_args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                            except (json.JSONDecodeError, TypeError):
                                tc_args = {}
                            coro = self._execute_tool_call(tc, tc_args, session_state)
                            task = asyncio.create_task(coro)
                            session_state.activity_tracker.start(tc.call_id, tc.name, tc_args, task=task, tool_call=tc)
                            background_tasks[tc.call_id] = (tc, task)

                # 记忆：每 10 条（消息/工具调用）触发一次 search + record
                while is_neo4j_available() and session_state.memory_batch.has_ready_batch():
                    ready_batch = session_state.memory_batch.pop_ready_batch()
                    memory_text, fixed_keywords = self._build_memory_batch_payload(ready_batch)
                    if not memory_text.strip():
                        continue

                    related_memory = full_memory_search(
                        memory_text,
                        save_temp_memory=False,
                        add_keywords=fixed_keywords,
                    )
                    session_state.memory_cache.add(related_memory)
                    logger.info(
                        f"[记忆批处理] session={session_key} 触发一次，条数={len(ready_batch)}，"
                        f"关键词数={len(fixed_keywords)}，关联节点={len(related_memory.get('nodes', []))}"
                    )

                    record_payload = (
                        f"{memory_text}\n"
                        f"\n[固定关键词]\n{fixed_keywords}"
                    )
                    asyncio.create_task(
                        memory_writer.full_memory_record(record_payload, related_memory)
                    )

                # Step 5: 等待任务完成或 buffer 新消息（至多 BUFFER_WAIT_TIMEOUT 秒）
                if background_tasks:
                    pending = [t for _, t in background_tasks.values()]
                    await asyncio.wait(pending, timeout=BUFFER_WAIT_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
                elif not session_state.input_buffer.has_pending():
                    await asyncio.sleep(BUFFER_WAIT_TIMEOUT)

                absolute_round += 1

                # Step 6: 终止判断 — 后台任务 & buffer 都空 → 无需继续
                if not background_tasks and not session_state.input_buffer.has_pending():
                    print("\n暂无任务，轮次终止\n")
                    break
        finally:
            # 清理残留的后台任务
            for call_id, (_, task) in list(background_tasks.items()):
                if not task.done():
                    task.cancel()
                session_state.activity_tracker.complete(call_id)
            background_tasks.clear()

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