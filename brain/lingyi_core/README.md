# LingYi Core 核心引擎

铃依 AI 的主调度引擎，负责协调对话、工具调用、记忆检索、图片描述、流式 TTS 与会话状态管理。

## 文件清单

| 文件 | 职责 |
|---|---|
| [lingyi_core.py](lingyi_core.py) | `LingYiCore` 主调度器 + 工具循环 + 流式 TTS bridge + 视觉描述 |
| [session_state.py](session_state.py) | 单 session 容器与子组件（记忆缓存、工具追踪、输入缓冲、批次记忆等） |
| [tool_manager.py](tool_manager.py) | 多源工具聚合（本地 brain/tools + agentserver agents），前缀路由 |
| [chat_logger.py](chat_logger.py) | 统一的 `chat_logs/chat_logs_YYYY_MM_DD.txt` 写入入口 |
| [model_logger.py](model_logger.py) | 模型调用调试日志（输入/输出/工具结果），独立于核心调度 |
| [LingYi_prompt.xml](LingYi_prompt.xml) | 全局人格 prompt 模板 |

## 顶层组件: `LingYiCore`

单例 AI 调度器，由 `main.py` 在程序启动时实例化，对外暴露：

- `__init__(main_prompt: str)` — 拼接核心人格与子模块 prompt
- `_compose_main_prompt(module_prompt)` — 模式切换时（如助手模式）重组 prompt
- `get_session_state(session_key) -> SessionState` — 按 session 隔离状态
- `process_message(session_key, stream_text_callback=None, on_text_output=None)` — 消费 buffer 中的消息，进入工具循环并产出回复
- `flush_all_pending_memory()` — 程序关闭时调用，强制刷新所有未保存的记忆批次

内部聚合：
- `client: OpenAI` — 主模型客户端 (`config.main_api`)
- `_vision_client: OpenAI` — 视觉描述客户端，按需懒加载 (`config.vision_api`)
- `tool_manager: ToolManager` — 注册了 brain/tools/（前缀 `main-`）与 agentserver agents（前缀 `main-`）
- `_session_states: dict[str, SessionState]`
- `_memory_writers: dict[str, MemoryWriter]` — 按 session 隔离已提取事件去重
- `_background_tasks: set[asyncio.Task]` — 持有 fire-and-forget 任务的强引用，避免被 GC 提前回收（视觉描述等）

## 单 session 状态: `SessionState`

每个 session（如 PC 助手 `"default"`、QQ 频道 ID）拥有独立实例。组合的子组件：

| 子组件 | 类 | 作用 |
|---|---|---|
| `memory_cache` | `MemoryCache(max_entries=3)` | 缓存最近 3 次格式化记忆，按行去重供模型阅读 |
| `memory_batch` | `MemoryBatchBuffer(trigger_count=20, batch_size=25)` | 累积消息+valuable 工具结果，每满 20 条触发一次"搜索→记录"，批次窗口含 5 条 overlap |
| `activity_tracker` | `ActiveToolTracker` | 跟踪当前正在执行的工具（call_id, name, args, task），可被 `cancel_task` 工具取消 |
| `input_buffer` | `InputBuffer` | 模型思考期间外部到达的消息暂存，工具循环间隙释放给模型 |
| `conversation_context` | `ConversationContext(max_messages=25)` | 滚动维护最近 25 条格式化消息（含图片描述、valuable 工具结果） |
| `tool_context: dict` | — | 调用方注入的运行期上下文，常见键：`screen_capture_enabled`、`assistant_reply_callback`、`session`、`event` 等 |
| `external_pending_images: list[dict]` | — | 外部注入的待处理图片 `[{data_url, description}]`（如助手模式截屏） |
| `screen_context: str` | — | 屏幕 OCR 最新文字（被动环境信息） |
| `scratchpad: str` | — | AI 可读写的临时便签（session 级） |
| `_idle_flush_task` | `asyncio.Task` | 空闲超时刷新计时器（默认 600s） |
| `_memory_tasks: set` | — | 后台记忆批次任务的强引用集合 |

### 长期记忆批次机制 `MemoryBatchBuffer`

- 每条**消息**与每条**带 `{valuable}` 标签的工具结果**都会被 `add_entry()` 累积。
- 累积到 `trigger_count=20` 条时 `process_ready_batches()` 启动后台任务：
  - 在线程池中跑同步的 `full_memory_search`（避免阻塞 event loop）
  - 完成后异步 `MemoryWriter.full_memory_record` 写入 Neo4j
