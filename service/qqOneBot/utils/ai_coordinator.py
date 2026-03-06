
from typing import Any, Optional

from system.config import config
from utils.conversation_session import ConversationSession

class AICoordinator:
    """AI 协调器，处理 AI 回复逻辑、Prompt 构建和队列管理"""

    def __init__(
        self,
        # ai: Any,  # AIClient
        # session: ConversationSession,
        # sender: MessageSender,
        onebot: Any,  # OneBotClient
    ) -> None:
        self.config = config
        # self.session = session
        # self.sender = sender
        self.onebot = onebot