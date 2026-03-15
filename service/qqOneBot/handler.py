"""消息处理和命令分发"""

import logging
from typing import Any

from brain.lingyi_core.lingyi_core import LingYiCore
from system.config import config
from onebot import OneBotClient, get_message_content, get_message_sender_id
from utils.conversation_session import ConversationSession
from utils.ai_coordinator import AICoordinator
from utils.logging import log_debug_json

logger = logging.getLogger(__name__)

class MessageHandler:
    """消息处理器"""

    def __init__(
        self,
        onebot: OneBotClient,
        ai: LingYiCore,
        tool_registry=None,
    ) -> None:
        self.onebot = onebot
        self.sessions: dict[str, ConversationSession] = {}  # 会话存储 {session_key: ConversationSession}
        self.ai_coordinator = AICoordinator(
            ai,
            onebot,
            tool_registry=tool_registry,
        )
    

    async def handle_message(self, event: dict[str, Any]) -> None:
        """
        event格式: {
        'self_id': 3750603665, 
        'user_id': 2319546113, 
        'time': 1772664937, 
        'message_id': 319397807, 
        'message_seq': 319397807, 
        'real_id': 319397807, 
        'real_seq': '374286', 
        'message_type': 'group', 
        'sender': {
            'user_id': 2319546113, 
            'nickname': '星空逐夜', 
            'card': '奥莉维娅（星空', 'role': 'admin'}, 
        'raw_message': '看看我能不能收到什么了', 
        'font': 14, 'sub_type': 'normal', 
        'message': [{'type': 'text', 'data': {'text': '看看我能不能收到什么了'}}], 
        'message_format': 'array', 
        'post_type': 'message', 
        'group_id': 485228134, 
        'group_name': '麻辣子（重启中）'}
        """

        # 记录logger
        if logger.isEnabledFor(logging.DEBUG):
            log_debug_json(logger, "[事件数据]", event)
        
        # 1. 检查群聊/私聊，黑白名单，记录session_key
        group_id = event.get("group_id", 0)
        sender_id = get_message_sender_id(event)
        # 1.1 检查群聊/私聊
        message_type = event.get("message_type", "")
        if message_type == "":
            message_type = "private" if group_id == 0 else "group"
        # 1.2 检查黑白名单，记录session_key
        if message_type == "group":  # 群聊
            # 如果白名单不为空，且该群聊不在白名单中直接return
            if config.qq_config.group_whitelist:
                if group_id not in config.qq_config.group_whitelist:
                    logger.debug(f"群聊 {group_id} 不在白名单中，消息无需记录和处理")
                    return
            # 如果白名单为空，该群聊在黑名单中则直接return
            elif group_id in config.qq_config.group_blacklist:
                logger.debug(f"群聊 {group_id} 在黑名单中，消息无需记录和处理")
                return
            session_key = f"group_{group_id}"
        else:  # 私聊
            # 如果发送者是自己，消息已记录，不做进一步处理
            if sender_id == config.qq_config.bot_qq:
                logger.debug(f"收到自己的消息，仅记录消息")
                return
            # 如果白名单不为空，且该用户不在白名单中直接return
            if config.qq_config.private_whitelist:
                if sender_id not in config.qq_config.private_whitelist:
                    logger.debug(f"用户 {sender_id} 不在白名单中，消息无需记录和处理")
                    return
            # 如果白名单为空，该用户在黑名单中则直接return
            elif sender_id in config.qq_config.private_blacklist:
                logger.debug(f"用户 {sender_id} 在黑名单中，消息无需记录和处理")
                return
            session_key = f"private_{sender_id}"

        # 2. 获取或创建会话，记录消息
        if session_key not in self.sessions:
            session_type = "qq_group" if message_type == "group" else "qq_private"
            session_id = group_id if message_type == "group" else sender_id
            session = ConversationSession(session_type, session_id)
            self.sessions[session_key] = session
            logger.debug(f"创建新的会话: {session_key}")
            # 群聊会话首次创建时，从 OneBot 拉取历史消息补全日志
            await session.backfill_history(self.onebot)
        session = self.sessions[session_key]

        # 3. 处理消息
        # 3.1 处理拍一拍事件
        post_type = event.get("post_type", "message")
        if post_type == "notice" and event.get("notice_type") == "poke":
            target_id = event.get("target_id", 0)
            # 只有拍机器人才响应
            if target_id != config.qq_config.bot_qq:
                logger.debug("[通知] 忽略拍一拍目标非机器人: target=%s", target_id)
                return
            # 响应拍一拍
            if message_type == "group":
                member_info = await self.onebot.get_group_member_info(self, group_id, sender_id, no_cache = False)
                if isinstance(member_info, dict):
                    sender_card = str(member_info.get("card", "")).strip()
                    sender_nickname = str(member_info.get("nickname", "")).strip()
                sender_display_name = sender_card if sender_card else sender_nickname
                await self.ai_coordinator.handle_poke(session_key, event, sender_display_name, session)
            else:
                await self.ai_coordinator.handle_poke(session_key, event, "用户", session)

        # 3.2 处理群聊消息
        elif post_type == "message" and message_type == "group":
            # 记录消息到会话上下文和历史文件
            session.add_message(event)
            # 检查是否@了机器人
            message_list = event.get("message", [])
            at_bot = False
            for seg in message_list:
                if seg.get("type") == "at" and str(seg.get("data", {}).get("qq", "")) == str(config.qq_config.bot_qq):
                    at_bot = True
                    break
            await self.ai_coordinator.handle_group_message(session_key, event, at_bot, session)
        
        # 3.3 处理私聊消息
        elif post_type == "message" and message_type == "private":
            # 记录消息到会话上下文和历史文件
            session.add_message(event)
            await self.ai_coordinator.handle_private_message(session_key, event, session)

