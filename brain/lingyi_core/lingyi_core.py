import logging
from openai import OpenAI
from system.config import config
from brain.memory.search_memory import full_memory_search
from brain.memory.record_memory import MemoryWriter
from brain.memory.memory_context import MemoryContext
from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager

logger = logging.getLogger(__name__)

# 初始化 OpenAI 客户端
client = OpenAI(
    api_key=config.api.api_key,
    base_url=config.api.base_url,
)


class LingYiCore:
    """AI 模型客户端"""

    def __init__(self, prompt) -> None:
        self.prompt = prompt
        self._kg_manager = get_knowledge_graph_manager()
        self._memory_contexts: dict[str, MemoryContext] = {}

    def _get_memory_writer(self, session_key: str) -> MemoryWriter:
        """获取指定会话的 MemoryWriter（按 session_key 隔离 MemoryContext）"""
        if session_key not in self._memory_contexts:
            self._memory_contexts[session_key] = MemoryContext(session_id=session_key)
        return MemoryWriter(kg_manager=self._kg_manager, memory_context=self._memory_contexts[session_key])

    def process_qq_group_message(self, information: str, session_key: str = "default", key_word_list: list[str] = None) -> str:
        """处理输入信息，生成回复"""
        print(f"收到输入信息: {information}")

        # 获取 memory_writer（内含该会话的 MemoryContext）
        memory_writer = self._get_memory_writer(session_key)

        # 1. 搜索相关记忆（自动从 MemoryContext 获取上下文）
        related_memory = full_memory_search(information, save_temp_memory=False, add_keywords=key_word_list, memory_context=memory_writer.memory_context)
        logger.debug(f"检索到 {len(related_memory.get('nodes', []))} 个相关记忆节点")
        
        '''# 2. 调用主模型生成回复
        messages = [
            {"role": "system", "content": self.prompt},
        ]
        
        user_content = information
        if related_memory:
            user_content = related_memory + "\n\n【当前消息】\n" + information
        
        messages.append({"role": "user", "content": user_content})
        
        try:
            response = client.chat.completions.create(
                model=config.api.model,
                messages=messages,
                temperature=config.api.temperature,
                max_tokens=config.api.max_tokens,
            )
            reply = response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"主模型调用失败: {e}")
            return ""'''
        
        # 3. 记录记忆
        memory_writer.full_memory_record(information, related_memory)
        
        # return reply