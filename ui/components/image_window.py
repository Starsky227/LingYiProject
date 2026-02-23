# 界面UI image_window设计，为一个位于页面右侧的长方形区域（在title_bar下方）：
# 用于显示AI的人设图片，图片名称由UI配置中的IMAGE_NAME指定。

import os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap


class ImageWindow(QWidget):
    """右侧图片展示区域：显示 AI 人设图片"""

    IMAGE_RATIO = 0.2  # 占窗口宽度的 20%

    def __init__(self, image_name: str = "", parent=None):
        super().__init__(parent)
        self.setMinimumWidth(150)
        self.setStyleSheet("background: transparent;")
        self._original_pixmap = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.image_label.setStyleSheet("background: transparent;")
        layout.addWidget(self.image_label)

        if image_name:
            self.load_image(image_name)

    def load_image(self, image_name: str):
        """加载并显示指定图片"""
        # 图片路径: ui/img/<image_name>
        current_dir = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.normpath(os.path.join(current_dir, "..", "img", image_name))

        if not os.path.exists(image_path):
            self.image_label.setText("未找到图片")
            self.image_label.setStyleSheet("color: rgba(120,120,120,180); background: transparent;")
            return

        pix = QPixmap(image_path)
        if pix.isNull():
            self.image_label.setText("无法加载图片")
            self.image_label.setStyleSheet("color: rgba(120,120,120,180); background: transparent;")
            return

        self._original_pixmap = pix
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        """根据当前尺寸刷新图片"""
        if self._original_pixmap and not self._original_pixmap.isNull():
            available_w = self.width() - 8
            available_h = self.height() - 8
            scaled = self._original_pixmap.scaled(
                available_w, available_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.image_label.setPixmap(scaled)

    def resizeEvent(self, event):
        """窗口大小变化时重新缩放图片"""
        super().resizeEvent(event)
        self._refresh_pixmap()