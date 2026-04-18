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


# ---- 模型调试日志（输入 + 输出 + 工具结果，写入同一文件） ----
_MODEL_LOG_DIR = Path("data/cache/model_input_logs")
_MODEL_LOG_MAX_FILES = 5


def _write_model_log(content: str) -> None:
    """将内容追加写入按日期命名的调试日志文件，最多保留 _MODEL_LOG_MAX_FILES 个文件。"""
    try:
        _MODEL_LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y_%m_%d")
        log_file = _MODEL_LOG_DIR / f"model_log_{today}.txt"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(content)

        # 清理旧文件
        log_files = sorted(_MODEL_LOG_DIR.glob("model_log_*.txt"))
        if len(log_files) > _MODEL_LOG_MAX_FILES:
            for old_file in log_files[:-_MODEL_LOG_MAX_FILES]:
                old_file.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"[模型日志] 写入失败: {e}")


def _serialize_input_items(input_items: list, round_num: int) -> str:
    """将 input_items 序列化为模型实际阅读到的文本格式。"""
    lines: list[str] = []
    lines.append(f"第 {round_num} 轮模型调用  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 第一条 system 消息是 prompt，省略其内容；后续 system 消息（屏幕环境/便签/记忆等）正常记录
    prompt_skipped = False

    for item in input_items:
        if isinstance(item, dict):
            role = item.get("role", "")
            content = item.get("content", "")
            item_type = item.get("type", "")

            if item_type == "function_call_output":
                lines.append(f"[function_call_output] call_id={item.get('call_id', '')}")
                lines.append(str(item.get("output", "")))
                lines.append("")
            elif role == "system" and not prompt_skipped:
                # 首条 system 消息是主 prompt，省略内容
                prompt_skipped = True
                lines.append("[system] (prompt省略)")
                lines.append("")
            elif isinstance(content, list):
                # 多模态内容（图片+文本）
                lines.append(f"[{role}]")
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "input_text":
                            lines.append(part.get("text", ""))
                        elif part.get("type") == "input_image":
                            lines.append("[图片内容 - 省略base64数据]")
                lines.append("")
            elif content:
                lines.append(f"[{role}]")
                lines.append(str(content))
                lines.append("")
        else:
            # SDK 对象（reasoning / function_call 等）
            obj_type = getattr(item, "type", "unknown")
            if obj_type == "function_call":
                name = getattr(item, "name", "")
                arguments = getattr(item, "arguments", "")
                call_id = getattr(item, "call_id", "")
                lines.append(f"[function_call] {name}  call_id={call_id}")
                lines.append(str(arguments))
                lines.append("")
            elif obj_type == "reasoning":
                summaries = getattr(item, "summary", None)
                if summaries:
                    lines.append("[reasoning]")
                    for s in summaries:
                        lines.append(getattr(s, "text", ""))
                    lines.append("")
            else:
                lines.append(f"[{obj_type}] {str(item)[:500]}")
                lines.append("")

    return "\n".join(lines)


def _serialize_model_output(response, round_num: int) -> str:
    """将模型响应序列化为可读文本。"""
    lines: list[str] = []
    lines.append(f"第 {round_num} 轮模型输出  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    for item in response.output:
        item_type = getattr(item, "type", "unknown")
        if item_type == "message":
            for content in getattr(item, "content", []):
                if getattr(content, "type", "") == "output_text":
                    lines.append("[assistant]")
                    lines.append(getattr(content, "text", ""))
                    lines.append("")
        elif item_type == "function_call":
            name = getattr(item, "name", "")
            arguments = getattr(item, "arguments", "")
            call_id = getattr(item, "call_id", "")
            lines.append(f"[function_call] {name}  call_id={call_id}")
            lines.append(str(arguments))
            lines.append("")
        elif item_type == "reasoning":
            summaries = getattr(item, "summary", None)
            if summaries:
                lines.append("[reasoning]")
                for s in summaries:
                    lines.append(getattr(s, "text", ""))
                lines.append("")
        else:
            lines.append(f"[{item_type}] {str(item)[:500]}")
            lines.append("")

    # 附加 usage 信息
    usage = getattr(response, "usage", None)
    if usage:
        lines.append(f"[usage] input_tokens={getattr(usage, 'input_tokens', '?')}  "
                      f"output_tokens={getattr(usage, 'output_tokens', '?')}")
        lines.append("")

    return "\n".join(lines)


def _log_model_input(input_items: list, round_num: int) -> None:
    """记录本轮模型输入。"""
    content = _serialize_input_items(input_items, round_num)
    separator = "=" * 60
    _write_model_log(f"{separator}\n[INPUT]\n{content}\n")


def _log_model_output(response, round_num: int) -> None:
    """记录本轮模型输出（文本消息 + 工具调用 + reasoning + usage）。"""
    content = _serialize_model_output(response, round_num)
    _write_model_log(f"[OUTPUT]\n{content}\n")


def _log_tool_result(tool_name: str, call_id: str, result: str, round_num: int) -> None:
    """记录工具执行结果。"""
    lines = [
        f"[TOOL_RESULT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  round={round_num}",
        f"工具: {tool_name}  call_id={call_id}",
        f"结果: {result[:2000]}",
        "",
    ]
    _write_model_log("\n".join(lines) + "\n")


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
    ) -> list:
        """处理输入信息，通过工具调用循环生成回复

        从 InputBuffer 收集消息（等待 2-5s 批量窗口），然后进入工具调用循环。
        所有工具统一通过 tool_manager 执行。

        Args:
            session_key: 会话标识（用于隔离 MemoryWriter / SessionState）
            stream_text_callback: 流式文本回调，签名 (sentence: str, is_first: bool)。
                当设置时，模型文本输出将以句子为单位实时回调，用于流式 TTS。

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
        pending_images: list[dict] = []  # 工具产出的待注入图片 [{data_url, description}]

        # 收集外部注入的图片（如助手模式截屏发送）
        if session_state.external_pending_images:
            pending_images.extend(session_state.external_pending_images)
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
                            )
                            session_state.memory_cache.add(formatted_memory)
                            logger.info(f"[记忆搜索] 已缓存格式化记忆")
                        except Exception as e:
                            logger.warning(f"[记忆搜索] 失败: {e}")

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
                        # 确保每条消息以换行结尾，避免与后续条目粘连
                        entry = msg_text if msg_text.endswith("\n") else msg_text + "\n"
                        session_state.conversation_context.add_message(entry)
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

                    # 组装模型输入：prompt + 便签 + 记忆 + 屏幕环境 + 消息上下文 + 工具进度
                    input_items = list(prompt_items) #prompt
                    if session_state.scratchpad:  # 便签
                        input_items.append({"role": "system", "content": f"[你的便签]\n{session_state.scratchpad}"})
                    memory_text = session_state.memory_cache.get_merged() #记忆
                    if memory_text:
                        input_items.append({"role": "system", "content": f"[相关记忆] 请参考以下记忆信息进行回复\n{memory_text}"})
                    if session_state.screen_context:  # 屏幕环境
                        input_items.append({"role": "system", "content": f"[屏幕环境]\n{session_state.screen_context}"})
                    input_items.append({"role": "user", "content": message_chunk}) #消息上下文
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

                        if stream_text_callback:
                            # 流式调用：逐 token 接收，按句子回调
                            accumulator = _SentenceAccumulator(stream_text_callback)
                            response = None
                            stream = self.client.responses.create(**create_kwargs, stream=True)
                            for event in stream:
                                event_type = getattr(event, 'type', None)
                                if event_type == "response.output_text.delta":
                                    accumulator.add(event.delta)
                                elif event_type == "response.completed":
                                    response = event.response
                            accumulator.flush()
                            if response is None:
                                logger.error("流式调用未收到 response.completed 事件")
                                break
                        else:
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
                            formatted = f"{time_str} [工具结果-{tool_name}] {v_result}\n"
                            session_state.conversation_context.add_message(formatted)
                        valuable_results.clear()

                    # 模型的文本消息记录到会话历史（history_context 每轮刷新，无需再加入 tool_items）
                    if output_messages:
                        for om in output_messages:
                            text_parts = [c.text for c in getattr(om, 'content', []) if getattr(c, 'type', '') == 'output_text' and getattr(c, 'text', '')]
                            if text_parts:
                                reply_text = ''.join(text_parts)
                                # 立即回调：通知外部推送 UI 气泡/日志
                                if on_text_output:
                                    on_text_output(reply_text)
                                time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                formatted = f"{time_str} <{config.system.ai_name}> {reply_text}\n"
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
        }
        if self.tool_manager.has_tool(tc.name):
            result = await self.tool_manager.execute_tool(tc.name, tc_args, context)
        else:
            result = f"未找到工具: {tc.name}"
        return str(result)

    # ------------------------------------------------------------------ #
    #  记忆刷新：程序关闭
    # ------------------------------------------------------------------ #

    async def flush_all_pending_memory(self) -> None:
        """程序关闭时调用：强制刷新所有 session 中未保存的记忆"""
        for session_key, session_state in list(self._session_states.items()):
            session_state.cancel_idle_flush()
            memory_writer = self._get_memory_writer(session_key)
            await session_state.flush_pending_memory(memory_writer)