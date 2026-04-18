#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
brain/memory 本地 Agent 运行器

将 agentserver/runner.py 和 agentserver/agent_registry.py 中
memory_agent 所需的最小子集内联至此，使 brain/memory 可独立运行，
不依赖 agentserver。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 记忆工具调用日志 — 写入 data/memory_graph/memory_tool_YYYYMMDD.log
# ---------------------------------------------------------------------------

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "memory_graph"
_MAX_LOG_FILES = 3


def _rotate_log_files() -> None:
    """保留最近 _MAX_LOG_FILES 个日志文件，删除更早的。"""
    logs = sorted(_LOG_DIR.glob("memory_tool_*.log"))
    while len(logs) > _MAX_LOG_FILES:
        oldest = logs.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass


def _write_memory_log(entry: dict) -> None:
    """追加一条 JSON 日志到当天的日志文件。"""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    log_path = _LOG_DIR / f"memory_tool_{today}.log"
    entry["timestamp"] = datetime.now().isoformat()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _rotate_log_files()
    except OSError as e:
        logger.warning("写入记忆日志失败: %s", e)


# ---------------------------------------------------------------------------
# config.json 格式辅助
# ---------------------------------------------------------------------------

def _extract_tool_name(cfg: Dict[str, Any]) -> str:
    fn = cfg.get("function")
    if isinstance(fn, dict):
        return fn.get("name", "")
    return ""


def _normalize_tool_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
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
    args: Any,
    logger: logging.Logger = None,
    tool_name: str = "",
) -> Dict[str, Any]:
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
# 模型配置 & AI 客户端
# ---------------------------------------------------------------------------

@dataclass
class _ModelConfig:
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7

    def __post_init__(self):
        if not self.model:
            from system.config import config
            cfg = config.agent_api
            self.model = cfg.agent_model
            self.max_tokens = cfg.agent_max_tokens
            self.temperature = cfg.agent_temperature


class _AIClient:
    """轻量 OpenAI 客户端，仅供 memory_agent 使用。"""

    def __init__(self):
        self.agent_config = _ModelConfig()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            from system.config import config
            cfg = config.agent_api
            self._client = AsyncOpenAI(
                api_key=cfg.agent_api_key,
                base_url=cfg.agent_base_url,
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
# 工具注册表
# ---------------------------------------------------------------------------

class _ToolRegistry:
    """扫描 tools/ 子目录，动态加载并执行工具。"""

    def __init__(self, tools_dir: Path, agent_name: str = "memory_agent"):
        self.tools_dir = tools_dir
        self.agent_name = agent_name
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
                logger.debug("载入工具定义: %s", name)
            except Exception as e:
                logger.error("加载工具失败 %s: %s", item_dir, e)

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
            logger.exception("执行工具 %s 失败", name)
            return f"工具执行失败: {e}"

    def _ensure_handler_loaded(self, name: str):
        item = self._items.get(name)
        if not item or item["handler"] is not None:
            return
        handler_path = item["handler_path"]
        module_name = f"brain.memory._tool_.{self.agent_name}.{name}"
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
# Agent 运行循环
# ---------------------------------------------------------------------------

async def run_memory_agent(
    *,
    agent_name: str,
    user_content: str,
    system_prompt: str,
    tools_dir: Path,
    max_iterations: int = 20,
    tool_error_prefix: str = "错误",
) -> str:
    """执行 memory_agent 工具调用循环。"""
    if not user_content.strip():
        _write_memory_log({"event": "skip", "reason": "空内容"})
        return "无内容"

    _write_memory_log({
        "event": "start",
        "user_content_preview": user_content[:300],
    })

    tool_registry = _ToolRegistry(tools_dir, agent_name=agent_name)
    tools = tool_registry.get_tools_schema()

    ai_client = _AIClient()
    agent_config = ai_client.agent_config

    context: Dict[str, Any] = {"ai_client": ai_client}
    input_items: list[Any] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    for iteration in range(1, max_iterations + 1):
        logger.debug("[%s] iteration=%s", agent_name, iteration)
        try:
            response = await ai_client.request_model(
                model_config=agent_config,
                input_items=input_items,
                max_output_tokens=agent_config.max_tokens,
                call_type=f"agent:{agent_name}",
                tools=tools if tools else None,
            )

            content_text = ""
            function_calls = []
            for item in response.output:
                if item.type == "message":
                    for c in getattr(item, "content", []):
                        if getattr(c, "type", "") == "output_text":
                            content_text += c.text
                elif item.type == "function_call":
                    function_calls.append(item)

            if not function_calls:
                _write_memory_log({
                    "event": "ai_decision",
                    "iteration": iteration,
                    "action": "no_tool_call",
                    "ai_text": content_text[:500],
                })
                return content_text

            input_items.extend(response.output)

            tool_tasks: list[asyncio.Future[Any]] = []
            tool_call_ids: list[str] = []

            for fc in function_calls:
                fn_name = fc.name
                logger.info("[%s] tool=%s", agent_name, fn_name)
                function_args = parse_tool_arguments(
                    fc.arguments, logger=logger, tool_name=fn_name
                )
                if not isinstance(function_args, dict):
                    function_args = {}
                _write_memory_log({
                    "event": "tool_call",
                    "iteration": iteration,
                    "tool": fn_name,
                    "args": function_args,
                })
                tool_call_ids.append(fc.call_id)
                tool_tasks.append(
                    asyncio.ensure_future(
                        tool_registry.execute_tool(fn_name, function_args, context)
                    )
                )

            logger.info("[%s] executing %s tools in parallel", agent_name, len(tool_tasks))
            results = await asyncio.gather(*tool_tasks, return_exceptions=True)

            for index, tool_result in enumerate(results):
                output_str = (
                    f"{tool_error_prefix}: {tool_result}"
                    if isinstance(tool_result, Exception)
                    else str(tool_result)
                )
                _write_memory_log({
                    "event": "tool_result",
                    "iteration": iteration,
                    "call_id": tool_call_ids[index],
                    "output": output_str[:500],
                })
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tool_call_ids[index],
                    "output": output_str,
                })

        except Exception as exc:
            logger.exception("[%s] 执行失败: %s", agent_name, exc)
            _write_memory_log({
                "event": "error",
                "iteration": iteration,
                "error": str(exc),
            })
            return f"处理失败: {exc}"

    _write_memory_log({"event": "max_iterations_reached", "max": max_iterations})
    return "达到最大迭代次数"