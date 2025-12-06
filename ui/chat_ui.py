import os
import json
import threading
import datetime

from system.config import config
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QTextEdit,
                             QHBoxLayout, QSizePolicy, QLabel)
from PyQt5.QtCore import pyqtSignal, Qt, QEvent
from PyQt5.QtGui import QTextCursor, QFontMetrics, QKeyEvent, QFont, QPixmap

# 获取当前文件夹路径
current_dir = os.path.dirname(os.path.abspath(__file__))

# 拼接出 config.json 的路径（在上一级目录）
config_path = os.path.join(current_dir, "..", "config.json")
config_path = os.path.normpath(config_path)

# 读取 JSON 配置
# API 配置
API_KEY = config.api.api_key
API_URL = config.api.base_url
MODEL = config.api.model
AI_NAME = config.system.ai_name
USERNAME = config.ui.username
TEXT_SIZE = config.ui.text_size
IMAGE_NAME = config.ui.image_name
LOGS_DIR = config.system.log_dir

def write_chat_log(sender: str, text: str, timestamp: str = None):
    """将单条对话追加到 brain/memory/logs/chat_logs/chat_logs_YYYY_MM_DD.txt
    
    Args:
        sender: 发送者名称
        text: 消息内容
        timestamp: 时间戳，如果为None则使用当前时间（用户消息），否则使用指定时间戳（AI消息）
    """
    try:
        # 使用配置中的日志目录
        logs_dir = os.path.join(LOGS_DIR, "chat_logs")
        os.makedirs(logs_dir, exist_ok=True)
        filename = datetime.datetime.now().strftime("chat_logs_%Y_%m_%d.txt")
        path = os.path.join(logs_dir, filename)
        
        # 根据timestamp参数决定使用的时间戳
        if timestamp is None:
            # 用户消息，使用当前时间
            ts = datetime.datetime.now().strftime("%H:%M:%S")
        else:
            # AI消息，解析传入的ISO格式时间戳
            try:
                dt = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                ts = dt.strftime("%H:%M:%S")
            except (ValueError, AttributeError):
                # 如果解析失败，回退到当前时间
                ts = datetime.datetime.now().strftime("%H:%M:%S")
        
        # 将换行替换为空格以保持单行记录
        safe_text = text.replace("\r", " ").replace("\n", " ")
        # 时间戳用[]框住
        line = f"[{ts}] <{sender}> {safe_text}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        # 写日志失败不影响主流程，打印到控制台以便排查
        print(f"[日志错误] 无法写入聊天日志: {e}")

