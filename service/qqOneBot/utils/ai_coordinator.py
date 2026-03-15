
from datetime import datetime, time
import os
import sys
from typing import Any, Optional
import logging


# 添加项目根目录到Python路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from brain.lingyi_core.lingyi_core import LingYiCore
from system.config import config
from utils.conversation_session import ConversationSession
from utils.common import extract_text
from onebot import OneBotClient, get_message_content

logger = logging.getLogger(__name__)

def load_prompt_file(filename: str, description: str = "") -> str:
    """
    加载提示词文件的通用函数，支持从任意位置导入调用。
    Args:
        filename: 相对于项目根目录的路径（如 "brain/memory/prompt/memory_record.txt"）
                  或仅文件名（如 "memory_record.txt"，将在当前文件同级的 prompt 目录下查找）
        description: 文件描述（用于错误提示）
    Returns:
        文件内容字符串，失败时返回空字符串
    """
    # 优先尝试 service/qqOneBot/Prompt 目录
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "Prompt", filename)
    # 如果找不到，回退到项目根目录
    if not os.path.exists(prompt_path):
        prompt_path = os.path.join(project_root, filename)
    
    if not os.path.exists(prompt_path):
        error_msg = f"{description}提示词文件不存在: {prompt_path}"
        logger.error(error_msg)
        return ""
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            logger.error(f"{description}提示词文件为空: {prompt_path}")
        return content

AI_MAIN_PROMPT = load_prompt_file("qq_prompt.txt", "AI主提示词")
MEMORY_RECORD_PROMPT = load_prompt_file("qqMemoryRecord.txt", "记忆存储")
EVENT_EXTRACT_PROMPT = load_prompt_file("qqEventExtract.txt", "事件提取")


class AICoordinator:
    """AI 协调器，处理 AI 回复逻辑、Prompt 构建和队列管理"""

    def __init__(
        self,
        ai: LingYiCore,
        onebot: Any,  # OneBotClient
        tool_registry: Any = None,
    ) -> None:
        self.config = config
        self.ai = ai
        self.onebot = onebot
        self.tool_registry = tool_registry

    def _make_tool_executor(self, session_key: str, event: dict[str, Any], session: Any = None):
        """创建一个携带上下文的工具执行器闭包"""
        if not self.tool_registry:
            return None
        registry = self.tool_registry
        onebot = self.onebot

        async def tool_executor(name: str, args: dict[str, Any]) -> str:
            context = {
                "onebot": onebot,
                "session": session,
                "session_key": session_key,
                "event": event,
            }
            return await registry.execute(name, args, context)

        return tool_executor
    
    def extract_event_metadata(self, event: dict[str, Any]) -> dict[str, Any]:
        """从事件中提取发送者和群组元数据
        
        Returns:
            包含 user_id, display_name, group_display_name, role 的字典
        """
        message_type = event.get("message_type", "")
        if message_type == "":
            group_id = event.get("group_id", 0)
            message_type = "private" if group_id == 0 else "group"

        time_str = datetime.fromtimestamp(event.get("time", time.time())).strftime("%Y-%m-%d %H:%M:%S")

        sender = event.get("sender", {})
        user_id = sender.get("user_id", "unknown")
        card = sender.get("card", "")
        role = sender.get("role", "")
        nickname = sender.get("nickname", "")
        display_name = card if card else nickname
        
        group_name = event.get("group_name", "")
        group_id = event.get("group_id", 0)
        if group_id:
            group_display_name = f"[{group_name}({group_id})]"
        else:
            group_display_name = ""
        
        message = get_message_content(event.get("message", []))
        return {
            'message_type': message_type,
            'time_str': time_str,
            'user_id': user_id,
            'display_name': display_name,
            'role': role,
            'group_display_name': group_display_name,
            'message': message,
        }
    
    async def handle_poke(self, session_key: str, event: dict[str, Any], sender_name: str, session: ConversationSession = None) -> None:
        """处理拍一拍事件"""
        meta = self.extract_event_metadata(event)
        history_context = session.get_formatted_context() if session else ""
        tool_executor = self._make_tool_executor(session_key, event, session)
        # 组装关键词列表：包含发送者和群组信息
        if meta['message_type'] == "group":
            key_word_list = [
                str(meta['user_id']),
                meta['display_name'],
                meta['group_display_name'],
            ]
            message_content = f"{meta['group_display_name']} {meta['time_str']} {sender_name} 拍了拍你"
        elif meta['message_type'] == "private":
            key_word_list = [
                str(meta['user_id']),
                meta['display_name'],
            ]
            message_content = f"{meta['time_str']} {sender_name} 拍了拍你"
        await self.ai.process_qq_message(message_content, session_key, key_word_list, history_context=history_context, tool_executor=tool_executor)

    async def handle_group_message(self, session_key: str, event: dict[str, Any], at_bot: bool, session: ConversationSession = None) -> None:
        """处理群消息"""
        meta = self.extract_event_metadata(event)
        history_context = session.get_formatted_context() if session else ""
        tool_executor = self._make_tool_executor(session_key, event, session)
        # 组装关键词列表：包含发送者和群组信息
        key_word_list = [
            str(meta['user_id']),
            meta['display_name'],
            meta['group_display_name'],
        ]
        message_content = f"{meta['group_display_name']} {meta['time_str']} <{meta['display_name']}({meta['user_id']}){meta['role']}> {meta['message']}\n"
        if at_bot:
            await self.ai.process_qq_message(message_content, session_key, key_word_list, "[群聊消息] 有人 @ 你，建议进行回复", history_context=history_context, tool_executor=tool_executor)
        else:
            await self.ai.process_qq_message(message_content, session_key, key_word_list, "[群聊消息] 如非直接指向你的消息，建议保持静默", history_context=history_context, tool_executor=tool_executor)
    
    async def handle_private_message(self, session_key: str, event: dict[str, Any], session: ConversationSession = None) -> None:
        """处理私聊消息"""
        meta = self.extract_event_metadata(event)
        history_context = session.get_formatted_context() if session else ""
        tool_executor = self._make_tool_executor(session_key, event, session)
        # 组装关键词列表：包含发送者信息
        key_word_list = [
            str(meta['user_id']),
            meta['display_name'],
        ]
        message_content = f"{meta['time_str']} <{meta['display_name']}({meta['user_id']})> {meta['message']}\n"
        await self.ai.process_qq_message(message_content, session_key, key_word_list, "[私聊消息] 用户专门向你发送的消息，建议进行回复", history_context=history_context, tool_executor=tool_executor)