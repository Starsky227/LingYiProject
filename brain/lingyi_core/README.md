# LingYi Core 核心引擎

铃依AI的主调度引擎，负责协调对话、工具调用、记忆检索和会话状态管理。

## 文件说明

### lingyi_core.py - 主协调器
- **LingYiCore 类**：核心单例，管理OpenAI客户端与多会话状态
- 从 `LingYi_prompt.xml` 加载全局人格Prompt
- 聚合多源工具注册（brain/tools/ → main- 前缀，agentserver agents → main- 前缀）
- 每会话独立的记忆缓存、工具进度追踪、输入缓冲
- `MAX_TOOL_ROUNDS = 10`：防止无限工具调用循环
- 工具返回值通过 `{valuable}` 标签标记有价值的结果

### session_state.py - 会话状态管理
- **MemoryCache**：缓存记忆搜索结果并自动去重
- **ActiveToolTracker**：追踪正在执行的工具调用
- **InputBuffer**：在AI思考时缓冲新消息，工具返回时释放
- **SessionState**：以上三者的统一管理

### tool_manager.py - 多源工具聚合器
- **ToolManager**：统一注册不同来源的工具
- **LocalToolRegistry**：扫描本地工具目录
- **AgentSubRegistry**：发现并注册agentserver中的Agent
- 前缀路由机制（main-/qq- 等），统一输出 Responses API 格式Schema

### LingYi_prompt.xml - 人格Prompt模板
铃依的主人格设定，定义发言风格、行为准则等。

## 设计架构

### 输入
1. **session key**：频道隔离，确保不同频道（主UI/QQ/其他）的会话完全独立
2. **caller message**：消息来源标注
3. **prompt**：对应频道的行动指南
4. **keyword list**：供记忆系统的额外关键词

### 自动组装
1. **core prompt** + 频道 prompt → 系统提示词
2. **memory**：每会话独立的记忆模块，缓存最近记忆并去重
3. **tool list**：主工具箱 + 分支工具箱（QQ工具等），MCP/Agent伪装为tool
4. **activity tracker**：反馈当前工具执行状态
5. **input buffer**：AI思考时缓冲新消息

### 工作流程
```
0. 消息进入buffer → 等待2-5s打包输入
1. 信息汇总 → 提交大模型
2. 大模型思考（新消息进buffer，禁止直接输入）
3. 大模型输出 message + tool_call
4. buffer内容随tool_call一起输入 / 等待1s后直接输入
5. buffer & tool progress 都空则结束，否则重复1-4
```

### 模型输出
- **message**：文本回复
- **tool_call**：工具调用（支持 cancel 取消单个工具、end 终止当前轮次）