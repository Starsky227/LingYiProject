"""
屏幕区域框选工具

弹出一个全屏半透明遮罩，用户通过鼠标拖拽选定矩形区域。
选定后返回 (left, top, width, height)。

用法：
    from service.pcAssistant.screen_text_extract.region_selector import select_screen_region
    region = select_screen_region()  # 阻塞直到用户选完或按 Esc 取消
    if region:
        left, top, width, height = region
"""

import sys
from typing import Optional, Tuple

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QPoint, QRect, QEventLoop
from PyQt5.QtGui import QPainter, QColor, QCursor, QPen


class _RegionSelectorOverlay(QWidget):
    """全屏半透明遮罩，用户拖拽选区"""

    def __init__(self):
        super().__init__()
        self._origin: Optional[QPoint] = None
        self._current: Optional[QPoint] = None
        self._result: Optional[Tuple[int, int, int, int]] = None
        self._loop = QEventLoop(self)

        # 无边框 + 置顶 + 全屏
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CrossCursor))
        self.showFullScreen()

    @property
    def result(self) -> Optional[Tuple[int, int, int, int]]:
        return self._result

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        painter = QPainter(self)

        # 半透明黑色遮罩
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        if self._origin and self._current:
            sel = QRect(self._origin, self._current).normalized()

            # 选中区域透明（擦掉遮罩）
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(sel, Qt.transparent)

            # 选框边框
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            pen = QPen(QColor(0, 174, 255), 2, Qt.SolidLine)
            painter.setPen(pen)
            painter.drawRect(sel)

            # 尺寸标注
            w, h = sel.width(), sel.height()
            label = f"{w} × {h}"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(sel.x() + 4, sel.y() - 6, label)

        painter.end()

    # ---------- 鼠标事件 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.globalPos()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event):
        if self._origin:
            self._current = event.globalPos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._origin and self._current:
            sel = QRect(self._origin, self._current).normalized()
            if sel.width() > 10 and sel.height() > 10:
                self._result = (sel.x(), sel.y(), sel.width(), sel.height())
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._result = None
            self.close()

    def closeEvent(self, event):
        self._loop.quit()
        super().closeEvent(event)

    def wait(self) -> Optional[Tuple[int, int, int, int]]:
        """阻塞等待用户完成选区，返回结果"""
        self._loop.exec_()
        return self._result


def select_screen_region() -> Optional[Tuple[int, int, int, int]]:
    """弹出全屏遮罩，让用户框选屏幕区域。

    Returns:
        (left, top, width, height) 或 None（用户按 Esc 取消）
    """
    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv)
        owns_app = True

    overlay = _RegionSelectorOverlay()
    result = overlay.wait()

    if owns_app:
        app.quit()

    return result


# 支持直接运行测试
if __name__ == "__main__":
    region = select_screen_region()
    if region:
        print(f"选定区域: left={region[0]}, top={region[1]}, width={region[2]}, height={region[3]}")
    else:
        print("已取消选择")
