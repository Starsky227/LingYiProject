"""为不同会话准备的独立的上下文存储，及其他内容的记录空间"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from system.config import config
from utils.common import extract_text

logger = logging.getLogger(__name__)


class ConversationSession:

    def __init__(self, session_type: str, session_id: int):
        self.session_type = session_type
        self.session_id = session_id
        self.session_key = f"{session_type}_{session_id}"
        self.context: list[dict[str, Any]] = []

    def add_message(self, event: dict[str, Any]) -> None:
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
        
        添加消息到上下文，最多保留15条
        """
        self.context.append(event)
        if len(self.context) > 15:
            self.context = self.context[-15:]
        
        # 写入历史记录
        self.write_message_history(event)

    def extract_event_metadata(self, event: dict[str, Any]) -> dict[str, Any]:
        """从事件中提取发送者和群组元数据
        
        Returns:
            包含 user_id, display_name, group_display_name, role 的字典
        """
        sender = event.get("sender", {})
        user_id = sender.get("user_id", "unknown")
        card = sender.get("card", "")
        role = sender.get("role", "")
        nickname = sender.get("nickname", "")
        display_name = card if card else nickname
        
        group_name = event.get("group_name", "")
        if group_name:
            group_display_name = f"[{group_name}({self.session_id})]"
        else:
            group_display_name = f"[({self.session_id})]"
        
        return {
            "user_id": user_id,
            "display_name": display_name,
            "group_display_name": group_display_name,
            "role": role,
        }

    def parse_message_content(self, event: dict[str, Any]) -> str:
        """解析事件为格式化的消息行"""
        # 提取时间
        timestamp = event.get("time", time.time())
        msg_time = datetime.fromtimestamp(timestamp)
        
        # 提取发送者和群组信息
        meta = self.extract_event_metadata(event)
        
        # 提取消息内容
        message_content = event.get("message", [])
        text = extract_text(message_content, 0)  # 不过滤任何@
        
        # 构建消息行
        time_str = msg_time.strftime("%Y-%m-%d %H:%M:%S")
        role_suffix = f"{meta['role']}" if meta["role"] else ""
        return f"{meta['group_display_name']} {time_str} <{meta['display_name']}({meta['user_id']}){role_suffix}> {text}\n"

    def get_formatted_context(self) -> str:
        """将 context 中的所有事件格式化为历史消息文本"""
        if not self.context:
            return ""
        return "".join(self.parse_message_content(event) for event in self.context)

    def write_message_history(self, event: dict[str, Any]) -> None:
        """将消息写入历史记录文件"""
        try:
            message_line = self.parse_message_content(event)
            
            # 确定文件路径
            timestamp = event.get("time", time.time())
            msg_time = datetime.fromtimestamp(timestamp)
            year_month = msg_time.strftime("%Y_%m")
            log_dir = Path(f"logs/qqOnebot/chat_history/{self.session_id}")
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"{year_month}.txt"
            
            # 写入文件
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(message_line)
                
        except Exception as e:
            logger.warning(f"写入消息历史失败: {e}")

    def _get_last_recorded_timestamp(self) -> float | None:
        """从日志文件中读取最后一条记录的时间戳（秒级 UNIX 时间）"""
        log_dir = Path(f"logs/qqOnebot/chat_history/{self.session_id}")
        if not log_dir.exists():
            return None

        # 按文件名降序排列找到最新的日志文件
        log_files = sorted(log_dir.glob("*.txt"), reverse=True)
        if not log_files:
            return None

        # 从最新日志文件的末尾找最后一行有效时间戳
        time_pattern = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception:
                continue

            for line in reversed(lines):
                match = time_pattern.search(line)
                if match:
                    try:
                        dt = datetime.strptime(match.group(), "%Y-%m-%d %H:%M:%S")
                        return dt.timestamp()
                    except ValueError:
                        continue
        return None

    async def backfill_history(self, onebot) -> None:
        """启动时从 OneBot 拉取群历史消息，补全本地日志（仅群聊会话有效）"""
        if self.session_type not in ("group", "qq_group"):
            return

        group_id = self.session_id
        last_ts = self._get_last_recorded_timestamp()

        try:
            history_messages = await onebot.get_group_msg_history(group_id)
        except Exception as e:
            logger.warning(f"拉取群 {group_id} 历史消息失败: {e}")
            return

        if not history_messages:
            return

        # 按时间升序排列
        history_messages.sort(key=lambda m: m.get("time", 0))

        new_count = 0
        for msg in history_messages:
            msg_ts = msg.get("time", 0)
            # 跳过已记录的消息（时间戳 <= 日志末尾）
            if last_ts is not None and msg_ts <= last_ts:
                continue
            # 写入日志
            self.write_message_history(msg)
            new_count += 1

        if new_count:
            logger.info(f"群 {group_id} 补全了 {new_count} 条历史消息")
