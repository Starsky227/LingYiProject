# 界面UI title_bar设计，为一个位于页面顶端的横向细长条：
# 最左侧显示[AI名称]的聊天窗口
# 最右侧拥有两个按钮：缩小至托盘、关闭

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QPainter, QLinearGradient, QColor, QBrush, QPainterPath


class TitleBar(QWidget):
    """自定义标题栏：左侧显示标题，右侧显示最小化和关闭按钮，支持拖动窗口"""

    TITLE_BAR_HEIGHT = 50

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._parent_window = parent
        self._drag_pos = None
        self._is_dragging = False
        self.setFixedHeight(self.TITLE_BAR_HEIGHT)
        self.setAutoFillBackground(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(4)

        # 左侧标题
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                font-weight: bold;
                background: transparent;
            }
        """)
        layout.addWidget(self.title_label)

        layout.addStretch()

        # 缩小至托盘按钮
        self.minimize_btn = QPushButton("─")
        self.minimize_btn.setFixedSize(36, 36)
        self.minimize_btn.setCursor(Qt.PointingHandCursor)
        self.minimize_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 30);
                color: rgba(255, 255, 255, 220);
                border: none;
                border-radius: 18px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 60);
                color: white;
            }
        """)
        self.minimize_btn.clicked.connect(self._on_minimize)
        layout.addWidget(self.minimize_btn)

        # 关闭按钮
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(36, 36)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 30);
                color: rgba(255, 255, 255, 220);
                border: none;
                border-radius: 18px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(220, 50, 50, 220);
                color: white;
            }
        """)
        self.close_btn.clicked.connect(self._on_close)
        layout.addWidget(self.close_btn)

    def _on_minimize(self):
        """最小化到托盘/任务栏"""
        if self._parent_window:
            self._parent_window.showMinimized()

    def _on_close(self):
        """关闭窗口"""
        if self._parent_window:
            self._parent_window.close()

    # ---- 拖动窗口支持 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_pos = event.globalPos() - self._parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._is_dragging and event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self._parent_window.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._is_dragging = False
        self._drag_pos = None

    def set_title(self, title: str):
        """更新标题文字"""
        self.title_label.setText(title)

    def paintEvent(self, event):
        """绘制蓝色渐变背景（带圆角顶部）"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 蓝色渐变
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, QColor(0, 100, 210))
        gradient.setColorAt(1, QColor(0, 140, 235))

        # 路径：顶部圆角，底部直角
        path = QPainterPath()
        r = 20  # 圆角半径
        rect = self.rect()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + r)
        path.arcTo(rect.left(), rect.top(), r * 2, r * 2, 180, -90)
        path.lineTo(rect.right() - r, rect.top())
        path.arcTo(rect.right() - r * 2, rect.top(), r * 2, r * 2, 90, -90)
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)