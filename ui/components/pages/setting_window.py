# 界面UI main_window 展示设置界面时显示。
# 整体为一个可下拉的滚动区域，包含多个可设置项。
# 保存后即时加载（热重载 config）。

import json
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QLineEdit, QCheckBox, QPushButton, QGroupBox, QFormLayout,
    QDoubleSpinBox, QSpinBox, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal

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
    QLineEdit, QDoubleSpinBox, QSpinBox {
        border: 1px solid rgba(0,0,0,40); border-radius: 6px;
        padding: 4px 8px; font-size: 13px;
        background: rgba(255,255,255,200);
    }
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {
        border-color: rgba(0,120,215,180);
    }
"""
_LABEL_STYLE = "color: rgba(50,50,50,200); font-size: 13px; background: transparent;"
_HINT_STYLE = "color: rgba(140,140,140,200); font-size: 11px; background: transparent;"


def _make_group(title: str):
    grp = QGroupBox(title)
    grp.setStyleSheet(_GROUP_STYLE)
    form = QFormLayout(grp)
    form.setContentsMargins(12, 12, 12, 12)
    form.setSpacing(10)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    return grp, form


def _line_edit(value: str = "", placeholder: str = "", password: bool = False) -> QLineEdit:
    w = QLineEdit(str(value))
    if placeholder:
        w.setPlaceholderText(placeholder)
    if password:
        w.setEchoMode(QLineEdit.Password)
    w.setStyleSheet(_FIELD_STYLE)
    return w


def _double_spin(value: float, lo: float, hi: float, step: float = 0.1, decimals: int = 2) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    w.setValue(value)
    w.setStyleSheet(_FIELD_STYLE)
    return w


def _int_spin(value: int, lo: int, hi: int, step: int = 1) -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(value)
    w.setStyleSheet(_FIELD_STYLE)
    return w


def _lbl(text: str, hint: bool = False) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(_HINT_STYLE if hint else _LABEL_STYLE)
    l.setWordWrap(True)
    return l


class SettingPage(QWidget):
    """设置页面 — 真实配置表单，保存后热重载"""

    config_reloaded = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")

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

    # ------------------------------------------------------------------ #
    #  表单构建
    # ------------------------------------------------------------------ #

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

    def _build_form(self):
        c = _cfg
        cl = self._content_layout

        # ---- 系统 ----
        grp, form = _make_group("🖥  系统")
        self._add_field(form, "AI 名称", "system.ai_name", _line_edit(c.system.ai_name))
        self._add_field(form, "用户名", "system.user_name", _line_edit(c.system.user_name))
        cl.addWidget(grp)

        # ---- 主 API ----
        grp, form = _make_group("🔑  主 API")
        self._add_field(form, "API Key", "main_api.api_key",
                        _line_edit(c.main_api.api_key, password=True))
        self._add_field(form, "Base URL", "main_api.base_url",
                        _line_edit(c.main_api.base_url))
        self._add_field(form, "模型", "main_api.model",
                        _line_edit(c.main_api.model))
        self._add_field(form, "Temperature", "main_api.temperature",
                        _double_spin(c.main_api.temperature, 0.0, 2.0, 0.05))
        self._add_field(form, "Max Tokens", "main_api.max_tokens",
                        _int_spin(c.main_api.max_tokens, 1, 32768, 256))
        cl.addWidget(grp)

        # ---- 记忆 API ----
        grp, form = _make_group("🧠  记忆 API")
        self._add_field(form, "记忆记录 API Key", "memory_api.memory_record_api_key",
                        _line_edit(c.memory_api.memory_record_api_key, password=True))
        self._add_field(form, "记忆记录 Base URL", "memory_api.memory_record_base_url",
                        _line_edit(c.memory_api.memory_record_base_url))
        self._add_field(form, "记忆记录模型", "memory_api.memory_record_model",
                        _line_edit(c.memory_api.memory_record_model))
        self._add_field(form, "Embedding API Key", "memory_api.embedding_api_key",
                        _line_edit(c.memory_api.embedding_api_key, password=True))
        self._add_field(form, "Embedding Base URL", "memory_api.embedding_base_url",
                        _line_edit(c.memory_api.embedding_base_url))
        self._add_field(form, "Embedding 模型", "memory_api.embedding_model",
                        _line_edit(c.memory_api.embedding_model))
        self._add_field(form, "记忆衰减因子", "memory_api.memory_decay_factor",
                        _double_spin(c.memory_api.memory_decay_factor, 0.0, 1.0, 0.05),
                        hint="1.0 = 不衰减，0.0 = 完全衰减")
        cl.addWidget(grp)

        # ---- GRAG / Neo4j ----
        grp, form = _make_group("🕸  图谱记忆 (Neo4j / GRAG)")
        cb = QCheckBox("启用 GRAG 记忆系统")
        cb.setChecked(c.grag.enabled)
        cb.setStyleSheet("background: transparent; font-size: 13px;")
        form.addRow(cb)
        self._fields["grag.enabled"] = cb
        self._add_field(form, "Neo4j URI", "grag.neo4j_uri",
                        _line_edit(c.grag.neo4j_uri))
        self._add_field(form, "用户名", "grag.neo4j_user",
                        _line_edit(c.grag.neo4j_user))
        self._add_field(form, "密码", "grag.neo4j_password",
                        _line_edit(c.grag.neo4j_password, password=True))
        self._add_field(form, "数据库", "grag.neo4j_database",
                        _line_edit(c.grag.neo4j_database))
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

    # ------------------------------------------------------------------ #
    #  保存 & 热重载
    # ------------------------------------------------------------------ #

    def _collect_values(self) -> dict:
        result: dict = {}
        for key, widget in self._fields.items():
            parts = key.split(".")
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QDoubleSpinBox):
                value = widget.value()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            else:
                value = widget.text().strip()
            node = result
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return result

    def _save_and_reload(self):
        from system.config import hot_reload_config

        # 读取现有 config.json
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

        # 表单变更 merge
        form_values = self._collect_values()

        self._deep_merge(existing, form_values)

        # 写回 config.json
        try:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入配置文件失败：{e}")
            return

        # 热重载
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
                SettingPage._deep_merge(base[k], v)
            else:
                base[k] = v

    def _refresh_from_config(self):
        from system.config import config as c
        mapping = {
            "system.ai_name": lambda: c.system.ai_name,
            "system.user_name": lambda: c.system.user_name,
            "main_api.api_key": lambda: c.main_api.api_key,
            "main_api.base_url": lambda: c.main_api.base_url,
            "main_api.model": lambda: c.main_api.model,
            "main_api.temperature": lambda: c.main_api.temperature,
            "main_api.max_tokens": lambda: c.main_api.max_tokens,
            "memory_api.memory_record_api_key": lambda: c.memory_api.memory_record_api_key,
            "memory_api.memory_record_base_url": lambda: c.memory_api.memory_record_base_url,
            "memory_api.memory_record_model": lambda: c.memory_api.memory_record_model,
            "memory_api.embedding_api_key": lambda: c.memory_api.embedding_api_key,
            "memory_api.embedding_base_url": lambda: c.memory_api.embedding_base_url,
            "memory_api.embedding_model": lambda: c.memory_api.embedding_model,
            "memory_api.memory_decay_factor": lambda: c.memory_api.memory_decay_factor,
            "grag.enabled": lambda: c.grag.enabled,
            "grag.neo4j_uri": lambda: c.grag.neo4j_uri,
            "grag.neo4j_user": lambda: c.grag.neo4j_user,
            "grag.neo4j_password": lambda: c.grag.neo4j_password,
            "grag.neo4j_database": lambda: c.grag.neo4j_database,
        }
        for key, getter in mapping.items():
            w = self._fields.get(key)
            if w is None:
                continue
            try:
                value = getter()
            except Exception:
                continue
            if isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(value))
            elif isinstance(w, QSpinBox):
                w.setValue(int(value))
            else:
                w.setText(str(value) if value is not None else "")
