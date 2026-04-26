import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from openai import OpenAI
from system.config import config
from brain.memory.search_memory import get_formatted_memory_graph
from brain.memory.record_memory import MemoryWriter
from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from system.system_checker import is_neo4j_available
from brain.lingyi_core.tool_manager import ToolManager, LocalToolRegistry, AgentSubRegistry
from brain.lingyi_core.session_state import SessionState
from brain.lingyi_core.session_state import MemoryBatchBuffer  # 仅用于 _dedupe_keywords 静态方法
from brain.lingyi_core.chat_logger import write_chat_log
from brain.lingyi_core.model_logger import (
    log_model_input as _log_model_input,
    log_model_output as _log_model_output,
    log_tool_result as _log_tool_result,
)

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

# 流式文本句子分隔符
_SENTENCE_DELIMITERS = re.compile(r'[。！？.!?\n；;]')


class _SentenceAccumulator:
    """流式文本句子累积器 — 缓冲 token 直到句子边界，逐句回调"""

    def __init__(self, callback: Callable[[str, bool], None]):
        self._buffer = ""
        self._callback = callback
        self._count = 0

    def add(self, text: str) -> None:
        self._buffer += text
        while True:
            match = _SENTENCE_DELIMITERS.search(self._buffer)
            if match is None:
                break
            end = match.end()
            sentence = self._buffer[:end].strip()
            self._buffer = self._buffer[end:]
            if sentence:
                self._callback(sentence, self._count == 0)
                self._count += 1

    def flush(self) -> None:
        if self._buffer.strip():
            self._callback(self._buffer.strip(), self._count == 0)
            self._count += 1
            self._buffer = ""


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
        # 视觉描述客户端：延迟到首次使用时创建（用独立的 VisionAPIConfig）
        self._vision_client: Optional[OpenAI] = None

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

        # 强引用集合：避免 fire-and-forget asyncio.create_task 被 GC 回收
        # （图片描述任务、记忆批次后台任务都注册到这里）
        self._background_tasks: set[asyncio.Task] = set()

    def _spawn_background(self, coro) -> "asyncio.Task | None":
        """安全地创建一个 fire-and-forget 后台任务并保留强引用，避免被 GC 回收。"""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            # 没有运行中的事件循环（极少见，例如关闭路径上）
            return None
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

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
        stream_text_callback: Optional[Callable[[str, bool], None]] = None,
        on_text_output: Optional[Callable[[str], None]] = None,
        on_stream_delta: Optional[Callable[[str], None]] = None,
    ) -> list:
        """处理输入信息，通过工具调用循环生成回复

        从 InputBuffer 收集消息（等待 2-5s 批量窗口），然后进入工具调用循环。
        所有工具统一通过 tool_manager 执行。

        Args:
            session_key: 会话标识（用于隔离 MemoryWriter / SessionState）
            stream_text_callback: 流式文本回调，签名 (sentence: str, is_first: bool)。
                当设置时，模型文本输出将以句子为单位实时回调，用于流式 TTS。
            on_text_output: 文本输出回调，签名 (text: str)，每条 message 完整时触发一次。
            on_stream_delta: 流式 token 回调，签名 (delta: str)，每个 output_text.delta
                立刻触发。用于 UI 层做逐字浮现的"生长气泡"效果，不做句子缓冲。
                与 stream_text_callback 互不影响——前者按 token、后者按句。

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
                stream_text_callback=stream_text_callback,
                on_text_output=on_text_output,
                on_stream_delta=on_stream_delta,
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
        stream_text_callback: Optional[Callable[[str, bool], None]] = None,
        on_text_output: Optional[Callable[[str], None]] = None,
        on_stream_delta: Optional[Callable[[str], None]] = None,
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

        # ---- chat_logs：统一记录 AI 输出（用户输入在 ingestion 处理 incoming 时记录）----
        ai_name = config.system.ai_name
        original_on_text_output = on_text_output

        def _logged_on_text_output(text: str) -> None:
            try:
                write_chat_log(f"<{ai_name}> {text}")
            except Exception:
                pass
            if original_on_text_output:
                original_on_text_output(text)
        on_text_output = _logged_on_text_output

        # 把发言回调注入 tool_context，供 speak 工具消费
        # —— 模型唯一的发言路径就是调用 speak 工具，纯文本输出不再被推送到 UI/TTS
        session_state.tool_context["_on_text_output"] = on_text_output

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
        pending_images: list[dict] = []  # 工具产出的待注入图片 [{data_url, description}]

        # 收集外部注入的图片（如助手模式截屏发送）
        if session_state.external_pending_images:
            pending_images.extend(session_state.external_pending_images)
            # 同时为每张外部注入的图片异步生成文本描述，写入历史/记忆，
            # 使后续轮次的 history_context 能"看到"这张图的内容。
            for img_item in session_state.external_pending_images:
                self._spawn_background(
                    self._describe_image_for_history(img_item, session_state)
                )
            session_state.external_pending_images.clear()

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
                    _log_tool_result(tc_obj.name, call_id, str(result), absolute_round)

                    # 检查 {valuable} 标记
                    result_str = str(result)
                    if result_str.startswith(VALUABLE_TAG):
                        result_str = result_str[len(VALUABLE_TAG):]
                        valuable_results.append((tc_obj.name, result_str))
                        # 长期记忆批次：仅 valuable 工具结果计入触发计数
                        session_state.memory_batch.add_entry(
                            text=result_str,
                            fixed_keywords=[],
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

                # 历史消息：按 role 拆分（user / assistant），不再拼成单个 user 大块
                history_role_messages = session_state.conversation_context.get_role_messages()

                # 本轮"新消息"块（仅当本轮收到新消息时生成）；
                # 不再提前写入 conversation_context —— 等模型收过本轮 user 之后再回写，
                # 否则历史里和新消息块会出现同一段内容两次。
                new_message_block: str | None = None

                if incoming:
                    rounds_since_input = 0
                    new_messages = [msg["message"] for msg in incoming]

                    # chat_logs：统一记录每条新进来的用户/外部消息（原文完整写入）
                    for msg in incoming:
                        try:
                            write_chat_log(str(msg.get("message", "")))
                        except Exception:
                            pass

                    # 立即搜索长期记忆：每次收到新消息即触发一次记忆搜索
                    if is_neo4j_available():
                        try:
                            search_text = "\n".join(new_messages)
                            search_keywords = []
                            for msg in incoming:
                                search_keywords.extend(msg.get("key_words", []) or [])
                            formatted_memory = get_formatted_memory_graph(
                                search_text,
                                add_keywords=search_keywords or None,
                                max_expansion_rounds=1,
                            )
                            session_state.memory_cache.add(formatted_memory)
                            logger.info(f"[记忆搜索] 已缓存格式化记忆")
                        except Exception as e:
                            logger.warning(f"[记忆搜索] 失败: {e}")

                    # 长期记忆批次：消息计入触发计数
                    for msg in incoming:
                        msg_text = str(msg.get("message", "")).strip()
                        msg_keywords = MemoryBatchBuffer._dedupe_keywords(msg.get("key_words", []) or [])
                        session_state.memory_batch.add_entry(
                            text=msg_text,
                            fixed_keywords=msg_keywords,
                        )

                    caller_message = incoming[-1].get("caller_message", caller_message)
                    logger.info(f"[收到消息] {len(incoming)} 条")

                    new_message_block = (
                        "[新消息]\n[消息来源] " + caller_message + "\n" + "\n".join(new_messages)
                    )

                    # 回写到 conversation_context，供下一轮作为历史使用。
                    # 本轮新消息合并为单条 user 条目（同批次 → 同一条 message）。
                    session_state.conversation_context.add_message(
                        "\n".join(new_messages), role="user"
                    )

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

                    # 组装模型输入：prompt + 便签 + 记忆 + 历史(role-based) + 新消息 + 工具进度
                    input_items = list(prompt_items) #prompt
                    if session_state.scratchpad:  # 便签
                        input_items.append({"role": "system", "content": f"[你的便签]\n{session_state.scratchpad}"})
                    memory_text = session_state.memory_cache.get_merged() #记忆
                    if memory_text:
                        input_items.append({"role": "system", "content": f"[相关记忆] 以下记忆信息可能相关，可以作为参考进行回复\n{memory_text}"})

                    # 历史按 role 拆分注入 —— 让模型清楚区分"自己的发言"与"用户/外部输入"，
                    # 避免历史里 <ai_name> 行被误读为可复读的模板。
                    for hm in history_role_messages:
                        input_items.append({"role": hm["role"], "content": hm["content"]})

                    # 本轮新消息 —— 单独作为最新一条 user message
                    if new_message_block is not None:
                        input_items.append({"role": "user", "content": new_message_block})
                    elif not history_role_messages:
                        # 极少数情况：既无历史也无新消息（首轮空场），保持模型有一个明确的提示
                        input_items.append({"role": "user", "content": "[新消息]没有新消息，请检查工具返回结果，"})
                    else:
                        # 仅工具结果触发的轮次：用一个简短 user 提示引导模型基于工具结果继续
                        input_items.append({"role": "user", "content": "[新消息]没有新消息，请检查工具返回结果，"})

                    input_items.extend(tool_items) #工具信息
                    progress_text = session_state.activity_tracker.get_status_text()
                    if progress_text:
                        input_items.append({"role": "system", "content": progress_text})

                    # 注入工具产出的图片（如 view_screen 截图）
                    if pending_images:
                        for img_item in pending_images:
                            input_items.append({
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": img_item.get("description", "截图")},
                                    {"type": "input_image", "image_url": img_item["data_url"]},
                                ],
                            })
                        logger.info(f"[图片注入] 注入 {len(pending_images)} 张图片")
                        pending_images.clear()

                    # 记录本轮完整模型输入到日志文件
                    _log_model_input(input_items, absolute_round + 1)

                    try:
                        create_kwargs: dict[str, Any] = {
                            "model": config.main_api.model,
                            "input": input_items,
                            "reasoning": {"effort": "medium"},
                            "max_output_tokens": config.main_api.max_tokens,
                        }
                        if tools_param:
                            create_kwargs["tools"] = tools_param

                        # 流式相关参数已废弃：模型唯一发言通道是 speak 工具，
                        # 纯文本输出不再被推送，故无需逐 token / 逐句回调。
                        # （保留 stream_text_callback / on_stream_delta 形参以兼容老调用方，但不再生效）
                        if stream_text_callback or on_stream_delta:
                            logger.debug("[流式] 已停用：发言改为通过 speak 工具，stream_* 回调将被忽略")
                        response = self.client.responses.create(**create_kwargs)

                        output_messages, tool_calls = self._parse_response(response)
                    except Exception as e:
                        logger.error(f"主模型调用失败 (第{absolute_round + 1}轮): {e}")
                        break

                    # 记录本轮模型输出到日志文件
                    _log_model_output(response, absolute_round + 1)

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
                            formatted = f"{time_str} [工具结果-{tool_name}] {v_result}"
                            session_state.conversation_context.add_message(formatted, role="user")
                        valuable_results.clear()

                    # 模型的文本消息：仅作内部独白记录到日志，**不**推送 UI/TTS、**不**进历史/记忆。
                    # 唯一合法的发言路径是调用 `speak` 工具。这样做的目的是：
                    #   1. 避免模型用纯文本输出作为发言（绕过沉默判定）
                    #   2. 避免历史里堆叠的 assistant 文本反过来诱导模型复读
                    if output_messages:
                        for om in output_messages:
                            text_parts = [c.text for c in getattr(om, 'content', []) if getattr(c, 'type', '') == 'output_text' and getattr(c, 'text', '')]
                            if not text_parts:
                                continue
                            stray_text = ''.join(text_parts).strip()
                            if stray_text:
                                logger.info(f"[文本输出忽略] 模型未通过 speak 工具发言，已丢弃: {stray_text[:80]}")

                    # 启动新的工具调用为后台异步任务（不阻塞循环）
                    if tool_calls:
                        for tc in tool_calls:
                            try:
                                tc_args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                            except (json.JSONDecodeError, TypeError):
                                tc_args = {}
                            coro = self._execute_tool_call(tc, tc_args, session_state, _pending_images=pending_images)
                            task = asyncio.create_task(coro)
                            session_state.activity_tracker.start(tc.call_id, tc.name, tc_args, task=task, tool_call=tc)
                            background_tasks[tc.call_id] = (tc, task)

                # 记忆：每 20 条（消息/工具调用）触发一次 record
                await session_state.process_ready_batches(memory_writer)

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

        # 对话结束后启动空闲计时器，如果 10 分钟内无新对话则强制刷新未保存的记忆
        session_state.schedule_idle_flush(memory_writer)

        return all_output_messages

    async def _execute_tool_call(
        self,
        tc,
        tc_args: dict,
        session_state: "SessionState",
        **kwargs,
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
            "_pending_images": kwargs.get("_pending_images", []),
            "_session_state": session_state,
            # speak 工具需要这个回调把文本送到 UI 气泡 + TTS（外部传入 on_text_output 的包装版）
            "_on_text_output": session_state.tool_context.get("_on_text_output"),
        }
        if self.tool_manager.has_tool(tc.name):
            result = await self.tool_manager.execute_tool(tc.name, tc_args, context)
        else:
            result = f"未找到工具: {tc.name}"
        return str(result)

    # ------------------------------------------------------------------ #
    #  外部注入图片 → 异步生成文本描述写入历史
    # ------------------------------------------------------------------ #

    def _get_vision_client(self) -> OpenAI:
        """懒加载 vision 模型客户端（用独立的 VisionAPIConfig）"""
        if self._vision_client is None:
            vision_cfg = config.vision_api
            self._vision_client = OpenAI(
                api_key=vision_cfg.vision_api_key,
                base_url=vision_cfg.vision_base_url,
            )
        return self._vision_client

    async def _describe_image_for_history(
        self,
        img_item: dict,
        session_state: "SessionState",
    ) -> None:
        """异步对外部注入的图片生成文本描述，并以 [图片{描述}] 形式写入历史与记忆。

        本方法与主轮次模型调用并行执行：
        - 当前轮次模型直接看到原图（多模态 input_image）；
        - 该描述用于**后续轮次**的 history_context，让会话能"记住"这张图的内容。
        """
        vision_cfg = config.vision_api
        if not vision_cfg.enabled:
            return

        data_url = (img_item.get("data_url") or "").strip()
        if not data_url:
            return

        user_hint = (img_item.get("description") or "用户提供的图片").strip()

        try:
            client = self._get_vision_client()
            response = await asyncio.to_thread(
                client.responses.create,
                model=vision_cfg.vision_model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "你是图像描述助手。请用一段中文详细描述图片中的所有可见内容："
                            "场景、物体、人物、UI 界面、可见文字等都要覆盖到。"
                            "直接输出描述本身，不要带任何前缀、序号或解释。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": f"图片用途：{user_hint}\n请详细描述这张图片。"},
                            {"type": "input_image", "image_url": data_url},
                        ],
                    },
                ],
                max_output_tokens=1024,
            )
            description = (getattr(response, "output_text", "") or "").strip()
        except Exception as e:
            logger.warning(f"[图片描述] 调用 vision 模型失败: {e}")
            return

        # 压成单行，避免破坏 history_context 的逐行格式
        description = " ".join(description.split())
        if not description:
            return

        formatted = f"[图片{{{description}}}]"
        try:
            session_state.conversation_context.add_message(formatted, role="user")
        except Exception as e:
            logger.warning(f"[图片描述] 写入会话上下文失败: {e}")
        try:
            write_chat_log(formatted)
        except Exception:
            pass
        try:
            session_state.memory_batch.add_entry(text=formatted, fixed_keywords=[])
        except Exception:
            pass
        logger.info(f"[图片描述] 已写入历史: {description[:80]}{'...' if len(description) > 80 else ''}")

    # ------------------------------------------------------------------ #
    #  记忆刷新：程序关闭
    # ------------------------------------------------------------------ #

    async def flush_all_pending_memory(self) -> None:
        """程序关闭时调用：强制刷新所有 session 中未保存的记忆"""
        for session_key, session_state in list(self._session_states.items()):
            session_state.cancel_idle_flush()
            memory_writer = self._get_memory_writer(session_key)
            await session_state.flush_pending_memory(memory_writer)