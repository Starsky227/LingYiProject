import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    enabled = bool(context.get("voice_output_enabled", False))
    speaker = context.get("voice_output_service")

    if not enabled or speaker is None:
        return "语音模式未开启，speak_text 当前不可用"

    text = str(args.get("text", "")).strip()
    if not text:
        return "缺少必要参数: text"

    voice = str(args.get("voice", "") or "").strip()
    language = str(args.get("language", "") or "").strip()
    instructions = str(args.get("instructions", "") or "").strip()

    try:
        speed = float(args.get("speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0

    try:
        return speaker.speak_text(
            text=text,
            voice=voice,
            instructions=instructions,
            speed=speed,
            language=language,
        )
    except Exception as e:
        logger.error(f"[speak_text] failed: {e}")
        return f"语音播放失败: {e}"
