# pyinstaller适配
import os
import sys
import subprocess

# Windows DLL 兼容修复 —— 必须在所有第三方库之前执行
# Python 3.8+ 不再自动搜索 PATH 中的 DLL；torch 的 C 扩展 (c10.dll 等)
# 需要显式注册目录，否则 qwen_tts / faster_whisper 等后续库会因找不到依赖而失败。
if sys.platform == "win32":
    try:
        import torch as _torch_early
        _torch_lib = os.path.join(os.path.dirname(_torch_early.__file__), "lib")
        if os.path.isdir(_torch_lib):
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(_torch_lib)
            if _torch_lib not in os.environ.get("PATH", ""):
                os.environ["PATH"] = _torch_lib + os.pathsep + os.environ.get("PATH", "")
        del _torch_early, _torch_lib
    except Exception:
        pass

# 标准库导入
import asyncio
import logging
import socket
import threading
import time
from pathlib import Path

import requests
import uvicorn

# 修复Windows socket兼容性问题
if not hasattr(socket, 'EAI_ADDRFAMILY'):
    # Windows系统缺少这些错误码，添加兼容性常量
    socket.EAI_ADDRFAMILY = -9
    socket.EAI_AGAIN = -3
    socket.EAI_BADFLAGS = -1
    socket.EAI_FAIL = -4
    socket.EAI_MEMORY = -10
    socket.EAI_NODATA = -5
    socket.EAI_NONAME = -2
    socket.EAI_OVERFLOW = -12
    socket.EAI_SERVICE = -8
    socket.EAI_SOCKTYPE = -7
    socket.EAI_SYSTEM = -11

# 第三方库导入
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

# 本地模块导入
from system.config import config, AI_NAME
from system.system_checker import run_system_check
from brain.lingyi_core.lingyi_core import LingYiCore
from ui.pyqt_chat_ui import ChatWindow