- 批次窗口实际为 `batch_size=25`，包含上一批末尾的 5 条 overlap，提升上下文连续性。
- `flush_all()`：用于关闭/超时场景，强制弹出所有剩余条目。
- `MEMORY_IDLE_FLUSH_TIMEOUT = 600` 秒：每次 `process_message` 结束时启动倒计时，超时后强制刷新一次。

## 工具系统: `tool_manager`

```
ToolManager
 ├─ ("main-", LocalToolRegistry(brain/tools/))   ← cancel_task / record_memory / scratchpad / search_memory / view_screen
 └─ ("main-", AgentSubRegistry)                  ← agentserver/ 下的所有 agent（伪装为工具）
```

- `normalize_tool_schema()` 同时支持 Chat Completions 嵌套格式与 Responses API 扁平格式。
- `LocalToolRegistry` 扫描子目录找 `config.json + handler.py`，handler 懒加载，按 `importlib` 重新导入。
- `AgentSubRegistry` 委托 `agentserver.agent_registry.discover_agents` + `_make_lazy_agent_handler`。
- `execute_tool(name, args, context)` 按完整名（含前缀）路由到对应 sub-registry。

## `process_message` 工作流

```
0. _collect_batch: 等待 BATCH_MIN_WAIT(2s) ~ BATCH_MAX_WAIT(5s) 的批量消息窗口
1. drain buffer 得到本轮 incoming
   ├─ 写 chat_log
   ├─ 触发一次轻量记忆搜索 (max_expansion_rounds=1) → memory_cache.add()
   └─ 加入 memory_batch
2. tool_items 过期清理 (TOOL_CALL_TTL=3 轮)
3. 组装 input_items: prompt + scratchpad + memory + history+new_messages + tool_items + activity_status + pending_images
   → 写 model_log (model_logger.log_model_input)
4. 调用 client.responses.create()
   ├─ 流式: _SentenceAccumulator 按 [。！？.!?\n；;] 切句，逐句回调 stream_text_callback
   └─ 非流式: 直接收取
5. 解析 response → reasoning + function_call 入 tool_items；message 通过 on_text_output 回调，并入 conversation_context + memory_batch
6. 启动 tool_calls 为后台 asyncio.Task（不阻塞循环）
7. process_ready_batches → 满 20 条触发记忆批次
8. asyncio.wait(BUFFER_WAIT_TIMEOUT=1s) 等待任务完成或新消息
9. 终止判断: 后台任务全空 + buffer 空 → 退出
   └─ schedule_idle_flush(600s) 启动空闲刷新计时器
```

工具循环上限 `MAX_TOOL_ROUNDS = 10`，触发后强制结束本次 `process_message`。

## 视觉描述管道（外部注入图片）

外部（如助手模式截屏）通过 `session_state.external_pending_images.append({"data_url", "description"})` 注入图片：

1. **当轮**: 图片以多模态 `input_image` 直接送入主模型 → 模型看到原图。
2. **并行**: `_describe_image_for_history` 提交到 `_background_tasks`，调用 `vision_api` 生成中文描述。
3. 描述写入 `conversation_context` 和 `memory_batch`，格式为 `[图片{描述}]`，让**后续轮次**仍能回忆该图。

需 `config.vision_api.enabled = True`，独立 `VisionAPIConfig`（key/url/model）。

## 流式 TTS Bridge: `_SentenceAccumulator`

- 接收 token 流，按句子分隔符 `[。！？.!?\n；;]` 切句。
- 每完成一句调用 `callback(sentence, is_first)`，`is_first` 用于 TTS 引擎切换"首句直送/后续排队"策略。
- 流结束时调用 `flush()` 输出最后未结尾的残段。

## 调试日志

| 日志 | 路径 | 触发 |
|---|---|---|
| 模型 I/O | `data/cache/model_input_logs/model_log_YYYY_MM_DD.txt` | 每轮 model 调用前后 + 工具结果，最多保留 5 个文件 |
| 对话 | `{system.log_dir}/chat_logs/chat_logs_YYYY_MM_DD.txt` | 用户/AI 每条文本输出，含 `[图片{}]` 描述 |

## 与 main.py 的接口契约

