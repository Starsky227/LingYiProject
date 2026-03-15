import json
import logging
from typing import Any, Awaitable, Callable, Optional

from openai import OpenAI
from system.config import config
from brain.memory.search_memory import full_memory_search
from brain.memory.record_memory import MemoryWriter
from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from system.system_checker import is_neo4j_available

logger = logging.getLogger(__name__)

# 初始化 OpenAI 客户端
client = OpenAI(
    api_key=config.api.api_key,
    base_url=config.api.base_url,
)


# 工具执行器类型: async (name, args) -> str
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]

# 最大工具调用轮次，防止无限循环
MAX_TOOL_ROUNDS = 10

# end 工具返回的终止标记
END_SIGNAL = "__END__"


class LingYiCore:
    """AI 模型客户端"""

    def __init__(self, main_prompt: str, available_tools: list[dict[str, Any]] | None = None) -> None:
        self.main_prompt = main_prompt
        self.available_tools = available_tools or []
        self._kg_manager = get_knowledge_graph_manager()
        self._memory_writers: dict[str, MemoryWriter] = {}

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

    async def process_qq_message(
        self,
        input_message: str,
        session_key: str = "default",
        key_word_list: list[str] = None,
        pre_str: str = "收到消息",
        history_context: str = "",
        tool_executor: Optional[ToolExecutor] = None,
    ) -> list:
        """处理输入信息，通过工具调用循环生成回复

        Args:
            input_message: 当前消息（已格式化）
            session_key: 会话标识（用于隔离 MemoryWriter）
            key_word_list: 额外关键词列表
            pre_str: 消息前缀提示
            history_context: 来自 ConversationSession 的格式化历史消息文本
            tool_executor: 异步工具执行器回调，签名 async (name, args) -> str

        Returns:
            模型输出的消息列表
        """
        logger.info(f"收到输入信息: {input_message}")

        # 获取 memory_writer（按会话隔离，内含已提取事件记录用于去重）
        memory_writer = self._get_memory_writer(session_key)

        # 1. 搜索相关记忆
        related_memory = None
        if is_neo4j_available():
            related_memory = full_memory_search(input_message, save_temp_memory=False, add_keywords=key_word_list)
            logger.debug(f"检索到 {len(related_memory.get('nodes', []))} 个相关记忆节点")
        else:
            logger.info("Neo4j未连接，跳过记忆搜索")

        # 2. 准备主模型输入
        input_items: list[Any] = [{"role": "system", "content": self.main_prompt}]

        if related_memory and related_memory.get("nodes"):
            input_items.append({"role": "system", "content": f"[相关记忆] 请参考以下记忆信息进行回复\n{related_memory}"})

        # 使用来自 ConversationSession 的完整对话历史
        if history_context:
            input_items.append({"role": "user", "content": f"[消息记录] 请注意鉴别可能包含你自己(qq{config.qq_config.bot_qq})的信息\n{history_context}"})

        input_items.append({"role": "user", "content": f"{pre_str}\n{input_message}"})

        # 3. 工具调用循环
        all_output_messages = []
        tools_param = self.available_tools if self.available_tools else None

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                create_kwargs: dict[str, Any] = {
                    "model": config.api.model,
                    "input": input_items,
                    "temperature": config.api.temperature,
                    "max_output_tokens": config.api.max_tokens,
                }
                if tools_param:
                    create_kwargs["tools"] = tools_param

                response = client.responses.create(**create_kwargs)
                output_messages, tool_calls = self._parse_response(response)
            except Exception as e:
                logger.error(f"主模型调用失败 (第{round_num + 1}轮): {e}")
                break

            all_output_messages.extend(output_messages)

            # 没有工具调用或没有执行器，结束循环
            if not tool_calls or not tool_executor:
                break

            # 将模型的完整输出追加到输入中（用于下一轮对话）
            input_items.extend(response.output)

            # 执行每个工具调用，并将结果追加到输入
            end_signaled = False
            for tc in tool_calls:
                try:
                    tc_args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                except (json.JSONDecodeError, TypeError):
                    tc_args = {}

                logger.info(f"[工具调用] {tc.name} 参数={tc_args}")
                try:
                    result = await tool_executor(tc.name, tc_args)
                except Exception as e:
                    logger.error(f"[工具执行异常] {tc.name}: {e}")
                    result = f"工具执行出错: {e}"

                logger.info(f"[工具结果] {tc.name} -> {result[:200] if len(str(result)) > 200 else result}")
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tc.call_id,
                    "output": str(result),
                })

                if result == END_SIGNAL:
                    end_signaled = True

            if end_signaled:
                logger.info("[工具循环] 收到 end 信号，结束本轮处理")
                break
        else:
            logger.warning(f"工具调用循环达到上限 ({MAX_TOOL_ROUNDS} 轮)")

        # 4. 记录记忆
        if is_neo4j_available():
            memory_writer.full_memory_record(input_message, related_memory)

        return all_output_messages