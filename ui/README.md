# UI 模块

本模块包含 LingYi AI Agent 项目的用户界面组件。

## 文件结构

```
ui/
├── chat_ui.py      # 主聊天界面组件
├── img/            # 界面图片资源
│   └── LingYi_img.png  # AI头像图片
├── __init__.py     # 包初始化文件
└── README.md       # 本文档
```

## 主要功能

### chat_ui.py
- **ChatUI类**: 基于PyQt5的聊天界面组件
- **功能特性**:
  - 实时聊天对话界面
  - 支持输入栏换行（自适应高度，最多4行）
  - 聊天记录保存（自动记录到日志文件）
  - 流式响应显示支持
  - 支持thinking过程显示
  - 可配置的文本大小和AI名称（需要手动前往config.json）

- **主要方法**:
  - `write_chat_log()`: 将对话记录保存到日志文件
  - `send_message()`: 发送用户消息
  - `update_chat_display()`: 更新聊天显示区域
  - `handle_chunk()`: 处理AI响应的流式数据

### img文件夹
存放界面所需的图片资源：
- `LingYi_img.png`: AI助手的头像图片

## 配置说明

UI模块从 `config.json` 读取以下配置：
- `ui.username`: 用户显示名称
- `ui.text_size`: 界面文本字体大小
- `ui.image_name`: AI头像图片名称
- `system.ai_name`: AI助手名称
- `system.log_dir`: 聊天日志保存目录

## 使用方法

```python
from ui.chat_ui import ChatUI
from PyQt5.QtWidgets import QApplication
import sys

# 创建应用程序
app = QApplication(sys.argv)

# 创建聊天界面（需要传入chat_with_model函数）
def your_chat_function(messages):
    # 你的AI对话逻辑
    pass

chat_ui = ChatUI(your_chat_function, "LingYi")
chat_ui.show()

# 运行应用程序
sys.exit(app.exec_())
```

## 依赖要求

- PyQt5: GUI框架
- 系统配置模块 (`system.config`)

## 日志功能

聊天记录会自动保存到 `{log_dir}/chat_logs/chat_logs_YYYY_MM_DD.txt` 文件中，格式为：
```
[HH:MM:SS] <发送者> 消息内容
```

## 界面特色

- 左侧为主聊天区域，支持滚动查看历史消息
- 右侧显示AI头像
- 底部为输入框，支持回车发送和Shift+回车换行
- 响应式布局，适应不同窗口大小