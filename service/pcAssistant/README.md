# pcAssistant 模块

PC 端桌面助手的入口与本地服务集合。负责把 PyQt 聊天 UI、本地语音 I/O、屏幕 OCR、QQ Bot 子进程、记忆云图等服务粘合到 `LingYiCore` 推理上。

> 项目根的 [main.py](../../main.py) 才是当前实际启动入口。本目录下的 [pc_main.py](pc_main.py) 是早期的精简启动器，与 `main.py` 存在大量重复（详见 §6 已知问题）。

---

## 1. 目录结构

```
service/pcAssistant/
├── pc_main.py                       # 早期独立启动器（与 root main.py 重复）
├── service_manager.py               # PCServiceManager —— 本模块的核心编排器
├── plan.md                          # 早期规划草稿（基本为空）
├── prompt/                          # 助手模式专用 prompt（assistant_mode 切换时使用）
│
├── screen_text_extract/             # 屏幕区域 OCR 服务
│   ├── screen_text_extractor.py       # ScreenTextExtractor：mss + PaddleOCR
│   ├── region_selector.py             # 全屏区域框选浮层
│   └── setup.ps1                      # PaddleOCR 一次性初始化脚本
│
├── voice_input_VDL/                 # 麦克风 + VAD + 转写
│   └── vad_voice_input.py             # VoiceInputVDLService
│
├── voice_output/                    # TTS 合成与播放
│   ├── qwen_tts_service.py            # 本地 Qwen3-TTS（默认）
│   ├── stepfun_tts_service.py         # 阶跃星辰 TTS（备选）
│   └── design_voice/                  # 音色克隆/音色设计辅助脚本
│
└── tools/                           # PC 专属工具目录（目前为空）
    └── (send_reply 工具已废弃，助手模式现直接输出文本作为发言，留空则沉默)
```

行数概览（含空行）：

| 文件 | 行数 |
|---|---:|
| [service_manager.py](service_manager.py) | 547 |
| [voice_input_VDL/vad_voice_input.py](voice_input_VDL/vad_voice_input.py) | 468 |
| [voice_output/qwen_tts_service.py](voice_output/qwen_tts_service.py) | 435 |
| [voice_output/stepfun_tts_service.py](voice_output/stepfun_tts_service.py) | 517 |
| [screen_text_extract/screen_text_extractor.py](screen_text_extract/screen_text_extractor.py) | 292 |
| [pc_main.py](pc_main.py) | 213 |

---

## 2. 启动链

```
main.py (root)
 ├─ QApplication / ChatWindow
 ├─ LingYiCore(main_prompt)                     ← brain/lingyi_core
 ├─ ServiceManager()                            ← root main.py 内定义
 │    └─ PCServiceManager()                     ← service_manager.py
 ├─ window.set_chat_callback(chat_with_lingyi_core)
 ├─ window.set_service_manager(service_manager)
 ├─ window.set_screenshot_callback(send_screenshot_to_ai)
 └─ service_manager.start_all_servers()
       ├─ MCP Server (uvicorn, 后台线程)
       ├─ Agent Server (uvicorn, 后台线程)
       └─ 屏幕 OCR / 语音 I/O / QQ / NapCat / Memory Visualizer 按需起停
```

UI 用户消息 → `chat_with_lingyi_core(messages, on_response)` → `session_state.input_buffer.put()` → `LingYiCore.process_message("default", stream_text_callback=...)`，文本通过句子级回调流式回 UI。

---

## 3. PCServiceManager 公共 API

`PCServiceManager` 是其他模块（UI、root `ServiceManager`、`pc_main.py`）唯一需要直面的类。所有方法都做空守护，不会因单个子服务初始化失败影响整体。

### 3.1 语音输入（VAD + Whisper）

```text
is/start/stop/toggle_voice_input() -> bool
set_voice_text_callback(callback: Callable[[str], None])
```

实现：[voice_input_VDL/vad_voice_input.py](voice_input_VDL/vad_voice_input.py)
- 三段流水线（capture → VAD → transcribe），各自一个 daemon 线程
- 自动挑选物理麦克风，过滤虚拟/loopback 设备
- Silero VAD + faster-whisper 离线转写
- TTS 播放期间自动 mute（`_muted` Event），避免回声自激

