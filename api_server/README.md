# API Server 模块

历史遗留模块，原本用于 LLM API 通信服务层。

当前 LLM 通信功能已直接集成在 `brain/lingyi_core/lingyi_core.py` 中，通过 OpenAI 客户端完成模型调用。

## 状态

- 当前目录仅包含本 README
- LLM 调用逻辑由 LingYiCore 统一管理