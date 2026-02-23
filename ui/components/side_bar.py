# 界面UI side_bar设计，为一个位于页面左侧的垂直细长条（在title_bar下方）：
# 具有多个可点击的按钮，可以用来main_window展示的内容。
# 1. 聊天按钮：使main_window显示聊天界面。
# 2. 设置按钮：使main_window显示设置界面。

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QPainter, QColor, QBrush


class SideBar(QWidget):
    """侧边栏：提供页面切换按钮"""

    # 信号：page_index (0=聊天, 1=设置)
    page_switched = pyqtSignal(int)

    SIDEBAR_WIDTH = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.SIDEBAR_WIDTH)
        # 背景色通过 paintEvent 绘制，避免被父窗口覆盖
        self._bg_color = QColor(230, 235, 245, 255)
        self._border_color = QColor(0, 0, 0, 20)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(8)

        # 聊天按钮
        self.chat_btn = self._make_button("💬", "聊天")
        self.chat_btn.setChecked(True)
        self.chat_btn.clicked.connect(lambda: self._switch_page(0))
        layout.addWidget(self.chat_btn)

        # 设置按钮
        self.settings_btn = self._make_button("⚙", "设置")
        self.settings_btn.clicked.connect(lambda: self._switch_page(1))
        layout.addWidget(self.settings_btn)

        layout.addStretch()

        self._buttons = [self.chat_btn, self.settings_btn]
        self._current_index = 0

    def _make_button(self, icon_text: str, tooltip: str) -> QPushButton:
        """创建侧边栏按钮"""
        btn = QPushButton(icon_text)
        btn.setFixedSize(48, 48)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(80, 80, 80, 200);
                border: none;
                border-radius: 12px;
                font-size: 22px;
            }
            QPushButton:hover {
                background: rgba(0, 120, 215, 30);
                color: rgba(0, 100, 200, 255);
            }
            QPushButton:checked {
                background: rgba(0, 120, 215, 50);
                color: rgba(0, 100, 200, 255);
            }
        """)
        return btn

    def _switch_page(self, index: int):
        """切换页面并更新按钮状态"""
        if index == self._current_index:
            # 保持当前按钮选中
            self._buttons[index].setChecked(True)
            return
        self._current_index = index
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        self.page_switched.emit(index)

    def paintEvent(self, event):
        """绘制侧边栏背景和右侧边框"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._bg_color))
        painter.drawRect(self.rect())
        # 右侧边框线
        painter.setPen(self._border_color)
        painter.drawLine(self.rect().topRight(), self.rect().bottomRight())