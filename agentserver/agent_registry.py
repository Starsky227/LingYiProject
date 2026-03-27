# -*- coding: utf-8 -*-
"""
Agent 注册与发现模块

提供:
- AgentToolRegistry: 扫描 agent 私有 tools/ 目录，供 runner.py 使用
- parse_tool_arguments: 工具参数解析
- discover_agents: 发现 agentserver 下所有可用 agent
- register_agents_to_registry: 将 agent 注册为工具到已有 registry
- AgentAIClient: 为 runner.py 提供 ai_client 接口的适配器
"""

import asyncio
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# config.json 格式辅助
# ---------------------------------------------------------------------------

def _extract_tool_name(config: Dict[str, Any]) -> str:
    """从 OpenAI Tools API 嵌套格式 config.json 中提取工具/agent 名称。

    格式: {"type": "function", "function": {"name": "xxx", ...}}
    """
    fn = config.get("function")
    if isinstance(fn, dict):
        return fn.get("name", "")
    return ""


def _normalize_tool_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
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


# ---------------------------------------------------------------------------
# 工具参数解析
# ---------------------------------------------------------------------------

def parse_tool_arguments(
    args: Any, logger: logging.Logger = None, tool_name: str = ""
) -> Dict[str, Any]:
    """将工具调用的参数统一解析为字典。"""
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


# ---------------------------------------------------------------------------
# Agent 模型配置 & AI 客户端适配器
# ---------------------------------------------------------------------------

@dataclass
class AgentModelConfig:
    """Agent 模型配置"""
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7

    def __post_init__(self):
        if not self.model:
            from system.config import config
            agent_cfg = config.agent_api
            self.model = agent_cfg.agent_model
            self.max_tokens = agent_cfg.agent_max_tokens
            self.temperature = agent_cfg.agent_temperature


class AgentAIClient:
    """为 runner.py 提供 ai_client 接口的 OpenAI 适配器。

    runner.py 期望 ai_client 提供:
      - agent_config (AgentModelConfig)
      - await request_model(...) -> Responses API response 对象
    """

    def __init__(self):
        self.agent_config = AgentModelConfig()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            from system.config import config
            agent_cfg = config.agent_api
            self._client = AsyncOpenAI(
                api_key=agent_cfg.agent_api_key,
                base_url=agent_cfg.agent_base_url,
            )
        return self._client

    async def request_model(
        self,
        model_config=None,
        input_items=None,
        max_output_tokens=None,
        call_type="",
        tools=None,
    ):
        client = self._get_client()
        cfg = model_config or self.agent_config
        kwargs = {
            "model": cfg.model,
            "input": input_items or [],
            "max_output_tokens": max_output_tokens or cfg.max_tokens,
            "temperature": cfg.temperature,
        }
        if tools:
            kwargs["tools"] = tools

        return await client.responses.create(**kwargs)


# ---------------------------------------------------------------------------
# AgentToolRegistry — 扫描 agent 私有 tools/ 目录
# ---------------------------------------------------------------------------

