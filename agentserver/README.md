# AgentServer 智能体服务框架

基于 FastAPI 的智能体发现、注册和执行框架。将文件分析、信息检索、网络搜索等能力封装为独立的 Agent，统一通过 LingYiCore 的 ToolManager 以工具形式调用。

## 核心文件

### agent_server.py - FastAPI 服务
- Agent 发现与健康检查 HTTP 服务（端口 8001）
- 启动时自动扫描并注册所有子Agent
- 接口：`GET /health`、`GET /agents`

### agent_registry.py - Agent 发现与注册
- `discover_agents()` 扫描子目录中的 `callable.json` / `config.json`
- `AgentToolRegistry` 解析 OpenAI Tools API 格式的工具Schema
- `AgentAIClient` 适配器供 runner.py 调用

### runner.py - 通用 Agent 执行器
- `run_agent_with_tools()` 统一的异步Agent循环
- 加载Agent目录下的 `prompt.md` 作为系统提示词
- 支持工具发现、LLM迭代、工具结果注入、可配置最大迭代次数

### http_client.py - HTTP请求工具
- `request_with_retry()` 带指数退避的异步请求
- 自动处理 429 限流和 5xx 错误

### http_config.py - 外部API配置
- 请求超时和重试次数配置

### config.py - 服务配置
- 从 `system.config` 获取 agent_server 端口（默认 8001）

## 子Agent

### file_analysis_agent/ - 文件分析Agent
- 支持 PDF/Word/Excel/PPT/代码/图片/压缩包 等格式
- 先下载文件 → 检测类型 → 调用对应解析工具
- Tools: `analyze_code/`, `analyze_multimodal/`, `detect_file_type/`, `download_file/`, `extract_pdf/`, `extract_xlsx/`, `extract_pptx/`, `extract_docx/`, `read_text_file/`, `cleanup_temp/`

### info_agent/ - 信息检索Agent
- 通过外部API获取各类实时信息
- 由 `callable.json` 定义对外暴露的调用接口

### web_agent/ - 网络搜索Agent
- 网页抓取与搜索能力，支持 MCP 浏览器集成
- `mcp.json` 定义Agent私有的MCP工具配置
- 调用时自动加载对应MCP工具

## 启动方式

Agent Server 已集成到主流程，通过 `main.py` 统一启动。

独立调试：
```bash
uvicorn agentserver.agent_server:app --host 0.0.0.0 --port 8001
```

## 开发新Agent

1. 在 `agentserver/` 下创建新目录
2. 添加 `config.json`（工具Schema定义）和 `handler.py`（执行逻辑）
3. 可选 `callable.json`（对外暴露接口）、`prompt.md`（系统提示词）、`mcp.json`（MCP集成）
4. 框架会自动发现并注册

## 架构特点

### 微服务架构
- **独立服务**：意图分析作为独立的HTTP服务运行
- **异步处理**：Fire-and-forget模式，不阻塞主流程
- **任务调度**：分析结果直接转化为可执行任务

### 核心组件

#### 1. Agent Server (`agent_server.py`)
- FastAPI服务，提供HTTP接口
- 管理全局模块状态
- 处理意图分析和任务规划请求

#### 2. Task Planner (`task_planner.py`)
- 评估任务可执行性
- 制定执行计划
- 支持MCP和Agent两种执行方式

#### 3. Task Scheduler (`task_scheduler.py`)
- 负责任务执行调度
- 管理任务生命周期
- 支持并发任务执行
  
使用示例：

```python
from agentserver.task_scheduler import get_task_scheduler

task_scheduler = get_task_scheduler()

# 创建并行任务列表（示例）
tasks = [
    {"type": "processor", "params": {"query": "示例任务A"}},
    {"type": "processor", "params": {"query": "示例任务B"}},
]

# 并行执行
results = await task_scheduler.schedule_parallel_execution(tasks)

# 查询统计
total = len(task_scheduler.task_registry)
running = len([t for t in task_scheduler.task_registry.values() if t.get("status") == "running"])
queued = len([t for t in task_scheduler.task_registry.values() if t.get("status") == "queued"])
```

#### 4. Task Deduper (`task_deduper.py`)
- 基于LLM的任务重复检测
- 防止重复执行相似任务
- 支持相似度计算

## API接口

### 意图识别与电脑控制执行
```http
POST /analyze_and_execute
Content-Type: application/json

{
    "messages": [
        {"role": "user", "content": "帮我打开浏览器并搜索Python教程"}
    ],
    "session_id": "main_session"
}
```

### 任务管理
```http
GET /tasks?session_id=main_session
GET /tasks/{task_id}
POST /tasks/{task_id}/cancel
```

### 健康检查
```http
GET /health
```

## 启动方式

### 统一启动（推荐）
Agent Server已集成到主流程中，通过`main.py`统一启动：

```bash
python main.py
```

这将同时启动：
- 主对话流程
- Agent Server (端口8001)
- API服务器 (端口8000)
- 语音输出服务
- 其他后台服务

### 独立启动（开发调试）
如果需要独立启动Agent Server进行调试：

```bash
uvicorn agentserver.agent_server:app --host 0.0.0.0 --port 8001
```

## 配置说明

在 `agentserver/config.py` 中可以配置：
- 服务端口和主机
- 任务超时时间
- 意图分析开关
- 日志级别等

## 与主流程集成

主对话流程通过HTTP客户端调用Agent Server：

```python
# 在apiserver/api_server.py中
async def _call_agent_server_analyze(self, messages, session_id):
    url = "http://localhost:8001/analyze_and_plan"
    payload = {"messages": messages, "session_id": session_id}
    # 发送请求...
```

## 优势对比

### 相比原集成架构的优势：
1. **解耦性**：意图分析独立运行，不影响主对话流程
2. **可扩展性**：支持分布式部署和负载均衡
3. **任务驱动**：分析结果直接转化为可执行任务
4. **容错性**：服务故障不影响主流程

### 适用场景：
- 需要大规模任务自动化的场景
- 多用户并发处理
- 分布式部署需求
- 任务执行监控和管理

## 注意事项

1. **服务依赖**：需要确保Agent Server在端口8001运行
2. **网络延迟**：HTTP调用会增加少量延迟
3. **错误处理**：需要处理网络异常和服务不可用情况
4. **资源管理**：需要合理配置任务并发数和超时时间

## 更新记录

- 2025-09-30: 修复导入路径，将 `apiserver.task_scheduler` 更正为 `agentserver.task_scheduler`（影响 `core/agent_manager.py` 与 `core/multi_agent_coordinator.py`），避免导入解析失败。
- 2025-10-06: 新增 `agentserver/task_scheduler.py` 轻量实现与用法示例，并在 `agentserver/agent_manager.py` 统一更正注释为“通过agentserver处理”。