# LingYiProject 0.3.1
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


### 项目框架

```
LingYiProject/
├── system/                    # 系统核心模块
│   ├── config.py             # 配置管理
│   ├── task_manager.py       # 任务调度器
│   └── prompts/              # AI提示词模板
│
├── brain/                     # AI智能大脑模块
│   ├── background_analyzer.py # 后台AI分析引擎
│   └── memory/               # 记忆管理子系统（近期更新内容主要在这里）
│       ├── knowledge_graph_manager.py  # 知识图谱管理
│       ├── relevant_memory_search.py   # 记忆搜索
│       ├── memorygraph_visualizer.py   # 图谱可视化
│       └── logs/             # 记忆日志存储
│
├── api_server/               # LLM通信服务
│   └── llm_service.py       # 模型调用与对话处理
│
├── mcpserver/               # MCP服务框架
│   ├── mcp_manager.py      # MCP客户端管理
│   ├── mcp_scheduler.py    # 任务调度器
│   ├── mcp_server.py       # HTTP服务器
│   └── agent_crawl4ai/     # 网页解析代理
│
├── ui/                      # 用户界面模块
│   ├── chat_ui.py          # PyQt5聊天界面
│   └── img/                # 界面资源
│
├── agentserver/            # 代理服务器（待开发）
├── main.py                 # 主程序入口
└── config.json             # 系统配置文件
```

**核心架构说明:**
- **system**: 提供配置管理和任务调度的基础服务
- **brain**: AI智能核心，负责意图分析、记忆管理和决策
- **api_server**: 与LLM模型通信的服务层
- **mcpserver**: 基于MCP协议的工具调用框架
- **ui**: PyQt5图形用户界面



### 📋 版本信息
**现版本内容总结：**
1. 正常对话（这部分随着更新现在已经半死不活了，相信来的各位也不是奔着这部分来的）
2. neo4j知识图谱（目前主要更新点在此）
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