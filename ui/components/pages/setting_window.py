# 界面UI main_window 展示设置界面时显示。
# 整体为一个可以下拉的滚动区域，包含多个可设置项。
# 当前无需实装。

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QLabel
from PyQt5.QtCore import Qt


class SettingPage(QWidget):
    """设置页面（当前为占位符）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
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
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(16)

        placeholder = QLabel("⚙  设置页面\n\n功能开发中，敬请期待...")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("""
            QLabel {
                color: rgba(120, 120, 120, 180);
                font-size: 16px;
                background: transparent;
                border: none;
            }
        """)
        content_layout.addWidget(placeholder)
        content_layout.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)