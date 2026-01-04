# API Server 模块

## 文件说明

### llm_service.py
- **功能**: LLM通信服务模块
- **主要特性**: 
  - 与模型通信（OpenAI API格式）
  - 流式响应支持
  - 智能意图分析与工具调用
  - 记忆检索与上下文组织
  - 聊天日志记录

## 核心功能

### 对话处理流程
1. **消息预处理**: 验证输入并记录日志
2. **意图分析**: 识别用户意图和任务类型
3. **记忆检索**: 从Neo4j查询相关历史记忆
4. **模型调用**: 流式调用本地LLM生成响应
5. **后处理**: 记录回复并提交记忆提取任务

### 主要接口
- `chat_with_model()` - 主对话处理函数
- `preload_model()` - 模型预加载
- `call_model_sync()` - 同步模型调用
- `write_chat_log()` - 聊天日志记录

## 使用方式
```python
from api_server.llm_service import chat_with_model
response = chat_with_model(messages, on_response_callback)
```

## 依赖关系

### 内部依赖
- `system.config`: 系统配置管理
- `system.intent_analyzer`: 意图分析
- `brain.memory.quintuples_extractor`: 记忆提取
- `brain.memory.relevant_memory_search`: 记忆检索

### 外部依赖
- `openai`: OpenAI API客户端
- `datetime`: 时间处理
- `json`: JSON数据处理

## 配置要求

模块需要以下配置项（在 `config.json` 中）：
- `api.api_key`: API密钥
- `api.base_url`: Ollama服务地址
- `api.model`: 使用的模型名称
- `system.ai_name`: AI名称
- `ui.username`: 用户名称
- `system.debug`: 调试模式开关

## 日志系统

### 聊天日志
- **位置**: `brain/memory/logs/chat_logs/`
- **格式**: `HH:MM:SS <发送者> 消息内容`
- **文件名**: `chat_logs_YYYY_MM_DD.txt`

### 调试日志
- DEBUG模式下输出详细的处理信息
- 包含意图分析结果、记忆查询状态等

## 错误处理

- **连接异常**: 自动捕获并报告通信错误
- **模型异常**: 优雅处理模型调用失败
- **记忆异常**: 记忆系统故障不影响对话功能
- **日志异常**: 日志写入失败不中断主流程

## 性能优化

- **空消息检测**: 避免处理空白消息，节省资源
- **流式响应**: 实时返回生成内容，提升用户体验
- **异步记忆**: 记忆提取任务异步执行，不阻塞对话
- **上下文限制**: 智能选择对话上下文，控制token使用

## 使用示例

```python
from api_server.llm_service import chat_with_model, preload_model

# 预加载模型
if preload_model():
    print("模型加载成功")

# 处理对话
messages = [{"role": "user", "content": "你好"}]

def on_response(chunk):
    print(chunk, end="", flush=True)

reply = chat_with_model(messages, on_response)
print(f"完整回复: {reply}")
```

## 维护说明

- 定期清理 `__pycache__` 缓存文件
- 监控日志文件大小，必要时进行轮转
- 关注模型服务的可用性和响应时间
- 定期检查记忆系统的连接状态
