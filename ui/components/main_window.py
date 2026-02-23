# 界面UI main_window设计，为一个位于页面中间的长方形区域（在title_bar下方）：
# 取决于side_bar的按钮点击，main_window可以展示不同的内容，具体内容在pages文件夹中。

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QStackedWidget

from ui.components.pages.chat_window import ChatPage
from ui.components.pages.setting_window import SettingPage


class MainWindow(QWidget):
    """主内容区域：使用 QStackedWidget 切换不同页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget(self)
        self.stack.setStyleSheet("""
            QStackedWidget {
                background: transparent;
                border: none;
            }
        """)

        # 页面 0: 聊天
        self.chat_page = ChatPage(self)
        self.stack.addWidget(self.chat_page)

        # 页面 1: 设置
        self.setting_page = SettingPage(self)
        self.stack.addWidget(self.setting_page)

        layout.addWidget(self.stack)

        # 默认显示聊天页面
        self.stack.setCurrentIndex(0)

    def switch_page(self, index: int):
        """切换到指定页面"""
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)