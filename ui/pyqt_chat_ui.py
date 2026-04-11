# 界面UI设计：
# 最顶端是一条细长的title_bar
# 下方分为三个部分：左侧的side_bar，中间的main_window，右侧的image_window

import os
import sys
import datetime
import threading

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + '/..'))

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QDesktopWidget
from PyQt5.QtCore import pyqtSignal, Qt, QRect, QPoint
from PyQt5.QtGui import QPainter, QBrush, QColor, QCursor

from system.config import config, logger
from ui.components.title_bar import TitleBar
from ui.components.side_bar import SideBar
from ui.components.main_window import MainWindow
from ui.components.image_window import ImageWindow


# 读取UI配置
def get_ui_config():
    """获取UI配置，确保使用最新的配置值"""
    return {
        'AI_NAME': config.system.ai_name,
        'USERNAME': config.system.user_name,
        'TEXT_SIZE': config.ui.text_size,
        'IMAGE_NAME': config.ui.image_name,
    }


def refresh_ui_constants():
    """刷新UI常量，确保使用最新的配置值"""
    global AI_NAME, USERNAME, TEXT_SIZE, IMAGE_NAME
    AI_NAME = config.system.ai_name
    USERNAME = config.system.user_name
    TEXT_SIZE = config.ui.text_size
    IMAGE_NAME = config.ui.image_name


refresh_ui_constants()


# ---- 聊天日志 ----
LOGS_DIR = config.system.log_dir


def write_chat_log(sender: str, text: str, timestamp: str = None):
    """将单条对话追加到 chat_logs 目录"""
    try:
        logs_dir = os.path.join(LOGS_DIR, "chat_logs")
        os.makedirs(logs_dir, exist_ok=True)
        filename = datetime.datetime.now().strftime("chat_logs_%Y_%m_%d.txt")
        path = os.path.join(logs_dir, filename)

        if timestamp is None:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
        else:
            try:
                dt = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                ts = dt.strftime("%H:%M:%S")
            except (ValueError, AttributeError):
                ts = datetime.datetime.now().strftime("%H:%M:%S")

        safe_text = text.replace("\r", " ").replace("\n", " ")
        line = f"[{ts}] <{sender}> {safe_text}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[日志错误] 无法写入聊天日志: {e}")


