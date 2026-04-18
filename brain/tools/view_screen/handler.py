"""
屏幕截图工具 — 截取当前屏幕画面供模型分析。

需要屏幕捕捉功能已启用（service_manager.screen_capture_enabled）。
截图以 base64 data URL 存入 context["_pending_images"]，
由 LingYiCore 在下一轮模型调用时作为 input_image 注入。
"""

import base64
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    reason = args.get("reason", "").strip()

    # 权限检查：屏幕捕捉功能是否已启用
    if not context.get("screen_capture_enabled", False):
        return "无权限：屏幕捕捉功能未启用。请让用户在侧边栏开启「屏幕捕捉」后再试。"

    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            # 截取主显示器全屏
            monitor = sct.monitors[1]  # monitors[0] 是所有屏幕的合集，[1] 是主屏
            screenshot = sct.grab(monitor)

            # 转为 PIL Image → JPEG（压缩体积）
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

            # 缩放到合理尺寸，避免 token 过多
            max_dim = 1920
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"

        # 将图片存入 pending_images，LingYiCore 会在下轮注入
        pending = context.setdefault("_pending_images", [])
        pending.append({
            "data_url": data_url,
            "description": f"屏幕截图（{reason}）" if reason else "屏幕截图",
        })

        size_kb = len(b64) * 3 // 4 // 1024
        logger.info(f"[view_screen] 截图完成，{img.width}x{img.height}，{size_kb}KB")
        return f"已截取屏幕画面（{img.width}x{img.height}），截图原因：{reason}"

    except ImportError as e:
        return f"缺少依赖：{e}。请安装 mss 和 Pillow。"
    except Exception as e:
        logger.error(f"[view_screen] 截图失败: {e}")
        return f"截图失败: {e}"