### 3.2 语音输出（TTS）

```text
is/start/stop/toggle_voice_output() -> bool
interrupt_voice_output() -> str
get_voice_output_service() -> TTSOutputService | None
```

默认 [voice_output/qwen_tts_service.py](voice_output/qwen_tts_service.py)：
- 本地 Qwen3-TTS-12Hz-0.6B-Base，参考音色文件 `LingYiVoice.wav` 实现声音克隆
- worker 线程负责合成，player 线程负责播放队列；可被 `interrupt_voice_output()` 即时打断
- **首次启动会自动从 HuggingFace 下载模型**（可能 1–5 分钟，目前 UI 无进度反馈）

### 3.3 语音交互（I+O 联动）

```text
is/start/stop/toggle_voice_interaction() -> bool
```

按顺序启动 TTS → 注入到 `mcpserver.voice_service.VoiceMCPService` → 启动 VAD → 把 VAD 也注入 VoiceMCPService。停止时反向。

### 3.4 屏幕 OCR

```text
is_screen_capture_enabled() -> bool
is/start/stop/toggle_screen_ocr() -> bool
start_screen_ocr(region, interval, hash_threshold, stable_count)
set_screen_ocr_callback(callback)
select_screen_region() -> tuple | None
set_screen_ocr_region(left, top, width, height)
```

实现：[screen_text_extract/screen_text_extractor.py](screen_text_extract/screen_text_extractor.py)
- mss 抓图 + dHash（9×8 灰度）感知哈希过滤无变化帧
- PaddleOCR 离线识别；区分"过渡态/稳定态"，仅在文本稳定后回调，避免重复推送
- 区域选择由 [region_selector.py](screen_text_extract/region_selector.py) 提供全屏蒙层框选

### 3.5 子进程服务

```text
is/start/stop/toggle_qq() -> bool                       # qqOneBot
is/start/stop_napcat() -> bool                          # NapCat OneBot 代理
is/open/close/toggle_memory_visualizer() -> bool        # 记忆云图浏览
```

均为 `subprocess.Popen` 启动，Windows 下用 `CREATE_NEW_PROCESS_GROUP`。退出时 `cleanup()` → `terminate()` → 3s 超时后 `kill()`。

### 3.6 助手模式

```text
enter_assistant_mode()
exit_assistant_mode()
is_assistant_mode() -> bool
```

切换 `LingYiCore.main_prompt` 为 [prompt/](prompt/) 中的助手专用人格。助手模式下模型可直接输出文本作为发言（流式），留空 message 表示沉默。

---

## 4. 线程 / 异步模型

| 线程 | 来源 | 角色 |
|---|---|---|
| Main (PyQt) | Qt | UI 渲染、信号派发 |
| async-bridge | [pc_main.py](pc_main.py) `_AsyncBridge` | 后台 asyncio loop，把 sync UI 调用桥接到 async lingyi_core |
| voice-vdl-{capture,vad,transcribe} | VAD service | 三段流水线 |
| tts-{worker,player} | TTS service | 合成 / 播放队列 |
| screen-ocr-capture | OCR service | 抓图 + 哈希 + OCR |
| qq / memviz / napcat 子进程 | service_manager | 独立进程 |
| MCP / Agent uvicorn | root main.py | 各自一个后台线程 |

跨线程边界的统一约定：
- 服务线程 → UI：调用 `window.submit_external_message()` / `add_pending_attachment()`，二者内部用 Qt signal 切回 UI 线程。
- UI → asyncio：通过 `bridge.run_sync(coro)`（`asyncio.run_coroutine_threadsafe + future.result()`），目前是**同步阻塞调用**，UI 在等待 lingyi_core 期间不响应。

---

## 5. 与外部模块的耦合点

