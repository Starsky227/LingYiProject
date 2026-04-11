# Brain 智能大脑模块

铃依的核心大脑，负责AI对话协调、工具调度、会话状态管理和记忆系统。

## 模块结构

```
brain/
├── task_manager.py          # 异步任务调度器
├── lingyi_core/             # LingYiCore 核心调度引擎
│   ├── lingyi_core.py       # 主协调器（OpenAI客户端、工具注册、会话管理）
│   ├── session_state.py     # 每会话状态（记忆缓存、工具追踪、输入缓冲）
│   ├── tool_manager.py      # 多源工具聚合器（前缀路由）
│   └── LingYi_prompt.xml    # 全局人格Prompt模板
├── memory/                  # 记忆管理子系统（Neo4j知识图谱）
│   └── ...                  # 详见 memory/README.md
└── tools/                   # 主工具集（main- 前缀注册）
    ├── cancel_task/
    ├── interrupt_voice/
    ├── record_memory/
    ├── search_memory/
    └── speak_text/
```

## 核心组件

### task_manager.py - 异步任务调度器
- 单例模式的 `TaskManager`，支持优先级队列（LOW/NORMAL/HIGH/URGENT）
- 任务状态追踪（pending/running/completed/failed/cancelled）
- 失败自动重试（默认最多3次）

### lingyi_core/ - LingYiCore 引擎
AI交互的核心调度器，详见 [lingyi_core/README.md](lingyi_core/README.md)

- **LingYiCore**: 管理OpenAI客户端、聚合多源工具、维护每会话状态
- **SessionState**: 记忆缓存去重、活跃工具追踪、输入消息缓冲
- **ToolManager**: 前缀路由（main-/qq-等）统一不同来源的工具Schema

### memory/ - 记忆管理子系统
基于Neo4j的知识图谱记忆系统，详见 [memory/README.md](memory/README.md)

## 使用方式

```python
from brain.lingyi_core.lingyi_core import LingYiCore

core = LingYiCore()
await core.initialize(session_key="main", prompt="系统Prompt")
response = await core.chat(session_key="main", messages=[...])
```