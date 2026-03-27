"""程序入口"""

import asyncio
import logging
import sys
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

from brain.lingyi_core.lingyi_core import LingYiCore
from service.qqOneBot.handler import MessageHandler
from service.qqOneBot.utils.ai_coordinator import load_prompt_file
from system.config import config
from onebot import OneBotClient
from qq_tools.tool_registry import BaseRegistry


def _get_log_level() -> tuple[int, str]:
    """从环境变量获取日志级别"""
    log_level = config.system.log_level
    level = getattr(logging, log_level, logging.INFO)
    return level, log_level


def _init_file_handler(root_logger: logging.Logger) -> None:
    """初始化按日期轮转的文件日志处理器，保存到 logs/qqOnebot/logger/ 目录"""
    log_dir = Path("logs/qqOnebot/logger")
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file_path = log_dir / "bot.log"

    file_log_format = (
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler = TimedRotatingFileHandler(
        log_file_path,
        when="midnight",
        interval=1,
        backupCount=15,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d.log"
    handler.setFormatter(logging.Formatter(file_log_format))
    root_logger.addHandler(handler)


async def main() -> None:
    """主函数"""
    logger = logging.getLogger(__name__)
    logger.info("[启动] 正在初始化 Lingyi ...")

    try:
        logger.info(f"[配置] 机器人 QQ: {config.qq_config.bot_qq}")
        logger.info(f"[配置] 主人 QQ: {config.qq_config.master_qq}")
    except ValueError as e:
        logger.error(f"[配置错误] 加载配置失败: {e}")
        sys.exit(1)

    # 初始化组件
    logger.info("[初始化] 正在加载核心组件...")
    try:
        onebot = OneBotClient(config.qq_config.onebot_ws_url, config.qq_config.onebot_token)
        qq_prompt = load_prompt_file("LY_qq_prompt.xml", "AI主提示词")

        # 初始化工具注册表，自动发现 qq_tools 下所有工具
        tools_dir = Path(__file__).parent / "qq_tools"
        qq_registry = BaseRegistry(base_dir=tools_dir, kind="tool")
        qq_registry.load_items()

        # 注册 QQ 专属工具 (prefix: qq-)
        ai = LingYiCore(qq_prompt)
        ai.tool_manager.register_sub_registry("qq-", qq_registry)
        logger.info(
            f"[初始化] 工具总计: {len(ai.tool_manager.get_tools_schema())} 个 "
            f"({ai.tool_manager.get_tool_names()})"
        )

        # 创建handler — 工具已统一注册到 tool_manager，由 lingyi_core 负责调用
        handler = MessageHandler(onebot, ai)

        onebot.set_message_handler(handler.handle_message)
        logger.info("[初始化] 核心组件加载完成")
    except Exception as e:
        logger.exception(f"[初始化错误] 组件初始化期间发生异常: {e}")
        sys.exit(1)

    logger.info("[启动] 机器人已准备就绪，开始连接 OneBot 服务...")


    try:
        await onebot.run_with_reconnect()
    except KeyboardInterrupt:
        logger.info("[退出] 收到退出信号 (Ctrl+C)")
    except Exception as e:
        logger.exception(f"[异常] 运行期间发生未捕获的错误: {e}")
    finally:
        logger.info("[清理] 正在关闭机器人并释放资源...")
        await onebot.disconnect()
        logger.info("[退出] 机器人已停止运行")


def run() -> None:
    """运行入口"""
    level, level_name = _get_log_level()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 过滤不关键的日志
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("onebot").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger().setLevel(level)

    # 初始化文件日志（按日期轮转，保留15天）
    _init_file_handler(logging.getLogger())

    asyncio.run(main())


if __name__ == "__main__":
    run()
