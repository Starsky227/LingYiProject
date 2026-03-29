import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Optional
import logging


# 添加项目根目录到Python路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from brain.lingyi_core.lingyi_core import LingYiCore
from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from system.config import config
from utils.conversation_session import ConversationSession
from utils.common import extract_text
from onebot import get_message_content, parse_message_time

logger = logging.getLogger(__name__)


MEDIA_LABELS = {
    "image": "图片",
    "file": "文件",
    "video": "视频",
    "record": "语音",
    "audio": "音频",
}


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

class AICoordinator:
    """“AI 协调器 — 将 QQ 消息推送到 lingyi_core 的 InputBuffer，由 lingyi_core 统一处理定时收集与模型/工具调用"""

    def __init__(
        self,
        ai: LingYiCore,
        onebot: Any,  # OneBotClient
    ) -> None:
        self.config = config
        self.ai = ai
        self.onebot = onebot
        self._session_contexts: dict[str, dict[str, Any]] = {}  # session_key -> {event, session}
        self._processing_tasks: dict[str, asyncio.Task] = {}  # session_key -> 运行中的 process_message task

    def extract_event_metadata(self, event: dict[str, Any]) -> dict[str, Any]:
        """从事件中提取发送者和群组元数据
        
        Returns:
            包含 user_id, display_name, group_display_name, role 的字典
        """
        message_type = event.get("message_type", "")
        if message_type == "":
            group_id = event.get("group_id", 0)
            message_type = "private" if group_id == 0 else "group"

        time_str = parse_message_time(event)

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
        
        message = extract_text(get_message_content(event))

        return {
            'message_type': message_type,
            'time_str': time_str,
            'user_id': user_id,
            'display_name': display_name,
            'role': role,
            'group_id': group_id,
            'group_display_name': group_display_name,
            'message': message,
        }

    def _build_address_info(self, session_key: str, event: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        """构造传递给 lingyi_core / agentserver 的通用地址讯息。"""
        attachments: list[dict[str, Any]] = []
        for segment in get_message_content(event):
            seg_type = str(segment.get("type", "")).strip()
            seg_data = segment.get("data", {}) or {}
            file_token = str(seg_data.get("file", "") or seg_data.get("url", "")).strip()
            if seg_type not in MEDIA_LABELS or not file_token:
                continue

            display = f"[{MEDIA_LABELS[seg_type]}: {file_token}]"
            attachments.append({
                "channel": "qq",
                "resolver": "qq_image_token" if seg_type == "image" else "qq_media_token",
                "media_type": seg_type,
                "token": file_token,
                "display": display,
                "name": Path(file_token).name or file_token,
                "downloadable": seg_type == "image",
            })

        return {
            "channel": "qq",
            "session_key": session_key,
            "message_type": meta.get("message_type", ""),
            "user_id": meta.get("user_id"),
            "group_id": meta.get("group_id"),
            "group_display_name": meta.get("group_display_name", ""),
            "attachments": attachments,
            "capabilities": [
                {
                    "resolver": "qq_image_token",
                    "description": "可将 QQ 图片 token 解析为下载地址",
                }
            ],
        }

    def _format_address_message(self, address_info: dict[str, Any]) -> str:
        """将结构化地址讯息格式化为给模型看的文本。"""
        attachments = address_info.get("attachments", []) or []
        if not attachments:
            return "[地址讯息]\n当前消息未携带可追踪附件。"

        lines = ["[地址讯息]", "当前窗口来源: QQ", "可追踪附件:"]
        for item in attachments:
            resolver = item.get("resolver", "unknown")
            display = item.get("display", item.get("token", ""))
            downloadable = "可下载" if item.get("downloadable") else "暂不可下载"
            lines.append(f"- {display} | resolver={resolver} | {downloadable}")
        lines.append("若工具找不到文件路径，可优先使用以上原始附件标记或 token 进行回溯下载。")
        return "\n".join(lines)

    async def _resolve_file_source_from_address(
        self,
        file_source: str,
        address_info: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """根据地址讯息回溯文件源，目前实现 QQ 图片 token -> URL。"""
        source = (file_source or "").strip()
        if not source:
            return None

        attachments = (address_info or {}).get("attachments", []) or []
        normalized = source.strip().strip('"').strip("'")
        normalized_name = Path(normalized).name or normalized

        for item in attachments:
            token = str(item.get("token", "")).strip()
            display = str(item.get("display", "")).strip()
            name = str(item.get("name", "")).strip()
            resolver = str(item.get("resolver", "")).strip()

            matched = normalized in {token, display, name} or normalized_name in {token, display, name}
            if not matched:
                continue

            if resolver == "qq_image_token":
                try:
                    resolved = await self.onebot.get_image(token)
                    if resolved:
                        logger.info(f"[AICoordinator] resolved QQ image token via address info: {token}")
                        return resolved
                except Exception as e:
                    logger.warning(f"[AICoordinator] resolve QQ image token failed: {e}")

        return None

    async def _ensure_qq_graph_bindings(self, sender_name: str, meta: dict[str, Any]) -> None:
        """确保 QQ 用户与群信息在记忆图谱中存在基础绑定关系。"""
        try:
            await asyncio.to_thread(self._ensure_qq_graph_bindings_sync, sender_name, meta)
        except Exception as e:
            logger.warning(f"[AICoordinator] ensure qq graph bindings failed: {e}")

    def _ensure_qq_graph_bindings_sync(self, sender_name: str, meta: dict[str, Any]) -> None:
        """同步执行图谱创建/复用逻辑（在线程中调用）。"""
        kg_manager = get_knowledge_graph_manager()
        if not kg_manager.connected and not kg_manager._ensure_connection():
            logger.debug("[AICoordinator] skip qq graph binding: neo4j unavailable")
            return

        character_name = (sender_name or meta.get("display_name") or "").strip()
        if not character_name:
            character_name = str(meta.get("user_id", "")).strip()

        user_id_str = str(meta.get("user_id", "")).strip()
        if not character_name or not user_id_str:
            logger.debug("[AICoordinator] skip qq graph binding: empty sender name or user id")
            return

        character_node_id = kg_manager.ensure_node_exists(
            node_type="Character",
            name=character_name,
            trust=0.4,
            context="qq",
            note="",
        )
        user_entity_node_id = kg_manager.ensure_node_exists(
            node_type="Entity",
            name=user_id_str,
            context="qq",
            note="",
        )

        if character_node_id and user_entity_node_id:
            kg_manager.ensure_relation_exists(
                start_node_id=character_node_id,
                end_node_id=user_entity_node_id,
                predicate="QQ号是",
                source="qq_auto_binding",
                confidence=0.95,
                importance=0.4,
                directivity="single",
                evidence="由QQ事件自动建立",
            )

        if meta.get("message_type") == "group":
            group_display_name = str(meta.get("group_display_name", "")).strip()
            if group_display_name and character_node_id:
                group_entity_node_id = kg_manager.ensure_node_exists(
                    node_type="Entity",
                    name=group_display_name,
                    context="qq",
                    note="qq群",
                )
                if group_entity_node_id:
                    kg_manager.ensure_relation_exists(
                        start_node_id=character_node_id,
                        end_node_id=group_entity_node_id,
                        predicate="在QQ群",
                        source="qq_auto_binding",
                        confidence=0.95,
                        importance=0.4,
                        directivity="single",
                        evidence="由QQ群消息自动建立",
                    )
    
    async def handle_poke(self, session_key: str, event: dict[str, Any], sender_name: str, session: ConversationSession = None) -> None:
        """处理拍一拍事件"""
        meta = self.extract_event_metadata(event)
        address_info = self._build_address_info(session_key, event, meta)
        await self._ensure_qq_graph_bindings(sender_name, meta)
        if meta['message_type'] == "group":
            key_word_list = [str(meta['user_id']), meta['display_name'], meta['group_display_name']]
            message_content = f"{meta['time_str']} {sender_name}({meta['user_id']}) 拍了拍你，建议查看上下文分析对方的需求。"
            caller_message = f"来自QQ群{meta['group_display_name']}的消息。"
        else:
            key_word_list = [str(meta['user_id']), meta['display_name']]
            message_content = f"{meta['time_str']} {sender_name}({meta['user_id']}) 拍了拍你，建议查看上下文分析对方的需求。"
            caller_message = f"来自QQ用户{sender_name}({meta['user_id']})的私聊消息。"
        await self._push_and_trigger(session_key, event, session, message_content, caller_message, key_word_list, address_info)

    async def handle_group_message(self, session_key: str, event: dict[str, Any], at_bot: bool, session: ConversationSession = None) -> None:
        """处理群消息"""
        meta = self.extract_event_metadata(event)
        address_info = self._build_address_info(session_key, event, meta)
        await self._ensure_qq_graph_bindings(meta['display_name'], meta)
        key_word_list = [str(meta['user_id']), meta['display_name'], meta['group_display_name']]
        if at_bot:
            message_content = f"{meta['time_str']} <{meta['display_name']}({meta['user_id']}){meta['role']}> !!有人@你，建议对其进行回复!! {meta['message']}\n"
            caller_message = f"来自QQ群{meta['group_display_name']}的消息。"
        else:
            message_content = f"{meta['time_str']} <{meta['display_name']}({meta['user_id']}){meta['role']}> {meta['message']}\n"
            caller_message = f"来自QQ群{meta['group_display_name']}的消息。"
        await self._push_and_trigger(session_key, event, session, message_content, caller_message, key_word_list, address_info)

    async def handle_private_message(self, session_key: str, event: dict[str, Any], session: ConversationSession = None) -> None:
        """处理私聊消息"""
        meta = self.extract_event_metadata(event)
        address_info = self._build_address_info(session_key, event, meta)
        await self._ensure_qq_graph_bindings(meta['display_name'], meta)
        key_word_list = [str(meta['user_id']), meta['display_name']]
        message_content = f"{meta['time_str']} <{meta['display_name']}({meta['user_id']})> {meta['message']}\n"
        caller_message = f"来自QQ用户{meta['display_name']}({meta['user_id']})的私聊消息，建议对其进行回复"
        await self._push_and_trigger(session_key, event, session, message_content, caller_message, key_word_list, address_info)

    # ---- 消息推送与处理触发 ----

    async def _push_and_trigger(
        self,
        session_key: str,
        event: dict[str, Any],
        session: Any,
        message_content: str,
        caller_message: str,
        key_word_list: list[str],
        address_info: Optional[dict[str, Any]] = None,
    ) -> None:
        """将消息推送到 input_buffer 并确保处理流程已启动"""
        session_state = self.ai.get_session_state(session_key)
        # 推入 input_buffer，由 lingyi_core 统一收集和处理
        await session_state.input_buffer.put(message_content, caller_message, key_word_list)
        # 更新 tool_context，供工具执行时动态引用最新的 QQ 上下文
        group_id = event.get("group_id", 0)
        user_id = event.get("sender", {}).get("user_id", 0)
        session_state.tool_context.update({
            "onebot": self.onebot,
            "session": session,
            "event": event,
            "address_info": address_info or {},
            "resolve_file_source_callback": self._resolve_file_source_from_address,
            "get_image_url_callback": self.onebot.get_image,
            "group_id": group_id if group_id else None,
            "user_id": user_id if user_id else None,
        })
        # 维护内部 event/session 引用
        self._session_contexts[session_key] = {"event": event, "session": session}
        # 触发处理（如果当前没有运行中的处理任务）
        self._ensure_processing(session_key)

    def _ensure_processing(self, session_key: str) -> None:
        """确保该 session 有运行中的 process_message，否则启动一个"""
        task = self._processing_tasks.get(session_key)
        if task and not task.done():
            return  # 已在处理中，新消息会通过 buffer 被 drain
        task = asyncio.ensure_future(self._run_processing(session_key))
        self._processing_tasks[session_key] = task

    async def _run_processing(self, session_key: str) -> None:
        """运行 process_message 并在结束后检查是否有缓冲消息遗留"""
        try:
            await self.ai.process_message(
                session_key=session_key,
            )
        except Exception as e:
            logger.error(f"[AICoordinator] session={session_key} 处理消息异常: {e}")
        finally:
            self._processing_tasks.pop(session_key, None)
            # 检查是否有在结束瞬间到达的新消息
            session_state = self.ai.get_session_state(session_key)
            if session_state.input_buffer.has_pending():
                self._ensure_processing(session_key)