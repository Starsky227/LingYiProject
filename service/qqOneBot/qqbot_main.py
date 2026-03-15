"""程序入口"""

import asyncio
import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

from brain.lingyi_core.lingyi_core import LingYiCore
from service.qqOneBot import onebot
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
    """初始化文件轮转日志处理器"""
    log_file_path = "logs/onebot_logs/bot.log"
    log_max_size = 10 *1024 * 1024 # 10 MB
    log_backup_count = 5

    log_dir = Path(log_file_path).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    file_log_format = (
        "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
    )
    handler = RotatingFileHandler(
        log_file_path,
        maxBytes=log_max_size,
        backupCount=log_backup_count,
        encoding="utf-8",
    )
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
        qq_prompt_path = os.path.join(os.path.dirname(__file__), "Prompt", "qq_prompt.txt")
        with open(qq_prompt_path, "r", encoding="utf-8") as f:
            qq_prompt = f.read().strip()
        memory_record_prompt = load_prompt_file("qqMemoryRecord.txt", "记忆存储")
        event_extract_prompt = load_prompt_file("qqEventExtract.txt", "事件提取")

        # 初始化工具注册表，自动发现 qq_tools 下所有工具
        tools_dir = Path(__file__).parent / "qq_tools"
        tool_registry = BaseRegistry(base_dir=tools_dir, kind="tool")
        tool_registry.load_items()
        logger.info(f"[初始化] 已注册 {len(tool_registry.get_schema())} 个QQ工具")

        ai = LingYiCore(qq_prompt, available_tools=tool_registry.get_schema())
        # 创建handler
        handler = MessageHandler(onebot, ai, tool_registry=tool_registry)

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
    asyncio.run(main())


if __name__ == "__main__":
    run()