class ChatUI(QWidget):
    # 定义从工作线程到 GUI 线程传递数据的信号
    chunk_received = pyqtSignal(str)
    thinking_received = pyqtSignal(str)  # 新增：thinking内容信号
    finished_reply = pyqtSignal()

    def __init__(self, chat_with_model, ai_name="AI"):
        super().__init__()
        # 窗口标题与初始尺寸
        self.setWindowTitle(f"{ai_name}")
        self.resize(900, 600)

        self.chat_with_model = chat_with_model
        self.ai_name = ai_name
        self.messages = []

        # 主水平布局：左侧为聊天区（垂直布局），右侧为图片区
        main_h_layout = QHBoxLayout(self)

        # 左侧：聊天与输入的垂直布局
        left_v_layout = QVBoxLayout()
        # 设置字体大小（从 config 读取）
        ui_font = QFont()
        ui_font.setPointSize(TEXT_SIZE)

        # 聊天显示区（只读）
        self.chat_box = QTextEdit(self)
        self.chat_box.setReadOnly(True)
        self.chat_box.setFont(ui_font)
        left_v_layout.addWidget(self.chat_box)

        # 底部输入区域：使用 QTextEdit 实现自适应高度（默认为 1 行，最多 4 行）
        bottom_layout = QHBoxLayout()

        self.input_field = QTextEdit(self)
        # 禁用富文本输入，保持纯文本
        self.input_field.setAcceptRichText(False)
        self.input_field.setFont(ui_font)

        # 计算单行高度与上下内边距，用于自适应高度
        font_metrics = QFontMetrics(self.input_field.font())
        self._single_line_height = font_metrics.lineSpacing()  # 单行高度
        self._v_padding = 12  # 上下内边距近似值（可根据需要调整）

        # 计算最小和最大高度（最多4行）
        min_h = max(30, int(self._single_line_height + self._v_padding))
        max_h = int(self._single_line_height * 4 + self._v_padding)
        self.input_field.setFixedHeight(min_h)
        self.input_field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # 文本变化时调整高度
        self.input_field.textChanged.connect(self._adjust_input_height)
        # 使用事件过滤器处理 Enter 发送与 Shift/Ctrl+Enter 换行
        self.input_field.installEventFilter(self)

        bottom_layout.addWidget(self.input_field)
        # 不使用发送按钮，改为回车发送
        left_v_layout.addLayout(bottom_layout)

        # 将左侧布局放入主布局
        from PyQt5.QtWidgets import QWidget as _QW
        left_container = _QW()
        left_container.setLayout(left_v_layout)
        main_h_layout.addWidget(left_container, stretch=1)

        # 右侧：图片显示区
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.image_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.image_label.setFixedWidth(220)  # 固定宽度，可调整

        # 尝试加载 img 文件夹下的 IMAGE_NAME
        image_path = os.path.normpath(os.path.join(current_dir, "..", "ui", "img", IMAGE_NAME))
        if os.path.exists(image_path):
            try:
                pix = QPixmap(image_path)
                if not pix.isNull():
                    # 按宽度缩放，保持纵横比
                    pix = pix.scaled(self.image_label.width(), self.height()-40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.image_label.setPixmap(pix)
                else:
                    self.image_label.setText("无法加载图片")
            except Exception:
                self.image_label.setText("加载图片出错")
        else:
            self.image_label.setText("未找到图片")

        main_h_layout.addWidget(self.image_label, stretch=0)

        # 占位符追踪（用于显示“AI 正在输入”）
        self.placeholder_shown = False
        self._placeholder_start = None
        self.thinking_mode = False  # 跟踪是否在thinking模式
        self.thinking_content = ""  # 留存thinking内容

        # 连接信号槽
        self.chunk_received.connect(self._append_chunk)
        self.thinking_received.connect(self._update_thinking)
        self.finished_reply.connect(self._finish_reply)

        # 保存高度限制并初始化高度
        self._min_input_height = min_h
        self._max_input_height = max_h
        self._adjust_input_height()

    def _adjust_input_height(self):
        """根据输入内容自动调整输入框高度（1 到 4 行）"""
        doc = self.input_field.document()
        # 文档的 blockCount() 大致等同于行数
        block_count = max(1, doc.blockCount())
        # 限制最大行数为 4
        lines = min(block_count, 4)
        new_h = int(self._single_line_height * lines + self._v_padding)
        new_h = max(self._min_input_height, min(new_h, self._max_input_height))
        # 仅在高度变化时设置，避免布局抖动
        if self.input_field.height() != new_h:
            self.input_field.setFixedHeight(new_h)

    def eventFilter(self, obj, event):
        """事件过滤：Enter 发送，Shift/Ctrl+Enter 换行"""
        if obj is self.input_field and isinstance(event, QKeyEvent) and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                # Shift 或 Ctrl 按住时插入换行
                if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                    cursor = self.input_field.textCursor()
                    cursor.insertText("\n")
                    return True
                # 普通 Enter：发送消息
                self.send_message()
                return True
        return super().eventFilter(obj, event)

    # 在聊天框尾部插入纯文本（线程须在主线程操作）
    def _append_text(self, text):
        cursor = self.chat_box.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.chat_box.ensureCursorVisible()

    def _append_chunk(self, chunk: str):
        """处理模型流式返回的文本块"""
        # 如果占位符仍在，说明这是第一块正式内容：移除占位并写入 AI 名前缀
        if self.placeholder_shown:
            self._remove_placeholder()
            self._append_text(f"{self.ai_name}: ")
            self.thinking_mode = False  # 退出thinking模式
        # 追加收到的文本块
        self._append_text(chunk)

    def _update_thinking(self, thinking_text: str):
        """更新thinking内容显示"""
        if not self.placeholder_shown:
            return
        
        self.thinking_content += thinking_text
        # 更新占位符内容
        cursor = self.chat_box.textCursor()
        cursor.setPosition(self._placeholder_start)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        
        # 重新插入更新的thinking内容
        thinking_display = self.thinking_content.replace('\n', '<br/>')
        cursor.insertHtml(f'<span style="color:#A9A9A9;font-style:italic">🤔 {self.ai_name} 正在思考...<br/>{thinking_display}<br/></span>')
        self.chat_box.ensureCursorVisible()

    def _finish_reply(self):
        """模型回复完成后的处理（换行分隔）"""
        # 在聊天框追加换行分隔
        self._append_text("\n")
        # 重置thinking状态
        self.thinking_mode = False
        self.thinking_content = ""

    def show_placeholder(self):
        """在聊天框显示灰色占位文本（AI 正在输入）"""
        if self.placeholder_shown:
            return
        cursor = self.chat_box.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._placeholder_start = cursor.position()
        # 插入灰色占位并换行
        cursor.insertHtml(f'<span style="color:#A9A9A9;font-style:italic">🤔 {self.ai_name} 正在思考...<br/></span>')
        self.chat_box.ensureCursorVisible()
        self.placeholder_shown = True
        self.thinking_mode = True
        self.thinking_content = ""

    def _remove_placeholder(self):
        """移除聊天框中的占位文本"""
        if not self.placeholder_shown or self._placeholder_start is None:
            return
        cursor = self.chat_box.textCursor()
        cursor.setPosition(self._placeholder_start)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        self.placeholder_shown = False
        self._placeholder_start = None
        self.thinking_mode = False
        self.thinking_content = ""

    # 用户触发：发送消息
    def send_message(self):
        # 获取输入内容（使用 toPlainText 以兼容 QTextEdit）
        user_input = self.input_field.toPlainText().strip()
        if not user_input:
            return
        
        # 记录用户消息到日志
        write_chat_log(USERNAME, user_input)
        
        # 在聊天框添加用户消息并换行
        self._append_text(f"{USERNAME}: {user_input}\n")
        # 清空输入框并重置高度
        self.input_field.clear()
        self._adjust_input_height()

        # 将用户消息加入消息列表并显示占位（等待模型返回）
        user_timestamp = datetime.datetime.now().isoformat(timespec='milliseconds')
        self.messages.append({
            "role": USERNAME, 
            "content": user_input,
            "timestamp": user_timestamp
        })
        self.show_placeholder()
        # 在工作线程中调用模型
        threading.Thread(target=self._worker_call_model, daemon=True).start()

    def _worker_call_model(self):
        """工作线程：调用模型并通过回调发送流式数据到主线程显示"""
        thinking_mode = False
        thinking_buffer = ""
        
        def on_response(chunk: str):
            nonlocal thinking_mode, thinking_buffer
            
            # 检测thinking模式的开始和结束
            if "<thinking>" in chunk:
                thinking_mode = True
                # 发送thinking之前的普通内容
                before_thinking = chunk.split("<thinking>")[0]
                if before_thinking:
                    self.chunk_received.emit(before_thinking)
                # 开始处理thinking内容
                thinking_content = chunk.split("<thinking>", 1)[1] if "<thinking>" in chunk else ""
                if "</thinking>" in thinking_content:
                    # thinking在同一块中结束
                    thinking_text = thinking_content.split("</thinking>")[0]
                    if thinking_text:
                        self.thinking_received.emit(thinking_text)
                    # 发送thinking之后的普通内容
                    after_thinking = thinking_content.split("</thinking>", 1)[1] if "</thinking>" in thinking_content else ""
                    if after_thinking:
                        self.chunk_received.emit(after_thinking)
                    thinking_mode = False
                else:
                    # thinking跨多个块
                    if thinking_content:
                        self.thinking_received.emit(thinking_content)
            elif "</thinking>" in chunk and thinking_mode:
                # thinking模式结束
                thinking_text = chunk.split("</thinking>")[0]
                if thinking_text:
                    self.thinking_received.emit(thinking_text)
                # 发送thinking之后的普通内容
                after_thinking = chunk.split("</thinking>", 1)[1] if "</thinking>" in chunk else ""
                if after_thinking:
                    self.chunk_received.emit(after_thinking)
                thinking_mode = False
            elif thinking_mode:
                # 在thinking模式中，发送到thinking显示
                self.thinking_received.emit(chunk)
            else:
                # 普通内容，直接发送
                self.chunk_received.emit(chunk)

        # 调用注入的 chat_with_model（可能会逐块调用 on_response）
        response_messages = self.chat_with_model(self.messages, on_response)
        
        # 处理返回的消息列表，提取最后一条assistant回复
        reply_content = ""
        if response_messages and isinstance(response_messages, list):
            # 找到最后一条assistant消息
            for msg in reversed(response_messages):
                if msg.get("role") == "assistant":
                    reply_content = msg.get("content", "")
                    break
        elif isinstance(response_messages, str):
            # 兼容旧版本，如果返回字符串则直接使用
            reply_content = response_messages
        
        # 记录AI回复到日志，逐条记录response_messages中的所有消息
        if response_messages and isinstance(response_messages, list):
            # 遍历所有消息并记录到日志
            for msg in response_messages:
                msg_role = msg.get("role", "")
                msg_content = msg.get("content", "").strip()
                msg_timestamp = msg.get("timestamp")
                
                if msg_content:  # 只记录非空内容
                    if msg_role == "assistant":
                        write_chat_log(AI_NAME, msg_content, msg_timestamp)
                    else:
                        # 如果有其他角色的消息，也可以记录
                        write_chat_log(msg_role, msg_content, msg_timestamp)
        elif isinstance(response_messages, str) and response_messages.strip():
            # 兼容旧版本字符串返回值
            write_chat_log(AI_NAME, response_messages.strip())
        
        # 将完整回复记录到消息列表并通知主线程完成
        assistant_timestamp = datetime.datetime.now().isoformat(timespec='milliseconds')
        self.messages.append({
            "role": "assistant", 
            "content": reply_content,
            "timestamp": assistant_timestamp
        })
        self.finished_reply.emit()