| 外部 | 触点 | 说明 |
|---|---|---|
| `brain/lingyi_core/lingyi_core.py` | `LingYiCore(main_prompt)`、`get_session_state()`、`process_message()`、`tool_manager.register_sub_registry("pc-", ...)` | 助手 prompt 切换、PC 工具注册 |
| `brain/lingyi_core/session_state.py` | `external_pending_images`, `input_buffer`, `tool_context["screen_capture_enabled"]` | 图片/语音/OCR 文本注入旁路 |
| `mcpserver.voice_service.VoiceMCPService` | `set_tts_service()` / `set_voice_input_service()` | 单例注入；当前用 try/except 静默处理失败 |
| `ui/pyqt_chat_ui.py` `ChatWindow` | `set_service_manager()`、`set_chat_callback()`、`set_screenshot_callback()`、`submit_external_message()`、`add_pending_attachment()`、`trigger_silent_processing()` | UI ↔ 服务双向耦合 |
| `system/config.py` | `config.main_api`、`config.stt`、`config.tts`、`config.vision_api`、`config.screen_ocr` | 全局配置读取 |
| `system/paths.py` | 缓存目录、模型本地目录 | 文件位置统一管理 |

---

## 6. 已知问题与改进建议

按影响排序。**保留现有功能**的前提下都可以渐进式重构。

### 🔴 P1 入口重复：`main.py` vs `pc_main.py`

[pc_main.py](pc_main.py)（213 行）和根目录 [main.py](../../main.py) 各自独立完成几乎相同的初始化：

| 步骤 | main.py | pc_main.py |
|---|---|---|
| QApplication + ChatWindow | ✅ L779 | ✅ L223 |
| LingYiCore | ✅ | ✅ L169 |
| PCServiceManager | ✅ via `ServiceManager` L132 | ✅ L172 |
| chat 回调 | ✅ `chat_with_lingyi_core` | ✅ `_make_chat_callback()` |
| 截屏发 AI | ✅ `send_screenshot_to_ai` | ❌ 未实现 |
| MCP / Agent uvicorn | ✅ | ❌ 未启动 |
| `_extract_reply_text()` | ✅ ~L85 | ✅ L78 重复定义 |

实际启动只走 `main.py`，`pc_main.py` 已是死路且功能落后。

**建议**：把 `pc_main.py` 删除，或改成 `from main import main; main()` 的薄壳。

### 🔴 P1 root `ServiceManager` 包装层冗余

`main.py` 中的 `ServiceManager` 类对 `PCServiceManager` 做了 50+ 个一一对应的 `if self.pc_service_manager: return self.pc_service_manager.xxx()` 包装方法。

**建议**：实现一个 `__getattr__` 委托（或直接让 `ServiceManager` 继承 `PCServiceManager`），消除 ~400 行样板代码；同时将"MCP/Agent uvicorn 启停"作为它独有的职责保留。

### 🟠 P2 `VoiceMCPService` 注入静默失败

[service_manager.py](service_manager.py) 里多处：

```python
try:
    voice_mcp = VoiceMCPService.get_instance()
    voice_mcp.set_tts_service(self._voice_output_service)
except Exception:
    pass   # 注入失败也不报，导致语音交互"半残"
```

**建议**：改成具名异常（`ImportError, AttributeError`）+ `logger.warning` 至少留痕；启动接口返回值反映"语音交互可用 / 仅 TTS 可用 / 不可用"。

### 🟠 P2 子服务 `stop()` 没有强制兜底

`VoiceInputVDLService.stop()` / `qwen_tts_service.stop()` 都用 `thread.join(timeout=2~3s)` 后不再校验。极端情况下旧线程还在跑，再次 `start()` 会创建第二组同名线程。

**建议**：`stop()` 末尾检查 `is_alive()`，若仍存活记日志并标记"未干净关闭"，下一次 `start()` 阻止启动并提示"请重启程序"。

### 🟠 P2 屏幕 OCR 回调同步阻塞捕获循环

[screen_text_extractor.py](screen_text_extract/screen_text_extractor.py) 的 `_tick()` 在抓图线程内同步调用 `text_callback`。回调链最终会把 OCR 文本推入 `input_buffer` + 触发 `LingYiCore.process_message`（虽然真正的模型调用在 asyncio loop 里，但 `asyncio.run` 本身和 `input_buffer.put` 都需要少量时间），慢的话会让屏幕检查卡顿。

**建议**：把 `text_callback` 投递到一个独立的小队列 / 线程池，抓图循环只负责丢消息。

### 🟠 P2 TTS 首次启动隐性下载

