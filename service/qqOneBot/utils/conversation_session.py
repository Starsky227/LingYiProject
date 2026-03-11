"""为不同会话准备的独立的上下文存储，及其他内容的记录空间"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from system.config import config
from utils.common import extract_text

logger = logging.getLogger(__name__)

# hold释放时的回调类型: async def callback(session, events) -> None
HoldReleaseCallback = Callable[["ConversationSession", list[dict[str, Any]]], Coroutine]

class ConversationSession:

    def __init__(self, session_type: str, session_id: int):
        self.session_type = session_type
        self.session_id = session_id
        self.session_key = f"{session_type}_{session_id}"
        self.context: list[dict[str, Any]] = []
        self.last_response_time: float = 0
        self.speak_freq: int = config.qq_config.speak_freq
        self.on_hold = False
        self.hold_message_id: int | None = None
        self._hold_timer_task: asyncio.Task | None = None
        self._on_hold_release: HoldReleaseCallback | None = None

    def set_hold_release_callback(self, callback: HoldReleaseCallback) -> None:
        """注册hold释放时的回调函数"""
        self._on_hold_release = callback

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
        
        添加消息到上下文，最多保留25条
        """
        self.context.append(event)
        if len(self.context) > 25:
            self.context = self.context[-25:]
        
        # 写入历史记录
        self.write_message_history(event)

    def check_frequency(self) -> str:
        """检查频率控制，返回状态: 'hold' | 'ready'
        
        - 'hold': 未达到speak_freq间隔，已自动设置hold状态并启动计时器
        - 'ready': 达到speak_freq间隔，可以处理消息
        """
        current_time = time.time()
        time_since_last = current_time - self.last_response_time

        if time_since_last <= self.speak_freq:
            remaining = self.speak_freq - time_since_last
            logger.debug(
                f"会话 {self.session_key} 未达到speak_freq间隔 "
                f"({time_since_last:.1f}s < {self.speak_freq}s)，hold {remaining:.1f}s"
            )
            self._enter_hold(self.context[-1].get("message_id") if self.context else None, remaining)
            return "hold"

        return "ready"

    def _enter_hold(self, message_id: int | None, wait_time: float) -> None:
        """进入hold状态并启动自动释放计时器"""
        self.on_hold = True
        self.hold_message_id = message_id
        # 取消旧的计时器（如果有）
        if self._hold_timer_task and not self._hold_timer_task.done():
            self._hold_timer_task.cancel()
        self._hold_timer_task = asyncio.create_task(self._hold_timer(wait_time))

    async def _hold_timer(self, wait_time: float) -> None:
        """hold计时器，到期后自动释放并触发回调"""
        try:
            await asyncio.sleep(wait_time)
            if not self.on_hold:
                return

            logger.debug(f"会话 {self.session_key} 解除hold状态，开始处理积累的消息")
            self.on_hold = False

            if self._on_hold_release and self.context:
                # 将积累的消息交给回调处理
                pending_events = list(self.context)
                await self._on_hold_release(self, pending_events)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"会话 {self.session_key} hold计时器出错: {e}", exc_info=True)

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

    def mark_responded(self) -> None:
        """标记已响应，更新时间戳并保留最近10条上下文"""
        self.last_response_time = time.time()
        self.context = self.context[-10:]