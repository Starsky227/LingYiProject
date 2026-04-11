# pyinstaller适配
import os
import sys
import subprocess

# 标准库导入
import asyncio
import logging
import socket
import threading
import time

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


def _is_ollama_required() -> bool:
    """根据配置判断是否需要本地 Ollama。"""
    candidates = [
        str(getattr(config.main_api, "base_url", "") or ""),
        str(getattr(config.memory_api, "embedding_base_url", "") or ""),
        str(getattr(config.agent_api, "agent_base_url", "") or ""),
        str(getattr(config.vision_api, "vision_base_url", "") or ""),
    ]
    model_candidates = [
        str(getattr(config.main_api, "model", "") or ""),
        str(getattr(config.memory_api, "embedding_model", "") or ""),
        str(getattr(config.agent_api, "agent_model", "") or ""),
        str(getattr(config.vision_api, "vision_model", "") or ""),
    ]

    for url in candidates:
        lower = url.lower()
        if "11434" in lower or "ollama" in lower:
            return True

    return any("ollama" in model.lower() for model in model_candidates)


def _is_ollama_healthy(timeout_sec: float = 1.5) -> bool:
    """探测 Ollama 服务是否可用。"""
    endpoints = [
        "http://127.0.0.1:11434/api/tags",
        "http://localhost:11434/api/tags",
    ]
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=timeout_sec)
            if resp.status_code == 200:
                return True
        except Exception:
            continue
    return False


def _start_ollama_process() -> bool:
    """尝试后台启动 ollama serve。"""
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        return True
    except FileNotFoundError:
        logger.error("未找到 ollama 命令，请先安装 Ollama 并加入 PATH")
    except Exception as e:
        logger.error(f"启动 Ollama 失败: {e}")
    return False


def ensure_ollama_running(wait_sec: int = 25) -> bool:
    """确保 Ollama 服务处于可用状态。"""
    if not _is_ollama_required():
        logger.info("当前配置未要求本地 Ollama，跳过检查")
        return True

    if _is_ollama_healthy():
        logger.info("Ollama 已在运行")
        return True

    logger.info("检测到需要 Ollama，正在尝试自动启动...")
    if not _start_ollama_process():
        return False

    deadline = time.time() + max(wait_sec, 1)
    while time.time() < deadline:
        if _is_ollama_healthy(timeout_sec=1.0):
            logger.info("Ollama 启动成功")
            return True
        time.sleep(1)

    logger.error("Ollama 启动超时，请手动执行: ollama serve")
    return False





# 服务管理器类
class ServiceManager:
    """服务管理器 - 统一管理所有后台服务"""
    
    def __init__(self):
        self.api_thread = None
        self.agent_thread = None
        self.mcp_thread = None
        self._services_ready = False
        self._pc_log_threads = {}
        
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

    # -------------------- 语音输入/输出转发接口（供 UI 调用） --------------------

    def is_voice_input_running(self) -> bool:
        if self.pc_service_manager:
            return self.pc_service_manager.is_voice_input_running()
        return False

    def toggle_voice_input(self) -> bool:
        if self.pc_service_manager:
            return self.pc_service_manager.toggle_voice_input()
        return False

    def is_voice_output_running(self) -> bool:
        if self.pc_service_manager:
            return self.pc_service_manager.is_voice_output_running()
        return False

    def toggle_voice_output(self) -> bool:
        if self.pc_service_manager:
            return self.pc_service_manager.toggle_voice_output()
        return False

    def interrupt_voice_output(self) -> str:
        if self.pc_service_manager:
            return self.pc_service_manager.interrupt_voice_output()
        return "语音输出未开启"

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
        
        print("=" * 30)
        print(f'【{AI_NAME}】已启动')
        print("=" * 30)
        
        # 将服务管理器注入到UI窗口（显示服务按钮）
        if window:
            window.set_service_manager(service_manager)
        
        # 启动服务（并行异步）
        service_manager.start_all_servers()
        
        _deffered_init_services._initialized = True



# =============== 启动器部分 ===============
if __name__ == "__main__":
    # 系统环境检测
    print("🚀 正在启动智能体...")
    print("=" * 50)

    # 主入口先确保 Ollama 可用（若配置需要）
    ollama_ok = ensure_ollama_running()
    if not ollama_ok:
        print("⚠️ Ollama 未就绪：将继续启动 UI，但依赖本地模型的功能可能不可用")

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
            # 在输入缓冲区中添加消息
            session_state = lingyi.get_session_state("default")
            
            # 将消息添加到缓冲区
            for msg in messages[-1:]:
                if msg.get("role") != "assistant":
                    text = str(msg.get("content", "") or "")
                    asyncio.run(
                        session_state.input_buffer.put(
                            message=text,
                            caller_message=text,
                        )
                    )
            
            # 异步处理消息
            response = asyncio.run(lingyi.process_message("default"))

            reply = _extract_reply_text(response)
            if reply:
                on_response(reply)
            else:
                reply = ""

            # 统一返回 UI 可识别结构
            return [{"role": "assistant", "content": reply}]
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
    app.aboutToQuit.connect(lambda: service_manager and service_manager.cleanup())
    
    sys.exit(app.exec_())