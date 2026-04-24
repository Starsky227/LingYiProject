# 桌宠模式窗口
# 点击桌宠按钮后，隐藏主窗口，显示一个始终置顶的可拖拽小窗口。
# 鼠标悬浮时左侧浮现功能按钮面板。

import ctypes
import ctypes.wintypes
import sys
import os
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect,
)
from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QPoint, pyqtSignal, QSize, QEasingCurve,
)
from PyQt5.QtGui import QPixmap, QCursor, QFont, QFontMetrics


# ------------------------------------------------------------------ #
#  气泡标签 — 单条消息气泡，10 秒后自动淡出消失
# ------------------------------------------------------------------ #

class _BubbleLabel(QLabel):
    """一个悬浮在助手图片上方的消息气泡"""

    removed = pyqtSignal(object)  # 自身被移除时发出

    BUBBLE_LIFETIME_MS = 10_000  # 10 秒显示时间
    FADE_DURATION_MS = 400       # 淡出动画时长
    MAX_BUBBLE_WIDTH = 260       # 气泡最大宽度（含 padding）
    PADDING_H = 12               # 与 stylesheet padding 保持一致
    PADDING_V = 8
    BORDER = 1

    def __init__(self, text: str, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)
        self.setFont(QFont("Microsoft YaHei", 9))
        self.setStyleSheet("""
            QLabel {
                background: rgba(255, 255, 255, 230);
                border: 1px solid rgba(180, 190, 210, 150);
                border-radius: 10px;
                padding: 8px 12px;
                color: #333;
            }
        """)

        # 阴影效果
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(Qt.gray)
        self.setGraphicsEffect(shadow)

        # 用 QFontMetrics 自己算宽高：
        # - 单行能放下 → 宽度 = 实际文本宽度（短文本不被拉伸）
        # - 超过最大宽 → 锁定到 MAX_BUBBLE_WIDTH 并按 word-wrap 计算所需高度
        fm = QFontMetrics(self.font())
        chrome_w = (self.PADDING_H + self.BORDER) * 2  # 左右 padding + 边框
        chrome_h = (self.PADDING_V + self.BORDER) * 2
        max_text_w = self.MAX_BUBBLE_WIDTH - chrome_w

        single_line_w = fm.horizontalAdvance(text)
        if single_line_w <= max_text_w and "\n" not in text:
            text_w = single_line_w
        else:
            text_w = max_text_w
        text_rect = fm.boundingRect(
            0, 0, text_w, 0,
            Qt.TextWordWrap | Qt.TextWrapAnywhere,
            text,
        )
        # +2 给字体 hinting 的安全裕量，避免末字被裁
        self.setFixedSize(text_rect.width() + chrome_w + 2,
                          text_rect.height() + chrome_h + 2)
        self.show()

        # 淡出动画用的透明度效果（和阴影分开，套在外层不行，直接用 windowOpacity 替代）
        self._opacity = 1.0

        # 生命周期计时器
        self._life_timer = QTimer(self)
        self._life_timer.setSingleShot(True)
        self._life_timer.setInterval(self.BUBBLE_LIFETIME_MS)
        self._life_timer.timeout.connect(self._start_fade_out)
        self._life_timer.start()

    def _start_fade_out(self):
        """开始淡出动画"""
        self._fade_timer = QTimer(self)
        self._fade_step = 0
        self._fade_steps = max(1, self.FADE_DURATION_MS // 30)
        self._fade_timer.setInterval(30)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_timer.start()

    def _fade_tick(self):
        self._fade_step += 1
        self._opacity = max(0.0, 1.0 - self._fade_step / self._fade_steps)
        # 直接设置样式透明度
        bg_alpha = int(230 * self._opacity)
        border_alpha = int(150 * self._opacity)
        text_alpha = int(255 * self._opacity)
        self.setStyleSheet(f"""
            QLabel {{
                background: rgba(255, 255, 255, {bg_alpha});
                border: 1px solid rgba(180, 190, 210, {border_alpha});
                border-radius: 10px;
                padding: 8px 12px;
                color: rgba(51, 51, 51, {text_alpha});
            }}
        """)
        if self._fade_step >= self._fade_steps:
            self._fade_timer.stop()
            self.removed.emit(self)
            self.deleteLater()


class PetModeWindow(QWidget):
    """助手模式 — 始终置顶的可拖拽小窗口，含悬浮按钮面板"""

    # 信号
    exit_pet_mode = pyqtSignal()
    screenshot_to_ai = pyqtSignal()
    service_feedback = pyqtSignal(str)

    PET_SIZE = 200  # 助手模式图片区域大小

    def __init__(self, pixmap: QPixmap, service_manager=None, parent=None):
        super().__init__(parent)
        self._service_manager = service_manager
        self._original_pixmap = pixmap
        self._dragging = False
        self._drag_start_pos = QPoint()
        self._active_bubbles: list[_BubbleLabel] = []  # 当前显示的气泡列表

        self._setup_window()
        self._setup_ui()
        self._start_topmost_timer()
        self._start_poll_timer()

    # ------------------------------------------------------------------ #
    #  窗口属性
    # ------------------------------------------------------------------ #

    def _setup_window(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # 不出现在任务栏
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # 初始位置：屏幕右下角（以图片位置为准）
        from PyQt5.QtWidgets import QDesktopWidget
        screen = QDesktopWidget().availableGeometry()
        panel_width = 44
        x = screen.right() - self.PET_SIZE - panel_width - 60
        y = screen.bottom() - self.PET_SIZE - 100
        self.move(x, y)

    # ------------------------------------------------------------------ #
    #  UI 布局：[按钮面板] [图片]
    # ------------------------------------------------------------------ #

    def _setup_ui(self):
        # 不使用布局管理器，改用绝对定位，避免按钮面板影响图片位置
        # 窗口固定大小：按钮面板宽度 + 图片区域
        panel_width = 44  # 按钮 36px + 左右边距 8px
        self.setFixedSize(panel_width + self.PET_SIZE, self.PET_SIZE)

        # 右侧图片（固定在窗口右侧）
        self._image_label = QLabel(self)
        self._image_label.setFixedSize(self.PET_SIZE, self.PET_SIZE)
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background: transparent;")
        self._image_label.move(panel_width, 0)

        # 左侧按钮面板（浮动在图片左侧，不影响图片位置）
        self._button_panel = self._build_button_panel()
        self._button_panel.setParent(self)
        self._button_panel.setFixedWidth(panel_width)
        self._button_panel.move(0, self.PET_SIZE - self._button_panel.sizeHint().height())
        self._button_panel_opacity = QGraphicsOpacityEffect(self._button_panel)
        self._button_panel_opacity.setOpacity(0.0)
        self._button_panel.setGraphicsEffect(self._button_panel_opacity)
        self._button_panel.setVisible(False)

        self._refresh_pixmap()

        # 面板隐藏延迟 timer
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(600)
        self._hide_timer.timeout.connect(self._do_hide_panel)

        # 动画
        self._fade_anim = QPropertyAnimation(self._button_panel_opacity, b"opacity")
        self._fade_anim.setDuration(200)
        self._fade_anim.setEasingCurve(QEasingCurve.InOutQuad)

    # ---- 按钮面板 ---- #

    _BTN_DEFS = [
        ("exit",    "❌", "退出助手模式"),
        ("screen",  "📸", "文字提取"),
        ("region",  "📐", "提取范围"),
        ("voice",   "🎙️", "语音交互"),
        ("snap",    "📷", "截屏发AI"),
    ]

    def _build_button_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background: transparent;")
        panel.setMouseTracking(True)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(4, 4, 4, 4)
        panel_layout.setSpacing(6)
        panel_layout.setAlignment(Qt.AlignBottom)

        self._pet_buttons: dict[str, QPushButton] = {}

        for btn_id, icon, tooltip in self._BTN_DEFS:
            btn = self._make_btn(btn_id, icon, tooltip)
            self._pet_buttons[btn_id] = btn
            panel_layout.addWidget(btn)

        return panel

    def _make_btn(self, btn_id: str, icon: str, tooltip: str) -> QPushButton:
        btn = QPushButton(icon)
        btn.setFixedSize(36, 36)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMouseTracking(True)

        # 可切换状态的按钮
        if btn_id in ("screen", "voice"):
            btn.setCheckable(True)

        btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 210);
                border: 1px solid rgba(180, 190, 210, 150);
                border-radius: 18px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: rgba(220, 230, 250, 240);
                border-color: rgba(100, 140, 220, 200);
            }
            QPushButton:checked {
                background: rgba(0, 120, 215, 200);
                border-color: rgba(0, 100, 190, 220);
            }
        """)

        btn.clicked.connect(lambda checked, b=btn_id: self._on_btn_click(b))
        return btn

    def _on_btn_click(self, btn_id: str):
        if btn_id == "exit":
            self.exit_pet_mode.emit()
            return

        if self._service_manager is None:
            self.service_feedback.emit("服务管理器未初始化")
            return

        try:
            if btn_id == "screen":
                running = self._service_manager.toggle_screen_capture()
                self._pet_buttons["screen"].setChecked(running)
                self.service_feedback.emit("文字提取已开启" if running else "文字提取已关闭")

            elif btn_id == "region":
                # 临时隐藏桌宠窗口以便全屏选区
                self.hide()
                QTimer.singleShot(200, self._do_select_region)

            elif btn_id == "voice":
                running = self._service_manager.toggle_voice_interaction()
                self._pet_buttons["voice"].setChecked(running)
                self.service_feedback.emit("语音交互已开启" if running else "语音交互已关闭")

            elif btn_id == "snap":
                self.screenshot_to_ai.emit()

        except Exception as e:
            self.service_feedback.emit(f"操作失败: {e}")

    def _do_select_region(self):
        """调用区域选择器，选完后应用到 OCR 服务并保存到配置文件"""
        try:
            if self._service_manager and hasattr(self._service_manager, 'select_screen_region'):
                region = self._service_manager.select_screen_region()
                if region:
                    left, top, width, height = region
                    # 应用到运行中的 OCR 服务
                    self._service_manager.set_screen_ocr_region(left, top, width, height)
                    # 持久化到 config.json
                    from system.config import save_screen_ocr_region
                    save_screen_ocr_region(list(region))
                    self.service_feedback.emit(f"提取范围已更新: {width}×{height}")
                else:
                    self.service_feedback.emit("区域选择已取消")
        except Exception as e:
            self.service_feedback.emit(f"区域选择失败: {e}")
        finally:
            self.show()
            self._force_topmost()

    # ------------------------------------------------------------------ #
    #  图片
    # ------------------------------------------------------------------ #

    def _refresh_pixmap(self):
        if self._original_pixmap and not self._original_pixmap.isNull():
            scaled = self._original_pixmap.scaled(
                self.PET_SIZE, self.PET_SIZE,
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            self._image_label.setPixmap(scaled)

    def update_pixmap(self, pixmap: QPixmap):
        self._original_pixmap = pixmap
        self._refresh_pixmap()

    # ------------------------------------------------------------------ #
    #  消息气泡（浮动在图片上方）
    # ------------------------------------------------------------------ #

    BUBBLE_GAP = 6        # 气泡之间的间距
    BUBBLE_BOTTOM_PAD = 8 # 最底部气泡距离图片顶部的间距

    def show_bubble(self, text: str) -> None:
        """显示一条消息气泡，从图片上方向上堆叠。"""
        if not text or not text.strip():
            return
        # 截断过长文本
        display_text = text.strip()
        if len(display_text) > 200:
            display_text = display_text[:197] + "..."

        bubble = _BubbleLabel(display_text, parent=None)
        # 设置为独立顶层窗口（无边框 + 置顶 + 工具窗口）
        bubble.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        bubble.setAttribute(Qt.WA_TranslucentBackground, False)
        bubble.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 注意：不要再调 bubble.adjustSize()，构造函数已用 QFontMetrics 锁定尺寸
        bubble.show()

        bubble.removed.connect(self._on_bubble_removed)
        self._active_bubbles.append(bubble)
        self._reposition_bubbles()

    def _on_bubble_removed(self, bubble: _BubbleLabel):
        """气泡淡出完成后从列表移除并重新排列"""
        if bubble in self._active_bubbles:
            self._active_bubbles.remove(bubble)
        self._reposition_bubbles()

    def _reposition_bubbles(self):
        """重新计算所有气泡的位置 — 从图片顶部向上堆叠"""
        if not self._active_bubbles:
            return

        panel_width = 44
        # 图片在屏幕上的位置
        pet_global = self.mapToGlobal(QPoint(panel_width, 0))
        # 气泡锚点：图片顶部中心
        anchor_x_center = pet_global.x() + self.PET_SIZE // 2
        cursor_y = pet_global.y() - self.BUBBLE_BOTTOM_PAD

        # 从最新（列表末尾）到最旧（列表开头）排列，最新的在最底部
        for bubble in reversed(self._active_bubbles):
            bw = bubble.width()
            bh = bubble.height()
            bx = anchor_x_center - bw // 2
            by = cursor_y - bh
            bubble.move(bx, by)
            cursor_y = by - self.BUBBLE_GAP

    # ------------------------------------------------------------------ #
    #  悬浮显示/隐藏按钮面板
    # ------------------------------------------------------------------ #

    def enterEvent(self, event):
        super().enterEvent(event)
        self._show_panel()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hide_timer.start()

    def _show_panel(self):
        self._hide_timer.stop()
        if not self._button_panel.isVisible():
            self._button_panel.setVisible(True)
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._button_panel_opacity.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def _do_hide_panel(self):
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._button_panel_opacity.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._on_fade_out_done)
        self._fade_anim.start()

    def _on_fade_out_done(self):
        try:
            self._fade_anim.finished.disconnect(self._on_fade_out_done)
        except Exception:
            pass
        if self._button_panel_opacity.opacity() < 0.05:
            self._button_panel.setVisible(False)

    # ------------------------------------------------------------------ #
    #  拖拽移动
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_start_pos)
            self._reposition_bubbles()
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False

    # ------------------------------------------------------------------ #
    #  始终置顶（Windows 特有：解决无边框全屏游戏遮挡）
    # ------------------------------------------------------------------ #

    def _start_topmost_timer(self):
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._force_topmost)
        self._topmost_timer.start(5000)
        # 初始也执行一次
        QTimer.singleShot(100, self._force_topmost)

    def _force_topmost(self):
        """使用 Windows API 保持窗口置顶"""
        if sys.platform != 'win32':
            return
        try:
            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  服务状态轮询
    # ------------------------------------------------------------------ #

    def _start_poll_timer(self):
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(3000)

    def _poll_status(self):
        if self._service_manager is None:
            return
        _checks = [
            ("screen", "is_screen_ocr_running"),
            ("voice",  "is_voice_interaction_running"),
        ]
        for btn_id, attr in _checks:
            btn = self._pet_buttons.get(btn_id)
            if btn is None:
                continue
            checker = getattr(self._service_manager, attr, None)
            if checker is None:
                continue
            try:
                actual = checker()
                if btn.isChecked() != actual:
                    btn.setChecked(actual)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  服务管理器注入
    # ------------------------------------------------------------------ #

    def set_service_manager(self, service_manager):
        self._service_manager = service_manager
        self._poll_status()

    # ------------------------------------------------------------------ #
    #  清理
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        self._topmost_timer.stop()
        self._poll_timer.stop()
        self._hide_timer.stop()
        # 清理所有悬浮气泡
        for bubble in list(self._active_bubbles):
            bubble.close()
            bubble.deleteLater()
        self._active_bubbles.clear()
        super().closeEvent(event)
