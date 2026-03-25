#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentServer 服务 — Agent 管理、任务调度与工具包 API

提供 agent 发现、任务记忆管理、文件编辑工具包等 HTTP 端点。
Agent 本身由 LingYi_core 通过 tool_executor 直接调用，
此服务器负责管理和监控。
"""

import asyncio
import uuid
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from system.config import config
from agentserver.agent_registry import discover_agents
from agentserver.task_scheduler import get_task_scheduler, TaskStep
from agentserver.toolkit_manager import toolkit_manager

# 配置日志
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 应用生命周期"""
    try:
        # 初始化任务调度器
        Modules.task_scheduler = get_task_scheduler()

        # 设置 LLM 配置用于智能压缩
        if hasattr(config, "api") and config.api:
            llm_config = {
                "model": config.api.model,
                "api_key": config.api.api_key,
                "api_base": config.api.base_url,
            }
            Modules.task_scheduler.set_llm_config(llm_config)

        # 发现可用 agent
        Modules.available_agents = discover_agents()
        agent_names = [a["name"] for a in Modules.available_agents]
        logger.info(f"AgentServer 初始化完成，发现 {len(agent_names)} 个 agent: {agent_names}")
    except Exception as e:
        logger.error(f"服务初始化失败: {e}")
        raise

    yield

    try:
        logger.info("AgentServer 服务已关闭")
    except Exception as e:
        logger.error(f"服务关闭失败: {e}")


app = FastAPI(title="AgentServer", version="2.0.0", lifespan=lifespan)


class Modules:
    """全局模块管理器"""
    task_scheduler = None
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

# ============ 任务记忆管理API ============

@app.get("/tasks")
async def get_tasks(session_id: Optional[str] = None):
    """获取任务列表"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        running_tasks = await Modules.task_scheduler.get_running_tasks()
        return {
            "success": True,
            "running_tasks": running_tasks,
            "session_id": session_id
        }
    except Exception as e:
        logger.error(f"获取任务列表失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """获取指定任务状态"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        task_status = await Modules.task_scheduler.get_task_status(task_id)
        if not task_status:
            raise HTTPException(404, f"任务 {task_id} 不存在")
        
        return {
            "success": True,
            "task": task_status
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/tasks/{task_id}/memory")
async def get_task_memory(task_id: str, include_key_facts: bool = True):
    """获取任务记忆摘要"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        memory_summary = await Modules.task_scheduler.get_task_memory_summary(task_id, include_key_facts)
        return {
            "success": True,
            "task_id": task_id,
            "memory_summary": memory_summary
        }
    except Exception as e:
        logger.error(f"获取任务记忆失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/memory/global")
async def get_global_memory():
    """获取全局记忆摘要"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        global_summary = await Modules.task_scheduler.get_global_memory_summary()
        failed_attempts = await Modules.task_scheduler.get_failed_attempts_summary()
        
        return {
            "success": True,
            "global_summary": global_summary,
            "failed_attempts": failed_attempts
        }
    except Exception as e:
        logger.error(f"获取全局记忆失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.post("/tasks/{task_id}/steps")
async def add_task_step(task_id: str, payload: Dict[str, Any]):
    """添加任务步骤"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        step = TaskStep(
            step_id=payload.get("step_id", str(uuid.uuid4())),
            task_id=task_id,
            purpose=payload.get("purpose", "执行步骤"),
            content=payload.get("content", ""),
            output=payload.get("output", ""),
            analysis=payload.get("analysis"),
            success=payload.get("success", True),
            error=payload.get("error")
        )
        
        await Modules.task_scheduler.add_task_step(task_id, step)
        
        return {
            "success": True,
            "message": "步骤添加成功",
            "step_id": step.step_id
        }
    except Exception as e:
        logger.error(f"添加任务步骤失败: {e}")
        raise HTTPException(500, f"添加失败: {e}")

@app.delete("/tasks/{task_id}/memory")
async def clear_task_memory(task_id: str):
    """清除任务记忆"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        success = await Modules.task_scheduler.clear_task_memory(task_id)
        if not success:
            raise HTTPException(404, f"任务 {task_id} 不存在")
        
        return {
            "success": True,
            "message": f"任务 {task_id} 的记忆已清除"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"清除任务记忆失败: {e}")
        raise HTTPException(500, f"清除失败: {e}")

@app.delete("/memory/global")
async def clear_global_memory():
    """清除全局记忆"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        await Modules.task_scheduler.clear_all_memory()
        return {
            "success": True,
            "message": "全局记忆已清除"
        }
    except Exception as e:
        logger.error(f"清除全局记忆失败: {e}")
        raise HTTPException(500, f"清除失败: {e}")

# ============ 会话级别的记忆管理API ============

@app.get("/sessions")
async def get_all_sessions():
    """获取所有会话的摘要信息"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        sessions = await Modules.task_scheduler.get_all_sessions()
        return {
            "success": True,
            "sessions": sessions,
            "total_sessions": len(sessions)
        }
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/sessions/{session_id}/memory")
async def get_session_memory_summary(session_id: str):
    """获取会话记忆摘要"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        summary = await Modules.task_scheduler.get_session_memory_summary(session_id)
        if "error" in summary:
            raise HTTPException(404, summary["error"])
        
        return {
            "success": True,
            "session_id": session_id,
            "memory_summary": summary
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取会话记忆摘要失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/sessions/{session_id}/compressed_memories")
async def get_session_compressed_memories(session_id: str):
    """获取会话的压缩记忆"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        memories = await Modules.task_scheduler.get_session_compressed_memories(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "compressed_memories": memories,
            "count": len(memories)
        }
    except Exception as e:
        logger.error(f"获取会话压缩记忆失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/sessions/{session_id}/key_facts")
async def get_session_key_facts(session_id: str):
    """获取会话的关键事实"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        key_facts = await Modules.task_scheduler.get_session_key_facts(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "key_facts": key_facts,
            "count": len(key_facts)
        }
    except Exception as e:
        logger.error(f"获取会话关键事实失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/sessions/{session_id}/failed_attempts")
async def get_session_failed_attempts(session_id: str):
    """获取会话的失败尝试"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        failed_attempts = await Modules.task_scheduler.get_session_failed_attempts(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "failed_attempts": failed_attempts,
            "count": len(failed_attempts)
        }
    except Exception as e:
        logger.error(f"获取会话失败尝试失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/sessions/{session_id}/tasks")
