# LingYiProject 0.5.5
This is a project started by a total beginner, a 3A project: AI coding, AI drawing, AI service. Projectstart at 2025/10/1
这是一个纯萌新打造的AI智能体计划，纯正的3A大作：AI编程，AI立绘，AI聊天。

### 🎯 重点感谢
本萌新大量借鉴了：
NagaAgent：https://github.com/Xxiii8322766509/NagaAgent

## 🚀 快速开始
**使用方法：**
```bash
# 首先使用一键配置
.\setup.ps1

# 然后双击start.bat启动
```

### 📋 系统要求

- **操作系统**: Windows 11 （10应该也行？）
- **Python**: 3.10+ (推荐 3.13)
- **内存**: 建议 4GB+ （需要跑neo4j）
- **存储**: 建议 5GB+ （取决于你需要存多少数据，不过部署要求不高）
- **Neo4j**: 需安装 Neo4j 数据库 + APOC 插件


### 项目框架

```
LingYiProject/
├── system/                    # 系统基础设施（配置、路径、环境校验）
│   ├── config.py             # Pydantic配置管理，热重载，端口分配
│   ├── paths.py              # 运行时数据路径定义
│   └── system_checker.py     # Neo4j连接校验，端口可用性检查
│
├── brain/                     # AI智能大脑
│   ├── task_manager.py       # 异步任务调度器（优先级队列）
│   ├── lingyi_core/          # LingYiCore 核心引擎
│   │   ├── lingyi_core.py    # 主协调器（OpenAI客户端、多源工具、会话管理）
│   │   ├── session_state.py  # 会话状态（记忆缓存、工具追踪、输入缓冲）
│   │   ├── tool_manager.py   # 多源工具聚合器（前缀路由）
│   │   └── LingYi_prompt.xml # 全局人格Prompt
│   ├── memory/               # 记忆管理子系统
│   │   ├── knowledge_graph_manager.py  # Neo4j知识图谱CRUD
│   │   ├── record_memory.py  # AI Agent驱动的记忆录入
│   │   ├── search_memory.py  # 关键词+向量语义搜索
│   │   ├── memorygraph_visualizer.py   # 交互式HTML图谱可视化
│   │   ├── _agent_runner.py  # 轻量Agent执行器
│   │   ├── tools/            # 记忆操作工具集
│   │   └── prompt/           # 记忆分析Prompt模板
│   └── tools/                # 主工具集（cancel_task, speak_text等）
│
├── agentserver/              # 智能体服务框架
│   ├── agent_server.py       # FastAPI Agent发现服务（端口8001）
│   ├── agent_registry.py     # Agent自动发现与注册
│   ├── runner.py             # 通用Agent循环执行器
│   ├── file_analysis_agent/  # 文件分析Agent（PDF/Word/Excel/PPT/代码/图片）
│   ├── info_agent/           # 信息检索Agent
│   └── web_agent/            # 网络搜索Agent（支持MCP集成）
│
├── mcpserver/                # MCP (Model Context Protocol) 服务
│   ├── mcp_server.py         # MCP HTTP服务（端口8003）
│   ├── mcp_manager.py        # MCP客户端会话管理
│   ├── mcp_scheduler.py      # 任务调度（10并发，重试机制）
│   ├── mcp_registry.py       # 服务注册中心
│   ├── mcp_support.py        # 动态服务发现与注册
│   └── agent_crawl4ai/       # Crawl4AI网页解析代理
│
├── service/                   # 后台服务
│   ├── pcAssistant/          # PC桌面服务（PyQt入口、子进程管理）
│   │   ├── pc_main.py        # 主应用入口（PyQt↔asyncio桥接）
│   │   ├── service_manager.py # 服务生命周期管理
│   │   ├── voice_input_VDL/  # 语音输入（VDL语音识别）
│   │   └── voice_output/     # 语音输出（Qwen3-TTS）
│   └── qqOneBot/             # QQ机器人服务
│       ├── qqbot_main.py     # QQ Bot入口（LingYiCore + QQ工具）
│       ├── onebot.py         # OneBot v11 WebSocket客户端
│       ├── handler.py        # QQ消息处理器
│       ├── Prompt/           # QQ专用人格Prompt
│       └── qq_tools/         # QQ专用工具集（qq- 前缀）
│
├── ui/                        # PyQt5 用户界面
│   ├── pyqt_chat_ui.py       # ChatWindow主入口组件
│   └── components/           # UI组件（标题栏/侧边栏/聊天页/设置页/立绘面板）
│
├── api_server/               # LLM API服务（预留）
├── data/                     # 运行时数据（缓存、日志、记忆图谱）
├── main.py                   # 主程序入口
└── config.json               # 系统配置文件
```

