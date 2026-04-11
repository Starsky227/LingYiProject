# UI 用户界面模块

基于 PyQt5 的桌面聊天界面，采用组件化设计，包含标题栏、侧边导航、聊天主窗口、AI立绘面板等。

## 文件结构

```
ui/
├── pyqt_chat_ui.py          # ChatWindow 主入口组件
├── components/
│   ├── main_window.py       # 中央内容区（QStackedWidget 页面切换）
│   ├── side_bar.py          # 左侧导航栏（💬聊天 / ⚙设置）
│   ├── title_bar.py         # 自定义标题栏（拖拽移动、最小化/关闭）
│   ├── image_window.py      # 右侧AI立绘面板 + 服务控制按钮
│   └── pages/
│       ├── chat_window.py   # 聊天页面
│       └── setting_window.py # 设置页面
├── img/                     # 界面图片资源
│   └── LingYi_img.png       # AI头像
└── __init__.py
```

## 核心组件

### pyqt_chat_ui.py - ChatWindow 主入口
- 组合所有UI组件：TitleBar（上）| SideBar（左）+ MainWindow（中）+ ImageWindow（右）
- Qt 信号：`chunk_received`、`thinking_received`（线程安全的流式更新）
- 对话日志自动写入 `data/chat_logs/` 目录

### components/image_window.py - 立绘与服务面板
- 显示AI人格形象图片（占窗口 20% 宽度）
- 服务控制按钮：QQ Bot、截图、语音输入、语音输出、桌宠模式
- 发射 `service_toggled(service_name, is_running)` 信号控制后台服务

### components/side_bar.py - 侧边导航
- 64px 宽度竖排按钮，切换聊天/设置页面
- 发射 `page_switched` 信号

### components/title_bar.py - 自定义标题栏
- 50px 高度，渐变背景，支持拖拽移动窗口
- 最小化、关闭按钮

## 配置说明

从 `config.json` 读取：
- `ui.username` / `ui.text_size` / `ui.image_name`
- `system.ai_name` / `system.log_dir`

## 日志功能

对话记录自动保存到 `data/chat_logs/chat_logs_YYYY_MM_DD.txt`：
```
[HH:MM:SS] <发送者> 消息内容
```