def _configure_logging() -> logging.Logger:
    """统一配置主进程日志输出，并放开 QQ/PC 服务日志可见性。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # 降低噪音日志
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # 放开关键服务日志，便于诊断 QQ 无响应。
    logging.getLogger("service.pcAssistant").setLevel(logging.INFO)
    logging.getLogger("service.qqOneBot").setLevel(logging.INFO)

    configured_logger = logging.getLogger("LingYiMain")
    configured_logger.setLevel(logging.INFO)
    return configured_logger


logger = _configure_logging()


# ===================== 全局变量 =====================
service_manager = None
window = None


def _extract_reply_text(output_messages: list) -> str:
    """从 lingyi_core 的 process_message 返回值中提取文本。"""
    parts = []
    for item in output_messages or []:
        # 兼容 dict 结构
        if isinstance(item, dict):
            if item.get("role") == "assistant":
                text = str(item.get("content", "") or "").strip()
                if text:
                    parts.append(text)
            continue

        # 兼容 Responses API message 对象
        content = getattr(item, "content", None)
        if isinstance(content, str):
            if content.strip():
                parts.append(content.strip())
        elif isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(str(text).strip())

    return "\n".join([p for p in parts if p]).strip()


# ===================== 单例后台 asyncio loop =====================
#
# 历史实现里每次 ``chat_with_lingyi_core`` / OCR 回调都用一次 ``asyncio.run()``，
# 这意味着：
#   1) 每次调用都新建并销毁一个 event loop（小开销但累积可观）；
#   2) 在 ``process_message`` 里通过 ``asyncio.create_task`` fire-and-forget 出去
#      的并行任务（例如附件图像的描述任务）会随着 loop 关闭而被取消，结果
#      "[图片{描述}]" 永远写不进对话历史。
#   3) 同一个 ``asyncio.Queue``（``input_buffer``）在不同的 loop 里被 put/await，
#      Python 3.10+ 会报 ``RuntimeError: ... attached to a different loop``。
#
# 所以这里维护一个全局后台 loop（独立守护线程）。所有 LingYiCore 协程都通过
# ``submit_async`` 提交到该 loop 里执行，``asyncio.create_task`` 创建的子任务
# 也共享这个 loop，能存活到协程自然完成。
_BACKGROUND_LOOP: asyncio.AbstractEventLoop | None = None
_BACKGROUND_LOOP_READY = threading.Event()


def _start_background_loop() -> None:
    global _BACKGROUND_LOOP
    loop = asyncio.new_event_loop()
    _BACKGROUND_LOOP = loop
    asyncio.set_event_loop(loop)
    _BACKGROUND_LOOP_READY.set()
    try:
        loop.run_forever()
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


def _ensure_background_loop() -> asyncio.AbstractEventLoop:
    if _BACKGROUND_LOOP is None:
        t = threading.Thread(
            target=_start_background_loop,
            name="lingyi-async-loop",
            daemon=True,
        )
        t.start()
        _BACKGROUND_LOOP_READY.wait(timeout=5.0)
    assert _BACKGROUND_LOOP is not None
    return _BACKGROUND_LOOP


def submit_async(coro):
    """把协程提交到后台 loop 并阻塞等待结果（在调用者线程中）。

    用法等价于 ``asyncio.run(coro)``，区别是所有调用共享同一个 loop。
    必须在非 loop 自身的线程上调用——UI worker 线程 / OCR 派发线程都满足。
    """
    loop = _ensure_background_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def submit_async_nowait(coro):
    """提交协程到后台 loop，不阻塞等待。返回 ``concurrent.futures.Future``。"""
    loop = _ensure_background_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop)


# 服务管理器类
class ServiceManager:
    """服务管理器 - 统一管理所有后台服务"""
    
    def __init__(self):
        self.api_thread = None
        self.agent_thread = None
        self.mcp_thread = None
        self._services_ready = False
        self._pc_log_threads = {}

        # 助手模式状态
        self._assistant_mode = False
        self._original_prompt: str | None = None
        self._lingyi = None
        
        # 初始化 PC 服务管理器（QQ、记忆云图等）
        try:
            from service.pcAssistant.service_manager import PCServiceManager
            self.pc_service_manager = PCServiceManager()
        except Exception as e:
            logger.warning(f"PCServiceManager 初始化失败: {e}")
            self.pc_service_manager = None
    
    def check_port_available(self, host, port):
        """检查端口是否可用"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return True
        except OSError:
            return False
    
    def start_all_servers(self):
        """并行启动所有服务：API、MCP、Agent、TTS - 优化版本"""
        print("🚀 正在并行启动所有服务...")
        print("=" * 50)
        threads = []
        service_status = {}  # 服务状态跟踪
        
        try:
            self._init_proxy_settings()
            # 预检查所有端口，减少重复检查
            from system.config import get_server_port
            port_checks = {
                'mcp': self.check_port_available("0.0.0.0", get_server_port("mcp_server")),
                'agent': self.check_port_available("0.0.0.0", get_server_port("agent_server")),
            }

            # MCP服务器
            if port_checks['mcp']:
                mcp_thread = threading.Thread(target=self._start_mcp_server, daemon=True)
                threads.append(("MCP", mcp_thread))
                service_status['MCP'] = "准备启动"
            else:
                print(f"⚠️  MCP服务器: 端口 {get_server_port('mcp_server')} 已被占用，跳过启动")
                service_status['MCP'] = "端口占用"

            # Agent服务器
            if port_checks['agent']:
                agent_thread = threading.Thread(target=self._start_agent_server, daemon=True)
                threads.append(("Agent", agent_thread))
                service_status['Agent'] = "准备启动"
            else:
                print(f"⚠️  Agent服务器: 端口 {get_server_port('agent_server')} 已被占用，跳过启动")
                service_status['Agent'] = "端口占用"
            
            # 显示服务启动计划
            print("\n📋 服务启动计划:")
            for service, status in service_status.items():
                if status == "准备启动":
                    print(f"   🔄 {service}服务器: 正在启动...")
                else:
                    print(f"   ⚠️  {service}服务器: {status}")
            
            print("\n🚀 开始启动服务...")
            print("-" * 30)

            # 批量启动所有线程
            for name, thread in threads:
                thread.start()
                print(f"✅ {name}服务器: 启动线程已创建")

            # 等待所有服务启动（给服务器启动时间）
            print("⏳ 等待服务初始化...")
            time.sleep(2)

            self._services_ready = True
            print("-" * 30)
            print(f"🎉 服务启动完成: {len(threads)} 个服务正在运行")
            print("=" * 50)
            
        except Exception as e:
            print(f"❌ 并行启动服务异常: {e}")

    def _init_proxy_settings(self):
        """初始化代理设置：若不启用代理，则清空系统代理环境变量"""
        # 检测 applied_proxy 状态
        if not config.main_api.applied_proxy:  # 当 applied_proxy 为 False 时
            print("检测到不启用代理，正在清空系统代理环境变量...")

            # 清空 HTTP/HTTPS 代理环境变量（跨平台兼容）
            proxy_vars = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
            for var in proxy_vars:
                if var in os.environ:
                    del os.environ[var]  # 删除环境变量
                    print(f"已清除代理环境变量: {var}")

            # 额外：确保 requests Session 没有全局代理配置
            global_session = requests.Session()
            if global_session.proxies:
                global_session.proxies.clear()
                print("已清空 requests Session 全局代理配置")



    def _start_mcp_server(self):
        """内部MCP服务器启动方法"""
        try:
            from mcpserver.mcp_server import app
            from system.config import get_server_port
            
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=get_server_port("mcp_server"),
                log_level="error",
                access_log=False,
                reload=False,
                ws_ping_interval=None,  # 禁用WebSocket ping
                ws_ping_timeout=None    # 禁用WebSocket ping超时
            )
        except Exception as e:
            print(f"   ❌ MCP服务器启动失败: {e}")
    
    def _start_agent_server(self):
        """内部Agent服务器启动方法"""
        try:
            from agentserver.agent_server import app
            from system.config import get_server_port
            
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=get_server_port("agent_server"),
                log_level="error",
                access_log=False,
                reload=False,
                ws_ping_interval=None,  # 禁用WebSocket ping
                ws_ping_timeout=None    # 禁用WebSocket ping超时
            )
        except Exception as e:
            print(f"   ❌ Agent服务器启动失败: {e}")

    def _ensure_pc_log_stream(self, process, stream_name: str):
        """将子进程 stdout 转发到主日志，便于在主窗口控制台定位问题。"""
        if not process or not getattr(process, "stdout", None):
            return

        existing = self._pc_log_threads.get(stream_name)
        if existing and existing.is_alive():
            return

        def _pump_stdout():
            try:
                while True:
                    line = process.stdout.readline()
                    if not line:
                        if process.poll() is not None:
                            break
                        time.sleep(0.1)
                        continue
                    logger.info(f"[{stream_name}] {line.rstrip()}")
            except Exception as e:
                logger.warning(f"[{stream_name}] 日志转发异常: {e}")

        t = threading.Thread(target=_pump_stdout, daemon=True, name=f"log-{stream_name}")
        self._pc_log_threads[stream_name] = t
        t.start()

    # ================================================================== #
    #  PC 服务管理方法（QQ Bot, 记忆云图等）
    # ================================================================== #

    def is_qq_running(self) -> bool:
        """检查 QQ Bot 是否运行"""
        if self.pc_service_manager:
            return self.pc_service_manager.is_qq_running()
        return False

    def start_qq(self) -> bool:
        """启动 QQ Bot（若已运行则不重复启动）"""
        if not self.pc_service_manager:
            return False
        running = self.pc_service_manager.start_qq()
        if running:
            self._ensure_pc_log_stream(
                getattr(self.pc_service_manager, "_qq_process", None),
                "QQBot",
            )
            self._ensure_pc_log_stream(
                getattr(self.pc_service_manager, "_napcat_process", None),
                "NapCat",
            )
        return running

    def is_memory_viz_running(self) -> bool:
        """检查记忆云图是否运行"""
        if self.pc_service_manager:
            return self.pc_service_manager.is_memory_viz_running()
        return False

    def open_memory_visualizer(self) -> bool:
        """启动记忆云图（若已运行则不重复启动）"""
        if not self.pc_service_manager:
            return False
        running = self.pc_service_manager.open_memory_visualizer()
        if running:
            self._ensure_pc_log_stream(
                getattr(self.pc_service_manager, "_memoryviz_process", None),
                "MemoryViz",
            )
        return running

    def toggle_qq(self) -> bool:
        """切换 QQ Bot 开关，返回操作后的运行状态"""
        if not self.pc_service_manager:
            return False
        running = self.pc_service_manager.toggle_qq()
        if running:
            self._ensure_pc_log_stream(
                getattr(self.pc_service_manager, "_qq_process", None),
                "QQBot",
            )
            self._ensure_pc_log_stream(
                getattr(self.pc_service_manager, "_napcat_process", None),
                "NapCat",
            )
        return running

    def toggle_memory_visualizer(self) -> bool:
        """切换记忆云图开关，返回操作后的运行状态"""
        if not self.pc_service_manager:
            return False
        running = self.pc_service_manager.toggle_memory_visualizer()
        if running:
            self._ensure_pc_log_stream(
                getattr(self.pc_service_manager, "_memoryviz_process", None),
                "MemoryViz",
            )
        return running

    # -------------------- 通用委托：语音 / 屏幕 / OCR 等 UI 接口 --------------------
    #
    # 历史上这里有 ~30 个手写的 if-pc-then-call-else-False 转发方法。
    # 现在统一通过 ``__getattr__`` 委托到 ``self.pc_service_manager``。
    # 仅在需要"额外副作用"（例如 QQ/MemViz 启动后挂日志转发）时才显式定义。
    #
    # 委托后行为：
    #   * pc_service_manager 已就绪 → 返回真实方法（保留原签名 / 返回值）；
    #   * pc_service_manager 为 None → 返回 no-op，调用结果一律为 ``False``。
    #     调用方原本就用 truthy 判断结果，所以兼容。
    _DELEGATE_NULL_DEFAULTS = {
        "interrupt_voice_output": "语音输出未开启",
        "select_screen_region": None,
    }

    def __getattr__(self, name: str):
        # __getattr__ 仅在常规属性查找失败后调用。
        # 不要拦截私有/dunder，以免污染 pickle / copy 等机制。
        if name.startswith("_"):
            raise AttributeError(name)
        pc = self.__dict__.get("pc_service_manager")
        if pc is not None and hasattr(pc, name):
            return getattr(pc, name)
        # PC 服务管理器缺失时的兼容兜底
        default = type(self)._DELEGATE_NULL_DEFAULTS.get(name, False)
        def _missing(*_args, **_kwargs):
            return default
        return _missing

    # -------------------- 助手模式 --------------------

    def set_lingyi(self, lingyi_instance) -> None:
        """注入 LingYiCore 引用（供助手模式切换 prompt / tools）。"""
        self._lingyi = lingyi_instance

    def is_assistant_mode(self) -> bool:
        return self._assistant_mode

    def enter_assistant_mode(self) -> bool:
        """进入助手模式：注入 LY_assistant_prompt，启用 send_reply 工具。"""
        if self._assistant_mode:
            return True
        if not self._lingyi:
            logger.warning("LingYiCore 未注入，无法进入助手模式")
            return False

        self._original_prompt = self._lingyi.main_prompt
        assistant_prompt = self._load_assistant_prompt()
        self._lingyi.main_prompt = self._lingyi._compose_main_prompt(assistant_prompt)
        self._assistant_mode = True
        logger.info("已进入助手模式")
        return True

    def exit_assistant_mode(self) -> None:
        """退出助手模式：恢复原始 prompt。"""
        if not self._assistant_mode:
            return
        if self._lingyi and self._original_prompt is not None:
            self._lingyi.main_prompt = self._original_prompt
        self._assistant_mode = False
        self._original_prompt = None
        logger.info("已退出助手模式")

    @staticmethod
    def _load_assistant_prompt() -> str:
        prompt_path = Path(os.path.dirname(__file__)) / "service" / "pcAssistant" / "prompt" / "LY_assistant_prompt.xml"
        if not prompt_path.exists():
            logger.warning(f"助手模式提示词文件不存在: {prompt_path}")
            return ""
        return prompt_path.read_text(encoding="utf-8").strip()

    def cleanup(self):
        """主程序退出时统一清理 PC 相关子服务。"""
        if self.pc_service_manager:
            try:
                self.pc_service_manager.cleanup()
            except Exception as e:
                logger.warning(f"ServiceManager cleanup failed: {e}")