**核心架构说明:**
- **system**: 配置管理（Pydantic + json5热重载）、路径定义、Neo4j/端口环境校验
- **brain**: AI核心引擎 LingYiCore，多源工具聚合（前缀路由），Per-Session状态管理，Neo4j知识图谱记忆
- **agentserver**: 插件化Agent框架，自动发现注册，通用Agent执行循环
- **mcpserver**: MCP协议工具调用，动态服务发现，并发任务调度
- **service**: 后台服务管理（PC桌面/QQ Bot/语音输入输出）
- **ui**: PyQt5组件化桌面界面



### 📋 版本信息
**现版本内容总结：**

**版本0.5.5**
1. 全新 LingYiCore 核心引擎：多源工具聚合（前缀路由 main-/qq-），Per-Session状态管理（记忆缓存、工具追踪、输入缓冲）
2. ToolManager 统一工具注册：brain/tools/、agentserver agents、QQ工具等通过前缀路由无缝集成
3. AgentServer 完整实现：自动发现注册子Agent（file_analysis/info/web），通用Agent执行循环（runner.py）
4. 记忆系统重构：MemoryWriter 通过独立Agent循环录入，search_memory 支持关键词+向量语义混合搜索
5. QQ Bot 服务：OneBot v11 WebSocket集成，独立人格Prompt，专用工具集
6. PC服务管理器：统一管理QQ Bot/语音/图谱可视化等子进程生命周期
7. UI组件化重构：ChatWindow 拆分为 TitleBar/SideBar/MainWindow/ImageWindow，支持服务控制面板

**版本0.4.0**
1. 正常对话（这部分随着更新现在已经半死不活了，相信来的各位也不是奔着这部分来的）
2. neo4j知识图谱（目前主要更新点在此，启动方式为运行memorygraph_visualizer.py）
3. 关键词提取，记忆自管理，之后大概会是选择多个模型协同运作（主要是节省token）
4. 几乎完全重写了记忆存储逻辑（之前的纯ai创作的版本已经基本优化的渣都不剩了）
5. 更详细的记忆系统说明详见memory文件下的README

**版本0.3.0**
1. 全面更新neo4j记忆逻辑，现在以全新的时间，地点，角色，实体四种节点为记忆基础。
2. 基本放弃五元组记忆输入，转交AI进行
3. 全新的记忆编辑html
4. 不确定基础的对话功能是否还在运作，当前开发重心转移到记忆系统。
5. 基本不再继续本地运行，转而使用api，记忆系统当前试用gpt-5-nano

**版本0.2.0**
1. 重新整理了AI的思维链
2. 实现了AI的工具调用
3. 初步进行了记忆系统的尝试，实装log系统

**版本：0.1.0**
1. 实装了三/五元组提取（参考了Naga）
2. 实装了neo4j记忆节点查阅
3. 修改了意图识别（不成功）

**版本：0.0.1**
1. 可以链接到模型了
2. 尝试了意图识别（不成功）
3. 有了立绘和ui
4. 上传了crawl4ai（但无法启用）
5. 复制了大量Naga的代码在库中，并未实际使用。