async def get_session_tasks(session_id: str):
    """获取会话的所有任务"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        tasks = await Modules.task_scheduler.get_session_tasks(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "tasks": tasks,
            "count": len(tasks)
        }
    except Exception as e:
        logger.error(f"获取会话任务失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.delete("/sessions/{session_id}/memory")
async def clear_session_memory(session_id: str):
    """清除指定会话的记忆"""
    if not Modules.task_scheduler:
        raise HTTPException(503, "任务调度器未就绪")
    
    try:
        success = await Modules.task_scheduler.clear_session_memory(session_id)
        if not success:
            raise HTTPException(404, f"会话 {session_id} 不存在")
        
        return {
            "success": True,
            "message": f"会话 {session_id} 的记忆已清除"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"清除会话记忆失败: {e}")
        raise HTTPException(500, f"清除失败: {e}")

# ============ 文件编辑工具包API ============

@app.get("/tools")
async def list_tools():
    """列出所有可用的工具"""
    try:
        tools = toolkit_manager.get_all_tools()
        return {
            "success": True,
            "tools": tools,
            "count": len(tools)
        }
    except Exception as e:
        logger.error(f"获取工具列表失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/toolkits")
async def list_toolkits():
    """列出所有可用的工具包"""
    try:
        toolkits = toolkit_manager.list_toolkits()
        return {
            "success": True,
            "toolkits": toolkits,
            "count": len(toolkits)
        }
    except Exception as e:
        logger.error(f"获取工具包列表失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.get("/toolkits/{toolkit_name}")
async def get_toolkit_info(toolkit_name: str):
    """获取工具包详细信息"""
    try:
        info = toolkit_manager.get_toolkit_info(toolkit_name)
        if not info:
            raise HTTPException(404, f"工具包不存在: {toolkit_name}")
        
        return {
            "success": True,
            "toolkit": info
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取工具包信息失败: {e}")
        raise HTTPException(500, f"获取失败: {e}")

@app.post("/tools/{toolkit_name}/{tool_name}")
async def call_tool(toolkit_name: str, tool_name: str, arguments: Dict[str, Any]):
    """调用工具"""
    try:
        result = await toolkit_manager.call_tool(toolkit_name, tool_name, arguments)
        return {
            "success": True,
            "toolkit": toolkit_name,
            "tool": tool_name,
            "result": result
        }
    except Exception as e:
        logger.error(f"调用工具失败 {toolkit_name}.{tool_name}: {e}")
        raise HTTPException(500, f"调用失败: {e}")

# ============ 文件编辑专用API ============

@app.post("/file/edit")
async def edit_file(request: Dict[str, str]):
    """编辑文件 - 使用SEARCH/REPLACE格式"""
    try:
        path = request.get("path")
        diff = request.get("diff")
        
        if not path or not diff:
            raise HTTPException(400, "缺少必要参数: path 和 diff")
        
        result = await toolkit_manager.call_tool("file_edit", "edit_file", {
            "path": path,
            "diff": diff
        })
        
        return {
            "success": True,
            "result": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"编辑文件失败: {e}")
        raise HTTPException(500, f"编辑失败: {e}")

@app.post("/file/write")
async def write_file(request: Dict[str, str]):
    """写入文件"""
    try:
        path = request.get("path")
        content = request.get("content")
        
        if not path or content is None:
            raise HTTPException(400, "缺少必要参数: path 和 content")
        
        result = await toolkit_manager.call_tool("file_edit", "write_file", {
            "path": path,
            "file_text": content
        })
        
        return {
            "success": True,
            "result": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"写入文件失败: {e}")
        raise HTTPException(500, f"写入失败: {e}")

@app.get("/file/read")
async def read_file(path: str):
    """读取文件"""
    try:
        result = await toolkit_manager.call_tool("file_edit", "read_file", {
            "path": path
        })
        
        return {
            "success": True,
            "content": result
        }
    except Exception as e:
        logger.error(f"读取文件失败: {e}")
        raise HTTPException(500, f"读取失败: {e}")

@app.get("/file/list")
async def list_files(directory: str = "."):
    """列出目录文件"""
    try:
        result = await toolkit_manager.call_tool("file_edit", "list_files", {
            "directory": directory
        })
        
        return {
            "success": True,
            "result": result
        }
    except Exception as e:
        logger.error(f"列出文件失败: {e}")
        raise HTTPException(500, f"列出失败: {e}")

if __name__ == "__main__":
    import uvicorn
    from agentserver.config import AGENT_SERVER_PORT
    uvicorn.run(app, host="0.0.0.0", port=AGENT_SERVER_PORT)