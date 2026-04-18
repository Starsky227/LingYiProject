# 界面UI image_window设计，为一个位于页面右侧的长方形区域（在title_bar下方）：
# 用于显示AI的人设图片，图片名称由UI配置中的IMAGE_NAME指定。

import os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap
from typing import Optional
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtCore import pyqtSignal


class ImageWindow(QWidget):
    """右侧图片展示区域：显示 AI 人设图片"""

    IMAGE_RATIO = 0.2  # 占窗口宽度的 20%

    # 服务状态变更信号 (service_name, new_is_running)
    service_toggled = pyqtSignal(str, bool)
    # 服务按钮点击反馈（用于在聊天气泡区显示，不写入上下文）
    service_feedback = pyqtSignal(str)
    # 助手模式请求信号
    assistant_mode_requested = pyqtSignal()

    def __init__(self, image_name: str = "", parent=None):
        super().__init__(parent)
        self.setMinimumWidth(150)
        self.setStyleSheet("background: transparent;")
        self._original_pixmap = None
        self._service_manager = None
        self._service_buttons: dict[str, QPushButton] = {}
        self._service_meta = {svc_id: (icon, name) for svc_id, icon, name in self._SERVICE_DEFS}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.image_label.setStyleSheet("background: transparent;")
        layout.addWidget(self.image_label)

        # 服务控制按钮面板（默认隐藏，set_service_manager 后显示）
        self._service_panel = self._build_service_panel()
        layout.addWidget(self._service_panel)
        self._service_panel.hide()

        layout.addStretch()

        if image_name:
            self.load_image(image_name)

    # ------------------------------------------------------------------ #
    #  服务按钮面板
    # ------------------------------------------------------------------ #

    _SERVICE_DEFS = [
        ("screen",  "📸",  "屏幕捕捉"),
        ("voice_interact", "🎙️", "语音交互"),
        ("assistant",     "🐾",  "助手模式"),
        ("memviz",  "🌐",  "记忆云图"),
    ]

    # 暂未实装的服务（只展示，不可交互）
    _STUB_SERVICES = set()
    _TOGGLE_SERVICES = {"screen", "voice_interact", "memviz"}
    _START_ONLY_SERVICES = set()

    def _build_service_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background: transparent;")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(4, 8, 4, 4)
        panel_layout.setSpacing(4)

        sep = QLabel("──────")
        sep.setAlignment(Qt.AlignHCenter)
        sep.setStyleSheet("color: rgba(180,180,180,150); font-size: 10px; background: transparent;")
        panel_layout.addWidget(sep)

        grid = QWidget()
        grid_layout = QVBoxLayout(grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(6)
        grid_layout.setAlignment(Qt.AlignTop)
        grid.setStyleSheet("background: transparent;")

        for svc_id, icon, tooltip in self._SERVICE_DEFS:
            btn = self._make_service_button(icon, tooltip, svc_id)
            self._service_buttons[svc_id] = btn
            grid_layout.addWidget(btn)

        panel_layout.addWidget(grid)
        return panel

    def _make_service_button(self, icon: str, tooltip: str, svc_id: str) -> QPushButton:
        btn = QPushButton(f"{icon} {tooltip}")
        btn.setFixedHeight(34)
        btn.setCheckable(svc_id in self._TOGGLE_SERVICES)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background: rgba(230, 235, 248, 200);
                border: 1px solid rgba(180, 190, 210, 150);
                border-radius: 8px;
                font-size: 13px;
                text-align: left;
                padding-left: 8px;
            }
            QPushButton:hover {
                background: rgba(210, 220, 245, 220);
            }
            QPushButton:checked {
                background: rgba(0, 120, 215, 180);
                border-color: rgba(0, 100, 190, 200);
                color: white;
            }
            QPushButton[running="true"] {
                background: rgba(34, 139, 34, 170);
                border-color: rgba(22, 115, 22, 220);
                color: white;
            }
            QPushButton:disabled {
                background: rgba(230, 230, 230, 120);
                color: rgba(160, 160, 160, 160);
                border-color: rgba(200, 200, 200, 100);
            }
        """)

        if svc_id in self._STUB_SERVICES:
            btn.setEnabled(False)
            btn.setToolTip(f"{tooltip}（暂未实装）")
        else:
            btn.clicked.connect(lambda checked, s=svc_id: self._on_service_toggle(s))

        return btn

    def _on_service_toggle(self, svc_id: str):
        if self._service_manager is None:
            print(f"[ImageWindow] 服务管理器未初始化")
            return

        try:
            if svc_id == "screen":
                running = self._service_manager.toggle_screen_capture()
                feedback = "屏幕捕捉已开启" if running else "屏幕捕捉已关闭"
            elif svc_id == "voice_interact":
                running = self._service_manager.toggle_voice_interaction()
                feedback = "语音交互已开启" if running else "语音交互已关闭"
            elif svc_id == "memviz":
                running = self._service_manager.toggle_memory_visualizer()
                feedback = "记忆云图已开启" if running else "记忆云图已关闭"
            elif svc_id == "assistant":
                self.assistant_mode_requested.emit()
                return
            else:
                return

            self._update_button_state(svc_id, running)
            self.service_toggled.emit(svc_id, running)
            self.service_feedback.emit(feedback)
        except Exception as e:
            print(f"[ImageWindow] 切换服务 {svc_id} 失败: {e}")
            self.service_feedback.emit(f"{self._service_meta.get(svc_id, ('', svc_id))[1]} 操作失败: {e}")

    def _update_button_state(self, svc_id: str, is_running: bool):
        btn = self._service_buttons.get(svc_id)
        if btn:
            if svc_id in self._TOGGLE_SERVICES:
                btn.setChecked(is_running)
            else:
                btn.setProperty("running", "true" if is_running else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def set_service_manager(self, service_manager) -> None:
        """注入服务管理器，显示服务控制面板"""
        self._service_manager = service_manager
        self._service_panel.show()
        # 同步初始状态（带错误处理）
        try:
            if hasattr(service_manager, 'is_memory_viz_running'):
                self._update_button_state("memviz", service_manager.is_memory_viz_running())
        except Exception as e:
            print(f"[ImageWindow] 获取记忆云图状态失败: {e}")

        try:
            if hasattr(service_manager, 'is_screen_capture_enabled'):
                self._update_button_state("screen", service_manager.is_screen_capture_enabled())
        except Exception as e:
            print(f"[ImageWindow] 获取屏幕捕捉状态失败: {e}")

        try:
            if hasattr(service_manager, 'is_voice_interaction_running'):
                self._update_button_state("voice_interact", service_manager.is_voice_interaction_running())
        except Exception as e:
            print(f"[ImageWindow] 获取语音交互状态失败: {e}")

        # 启动定时轮询：检测子进程是否已自行退出（如记忆云图关闭浏览器后自动停止）
        self._service_poll_timer = QTimer(self)
        self._service_poll_timer.timeout.connect(self._poll_service_status)
        self._service_poll_timer.start(3000)  # 每 3 秒检测一次

    def _poll_service_status(self):
        """定时轮询子进程状态，自动同步按钮到真实运行状态。
        解决：记忆云图关闭浏览器后进程自行退出，但 UI 按钮仍为激活态的问题。"""
        if self._service_manager is None:
            return
        _checks = [
            ("screen",          "is_screen_capture_enabled"),
            ("memviz",          "is_memory_viz_running"),
            ("voice_interact",  "is_voice_interaction_running"),
        ]
        for svc_id, attr in _checks:
            btn = self._service_buttons.get(svc_id)
            if btn is None:
                continue
            checker = getattr(self._service_manager, attr, None)
            if checker is None:
                continue
            try:
                actual_running = checker()
                # 仅在状态不一致时更新，避免无谓刷新
                if btn.isChecked() != actual_running:
                    self._update_button_state(svc_id, actual_running)
            except Exception:
                pass

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