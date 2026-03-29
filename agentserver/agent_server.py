#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentServer 服务 — Agent 发现与管理 API

提供 agent 发现与健康检查 HTTP 端点。
Agent 本身由 LingYi_core 通过 tool_executor 直接调用，
此服务器负责管理和监控。
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

from fastapi import FastAPI
from contextlib import asynccontextmanager

from agentserver.agent_registry import discover_agents

# 配置日志
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 应用生命周期"""
    try:
        # 发现可用 agent
        Modules.available_agents = discover_agents()
        agent_names = [a["name"] for a in Modules.available_agents]
        logger.info(f"AgentServer 初始化完成，发现 {len(agent_names)} 个 agent: {agent_names}")
    except Exception as e:
        logger.error(f"服务初始化失败: {e}")
        raise

    yield

    logger.info("AgentServer 服务已关闭")


app = FastAPI(title="AgentServer", version="2.0.0", lifespan=lifespan)


class Modules:
    """全局模块管理器"""
    available_agents: List[Dict[str, Any]] = []

def _now_iso() -> str:
    """获取当前时间ISO格式"""
    return datetime.now().isoformat()


# ============ API端点 ============

@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": _now_iso(),
        "agents": [a["name"] for a in Modules.available_agents],
        "agent_count": len(Modules.available_agents),
    }


@app.get("/agents")
async def list_agents():
    """列出所有已发现的 agent"""
    agents_info = []
    for a in Modules.available_agents:
        schema = a.get("schema", {})
        agents_info.append({
            "name": a["name"],
            "description": schema.get("description", ""),
            "parameters": schema.get("parameters", {}),
        })
    return {
        "success": True,
        "agents": agents_info,
        "count": len(agents_info),
    }


if __name__ == "__main__":
    import uvicorn
    from agentserver.config import AGENT_SERVER_PORT
    uvicorn.run(app, host="0.0.0.0", port=AGENT_SERVER_PORT)