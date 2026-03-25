"""
工具管理器 — 多来源 sub-registry 聚合

ToolManager 作为中心聚合器:
- 接收多个 sub-registry（各来源的工具注册表）
- 为每个 sub-registry 的工具名添加前缀（如 main-, qq-）
- 统一输出 Responses API 格式的 schema 列表
- 按前缀路由工具执行请求到对应的 sub-registry

Sub-registry 协议:
- get_schema() -> list[dict]: 返回归一化后的工具 schema 列表（Responses API 扁平格式）
- execute(name, args, context) -> str: 按原始名称执行指定工具
"""

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def normalize_tool_schema(schema: dict) -> dict:
    """统一为 Responses API 格式

    支持输入:
    - Responses API 扁平格式: {"type":"function", "name":..., ...}（直通）
    - Chat Completions 嵌套格式: {"type":"function", "function":{"name":...}}（展开）
    """
    if "function" in schema and isinstance(schema["function"], dict):
        func = schema["function"]
        return {
            "type": "function",
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {}),
        }
    return dict(schema)


@dataclass
class ToolEntry:
    """单个工具条目"""
    name: str
    schema: dict
    handler: Callable | None = None
    handler_path: Path | None = None
    source: str = "unknown"
    _loaded: bool = False


class ToolManager:
    """工具管理器 — 多来源 sub-registry 聚合

    接收多个 sub-registry，为各来源工具名添加前缀，
    统一输出 schema 列表并路由工具执行请求。
    """

    def __init__(self):
        self._sub_registries: list[tuple[str, Any]] = []  # (prefix, registry)
        self._tool_index: dict[str, tuple[str, Any]] = {}  # full_name -> (original_name, registry)

    def register_sub_registry(self, prefix: str, registry):
        """注册一个 sub-registry

        Args:
            prefix: 工具名前缀（如 "main-", "qq-"）
            registry: 需实现 get_schema() -> list[dict] 和 execute(name, args, context) -> str
        """
        self._sub_registries.append((prefix, registry))
        self._rebuild_index()
        names = [f"{prefix}{s.get('name', '')}" for s in registry.get_schema()]
        logger.info(f"[ToolManager] 注册 sub-registry (prefix={prefix!r}): {names}")

    def _rebuild_index(self):
        """重建 full_name → (original_name, registry) 索引"""
        self._tool_index.clear()
        for prefix, registry in self._sub_registries:
            for schema in registry.get_schema():
                original = schema.get("name", "")
                full_name = f"{prefix}{original}" if prefix else original
                self._tool_index[full_name] = (original, registry)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_tools_schema(self) -> list[dict]:
        """获取所有工具的 schema 列表（名称已加前缀，Responses API 格式）"""
        schemas = []
        for prefix, registry in self._sub_registries:
            for schema in registry.get_schema():
                prefixed = dict(schema)
                if prefix:
                    prefixed["name"] = f"{prefix}{prefixed.get('name', '')}"
                schemas.append(prefixed)
        return schemas

    def get_tool_names(self) -> list[str]:
        return list(self._tool_index.keys())

    def has_tool(self, name: str) -> bool:
        return name in self._tool_index

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def execute_tool(self, name: str, args: dict, context: dict = None) -> str:
        """执行指定工具（按前缀路由到对应 sub-registry）"""
        entry = self._tool_index.get(name)
        if not entry:
            raise KeyError(f"未找到工具: {name}")
        original_name, registry = entry
        return await registry.execute(original_name, args, context or {})


# ---------------------------------------------------------------------------
# Sub-registry 实现
# ---------------------------------------------------------------------------


class LocalToolRegistry:
    """本地工具注册表 — 扫描目录中含 config.json + handler.py 的工具子目录

    用于 brain/tools/ 等本地工具目录。
    """

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._items: dict[str, ToolEntry] = {}
        self._schemas: list[dict] = []

    def load_items(self):
        """扫描目录并加载工具定义（仅配置，handler 延迟加载）"""
        self._items.clear()
        self._schemas.clear()
        if not self._base_dir.is_dir():
            logger.debug(f"[LocalToolRegistry] 目录不存在: {self._base_dir}")
            return
        for item_dir in sorted(self._base_dir.iterdir()):
            if not item_dir.is_dir() or item_dir.name.startswith("_"):
                continue
            config_path = item_dir / "config.json"
            handler_path = item_dir / "handler.py"
            if not config_path.exists() or not handler_path.exists():
                continue
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                schema = normalize_tool_schema(raw)
                name = schema.get("name", "")
                if not name:
                    logger.warning(f"[LocalToolRegistry] 工具配置缺少 name: {config_path}")
                    continue
                entry = ToolEntry(name=name, schema=schema, handler_path=handler_path, source="brain")
                self._items[name] = entry
                self._schemas.append(schema)
                logger.info(f"[LocalToolRegistry] 发现工具: {name}")
            except Exception as e:
                logger.error(f"[LocalToolRegistry] 加载工具配置失败 {item_dir}: {e}")

    def get_schema(self) -> list[dict]:
        """返回所有工具的归一化 schema 列表"""
        return list(self._schemas)

    async def execute(self, name: str, args: dict, context: dict = None) -> str:
        """执行指定工具（延迟加载 handler）"""
        entry = self._items.get(name)
        if not entry:
            return f"未找到工具: {name}"
        if not entry._loaded and entry.handler_path:
            self._load_handler(entry)
        if not entry.handler:
            return f"工具 {name} 无可用 handler"
        result = await entry.handler(args, context or {})
        return str(result)

    def _load_handler(self, entry: ToolEntry):
        """懒加载 handler.py"""
        module_name = f"brain.tools.{entry.name}.handler"
        try:
            if module_name in sys.modules:
                del sys.modules[module_name]
            spec = importlib.util.spec_from_file_location(module_name, entry.handler_path)
            if not spec or not spec.loader:
                logger.error(f"[LocalToolRegistry] 加载 handler spec 失败: {entry.handler_path}")
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            if not hasattr(module, "execute"):
                logger.error(f"[LocalToolRegistry] handler 缺少 execute 函数: {entry.handler_path}")
                return
            entry.handler = module.execute
            entry._loaded = True
        except Exception as e:
            logger.error(f"[LocalToolRegistry] 加载 handler 失败 {entry.handler_path}: {e}")


class AgentSubRegistry:
    """Agent 注册表 — 发现 agentserver/ 下的 agents 并提供执行接口"""

    def __init__(self):
        self._schemas: list[dict] = []
        self._handlers: dict[str, Callable] = {}

    def discover(self, base_dir: "Path | None" = None):
        """发现并注册所有 agent"""
        try:
            from agentserver.agent_registry import discover_agents, _make_lazy_agent_handler

            agents = discover_agents(base_dir)
            for agent_info in agents:
                name = agent_info["name"]
                schema = normalize_tool_schema(agent_info["schema"])
                handler = _make_lazy_agent_handler(agent_info)
                self._schemas.append(schema)
                self._handlers[name] = handler
                logger.info(f"[AgentSubRegistry] 发现 agent: {name}")
        except Exception as e:
            logger.warning(f"[AgentSubRegistry] agent 发现失败（不影响基本功能）: {e}")

    def get_schema(self) -> list[dict]:
        return list(self._schemas)

    async def execute(self, name: str, args: dict, context: dict = None) -> str:
        handler = self._handlers.get(name)
        if not handler:
            return f"未找到 agent: {name}"
        result = await handler(args, context or {})
        return str(result)
