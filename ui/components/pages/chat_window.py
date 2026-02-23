# 界面UI main_window 展示聊天界面时显示。
# 上方为一个滚动区域，展示聊天记录，QQ风格的气泡对话框：每个气泡框上方用小字显示 <发言人> 发言时间，气泡框内显示发言内容。
# 下方为一个输入区域：
#     输入区域顶部有一个细长的工具栏，从最左侧开始有：上传文件，打开心智云图，开启/关闭语音输入。（当前这三个按钮均无需实装）。最右侧为一个发送按钮。
#     输入区域底部为一个文本输入框，用户可以在其中输入消息内容。文本输入框支持多行输入，按下Enter键可以发送消息（如果需要换行，可以使用Alt+Enter或Shift+Enter）。

import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QScrollArea,
    QPushButton, QLabel, QSizePolicy, QFrame
)
from PyQt5.QtCore import pyqtSignal, Qt, QEvent
from PyQt5.QtGui import QKeyEvent, QFontMetrics, QFont, QColor


class ChatBubble(QFrame):
    """QQ 风格聊天气泡"""

    def __init__(self, sender: str, content: str, timestamp: str, is_self: bool = False, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        # 上方小字：<发言人> 时间
        header = QLabel(f"<{sender}>  {timestamp}")
        header.setStyleSheet("""
            QLabel {
                color: rgba(100, 100, 100, 180);
                font-size: 11px;
                background: transparent;
                border: none;
            }
        """)
        header.setAlignment(Qt.AlignLeft if not is_self else Qt.AlignRight)
        layout.addWidget(header)

        # 气泡内容
        bubble = QLabel(content)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        # 气泡最大宽度为父容器的80%（通过 stretch 比例 1:4 实现）
        if is_self:
            # 用户消息 - 靠右，蓝色气泡
            bubble.setStyleSheet("""
                QLabel {
                    background: rgba(0, 120, 215, 200);
                    color: white;
                    border-radius: 12px;
                    padding: 10px 14px;
                    font-size: 14px;
                    border: none;
                }
            """)
            header.setAlignment(Qt.AlignRight)
            bubble_wrapper = QHBoxLayout()
            bubble_wrapper.setContentsMargins(0, 0, 0, 0)
            bubble_wrapper.addStretch(1)   # 左侧留白 20%
            bubble_wrapper.addWidget(bubble, 4)  # 气泡占 80%
            layout.addLayout(bubble_wrapper)
        else:
            # AI 消息 - 靠左，白底气泡
            bubble.setStyleSheet("""
                QLabel {
                    background: rgba(255, 255, 255, 240);
                    color: rgba(30, 30, 30, 220);
                    border-radius: 12px;
                    padding: 10px 14px;
                    font-size: 14px;
                    border: 1px solid rgba(0, 0, 0, 15);
                }
            """)
            header.setAlignment(Qt.AlignLeft)
            bubble_wrapper = QHBoxLayout()
            bubble_wrapper.setContentsMargins(0, 0, 0, 0)
            bubble_wrapper.addWidget(bubble, 4)  # 气泡占 80%
            bubble_wrapper.addStretch(1)   # 右侧留白 20%
            layout.addLayout(bubble_wrapper)


class ThinkingBubble(QFrame):
    """思考中占位气泡"""

    def __init__(self, ai_name: str, parent=None):
        super().__init__(parent)
        self.ai_name = ai_name
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        header = QLabel(f"<{ai_name}>")
        header.setStyleSheet("""
            QLabel {
                color: rgba(100, 100, 100, 180);
                font-size: 11px;
                background: transparent;
                border: none;
            }
        """)
        layout.addWidget(header)

        self.content_label = QLabel(f"🤔 {ai_name} 正在思考...")
        self.content_label.setWordWrap(True)
        self.content_label.setStyleSheet("""
            QLabel {
                background: rgba(240, 240, 240, 255);
                color: rgba(120, 120, 120, 220);
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 14px;
                font-style: italic;
                border: 1px solid rgba(0, 0, 0, 10);
            }
        """)
        bubble_wrapper = QHBoxLayout()
        bubble_wrapper.setContentsMargins(0, 0, 60, 0)
        bubble_wrapper.addWidget(self.content_label)
        bubble_wrapper.addStretch()
        layout.addLayout(bubble_wrapper)

    def update_thinking(self, thinking_text: str):
        """更新思考内容"""
        display = thinking_text.replace('\n', '<br/>')
        self.content_label.setText(f"🤔 {self.ai_name} 正在思考...\n{thinking_text}")


class ChatPage(QWidget):
    """聊天页面：上方气泡聊天区 + 下方输入区"""

    # 信号：用户发送消息
    message_sent = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ===== 上方：聊天气泡滚动区域 =====
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(0, 0, 0, 15);
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 0, 0, 60);
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(0, 0, 0, 100);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self.chat_content = QWidget()
        self.chat_content.setStyleSheet("background: rgb(240, 240, 242); border: none;")
        self.chat_layout = QVBoxLayout(self.chat_content)
        self.chat_layout.setContentsMargins(8, 8, 8, 8)
        self.chat_layout.setSpacing(4)
        self.chat_layout.addStretch()  # 底部弹簧，让气泡从上往下排列

        self.scroll_area.setWidget(self.chat_content)
        main_layout.addWidget(self.scroll_area, stretch=1)

        # ===== 下方：输入区域 =====
        input_container = QWidget()
        input_container.setStyleSheet("""
            QWidget {
                background: rgba(245, 245, 245, 255);
                border-top: 1px solid rgba(0, 0, 0, 30);
            }
        """)
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(8, 4, 8, 8)
        input_layout.setSpacing(4)

        # -- 工具栏 --
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self.upload_btn = self._make_toolbar_button("📎", "上传文件")
        self.mindmap_btn = self._make_toolbar_button("🧠", "心智云图")
        self.voice_btn = self._make_toolbar_button("🎤", "语音输入")

        toolbar.addWidget(self.upload_btn)
        toolbar.addWidget(self.mindmap_btn)
        toolbar.addWidget(self.voice_btn)
        toolbar.addStretch()

        self.send_btn = QPushButton("发送")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setFixedHeight(30)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 120, 215, 230);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 4px 16px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(0, 100, 200, 255);
            }
            QPushButton:pressed {
                background: rgba(0, 80, 170, 255);
            }
        """)
        self.send_btn.clicked.connect(self._on_send)
        toolbar.addWidget(self.send_btn)

        input_layout.addLayout(toolbar)

        # -- 文本输入框 --
        self.input_field = QTextEdit()
        self.input_field.setAcceptRichText(False)
        self.input_field.setPlaceholderText("输入消息，按 Enter 发送，Shift+Enter 换行...")
        self.input_field.setStyleSheet("""
            QTextEdit {
                background: rgba(255, 255, 255, 255);
                color: rgba(30, 30, 30, 240);
                border: 1px solid rgba(0, 0, 0, 30);
                border-radius: 8px;
                padding: 8px;
                font-size: 14px;
                selection-background-color: rgba(0, 120, 215, 100);
            }
            QTextEdit:focus {
                border: 1px solid rgba(0, 120, 215, 200);
            }
        """)

        # 高度自适应 (4~8行)
        font_metrics = QFontMetrics(self.input_field.font())
        self._single_line_height = font_metrics.lineSpacing()
        self._v_padding = 20
        self._min_input_height = max(120, int(self._single_line_height * 4 + self._v_padding))
        self._max_input_height = int(self._single_line_height * 8 + self._v_padding)
        self.input_field.setFixedHeight(self._min_input_height)
        self.input_field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.input_field.textChanged.connect(self._adjust_input_height)
        self.input_field.installEventFilter(self)

        input_layout.addWidget(self.input_field)
        main_layout.addWidget(input_container)

        # 思考中气泡引用
        self._thinking_bubble = None

    # ---- 工具栏按钮工厂 ----
    def _make_toolbar_button(self, icon_text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(icon_text)
        btn.setFixedSize(30, 30)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(80, 80, 80, 200);
                border: none;
                border-radius: 6px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 20);
                color: rgba(30, 30, 30, 255);
            }
        """)
        return btn

    # ---- 输入框高度自适应 ----
    def _adjust_input_height(self):
        doc = self.input_field.document()
        block_count = max(1, doc.blockCount())
        lines = min(block_count, 8)
        new_h = int(self._single_line_height * lines + self._v_padding)
        new_h = max(self._min_input_height, min(new_h, self._max_input_height))
        if self.input_field.height() != new_h:
            self.input_field.setFixedHeight(new_h)

    # ---- 事件过滤：Enter 发送，Shift/Alt+Enter 换行 ----
    def eventFilter(self, obj, event):
        if obj is self.input_field and isinstance(event, QKeyEvent) and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & (Qt.ShiftModifier | Qt.AltModifier):
                    cursor = self.input_field.textCursor()
                    cursor.insertText("\n")
                    return True
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    # ---- 发送消息 ----
    def _on_send(self):
        text = self.input_field.toPlainText().strip()
        if not text:
            return
        self.input_field.clear()
        self._adjust_input_height()
        self.message_sent.emit(text)

    # ---- 公共接口：添加聊天气泡 ----
    def add_bubble(self, sender: str, content: str, timestamp: str = None, is_self: bool = False):
        """添加一条聊天气泡"""
        if timestamp is None:
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        bubble = ChatBubble(sender, content, timestamp, is_self, self.chat_content)
        # 在 stretch 之前插入
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        # 滚动到底部
        self._scroll_to_bottom()

    def show_thinking(self, ai_name: str):
        """显示思考中占位气泡"""
        if self._thinking_bubble is not None:
            return
        self._thinking_bubble = ThinkingBubble(ai_name, self.chat_content)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._thinking_bubble)
        self._scroll_to_bottom()

    def update_thinking(self, thinking_text: str):
        """更新思考内容"""
        if self._thinking_bubble:
            self._thinking_bubble.update_thinking(thinking_text)
            self._scroll_to_bottom()

    def remove_thinking(self):
        """移除思考中占位气泡"""
        if self._thinking_bubble:
            self.chat_layout.removeWidget(self._thinking_bubble)
            self._thinking_bubble.deleteLater()
            self._thinking_bubble = None

    def _scroll_to_bottom(self):
        """滚动到最底部"""
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))