# 延迟初始化 - 避免启动时阻塞
def _deffered_init_services():
    """延迟初始化服务 - 在需要时才初始化"""
    global service_manager, window
    if not hasattr(_deffered_init_services, '_initialized'):
        # 系统环境检测
        run_system_check()
        
        # 初始化服务管理器
        service_manager = ServiceManager()

        # 注入 LingYiCore 引用（助手模式 prompt 切换需要）
        if lingyi:
            service_manager.set_lingyi(lingyi)

            # 注册助手模式工具（pc- 前缀），始终可用但仅在助手 prompt 指引下调用
            try:
                from brain.lingyi_core.tool_manager import LocalToolRegistry
                _assistant_tools_dir = Path(os.path.dirname(__file__)) / "service" / "pcAssistant" / "tools"
                _pc_registry = LocalToolRegistry(_assistant_tools_dir)
                _pc_registry.load_items()
                lingyi.tool_manager.register_sub_registry("pc-", _pc_registry)
                logger.info(f"助手模式工具已注册: {[s.get('name') for s in _pc_registry.get_schema()]}")
            except Exception as e:
                logger.warning(f"助手模式工具注册失败: {e}")
        
        print("=" * 30)
        print(f'【{AI_NAME}】已启动')
        print("=" * 30)
        
        # 将服务管理器注入到UI窗口（显示服务按钮）
        if window:
            window.set_service_manager(service_manager)
        
        # 注入语音输入文本回调：语音转写出文字后打断 TTS 并发送消息
        if window:
            def _on_voice_text(text: str):
                try:
                    from mcpserver.voice_service.voice_mcp_service import VoiceMCPService
                    svc = VoiceMCPService.get_instance()
                    svc.interrupt()   # 打断 TTS
                except Exception as e:
                    logger.warning(f"voice_text interrupt error: {e}")
                window.submit_external_message(text)

            service_manager.set_voice_text_callback(_on_voice_text)

        # 注入截屏发送给AI回调（助手模式使用）
        def send_screenshot_to_ai():
            """截取屏幕并注入到对话流"""
            try:
                import base64, io, mss
                from PIL import Image

                # 截屏前隐藏助手窗口，避免助手图片出现在截图中
                pet_win = getattr(window, '_pet_window', None) if window else None
                if pet_win and pet_win.isVisible():
                    pet_win.hide()
                    QApplication.processEvents()
                    time.sleep(0.05)

                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    screenshot = sct.grab(monitor)

                # 截屏后恢复助手窗口
                if pet_win:
                    pet_win.show()
                    img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                    max_dim = 1920
                    if img.width > max_dim or img.height > max_dim:
                        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=80)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    data_url = f"data:image/jpeg;base64,{b64}"

                if window:
                    window.add_pending_attachment("屏幕截图", data_url, "用户当前的屏幕截图")
            except Exception as e:
                logger.error(f"助手截屏失败: {e}")

        if window:
            window.set_screenshot_callback(send_screenshot_to_ai)

        # VAD 检测到语音不再打断 TTS，打断已移至转写完成时
        service_manager.set_speech_start_callback(None)

        # 屏幕 OCR 文字回调：通过新消息主动注入给 AI
        if lingyi:
            def _on_screen_ocr_text(text: str):
                try:
                    ss = lingyi.get_session_state("default")
                    # 主动注入：推入 input_buffer，让 AI 立即感知屏幕文字变化
                    submit_async(ss.input_buffer.put(
                        message=f"[屏幕内容]\n{text}",
                        caller_message="这是当前屏幕上的文字内容，通常是游戏剧情对话。可以适当进行评价。",
                    ))
                    # 如果当前没有正在进行的 AI 处理，触发静默处理
                    if not ss.input_buffer.is_processing and window:
                        window.trigger_silent_processing()
                except Exception as e:
                    logger.warning(f"screen_ocr callback error: {e}")
            service_manager.set_screen_ocr_callback(_on_screen_ocr_text)

        # 启动服务（并行异步）
        service_manager.start_all_servers()

        # 屏幕文字提取：根据配置自动启动
        if config.screen_ocr.enabled:
            region = tuple(config.screen_ocr.region)
            service_manager.start_screen_ocr(
                region=region,
                interval=config.screen_ocr.interval,
                hash_threshold=config.screen_ocr.hash_threshold,
                stable_count=config.screen_ocr.stable_count,
            )
        
        _deffered_init_services._initialized = True



