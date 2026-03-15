"""
将消息通过OneBot发送出去
默认发送到当前对话窗口
如果指定了 target_type 和 target_id 则发送到对应的qq或群聊。
如果发送失败，根据target_type返回对应的可发送群聊列表或好友列表。
如果发送成功，返回发送结果。
记录发送的消息到消息记录util/conversation_session.py中。
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    """发送QQ消息的工具处理函数

    Args:
        args: 工具调用参数，包含 message, target_type(可选), target_id(可选)
        context: 上下文信息，包含 onebot, session, session_key, event 等
    """
    onebot = context.get("onebot")
    if not onebot:
        return "错误: 上下文中缺少 onebot 客户端"

    message = args.get("message", "")
    if not message:
        return "错误: 消息内容不能为空"

    target_type = args.get("target_type", "")
    target_id = args.get("target_id", "")

    # 如果未指定目标，使用当前会话的信息
    if not target_type or not target_id:
        event = context.get("event", {})
        session_key: str = context.get("session_key", "")

        if session_key.startswith("group_"):
            target_type = "group"
            target_id = str(event.get("group_id", "")) or session_key.removeprefix("group_")
        elif session_key.startswith("private_"):
            target_type = "private"
            target_id = str(event.get("user_id", "")) or session_key.removeprefix("private_")
        else:
            return "错误: 无法确定发送目标，请指定 target_type 和 target_id"

    # 验证 target_id 为合法数字
    try:
        numeric_id = int(target_id)
    except (ValueError, TypeError):
        return f"错误: target_id 不是有效的数字: {target_id}"

    # 发送消息
    try:
        if target_type == "group":
            response = await onebot.send_group_message(numeric_id, message)
        elif target_type == "private":
            response = await onebot.send_private_message(numeric_id, message)
        else:
            return f"错误: 不支持的 target_type: {target_type}，仅支持 'private' 或 'group'"
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"发送消息失败: {error_msg}")
        # 发送失败时，尝试获取可用的目标列表
        return await _get_fallback_list(onebot, target_type, error_msg)

    # 记录发送的消息到会话
    session = context.get("session")
    if session:
        bot_event = _build_bot_event(context, message, target_type, numeric_id)
        session.add_message(bot_event)

    message_id = response.get("data", {}).get("message_id", "")
    logger.info(f"消息发送成功: target_type={target_type}, target_id={numeric_id}, message_id={message_id}")
    return f"消息发送成功 (message_id={message_id})"


async def _get_fallback_list(onebot: Any, target_type: str, error_msg: str) -> str:
    """发送失败时返回可用的群聊/好友列表"""
    try:
        if target_type == "group":
            result = await onebot._call_api("get_group_list")
            groups = result.get("data", [])
            if not groups:
                return f"发送群消息失败: {error_msg}。未获取到可用群聊列表。"
            group_lines = [f"  - {g.get('group_name', '未知')} (群号: {g.get('group_id', '')})" for g in groups]
            return f"发送群消息失败: {error_msg}\n可发送的群聊列表:\n" + "\n".join(group_lines)
        else:
            result = await onebot._call_api("get_friend_list")
            friends = result.get("data", [])
            if not friends:
                return f"发送私聊消息失败: {error_msg}。未获取到好友列表。"
            friend_lines = [f"  - {f.get('nickname', '未知')} (QQ: {f.get('user_id', '')})" for f in friends]
            return f"发送私聊消息失败: {error_msg}\n可发送的好友列表:\n" + "\n".join(friend_lines)
    except Exception as list_err:
        logger.warning(f"获取列表也失败了: {list_err}")
        return f"发送消息失败: {error_msg}。获取可用列表也失败: {list_err}"


def _build_bot_event(context: dict[str, Any], message: str, target_type: str, target_id: int) -> dict[str, Any]:
    """构建机器人发送消息的事件格式，用于记录到会话"""
    from system.config import config

    bot_qq = getattr(config.qq_config, "bot_qq", 0)
    event: dict[str, Any] = {
        "self_id": bot_qq,
        "user_id": bot_qq,
        "time": int(time.time()),
        "message_type": target_type,
        "sender": {
            "user_id": bot_qq,
            "nickname": "Bot",
            "card": "",
            "role": "",
        },
        "raw_message": message,
        "message": [{"type": "text", "data": {"text": message}}],
        "message_format": "array",
        "post_type": "message_sent",
    }
    if target_type == "group":
        event["group_id"] = target_id
        # 尝试从上下文事件中复制群名
        ctx_event = context.get("event", {})
        if ctx_event.get("group_id") == target_id:
            event["group_name"] = ctx_event.get("group_name", "")
    return event