class ChatWindow(QWidget):
    """主窗口：title_bar / side_bar / main_window / image_window 组装"""

    # 线程安全信号
    chunk_received = pyqtSignal(str)
    thinking_received = pyqtSignal(str)
    finished_reply = pyqtSignal()
    external_user_message = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        config.window = self
        self.messages = []
        self.chat_with_model = None  # 由外部注入
        self._service_manager = None

        self._setup_window()
        self._setup_layout()
        self._connect_signals()

    # 边缘拖拽调整大小的热区像素
    EDGE_MARGIN = 8
    MIN_WIDTH = 600
    MIN_HEIGHT = 400

    def _setup_window(self):
        """初始化窗口属性：大小、位置、无边框"""
        desktop = QDesktopWidget()
        screen = desktop.screenGeometry()
        self.window_width = int(screen.width() * 0.6)
        self.window_height = int(screen.height() * 0.6)
        self.resize(self.window_width, self.window_height)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)

        # 居中
        x = (screen.width() - self.window_width) // 2
        y = (screen.height() - self.window_height) // 2
        self.move(x, y)

        # 无边框 + 透明背景
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # 拖拽调整大小的状态
        self._resizing = False
        self._resize_edge = None  # 'left','right','top','bottom','tl','tr','bl','br'
        self._resize_start_pos = None
        self._resize_start_geo = None
        # image_window 初始宽度占比
        self._image_width_ratio = 0.2

    def paintEvent(self, event):
        """绘制圆角白色背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
        painter.setPen(QColor(0, 0, 0, 30))
        painter.drawRoundedRect(self.rect(), 20, 20)

    # ---- 边缘拖拽调整窗口大小 ----
    def _edge_at(self, pos):
        """判断鼠标位于哪条边缘，返回边缘标识或 None"""
        m = self.EDGE_MARGIN
        r = self.rect()
        x, y = pos.x(), pos.y()

        left = x < m
        right = x > r.width() - m
        top = y < m
        bottom = y > r.height() - m

        if top and left:
            return 'tl'
        if top and right:
            return 'tr'
        if bottom and left:
            return 'bl'
        if bottom and right:
            return 'br'
        if left:
            return 'left'
        if right:
            return 'right'
        if top:
            return 'top'
        if bottom:
            return 'bottom'
        return None

    def _cursor_for_edge(self, edge):
        if edge in ('left', 'right'):
            return Qt.SizeHorCursor
        if edge in ('top', 'bottom'):
            return Qt.SizeVerCursor
        if edge in ('tl', 'br'):
            return Qt.SizeFDiagCursor
        if edge in ('tr', 'bl'):
            return Qt.SizeBDiagCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edge = self._edge_at(event.pos())
            if edge:
                self._resizing = True
                self._resize_edge = edge
                self._resize_start_pos = event.globalPos()
                self._resize_start_geo = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_edge:
            delta = event.globalPos() - self._resize_start_pos
            geo = QRect(self._resize_start_geo)
            e = self._resize_edge

            if 'right' in e or e == 'tr' or e == 'br':
                geo.setRight(geo.right() + delta.x())
            if 'left' in e or e == 'tl' or e == 'bl':
                geo.setLeft(geo.left() + delta.x())
            if 'bottom' in e or e == 'bl' or e == 'br':
                geo.setBottom(geo.bottom() + delta.y())
            if 'top' in e or e == 'tl' or e == 'tr':
                geo.setTop(geo.top() + delta.y())

            # 最小尺寸约束
            if geo.width() < self.MIN_WIDTH:
                if 'left' in e or e == 'tl' or e == 'bl':
                    geo.setLeft(geo.right() - self.MIN_WIDTH)
                else:
                    geo.setRight(geo.left() + self.MIN_WIDTH)
            if geo.height() < self.MIN_HEIGHT:
                if 'top' in e or e == 'tl' or e == 'tr':
                    geo.setTop(geo.bottom() - self.MIN_HEIGHT)
                else:
                    geo.setBottom(geo.top() + self.MIN_HEIGHT)

            self.setGeometry(geo)
            # 按比例调整 image_window 宽度
            if hasattr(self, 'image_window'):
                sidebar_w = self.sidebar.width()
                available = self.width() - sidebar_w
                img_w = max(150, int(available * self._image_width_ratio))
                self.image_window.setFixedWidth(img_w)
            event.accept()
            return

        # 非拖拽时更新光标
        edge = self._edge_at(event.pos())
        self.setCursor(self._cursor_for_edge(edge))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            self._resize_edge = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _setup_layout(self):
        """构建整体布局：顶部标题栏 + 下方三栏（侧边栏 | 主内容 | 图片）"""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 标题栏
        self.titlebar = TitleBar(f'[{AI_NAME}]的聊天窗口', self)
        root_layout.addWidget(self.titlebar)

        # 下方容器
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # 左侧侧边栏
        self.sidebar = SideBar()
        body_layout.addWidget(self.sidebar)

        # 中间主内容
        self.main_window = MainWindow()
        body_layout.addWidget(self.main_window, stretch=1)

        # 右侧图片（按比例设置初始宽度）
        self.image_window = ImageWindow(IMAGE_NAME)
        sidebar_w = self.sidebar.width()
        available = self.width() - sidebar_w
        img_w = max(150, int(available * self._image_width_ratio))
        self.image_window.setFixedWidth(img_w)
        body_layout.addWidget(self.image_window)

        root_layout.addWidget(body, stretch=1)

    def _connect_signals(self):
        """连接组件间信号"""
        # 侧边栏切换页面
        self.sidebar.page_switched.connect(self.main_window.switch_page)

        # 聊天页面发送消息
        chat_page = self.main_window.chat_page
        chat_page.message_sent.connect(self._on_user_send)

        # 线程安全信号
        self.chunk_received.connect(self._on_chunk)
        self.thinking_received.connect(self._on_thinking)
        self.finished_reply.connect(self._on_finish_reply)
        self.external_user_message.connect(self._on_user_send)

    # ---- 外部注入模型调用 ----
    def set_chat_callback(self, chat_with_model):
        """注入聊天回调函数"""
        self.chat_with_model = chat_with_model

    def set_service_manager(self, service_manager):
        """注入服务管理器到图片窗口，显示服务按钮"""
        self._service_manager = service_manager
        if hasattr(self, 'image_window') and service_manager:
            self.image_window.set_service_manager(service_manager)
            try:
                self.image_window.service_feedback.disconnect(self._on_service_feedback)
            except Exception:
                pass
            self.image_window.service_feedback.connect(self._on_service_feedback)

        try:
            voice_btn = self.main_window.chat_page.voice_btn
            voice_btn.clicked.connect(self._toggle_voice_input)
        except Exception as e:
            print(f"[ChatWindow] 绑定语音按钮失败: {e}")

    def _toggle_voice_input(self):
        if not self._service_manager:
            print("[ChatWindow] 服务管理器未初始化")
            return
        try:
            self._service_manager.toggle_voice_input()
        except Exception as e:
            print(f"[ChatWindow] 切换语音输入失败: {e}")

    def submit_external_message(self, text: str) -> None:
        """线程安全地将外部输入（如语音转写）送入聊天流程。"""
        clean = (text or "").strip()
        if clean:
            self.external_user_message.emit(clean)

    def _on_service_feedback(self, text: str) -> None:
        """显示服务按钮点击反馈（仅显示气泡，不进入上下文/记忆/日志）。"""
        clean = (text or "").strip()
        if not clean:
            return
        timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")
        chat_page = self.main_window.chat_page
        chat_page.add_bubble("系统", clean, timestamp_str, is_self=False)

    # ---- 用户发送消息 ----
    def _on_user_send(self, text: str):
        """用户点击发送 / 按 Enter"""
        if self._service_manager and hasattr(self._service_manager, "interrupt_voice_output"):
            try:
                self._service_manager.interrupt_voice_output()
            except Exception as e:
                print(f"[ChatWindow] 打断语音输出失败: {e}")

        timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")
        user_timestamp = datetime.datetime.now().isoformat(timespec='milliseconds')

        # 写日志
        write_chat_log(USERNAME, text)

        # 添加气泡
        chat_page = self.main_window.chat_page
        chat_page.add_bubble(USERNAME, text, timestamp_str, is_self=True)

        # 记录消息
        self.messages.append({
            "role": USERNAME,
            "content": text,
            "timestamp": user_timestamp,
        })

        # 显示思考中
        chat_page.show_thinking(AI_NAME)

        # 工作线程调用模型
        if self.chat_with_model:
            threading.Thread(target=self._worker_call_model, daemon=True).start()

    # ---- 模型流式回调 ----
    def _on_chunk(self, chunk: str):
        """收到模型文本块 → 移除思考气泡，添加 AI 回复气泡"""
        chat_page = self.main_window.chat_page
        chat_page.remove_thinking()
        # 追加到最后一个 AI 气泡（如果已有则合并）
        # 简化：每次 chunk 都在结束时统一添加完整气泡
        if not hasattr(self, '_current_reply'):
            self._current_reply = ""
        self._current_reply += chunk

    def _on_thinking(self, thinking_text: str):
        """更新思考内容"""
        chat_page = self.main_window.chat_page
        chat_page.update_thinking(thinking_text)

    def _on_finish_reply(self):
        """模型回复完成"""
        chat_page = self.main_window.chat_page
        chat_page.remove_thinking()

        reply = getattr(self, '_current_reply', "")
        if reply:
            timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")
            chat_page.add_bubble(AI_NAME, reply, timestamp_str, is_self=False)
        self._current_reply = ""

    # ---- 工作线程 ----
    def _worker_call_model(self):
        """在工作线程中调用模型"""
        self._current_reply = ""
        thinking_mode = False

        def on_response(chunk: str):
            nonlocal thinking_mode
            if "<thinking>" in chunk:
                thinking_mode = True
                before = chunk.split("<thinking>")[0]
                if before:
                    self.chunk_received.emit(before)
                thinking_content = chunk.split("<thinking>", 1)[1] if "<thinking>" in chunk else ""
                if "</thinking>" in thinking_content:
                    thinking_text = thinking_content.split("</thinking>")[0]
                    if thinking_text:
                        self.thinking_received.emit(thinking_text)
                    after = thinking_content.split("</thinking>", 1)[1]
                    if after:
                        self.chunk_received.emit(after)
                    thinking_mode = False
                else:
                    if thinking_content:
                        self.thinking_received.emit(thinking_content)
            elif "</thinking>" in chunk and thinking_mode:
                thinking_text = chunk.split("</thinking>")[0]
                if thinking_text:
                    self.thinking_received.emit(thinking_text)
                after = chunk.split("</thinking>", 1)[1]
                if after:
                    self.chunk_received.emit(after)
                thinking_mode = False
            elif thinking_mode:
                self.thinking_received.emit(chunk)
            else:
                self.chunk_received.emit(chunk)

        response_messages = self.chat_with_model(self.messages, on_response)

        # 提取回复内容
        reply_content = ""
        if response_messages and isinstance(response_messages, list):
            for msg in reversed(response_messages):
                if msg.get("role") == "assistant":
                    reply_content = msg.get("content", "")
                    break
            # 记录日志
            for msg in response_messages:
                msg_role = msg.get("role", "")
                msg_content = msg.get("content", "").strip()
                msg_timestamp = msg.get("timestamp")
                if msg_content:
                    if msg_role == "assistant":
                        write_chat_log(AI_NAME, msg_content, msg_timestamp)
                    else:
                        write_chat_log(msg_role, msg_content, msg_timestamp)
        elif isinstance(response_messages, str) and response_messages.strip():
            reply_content = response_messages
            write_chat_log(AI_NAME, reply_content)

        assistant_timestamp = datetime.datetime.now().isoformat(timespec='milliseconds')
        self.messages.append({
            "role": "assistant",
            "content": reply_content,
            "timestamp": assistant_timestamp,
        })
        self.finished_reply.emit()


if __name__ == "__main__":
    # 单独激活UI界面进行调试
    import time
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)

    window = ChatWindow()
    window.show()

    # 模拟模型回调：收到用户消息后延迟返回固定回复
    def fake_chat_with_model(messages, on_response):
        """模拟模型流式回复，用于测试 UI"""
        time.sleep(0.5)  # 模拟思考延迟

        # 模拟 thinking
        on_response("<thinking>让我想想该怎么回答这个问题...</thinking>")
        time.sleep(0.3)

        # 模拟流式输出
        reply = "你好！这是一条测试回复。\n我是你的 AI 助手，当前处于 UI 调试模式。"
        for char in reply:
            on_response(char)
            time.sleep(0.02)

        return [{"role": "assistant", "content": reply, "timestamp": datetime.datetime.now().isoformat()}]

    window.set_chat_callback(fake_chat_with_model)

    # 预填几条示例气泡，方便查看视觉效果
    chat_page = window.main_window.chat_page
    chat_page.add_bubble(USERNAME, "你好，请做一下自我介绍。", "12:00:01", is_self=True)
    chat_page.add_bubble(AI_NAME, "你好！我是玲依，一个 AI 助手。当前在 UI 调试窗口，并非实际对话哦？", "12:00:03", is_self=False)
    chat_page.add_bubble(USERNAME, "你能做些什么？", "12:01:15", is_self=True)
    chat_page.add_bubble(AI_NAME, "现在我的功能无一例外全部都在开发中呢……", "12:01:18", is_self=False)

    sys.exit(app.exec_())