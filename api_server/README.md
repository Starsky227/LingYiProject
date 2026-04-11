# API Server 模块

预留模块，用于未来扩展 LLM API 通信服务层。

当前 LLM 通信功能已直接集成在 `brain/lingyi_core/lingyi_core.py` 中，通过 OpenAI 客户端完成模型调用。

## 状态

- 当前目录仅包含本 README
- LLM 调用逻辑由 LingYiCore 统一管理
- 未来可能独立为 HTTP API 服务供外部调用
- `api.api_key`: API密钥
- `api.base_url`: Ollama服务地址
- `api.model`: 使用的模型名称
- `system.ai_name`: AI名称
- `ui.username`: 用户名称
- `system.debug`: 调试模式开关

## 日志系统


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
