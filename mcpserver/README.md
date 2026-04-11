# MCPServer MCP服务模块

基于 Model Context Protocol (MCP) 的工具调用框架，提供动态服务发现、会话管理和任务调度。

## 核心文件

### mcp_server.py - MCP HTTP 服务
- FastAPI 应用（端口 8003）
- 接口：`POST /tasks`（提交MCP任务）、`GET /tasks/{task_id}`（查询任务状态）
- 启动时初始化 MCPManager 和 MCPScheduler

### mcp_manager.py - MCP 客户端会话管理
- 通过 `stdio_client` 管理 MCP 客户端连接
- Agent 间 Handoff 异常处理机制（`HandoffInputData`, `HandoffError` 等）
- 工具调用执行与结果协调

### mcp_scheduler.py - 任务调度器
- `MCPScheduler` 异步工作队列，默认最大 10 个并发任务
- 任务去重、失败重试（最多3次）
- 支持回调 URL 异步返回结果

### mcp_registry.py - 服务注册中心
- `get_service_info()`、`get_available_tools()`、`get_all_services_info()`
- 查询 `MCP_REGISTRY` 全局服务注册表

### mcp_support.py - 动态服务注册
- `scan_and_register_mcp_agents()` 扫描 `agent-manifest.json` 文件
- `create_agent_instance()` 动态实例化服务
- 全局变量：`MCP_REGISTRY`、`MANIFEST_CACHE`
- 过滤 `agentType: "mcp"` 类型的服务

## MCP代理

### agent_crawl4ai/ - Crawl4AI 网页解析代理
- 使用 Crawl4AI 进行高性能网页爬取
- 输出 AI 友好的 Markdown 格式
- 支持 CSS 选择器、JS 执行、截图等能力
- 配置通过 `agent-manifest.json` 定义

## 开发新MCP代理

1. 在 `mcpserver/` 下创建目录
2. 添加 `agent-manifest.json`（`agentType: "mcp"`）
3. 实现代理类
4. 框架通过 `scan_and_register_mcp_agents()` 自动发现