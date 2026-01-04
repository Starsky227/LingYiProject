# Brain 智能大脑模块

## 主要功能

### 核心模块

#### background_analyzer.py
- **功能**: 后台AI分析引擎
- **主要特性**: 
  - 多阶段AI分析流程
  - 智能意图识别与任务规划
  - 工具调用决策与执行控制
  - 记忆管理与上下文处理

### 子模块 memory/
记忆管理子系统，详见 [memory/README.md](memory/README.md)
- 知识图谱构建与管理
- 对话记忆提取与存储
- 相关记忆搜索与检索
- 记忆图谱可视化展示

## 使用方式
```python
from brain.background_analyzer import analyze_intent, generate_response
intent_type, todo_list = analyze_intent(messages, memories)
response = generate_response(messages, todo_list, memories, work_history, callback)
```