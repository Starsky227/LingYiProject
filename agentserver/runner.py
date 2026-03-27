from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiofiles

from agentserver.agent_registry import (
    AgentToolRegistry,
    parse_tool_arguments,
)


async def load_prompt_text(agent_dir: Path, default_prompt: str) -> str:
    """从 agent 目录加载 prompt.md，缺失时返回默认提示词。"""

    prompt_path = agent_dir / "prompt.md"
    if prompt_path.exists():
        async with aiofiles.open(prompt_path, "r", encoding="utf-8") as file:
            return await file.read()
    return default_prompt


async def run_agent_with_tools(
    *,
    agent_name: str,
    user_content: str,
    empty_user_content_message: str,
    default_prompt: str,
    context: dict[str, Any],
    agent_dir: Path,
    logger: logging.Logger,
    max_iterations: int = 20,
    tool_error_prefix: str = "错误",
) -> str:
    """执行通用 Agent 循环。

    该方法统一处理：
    - prompt 加载
    - LLM 迭代决策
    - tool call 并发执行
    - tool 结果回填 messages
    """

    if not user_content.strip():
        return empty_user_content_message

    tool_registry = AgentToolRegistry(
        agent_dir / "tools",
        current_agent_name=agent_name,
        is_main_agent=False,
    )
    tools = tool_registry.get_tools_schema()

    ai_client = context.get("ai_client")
    if not ai_client:
        return "AI client 未在上下文中提供"

    agent_config = ai_client.agent_config
    system_prompt = await load_prompt_text(agent_dir, default_prompt)

    agent_history = context.get("agent_history", [])

    input_items: list[Any] = [{"role": "system", "content": system_prompt}]
    if agent_history:
        input_items.extend(agent_history)
    input_items.append({"role": "user", "content": user_content})

    for iteration in range(1, max_iterations + 1):
        logger.debug("[Agent:%s] iteration=%s", agent_name, iteration)
        try:
            response = await ai_client.request_model(
                model_config=agent_config,
                input_items=input_items,
                max_output_tokens=agent_config.max_tokens,
                call_type=f"agent:{agent_name}",
                tools=tools if tools else None,
            )

            # 解析 Responses API 输出
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
                return content_text

            # 将完整模型输出（含 reasoning + function_call）加入 input
            input_items.extend(response.output)

            # 并发执行工具
            tool_tasks: list[asyncio.Future[Any]] = []
            tool_call_ids: list[str] = []

            for fc in function_calls:
                fn_name = fc.name
                logger.info("[Agent:%s] preparing tool=%s", agent_name, fn_name)

                function_args = parse_tool_arguments(
                    fc.arguments, logger=logger, tool_name=fn_name,
                )
                if not isinstance(function_args, dict):
                    function_args = {}

                tool_call_ids.append(fc.call_id)
                tool_tasks.append(
                    asyncio.ensure_future(
                        tool_registry.execute_tool(fn_name, function_args, context)
                    )
                )

            logger.info(
                "[Agent:%s] executing tools in parallel: count=%s",
                agent_name, len(tool_tasks),
            )
            results = await asyncio.gather(*tool_tasks, return_exceptions=True)

            for index, tool_result in enumerate(results):
                if isinstance(tool_result, Exception):
                    output_str = f"{tool_error_prefix}: {tool_result}"
                else:
                    output_str = str(tool_result)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tool_call_ids[index],
                    "output": output_str,
                })

        except Exception as exc:
            logger.exception("[Agent:%s] execution failed: %s", agent_name, exc)
            return f"处理失败: {exc}"

    return "达到最大迭代次数"
