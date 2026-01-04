# MCPServer MCP服务模块

## 文件说明

### 🎯 重点感谢
本萌新大量借鉴了：
NagaAgent：https://github.com/Xxiii8322766509/NagaAgent
理论上NagaAgent提供的mcp工具可以直接拖到这里使用

### 核心文件

#### mcp_manager.py
- **功能**: MCP客户端管理器
- **主要特性**: 
  - MCP服务连接与会话管理
  - 工具调用执行与结果处理
  - Handoff异常处理机制

#### mcp_registry.py
- **功能**: MCP服务注册中心
- **主要特性**:
  - 动态扫描并注册MCP代理
  - 服务信息缓存与查询
  - 工具能力发现

#### mcp_scheduler.py
- **功能**: MCP任务调度器
- **主要特性**:
  - 任务队列管理
  - 并发控制 (最大10个任务)
  - 失败重试机制 (最多3次)
  - 博弈论优化调度

#### mcp_server.py
- **功能**: MCP HTTP服务器
- **主要特性**:
  - FastAPI Web服务
  - RESTful API接口
  - 任务状态管理
  - 生命周期管理

#### mcp_support.py
- **功能**: MCP服务支持库
- **主要特性**:
  - Manifest文件加载
  - 动态代理实例创建
  - 服务扫描注册

### 代理示例

#### agent_crawl4ai/
- **功能**: Crawl4AI网页解析代理
- **能力**: 网页内容抓取并转换为Markdown格式
- **配置**: agent-manifest.json定义服务元数据

## 架构特点

- **插件化**: 基于Manifest配置的动态代理加载
- **可扩展**: 支持自定义MCP代理开发
- **高可用**: 任务重试、并发控制、异常处理
- **标准化**: 遵循MCP协议规范

## 使用方式

### 启动MCP服务
```python
from mcpserver.mcp_server import app
# FastAPI应用自动启动
```

### 注册新代理
```bash
# 在mcpserver目录下创建代理文件夹
# 添加agent-manifest.json配置文件
# 实现对应的代理类
```