# =============== 启动器部分 ===============
if __name__ == "__main__":
    # 提升进程优先级，避免全屏游戏等前台应用抢占 CPU 导致 TTS 合成卡顿
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentProcess()
            ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
            kernel32.SetPriorityClass(handle, ABOVE_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass

    # 系统环境检测
    print("🚀 正在启动智能体...")
    print("=" * 50)

    # 快速启动UI，后台服务延迟初始化
    app = QApplication(sys.argv)

    icon_path = os.path.join(os.path.dirname(__file__), "ui", "img", "window_icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # 初始化 LingYiCore
    try:
        lingyi = LingYiCore(main_prompt="")
        logger.info("LingYiCore 初始化成功")
    except Exception as e:
        logger.error(f"LingYiCore 初始化失败: {e}")
        lingyi = None

    # 创建聊天回调适配器
    def chat_with_lingyi_core(messages: list, on_response: callable):
        """使用 LingYiCore 处理消息"""
        if not lingyi:
            on_response("错误：LingYiCore 未初始化")
            return [{"role": "assistant", "content": "错误：模型未初始化"}]
        
        try:
            is_assistant = service_manager is not None and service_manager.is_assistant_mode()

            # 在输入缓冲区中添加消息
            session_state = lingyi.get_session_state("default")

            # 将 UI 待发送附件推入会话缓冲区
            if window and hasattr(window, '_attachments_for_send'):
                for att in window._attachments_for_send:
                    session_state.external_pending_images.append({
                        "data_url": att["data_url"],
                        "description": att.get("description", att.get("name", "附件")),
                    })
                window._attachments_for_send = []

            if is_assistant:
                caller_msg = "来自PC客户端的消息（助手模式：请自行判断是否需要回复）"
            else:
                caller_msg = "用户直接对你发送的消息，必须回复"
            
            # 将消息添加到缓冲区
            for msg in messages[-1:]:
                if msg.get("role") != "assistant":
                    text = str(msg.get("content", "") or "")
                    user_name = config.system.user_name or "用户"
                    submit_async(
                        session_state.input_buffer.put(
                            message=f"<{user_name}> {text}",
                            caller_message=caller_msg,
                        )
                    )

            # 更新工具上下文：屏幕捕捉权限
            session_state.tool_context["screen_capture_enabled"] = (
                service_manager is not None and service_manager.is_screen_capture_enabled()
            )

            # ---- 统一的即时回复处理器 ----
            # 每条 AI 回复（send_reply 工具 / 模型文本输出）立即推送 UI 气泡 + 写日志 + TTS
            all_immediate_replies: list[str] = []

            # 语音交互：准备流式 TTS（仅非助手模式 + 流式 TTS 配置时启用）
            stream_cb = None
            voice_mcp = None
            use_stream_tts = config.tts.stream
            if not is_assistant and service_manager and service_manager.is_voice_interaction_running():
                try:
                    from mcpserver.voice_service.voice_mcp_service import VoiceMCPService
                    voice_mcp = VoiceMCPService.get_instance()
                    if voice_mcp.has_tts and use_stream_tts:
                        voice_mcp.start_streaming()
                        def _on_sentence(sentence: str, is_first: bool) -> None:
                            voice_mcp.speak_sentence(sentence, is_first)
                        stream_cb = _on_sentence
                except Exception as e:
                    logger.warning(f"语音交互回调设置失败: {e}")

            def _on_reply_immediate(text: str):
                """每条 AI 回复立即触发 — UI 气泡 + TTS（chat_log 由 lingyi_core 统一写入）"""
                all_immediate_replies.append(text)
                if window:
                    window.immediate_reply_signal.emit(text)
                # 非流式 TTS：逐条播放（流式 TTS 由 stream_cb 按句子处理，此处跳过）
                if stream_cb is None and service_manager and service_manager.is_voice_interaction_running():
                    try:
                        from mcpserver.voice_service.voice_mcp_service import VoiceMCPService
                        _voice = VoiceMCPService.get_instance()
                        if _voice.has_tts:
                            _voice.stream_speak(text)
                    except Exception as e:
                        logger.warning(f"即时回复 TTS 失败: {e}")

            # 助手模式：send_reply 工具回调
            if is_assistant:
                session_state.tool_context["assistant_reply_callback"] = _on_reply_immediate

            # 异步处理消息（提交到后台常驻 loop）
            response = submit_async(
                lingyi.process_message(
                    "default",
                    stream_text_callback=stream_cb,
                    on_text_output=_on_reply_immediate if not is_assistant else None,
                )
            )

            # 流式会话结束：启动播放完毕监视
            if voice_mcp and stream_cb:
                try:
                    voice_mcp.finish_streaming()
                except Exception as e:
                    logger.warning(f"语音交互流式结束处理失败: {e}")

            # 提取最终回复内容（用于 messages 历史记录）
            if is_assistant:
                session_state.tool_context.pop("assistant_reply_callback", None)

            if all_immediate_replies:
                reply = "\n".join(all_immediate_replies)
            else:
                # 兜底：如果即时回调未触发，尝试从 response 提取（chat_log 已由 lingyi_core 写入）
                reply = _extract_reply_text(response) if not is_assistant else ""
                if reply:
                    on_response(reply)

            # 统一返回 UI 可识别结构（用于 messages 历史，不再触发气泡）
            return [{"role": "assistant", "content": reply or ""}]
        except Exception as e:
            logger.error(f"LingYiCore 处理失败: {e}")
            error_msg = f"处理失败: {str(e)}"
            on_response(error_msg)
            return [{"role": "assistant", "content": error_msg}]

    window = ChatWindow()
    window.setWindowTitle(f"{AI_NAME} - LingYiProject")
    window.set_chat_callback(chat_with_lingyi_core)
    window.show()

    # 在UI显示后异步初始化后台服务
    def init_services_async():
        """异步初始化后台服务"""
        try:
            _deffered_init_services()
        except Exception as e:
            print(f"⚠️ 后台服务初始化异常: {e}")

    # 使用定时器延迟初始化，避免阻塞UI
    from PyQt5.QtCore import QTimer
    QTimer.singleShot(120, init_services_async)

    # 退出时清理后台服务，避免残留子进程。
    def _on_quit():
        # 先刷新所有未保存的记忆（在后台 loop 上同步执行）
        if lingyi:
            try:
                submit_async(lingyi.flush_all_pending_memory())
            except Exception as e:
                logger.warning(f"记忆刷新失败: {e}")
        # 再清理后台服务
        if service_manager:
            service_manager.cleanup()
    app.aboutToQuit.connect(_on_quit)
    
    sys.exit(app.exec_())