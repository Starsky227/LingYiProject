
from typing import Any, Optional
import logging

from brain.lingyi_core.lingyi_core import LingYiCore
from system.config import config
from utils.conversation_session import ConversationSession

logger = logging.getLogger(__name__)


class AICoordinator:
    """AI 协调器，处理 AI 回复逻辑、Prompt 构建和队列管理"""

    def __init__(
        self,
        ai: LingYiCore,
        # session: ConversationSession,
        # sender: MessageSender,
        onebot: Any,  # OneBotClient
    ) -> None:
        self.config = config
        self.ai = ai
        # self.session = session
        # self.sender = sender
        self.onebot = onebot
    
    async def temp_message_handeling(self, session: ConversationSession, event: dict[str, Any]) -> None:
        """临时代码，直接处理消息，不经过队列和频率控制"""
        # 调用conversation session的parse_message_content
        message_content = session.parse_message_content(event)
        # 将结果直接提供给ai的process_incoming_information
        reply = self.ai.process_incoming_information(message_content, session_key=session.session_key)
        
        if not reply:
            return
        
        # 根据消息类型发送回复
        message_type = event.get("message_type", "private")
        if message_type == "group":
            group_id = event.get("group_id")
            # await self.onebot.send_group_message(group_id, reply)
        else:
            user_id = event.get("user_id")
            # await self.onebot.send_private_message(user_id, reply)