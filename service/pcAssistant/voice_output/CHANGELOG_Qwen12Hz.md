# voice_output — Qwen3-TTS-12Hz 改动记录

## 抹除 SoX 依赖

### 背景

`qwen-tts` 包内部的 **25Hz tokenizer** 路径依赖 SoX（`sox` 命令行工具）进行音频重采样。
在 Windows 上安装 SoX 较为麻烦，且经常出现环境问题。

### 12Hz tokenizer 完全不依赖 SoX

Qwen3-TTS 的 **12Hz** 系列模型（包括 0.6B-Base、0.6B-CustomVoice、1.7B-VoiceDesign 等）
使用的是 12Hz tokenizer，其内部音频处理链路完全基于 Python 原生库（`numpy`/`soundfile`），
**不经过 SoX 代码路径**。因此：

- **无需安装 SoX 二进制文件**（`sox.exe` 或系统 `sox` 包）
- **无需安装 Python `sox` 绑定库**
- `setup.ps1` 中不再包含任何 SoX 相关安装步骤
- 运行时不会出现 `FileNotFoundError: sox` 之类的错误

### 具体改动

1. **`setup.ps1`**
   - 下载 `Qwen/Qwen3-TTS-12Hz-0.6B-Base`
   - 无 SoX 安装步骤
   - 清理旧模型目录（0.6B-CustomVoice、0.6B-CustomVoice-Int4、1.7B-VoiceDesign）

2. **`config.json` / `config.json.example` / `system/config.py`**
   - `tts.model_path` → `Qwen/Qwen3-TTS-12Hz-0.6B-Base`
   - `tts.model_local_dir` → `data/cache/models/Qwen3-TTS-12Hz-0.6B-Base`
   - `tts.default_voice` → `""`（不再需要 speaker 名）

3. **`brain/tools/speak_text/config.json`**
   - 描述更新为 0.6B-Base
   - 移除 `voice`（speaker）和 `instructions` 参数

### VoiceDesign 归档

原 VoiceDesign 相关文件已归档至 `voice_output/design_voice/`：

```
design_voice/
  design_voice.py          # VoiceDesign 独立工具（1.7B-VoiceDesign）
  setup.ps1                # VoiceDesign 专用安装脚本
  source_voice.wav         # VoiceDesign 声源
  LingYiVoice001.wav       # 设计成品
  LingYiVoice002.wav       # 设计成品
  voice_output.wav         # 设计输出
```

该文件夹是独立的实验/设计工具，不影响外层 `QwenTTSOutputService` 的正常运行。
