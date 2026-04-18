# 界面UI QQ 配置页面：
# 顶部为 QQ Bot 启动/停止按钮，下方为 QQ 相关配置项表单。

import json
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QLineEdit, QPushButton, QGroupBox, QFormLayout, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

from system.config import config as _cfg

_CONFIG_PATH = str(Path(__file__).parent.parent.parent.parent / "config.json")

_SCROLL_STYLE = """
    QScrollArea { background: transparent; border: none; }
    QScrollBar:vertical {
        background: rgba(0,0,0,15); width: 8px; border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: rgba(0,0,0,60); border-radius: 4px; min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""
_GROUP_STYLE = """
    QGroupBox {
        font-size: 13px; font-weight: bold;
        border: 1px solid rgba(0,0,0,40); border-radius: 8px;
        margin-top: 8px; padding-top: 8px;
        background: rgba(255,255,255,180);
    }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
"""
_FIELD_STYLE = """
    QLineEdit {
        border: 1px solid rgba(0,0,0,40); border-radius: 6px;
        padding: 4px 8px; font-size: 13px;
        background: rgba(255,255,255,200);
    }
    QLineEdit:focus {
        border-color: rgba(0,120,215,180);
    }
"""
_LABEL_STYLE = "color: rgba(50,50,50,200); font-size: 13px; background: transparent;"
_HINT_STYLE = "color: rgba(140,140,140,200); font-size: 11px; background: transparent;"


def _lbl(text: str, hint: bool = False) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(_HINT_STYLE if hint else _LABEL_STYLE)
    l.setWordWrap(True)
    return l


def _line_edit(value: str = "", placeholder: str = "", password: bool = False) -> QLineEdit:
    w = QLineEdit(str(value))
    if placeholder:
        w.setPlaceholderText(placeholder)
    if password:
        w.setEchoMode(QLineEdit.Password)
    w.setStyleSheet(_FIELD_STYLE)
    return w


class QQPage(QWidget):
    """QQ 页面 — QQ Bot 启停 + QQ 配置表单"""

    config_reloaded = pyqtSignal()
    service_feedback = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
        self._service_manager = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL_STYLE)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 16, 16, 16)
        self._content_layout.setSpacing(12)

        self._fields: dict = {}
        self._build_form()

        scroll.setWidget(self._content)
        root.addWidget(scroll)

    def _build_form(self):
        c = _cfg
        cl = self._content_layout

        # ---- QQ Bot 启停按钮 ----
        self._toggle_btn = QPushButton("🤖  启动 QQ Bot")
        self._toggle_btn.setFixedHeight(44)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.setStyleSheet("""
            QPushButton {
                background: rgba(230, 235, 248, 220);
                border: 1px solid rgba(180, 190, 210, 150);
                border-radius: 10px;
                font-size: 15px; font-weight: bold;
                padding-left: 12px;
            }
            QPushButton:hover {
                background: rgba(210, 220, 245, 240);
            }
            QPushButton:checked {
                background: rgba(0, 120, 215, 200);
                border-color: rgba(0, 100, 190, 220);
                color: white;
            }
        """)
        self._toggle_btn.clicked.connect(self._on_toggle_qq)
        cl.addWidget(self._toggle_btn)

        # ---- QQ 配置 ----
        grp = QGroupBox("💬  QQ 配置")
        grp.setStyleSheet(_GROUP_STYLE)
        form = QFormLayout(grp)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._add_field(form, "机器人 QQ 号", "qq_config.bot_qq",
                        _line_edit(str(c.qq_config.bot_qq or "")))
        self._add_field(form, "主人 QQ 号", "qq_config.master_qq",
                        _line_edit(str(c.qq_config.master_qq or "")))
        self._add_field(form, "OneBot WS URL", "qq_config.onebot_ws_url",
                        _line_edit(c.qq_config.onebot_ws_url))
        self._add_field(form, "OneBot Token", "qq_config.onebot_token",
                        _line_edit(c.qq_config.onebot_token, password=True))
        cl.addWidget(grp)

        # ---- 保存按钮 ----
        save_btn = QPushButton("💾  保存并应用")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,120,215,200); color: white;
                font-size: 14px; font-weight: bold;
                border: none; border-radius: 8px; padding: 10px 24px;
            }
            QPushButton:hover { background: rgba(0,100,190,220); }
            QPushButton:pressed { background: rgba(0,80,170,240); }
        """)
        save_btn.clicked.connect(self._save_and_reload)

        self._status_label = _lbl("", hint=True)
        self._status_label.setAlignment(Qt.AlignHCenter)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        cl.addLayout(btn_row)
        cl.addWidget(self._status_label)
        cl.addStretch()

    def _add_field(self, form: QFormLayout, label: str, key: str, widget: QWidget, hint: str = ""):
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        vbox = QVBoxLayout(row)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)
        vbox.addWidget(widget)
        if hint:
            vbox.addWidget(_lbl(hint, hint=True))
        form.addRow(_lbl(label), row)
        self._fields[key] = widget

    # ------------------------------------------------------------------ #
    #  QQ Bot 启停
    # ------------------------------------------------------------------ #

    def _on_toggle_qq(self):
        if self._service_manager is None:
            self._toggle_btn.setChecked(False)
            self.service_feedback.emit("服务管理器未初始化")
            return
        try:
            running = self._service_manager.toggle_qq()
            self._update_toggle_state(running)
            feedback = "QQ Bot 已开启" if running else "QQ Bot 已关闭"
            self.service_feedback.emit(feedback)
        except Exception as e:
            self.service_feedback.emit(f"QQ Bot 操作失败: {e}")

    def _update_toggle_state(self, is_running: bool):
        self._toggle_btn.setChecked(is_running)
        self._toggle_btn.setText("🤖  QQ Bot 运行中" if is_running else "🤖  启动 QQ Bot")

    def set_service_manager(self, service_manager) -> None:
        self._service_manager = service_manager
        try:
            if hasattr(service_manager, 'is_qq_running'):
                self._update_toggle_state(service_manager.is_qq_running())
        except Exception as e:
            print(f"[QQPage] 获取 QQ 状态失败: {e}")

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_qq_status)
        self._poll_timer.start(3000)

    def _poll_qq_status(self):
        if self._service_manager is None:
            return
        checker = getattr(self._service_manager, 'is_qq_running', None)
        if checker is None:
            return
        try:
            actual = checker()
            if self._toggle_btn.isChecked() != actual:
                self._update_toggle_state(actual)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  保存 & 热重载
    # ------------------------------------------------------------------ #

    def _collect_values(self) -> dict:
        result: dict = {}
        for key, widget in self._fields.items():
            parts = key.split(".")
            value = widget.text().strip()
            node = result
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return result

    def _save_and_reload(self):
        from system.config import hot_reload_config

        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = f.read()
            try:
                import json5
                existing: dict = json5.load(open(_CONFIG_PATH, encoding="utf-8"))
            except ImportError:
                existing: dict = json.loads(raw)
        except Exception as e:
            QMessageBox.critical(self, "读取失败", f"无法读取配置文件：{e}")
            return

        form_values = self._collect_values()

        # qq 整型字段处理
        for qq_key in ("bot_qq", "master_qq"):
            qq_sect = form_values.setdefault("qq_config", {})
            raw_val = qq_sect.get(qq_key, "")
            try:
                qq_sect[qq_key] = int(str(raw_val).strip()) if str(raw_val).strip() else None
            except (ValueError, TypeError):
                qq_sect[qq_key] = None

        self._deep_merge(existing, form_values)

        try:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入配置文件失败：{e}")
            return

        try:
            hot_reload_config()
        except Exception as e:
            QMessageBox.warning(self, "热重载失败", f"配置已写入但热重载失败：{e}")
            return

        self._refresh_from_config()
        self._status_label.setText("✅ 配置已保存并应用")
        self.config_reloaded.emit()

    @staticmethod
    def _deep_merge(base: dict, override: dict):
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                QQPage._deep_merge(base[k], v)
            else:
                base[k] = v

    def _refresh_from_config(self):
        from system.config import config as c
        mapping = {
            "qq_config.bot_qq": lambda: str(c.qq_config.bot_qq or ""),
            "qq_config.master_qq": lambda: str(c.qq_config.master_qq or ""),
            "qq_config.onebot_ws_url": lambda: c.qq_config.onebot_ws_url,
            "qq_config.onebot_token": lambda: c.qq_config.onebot_token,
        }
        for key, getter in mapping.items():
            w = self._fields.get(key)
            if w is None:
                continue
            try:
                value = getter()
            except Exception:
                continue
            w.setText(str(value) if value is not None else "")
