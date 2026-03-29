from typing import Any, Dict
import logging
import httpx

from agentserver.http_client import get_json_with_retry
from agentserver.http_config import get_xxapi_url

logger = logging.getLogger(__name__)


async def execute(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """获取历史上的今天"""
    try:
        logger.info("获取历史上的今天")
        data = await get_json_with_retry(
            get_xxapi_url("/api/history"),
            default_timeout=15.0,
            context=context,
        )

        if data.get("code") != 200:
            return f"获取历史事件失败: {data.get('msg')}"

        history_list = data.get("data", [])
        if not history_list:
            return "暂无历史事件数据"

        result = "【历史上的今天】\n\n"

        for idx, event in enumerate(history_list, 1):
            result += f"{idx}. {event}\n"

        return result

    except httpx.TimeoutException:
        return "请求超时，请稍后重试"
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP 错误: {e}")
        return "获取历史事件失败：网络请求错误"
    except Exception as e:
        logger.exception(f"获取历史事件失败: {e}")
        return "获取历史事件失败，请稍后重试"