[qwen_tts_service.py](voice_output/qwen_tts_service.py) 首次调用会触发 `snapshot_download(...)`，UI 上没有任何进度提示，看起来像卡死。

**建议**：把"模型就绪检查"提到独立的初始化阶段，配合一个进度回调（`progress_callback(downloaded, total)`），UI 弹窗或状态栏显示。

### 🟡 P3 `pc_main.py._AsyncBridge.run_sync` 同步阻塞 UI

UI 在等待 `lingyi.process_message` 返回时整个聊天界面会假死（虽然有 `streaming` 回调推文本，但 `run_sync` 仍是阻塞的）。

**建议**：UI 侧改成 `bridge.run_async(coro, on_done=callback)`，回调里再用 Qt signal 推 UI；或直接用 [qasync](https://github.com/CabbageDevelopment/qasync) 把 Qt loop 和 asyncio loop 合一。

### 🟡 P3 配置在多处分散读取

`config.stt.*`、`config.tts.*` 在 service_manager、vad_voice_input、qwen_tts_service 各读一次，运行时改 `config.json` 不会热加载，且默认值在 `_VadConfig` dataclass 里另存一份。

**建议**：在每个服务的 `__init__` 集中读一次并存到实例字段，`reload()` 方法用于热更新；移除 dataclass 默认值与 pydantic 默认值之间的重复。

### 🟡 P3 子进程的 stdout / stderr 没显式关闭

Windows 下 `Popen(..., stdout=PIPE, stderr=PIPE)` 之后 `terminate()` 但未 `proc.stdout.close()`。长生命周期可能积管道字节。

**建议**：启动时不接管 stdio（`stdout=DEVNULL` 或继承），或在 `cleanup()` 显式 `close()`。

### 🟢 P4 死代码 / 空文件

- [`__init__.py`](__init__.py) 全空，无 `__all__`，没起到模块入口作用。
- [`plan.md`](plan.md) 早期规划，已不反映实际。
- [voice_input_VDL/__init__.py](voice_input_VDL/__init__.py) 仅 3 行无效内容。
- [voice_output/stepfun_tts_service.py](voice_output/stepfun_tts_service.py) 似为备用方案，当前未被默认引用，建议在 README 或注释里明确"备选"状态以免误以为是死代码。

### 🟢 P4 魔数集中化

像 `time.sleep(0.8)`（健康检查）、`timeout=20.0`（OneBot 启动等待）、`speech_confirm_ms=1000` 等散落在多处，建议集中到 `_TIMEOUTS` 常量并加注释说明每个值的来源依据。

---

## 7. 重构路线图（可选）

如果愿意做一次性整理，建议按以下顺序，**每步独立可验证**：

1. **入口合并**：删除 `pc_main.py` 或重写为 `from main import main; main()`。
2. **ServiceManager 瘦身**：用 `__getattr__` 委托替换 50+ 包装方法。
3. **静默 try/except 清理**：grep 全模块的 `except Exception:` / `except: pass`，逐个加日志或换具体异常。
4. **服务关闭安全网**：所有 `stop()` 加 `is_alive()` 校验。
5. **OCR 回调解耦**：单独的派发线程/队列。
6. **TTS 模型预下载**：把初次下载从"首次播放时"提前到"启动语音前"，并暴露进度。
7. **UI ↔ 服务事件总线**：长期目标，把 `submit_external_message` / `service_feedback` / 各类 callback 统一到一个 `EventBus`，便于测试与替换 UI。

---

## 8. 测试关注点

目前模块没有自动化测试。手测重点路径：

1. **语音交互联动**：`toggle_voice_interaction` → 说话被转写 → 模型回复经 TTS 播放 → 期间不应回声自激。
2. **屏幕 OCR**：选区 → 文字稳定后只推送一次；切换游戏画面后能继续推送。
3. **截屏发 AI**：`📷 截屏发AI` → 截图作为 `input_image` 进当前轮 + 异步生成 `[图片{描述}]` 写入历史/记忆。
4. **QQ + NapCat 启停**：先 NapCat 后 QQ，关闭顺序相反，无僵尸进程。
5. **退出清理**：主窗口关闭时所有子线程/子进程退出，无 `data/cache/voice_output/*.tmp` 残留。