```python
lingyi = LingYiCore(main_prompt="")
lingyi._compose_main_prompt(assistant_prompt)  # 模式切换
session = lingyi.get_session_state("default")
await session.input_buffer.put(message=..., caller_message=..., key_words=[...])
await lingyi.process_message("default",
    stream_text_callback=lambda s, is_first: tts.feed(s, is_first),
    on_text_output=lambda t: ui.push(t))
await lingyi.flush_all_pending_memory()  # 关闭时
```

`session.tool_context` 由调用方塞入运行期回调与上下文（`screen_capture_enabled`、`assistant_reply_callback`、QQ 的 `bot/event/session` 等）。

## 调参

| 常量 | 默认 | 含义 | 位置 |
|---|---|---|---|
| `MAX_TOOL_ROUNDS` | 10 | 单次 `process_message` 内最多调用模型的轮次 | [lingyi_core.py](lingyi_core.py) |
| `BUFFER_WAIT_TIMEOUT` | 1.0s | 无新上下文时等待新消息/任务的窗口 | [lingyi_core.py](lingyi_core.py) |
| `TOOL_CALL_TTL` | 3 轮 | 已完成的工具调用在 input_items 中存活轮次 | [lingyi_core.py](lingyi_core.py) |
| `BATCH_MIN_WAIT` / `BATCH_MAX_WAIT` | 2.0 / 5.0s | 入口消息批量收集窗口 | [lingyi_core.py](lingyi_core.py) |
| `MEMORY_IDLE_FLUSH_TIMEOUT` | 600s | 对话结束后强制刷新未保存记忆的空闲超时 | [session_state.py](session_state.py) |
| `MemoryBatchBuffer(trigger_count, batch_size)` | 20 / 25 | 触发条数 / 实际批次窗口（含 overlap） | [session_state.py](session_state.py) |
| `ConversationContext(max_messages)` | 25 | 历史消息滚动窗口 | [session_state.py](session_state.py) |
| `MemoryCache(max_entries)` | 3 | 缓存最近几次记忆搜索结果 | [session_state.py](session_state.py) |
| `_MODEL_LOG_MAX_FILES` | 5 | 模型调试日志保留天数 | [model_logger.py](model_logger.py) |

## 后续可选优化（建议清单）

以下改动**未实施**，仅作记录供后续选择：

1. **`_process_message_inner` 拆分** — 当前 ~250 行混合 6 类职责，可拆 4-5 个内部 helper（`_harvest_completed_tools` / `_intake_new_messages` / `_purge_stale_tool_items` / `_build_input_items` / `_call_model`）。低行为风险但 diff 较大。
2. **同步 SDK 调用 → `asyncio.to_thread`** — `client.responses.create` 与流式 `for event in stream` 是同步阻塞调用，会卡住整个后台 event loop（视觉描述路径已用 `to_thread`，主路径未用）。多 session/多并发场景值得优化；单用户场景影响有限。
3. **`tool_items` 类型统一** — 目前同时存 dict（`function_call_output`）和 SDK 对象（`function_call`/`reasoning`），清理代码用 `isinstance(item, dict)` + `getattr(item, "type", None)` 双判断。统一 `model_dump()` 转 dict 可大幅简化。
4. **`LocalToolRegistry._load_handler` 的 `del sys.modules[...]`** — 强制重载意图（开发期热重载？）需注释或改为单次加载。
5. **两个 `"main-"` 前缀 sub-registry** — 当前 brain/tools 与 agentserver agents 共享同一前缀，依赖名字不冲突；可改为单一 prefix + 内部 dispatch，或在 `register_sub_registry` 中检测 full_name 冲突。

## 本轮重构变更摘要

- 抽出 [model_logger.py](model_logger.py)：`_write_model_log` / `_serialize_*` / `log_model_input` / `log_model_output` / `log_tool_result` 与常量 `_MODEL_LOG_DIR`、`_MODEL_LOG_MAX_FILES` 全部移出，[lingyi_core.py](lingyi_core.py) 行数缩减 ~140。
- 修复 fire-and-forget `asyncio.create_task` 缺少强引用导致可能被 GC 回收的 bug：[lingyi_core.py](lingyi_core.py) 新增 `_background_tasks` 集合 + `_spawn_background()` helper；[session_state.py](session_state.py) 新增 `_memory_tasks` 集合用于记忆批次后台任务。
- 删除 `LingYiCore._dedupe_keywords` 重复实现，统一调用 `MemoryBatchBuffer._dedupe_keywords`。
