"""消息处理和命令分发"""

import logging
import os
import random
import time
import asyncio
from typing import Any

from brain.lingyi_core.lingyi_core import LingYiCore
from system.config import config
# from Undefined.faq import FAQStorage
# from Undefined.rate_limit import RateLimiter
# from Undefined.services.queue_manager import QueueManager
from onebot import OneBotClient, get_message_content, get_message_sender_id
from utils.conversation_session import ConversationSession
from utils.common import extract_text, parse_message_content_for_history
# from Undefined.utils.history import MessageHistoryManager
# from Undefined.utils.scheduler import TaskScheduler
# from Undefined.utils.sender import MessageSender
# from Undefined.services.security import SecurityService
# from Undefined.services.command import CommandDispatcher
from utils.ai_coordinator import AICoordinator

# from Undefined.scheduled_task_storage import ScheduledTaskStorage
from utils.logging import log_debug_json, redact_string

logger = logging.getLogger(__name__)

class MessageHandler:
    """消息处理器"""

    def __init__(
        self,
        onebot: OneBotClient,
        ai: LingYiCore,
        # faq_storage: FAQStorage,
        # task_storage: ScheduledTaskStorage,
    ) -> None:
        self.onebot = onebot
        self.sessions: dict[str, ConversationSession] = {}  # 会话存储 {session_key: ConversationSession}
        self.ai_coordinator = AICoordinator(
            ai,
            onebot,
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

        # 0. 检查黑白名单
        message_type = event.get("message_type", "private")
        
        if message_type == "group":
            group_id = event.get("group_id")
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
            user_id = event.get("user_id")
            # 如果白名单不为空，且该用户不在白名单中直接return
            if config.qq_config.private_whitelist:
                if user_id not in config.qq_config.private_whitelist:
                    logger.debug(f"用户 {user_id} 不在白名单中，消息无需记录和处理")
                    return
            # 如果白名单为空，该用户在黑名单中则直接return
            elif user_id in config.qq_config.private_blacklist:
                logger.debug(f"用户 {user_id} 在黑名单中，消息无需记录和处理")
                return
            
            session_key = f"private_{user_id}"

        # 1. 如果发送者是自己，消息已记录，不做进一步处理
        sender_id = get_message_sender_id(event)
        if sender_id == config.qq_config.bot_qq:
            logger.debug(f"收到自己的消息，仅记录消息")
            return
        # 2. 获取或创建对应的ConversationSession
        if session_key not in self.sessions:
            session_type = "group" if message_type == "group" else "private"
            session_id = event.get("group_id") if message_type == "group" else event.get("user_id")
            session = ConversationSession(session_type, session_id)
            session.set_hold_release_callback(self._on_hold_released)
            self.sessions[session_key] = session
            logger.debug(f"创建新的会话: {session_key}")
        
        session = self.sessions[session_key]

        # 3. 添加消息到上下文
        session.add_message(event)

        # 4. 频率控制
        # 如果on_hold状态，消息已记录，不做进一步处理
        if session.on_hold:
            logger.debug(f"会话 {session_key} 处于hold状态，仅记录消息")
            return

        # 检查频率，session会自动管理hold状态和计时器
        status = session.check_frequency()
        if status == "hold":
            return

        # 达到speak_freq间隔，处理消息
        logger.debug(f"会话 {session_key} 达到speak_freq间隔，准备处理消息")
        await self.process_message(session, event)

    async def _on_hold_released(
        self, session: ConversationSession, pending_events: list[dict[str, Any]]
    ) -> None:
        """hold释放回调，处理hold期间积累的消息"""
        if pending_events:
            await self.process_message(session, pending_events[-1])

    async def process_message(self, session: ConversationSession, event: dict[str, Any]) -> None:
        """处理消息"""
        message_content = get_message_content(event)
        text_preview = extract_text(message_content, config.qq_config.bot_qq)
        logger.info(f"处理消息 - 会话: {session.session_key}, 内容: {redact_string(text_preview)}")
        
        # 标记已响应
        session.mark_responded()
        
        # 这里添加实际的消息处理逻辑
        post_type = event.get("post_type", "message")

        """# 处理拍一拍事件（效果同被 @）
        if post_type == "notice" and event.get("notice_type") == "poke":
            target_id = event.get("target_id", 0)
            # 只有拍机器人才响应
            if target_id != config.qq_config.bot_qq:
                logger.debug(f"[通知] 忽略拍一拍目标非机器人: target={target_id}")
                return

            poke_group_id: int = event.get("group_id", 0)
            poke_sender_id: int = event.get("user_id", 0)

            if poke_group_id == 0:
                logger.info("[通知] 私聊拍一拍，触发私聊回复")
                await self.ai_coordinator.handle_private_reply(
                    poke_sender_id,
                    "(拍了拍你)",
                    [],
                    is_poke=True,
                    sender_name=str(poke_sender_id),
                )
            else:
                logger.info(f"[通知] 群聊拍一拍，触发群聊回复: group={poke_group_id}")
                await self.ai_coordinator.handle_auto_reply(
                    poke_group_id,
                    poke_sender_id,
                    "(拍了拍你)",
                    [],
                    is_poke=True,
                    sender_name=str(poke_sender_id),
                    group_name=str(poke_group_id),
                )
            return"""
        
        # 当前不管什么消息都直接丢给ai_coordinator
        asyncio.create_task(self.ai_coordinator.temp_message_handeling(session, event))
        return

        # 处理私聊消息
        if event.get("message_type") == "private":
            private_sender_id: int = get_message_sender_id(event)
            private_message_content: list[dict[str, Any]] = get_message_content(event)

            # 获取发送者昵称
            private_sender: dict[str, Any] = event.get("sender", {})
            private_sender_nickname: str = private_sender.get("nickname", "")

            # 获取私聊用户昵称
            user_name = private_sender_nickname
            if not user_name:
                try:
                    user_info = await self.onebot.get_stranger_info(private_sender_id)
                    if user_info:
                        user_name = user_info.get("nickname", "")
                except Exception as e:
                    logger.warning(f"获取用户昵称失败: {e}")

            text = extract_text(private_message_content, config.qq_config.bot_qq)
            safe_text = redact_string(text)
            logger.info(
                f"[私聊消息] sender={private_sender_id} name={user_name or private_sender_nickname} | {safe_text[:100]}"
            )

            # 如果是 bot 自己的消息，只保存不触发回复，避免无限循环
            if private_sender_id == config.qq_config.bot_qq:
                return

            # 私聊消息直接触发回复
            await self.ai_coordinator.handle_private_reply(
                private_sender_id,
                text,
                private_message_content,
                sender_name=user_name,
            )
            return
            