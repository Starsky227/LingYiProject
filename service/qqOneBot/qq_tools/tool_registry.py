import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

# 确保 service/qqOneBot 在 sys.path 中，以便导入 utils 等模块
_qqonebot_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _qqonebot_root not in sys.path:
    sys.path.insert(0, _qqonebot_root)

from utils.logging import log_debug_json, format_log_payload

logger = logging.getLogger(__name__)


def parse_tool_arguments(
    args: Any, logger: logging.Logger | None = None, tool_name: str = ""
) -> Dict[str, Any]:
    """将工具调用的参数统一解析为字典。

    支持: dict（直接返回）、JSON 字符串、None（返回空字典）。
    """
    if isinstance(args, dict):
        return args
    if args is None:
        return {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError) as e:
            if logger:
                logger.warning(f"解析工具参数失败 {tool_name}: {e}")
    return {}


@dataclass
class SkillItem:
    """技能项元数据

    封装了技能的配置信息、处理函数以及加载状态。
    """

    name: str
    config: Dict[str, Any]
    handler_path: Optional[Path]
    module_name: Optional[str]
    handler: Optional[Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Any]]] = None
    loaded: bool = False


class BaseRegistry:
    """
    基础注册表类，用于发现和加载技能（Tools/Agents）。

    提供统一的加载、验证、延迟加载执行、统计和热重载逻辑。
    """

    def __init__(
        self,
        base_dir: Path | str | None = None,
        kind: str = "skill",
        timeout_seconds: float = 480.0,
    ) -> None:
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path(".")

        self.kind = kind
        self.timeout_seconds = timeout_seconds
        self._items: Dict[str, SkillItem] = {}
        self._items_schema: List[Dict[str, Any]] = []

        self._items_lock = asyncio.Lock()

        self.skills_root = self._resolve_skills_root(self.base_dir)

    def _resolve_skills_root(self, base_dir: Path) -> Path:
        """解析技能定义的根目录，用于计算模块导入路径"""
        if base_dir.name in {"tools", "agents", "toolsets"} and base_dir.parent.name:
            return base_dir.parent
        return base_dir

    def _log_event(self, event: str, name: str = "", **fields: Any) -> None:
        parts = [f"event={event}", f"kind={self.kind}"]
        if name:
            parts.append(f"name={name}")
        for key, value in fields.items():
            parts.append(f"{key}={value}")
        logger.info("[skills] " + " ".join(parts))

    def _reset_items(self) -> None:
        self._items = {}
        self._items_schema = []

    def load_items(self) -> None:
        """从 base_dir 自动发现并加载技能定义（仅加载 config 配置文件，不导入 handler 代码）"""
        self._reset_items()

        if not self.base_dir.exists():
            logger.warning(f"目录不存在: {self.base_dir}")
            return

        self._discover_items_in_dir(self.base_dir, prefix="")

        item_names = list(self._items.keys())
        logger.info(
            f"[{self.__class__.__name__}] 成功加载了 {len(self._items_schema)} 个项目: {', '.join(item_names)}"
        )

    def _discover_items_in_dir(self, parent_dir: Path, prefix: str) -> None:
        for item in parent_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                self._register_item_from_dir(item, prefix=prefix)

    def _register_item_from_dir(self, item_dir: Path, prefix: str = "") -> None:
        """从特定目录解析并注册一个技能项

        该方法会查找目录下的 config.json 和 handler.py，并构造 SkillItem。

        参数:
            item_dir: 技能所在的目录路径
            prefix: 技能名称的前缀（用于命名隔离）
        """
        config_path = item_dir / "config.json"
        handler_path = item_dir / "handler.py"

        if not config_path.exists() or not handler_path.exists():
            logger.debug(f"目录 {item_dir} 缺少 config.json 或 handler.py，跳过")
            return

        try:
            config = self._load_config(config_path)
            if not config:
                return

            item = self._build_skill_item(item_dir, config, handler_path, prefix)
            self._items[item.name] = item
            self._items_schema.append(item.config)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "[%s加载] name=%s module=%s path=%s",
                    self.kind,
                    item.name,
                    item.module_name,
                    handler_path,
                )
                log_debug_json(logger, f"[{self.kind}配置] {item.name}", config)

        except Exception as e:
            logger.error(f"从 {item_dir} 加载失败: {e}")

    def _load_config(self, config_path: Path) -> Optional[Dict[str, Any]]:
        """加载并验证配置文件（支持扁平格式和嵌套格式）"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                logger.error(f"配置无效 {config_path.parent}: 不是有效的 JSON 对象")
                return None
            # 支持嵌套格式 {"type":"function","function":{"name":...}} → 展开为扁平格式
            if "function" in config and isinstance(config["function"], dict):
                func = config["function"]
                config = {
                    "type": config.get("type", "function"),
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {}),
                }
            if "name" not in config:
                logger.error(f"配置无效 {config_path.parent}: 缺少 name")
                return None
            return dict(config)
        except Exception as e:
            logger.error(f"加载配置文件失败 {config_path}: {e}")
            return None

    def _build_skill_item(
        self, item_dir: Path, config: Dict[str, Any], handler_path: Path, prefix: str
    ) -> SkillItem:
        """构建 SkillItem 对象"""
        original_name = config["name"]
        full_name = f"{prefix}{original_name}"

        if prefix:
            import copy

            config = copy.deepcopy(config)
            config["name"] = full_name

        module_name = self._build_module_name(item_dir)
        return SkillItem(
            name=full_name,
            config=config,
            handler_path=handler_path,
            module_name=module_name,
        )

    def _build_module_name(self, item_dir: Path) -> str:
        try:
            relative = item_dir.relative_to(self.skills_root)
            parts = [self.skills_root.name] + list(relative.parts)
            return ".".join(parts)
        except ValueError:
            parts = list(item_dir.parts[-3:])
            return ".".join(parts)

    def _load_handler_for_item(
        self, item: SkillItem, reload_module: bool = False
    ) -> None:
        """动态导入技能的 handler.py 模块并获取其 execute 函数"""
        if item.handler is not None and not reload_module:
            return

        if not item.handler_path or not item.module_name:
            return

        if reload_module and item.module_name in sys.modules:
            del sys.modules[item.module_name]

        spec = importlib.util.spec_from_file_location(
            item.module_name, item.handler_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"加载处理器 spec 失败: {item.handler_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "execute"):
            raise RuntimeError(f"{item.handler_path} 的处理器缺少 'execute' 函数")

        item.handler = module.execute
        item.loaded = True

    def get_schema(self) -> List[Dict[str, Any]]:
        return self._items_schema

    async def execute(
        self, name: str, args: Dict[str, Any], context: Dict[str, Any]
    ) -> str:
        """执行指定的技能，包含超时控制、异常处理及统计记录

        参数:
            name: 技能名称
            args: 此调用传入的参数字典
            context: 调用执行的上下文环境

        返回:
            执行结果的字符串表示
        """
        args = parse_tool_arguments(args, logger=logger, tool_name=name)
        async with self._items_lock:
            item = self._items.get(name)

        if not item:
            if logger.isEnabledFor(logging.INFO) and self.kind in {
                "tool",
                "agent_tool",
                "agent",
            }:
                logger.info(
                    "[%s调用] %s 参数=%s",
                    self.kind,
                    name,
                    format_log_payload(args),
                )
                logger.info(
                    "[%s返回] %s 结果=%s",
                    self.kind,
                    name,
                    format_log_payload(f"未找到项目: {name}"),
                )
            return f"未找到项目: {name}"

        if logger.isEnabledFor(logging.INFO) and self.kind in {
            "tool",
            "agent_tool",
            "agent",
        }:
            logger.info(
                "[%s调用] %s 参数=%s",
                self.kind,
                name,
                format_log_payload(args),
            )

        start_time = time.monotonic()
        result_payload: Any
        try:
            if logger.isEnabledFor(logging.DEBUG):
                log_debug_json(logger, f"[{self.kind}参数] {name}", args)
                logger.debug(
                    "[%s上下文] %s",
                    self.kind,
                    ", ".join(sorted(context.keys())),
                )
            if item.handler is None and item.handler_path:
                self._load_handler_for_item(item)
            handler = item.handler
            if not handler:
                result_payload = f"未找到项目: {name}"
                return_value = str(result_payload)
            else:
                result = await self._execute_with_timeout(handler, args, context)
                duration = time.monotonic() - start_time

                self._log_event(
                    "execute", name, status="success", duration_ms=int(duration * 1000)
                )
                if logger.isEnabledFor(logging.DEBUG):
                    log_debug_json(logger, f"[{self.kind}结果] {name}", result)
                result_payload = result
                return_value = str(result)

        except asyncio.TimeoutError:
            duration = time.monotonic() - start_time
            self._log_event(
                "execute", name, status="timeout", duration_ms=int(duration * 1000)
            )
            result_payload = f"执行 {name} 超时 (>{int(self.timeout_seconds)}s)"
            return_value = str(result_payload)

        except asyncio.CancelledError:
            duration = time.monotonic() - start_time
            self._log_event(
                "execute", name, status="cancelled", duration_ms=int(duration * 1000)
            )
            result_payload = f"执行 {name} 已取消"
            return_value = str(result_payload)

        except Exception as e:
            duration = time.monotonic() - start_time
            logger.exception(f"[执行异常] {name}")
            self._log_event(
                "execute", name, status="error", duration_ms=int(duration * 1000)
            )
            result_payload = f"执行 {name} 时出错: {str(e)}"
            return_value = str(result_payload)

        if logger.isEnabledFor(logging.INFO) and self.kind in {
            "tool",
            "agent_tool",
            "agent",
        }:
            logger.info(
                "[%s返回] %s 结果=%s",
                self.kind,
                name,
                format_log_payload(result_payload),
            )
        return return_value

    async def _execute_with_timeout(
        self,
        handler: Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Any]],
        args: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Any:
        if asyncio.iscoroutinefunction(handler):
            return await asyncio.wait_for(
                handler(args, context), timeout=self.timeout_seconds
            )
        return await asyncio.wait_for(
            asyncio.to_thread(handler, args, context), timeout=self.timeout_seconds
        )


