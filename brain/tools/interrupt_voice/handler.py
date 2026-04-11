import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    _ = args
    enabled = bool(context.get("voice_output_enabled", False))
    speaker = context.get("voice_output_service")

    if not enabled or speaker is None:
        return "语音模式未开启，interrupt_voice 当前不可用"

    try:
        return speaker.interrupt_playback(clear_pending=True)
    except Exception as e:
        logger.error(f"[interrupt_voice] failed: {e}")
        return f"打断语音失败: {e}"