class AgentToolRegistry:
    """扫描 agent 的 tools/ 子目录，为 runner.py 提供工具注册。"""

    def __init__(
        self,
        tools_dir: Path,
        current_agent_name: str = "",
        is_main_agent: bool = False,
    ):
        self.tools_dir = tools_dir
        self.agent_name = current_agent_name
        self._items: Dict[str, Dict[str, Any]] = {}
        self._schemas: List[Dict[str, Any]] = []
        self._load_tools()

    def _load_tools(self):
        if not self.tools_dir.exists():
            return
        for item_dir in sorted(self.tools_dir.iterdir()):
            if not item_dir.is_dir() or item_dir.name.startswith("_"):
                continue
            config_path = item_dir / "config.json"
            handler_path = item_dir / "handler.py"
            if not config_path.exists() or not handler_path.exists():
                continue
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    tool_config = json.load(f)
                name = _extract_tool_name(tool_config)
                if not name:
                    continue
                self._items[name] = {
                    "config": tool_config,
                    "handler_path": handler_path,
                    "handler": None,
                }
                self._schemas.append(_normalize_tool_schema(tool_config))
                logger.debug(f"  载入工具定义: {name}")
            except Exception as e:
                logger.error(f"加载工具失败 {item_dir}: {e}")

    # runner.py 调用入口 ------------------------------------------------

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        return list(self._schemas)

    async def execute_tool(
        self, name: str, args: Dict[str, Any], context: Dict[str, Any]
    ) -> str:
        item = self._items.get(name)
        if not item:
            return f"未找到工具: {name}"
        try:
            self._ensure_handler_loaded(name)
            handler = item["handler"]
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(handler(args, context), timeout=480)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(handler, args, context), timeout=480
                )
            return str(result)
        except asyncio.TimeoutError:
            return f"工具 {name} 执行超时"
        except Exception as e:
            logger.exception(f"执行工具 {name} 失败")
            return f"工具执行失败: {e}"

    def _ensure_handler_loaded(self, name: str):
        item = self._items.get(name)
        if not item or item["handler"] is not None:
            return
        handler_path = item["handler_path"]
        module_name = f"agentserver._agent_tool_.{self.agent_name}.{name}"
        if module_name in sys.modules:
            del sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, handler_path)
        if not spec or not spec.loader:
            raise RuntimeError(f"加载 handler 失败: {handler_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if not hasattr(module, "execute"):
            raise RuntimeError(f"{handler_path} 缺少 execute 函数")
        item["handler"] = module.execute


# ---------------------------------------------------------------------------
# Agent 发现 & 注册
# ---------------------------------------------------------------------------

# 需要排除的目录名（不是 agent 目录）
_EXCLUDED_DIRS = {
    "tools", "configs", "utils", "__pycache__",
    "agent_computer_control",
}


def discover_agents(base_dir: Path = None) -> List[Dict[str, Any]]:
    """发现 agentserver/ 下所有 agent 目录。

    一个有效的 agent 目录须包含 config.json 和 handler.py。

    Returns:
        List[dict]: 每个元素包含 name, schema, handler_path, dir
    """
    if base_dir is None:
        base_dir = Path(__file__).parent

    agents = []
    for item_dir in sorted(base_dir.iterdir()):
        if not item_dir.is_dir():
            continue
        if item_dir.name.startswith("_") or item_dir.name in _EXCLUDED_DIRS:
            continue
        config_path = item_dir / "config.json"
        handler_path = item_dir / "handler.py"
        if not config_path.exists() or not handler_path.exists():
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                agent_config = json.load(f)
            name = _extract_tool_name(agent_config)
            if not name:
                logger.warning(f"agent 配置缺少 name: {config_path}")
                continue
            agents.append({
                "name": name,
                "schema": agent_config,
                "handler_path": handler_path,
                "dir": item_dir,
            })
            logger.info(f"[AgentRegistry] 发现 agent: {name}")
        except Exception as e:
            logger.error(f"加载 agent 配置失败 {item_dir}: {e}")

    return agents


def _make_lazy_agent_handler(agent_info: Dict[str, Any]) -> Callable:
    """为 agent 创建延迟加载的 handler 包装器。

    首次调用时动态导入 handler.py，之后复用。
    自动注入 ai_client 到 context（如果缺失）。
    """
    handler_path = agent_info["handler_path"]
    name = agent_info["name"]
    _handler_cache: list = []  # use list to allow nonlocal mutation in closure

    async def wrapper(args: Dict[str, Any], context: Dict[str, Any]) -> str:
        if not _handler_cache:
            module_name = f"agentserver.{name}.handler"
            if module_name in sys.modules:
                del sys.modules[module_name]
            spec = importlib.util.spec_from_file_location(module_name, handler_path)
            if not spec or not spec.loader:
                return f"加载 agent {name} 失败"
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            if not hasattr(module, "execute"):
                return f"Agent {name} 缺少 execute 函数"
            _handler_cache.append(module.execute)

        # 注入 ai_client（runner.py 需要）
        if "ai_client" not in context:
            context["ai_client"] = AgentAIClient()

        result = await _handler_cache[0](args, context)
        return str(result)

    return wrapper
