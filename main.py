# pyinstaller适配
import os
import sys
import subprocess

# 标准库导入
import asyncio
import logging
import requests
import socket
import threading
import time
import uvicorn
import warnings

# 过滤弃用警告，提升启动体验
# warnings.filterwarnings("ignore", category=DeprecationWarning, module="websockets")
# warnings.filterwarnings("ignore", category=DeprecationWarning, module="uvicorn")
# warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*websockets.legacy.*")
# warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*WebSocketServerProtocol.*")
# warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*websockets.*")
# warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*uvicorn.*")

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
# from system.system_checker import run_system_check, run_quick_check
from system.config import config, AI_NAME
# from summer_memory.memory_manager import memory_manager
# from summer_memory.task_manager import start_task_manager, task_manager
# from ui.pyqt_chat_window import ChatWindow
# from ui.tray.console_tray import integrate_console_tray
from system.task_manager import task_manager
from ui.chat_ui import ChatUI
from api_server.llm_service import chat_with_model, preload_model

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("summer_memory")
logger.setLevel(logging.INFO)

# 过滤HTTP相关日志
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# 优化Live2D相关日志输出，减少启动时的信息噪音
logging.getLogger("live2d").setLevel(logging.WARNING)  # Live2D库日志
logging.getLogger("live2d.renderer").setLevel(logging.WARNING)  # 渲染器日志
logging.getLogger("live2d.animator").setLevel(logging.WARNING)  # 动画器日志
logging.getLogger("live2d.widget").setLevel(logging.WARNING)  # 组件日志
logging.getLogger("live2d.config").setLevel(logging.WARNING)  # 配置日志
logging.getLogger("live2d.config_dialog").setLevel(logging.WARNING)  # 配置对话框日志
logging.getLogger("OpenGL").setLevel(logging.WARNING)  # OpenGL日志
logging.getLogger("OpenGL.acceleratesupport").setLevel(logging.WARNING)  # OpenGL加速日志

# 获取当前脚本目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置日志 - 屏蔽 httpx 的 INFO 级别日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory")
logger.setLevel(logging.INFO)

# 屏蔽 httpx 和 openai 的 INFO 日志
# logging.getLogger("httpx").setLevel(logging.WARNING)
# logging.getLogger("openai").setLevel(logging.WARNING)


# 服务管理器类
class ServiceManager:
    """服务管理器 - 统一管理所有后台服务"""
    
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.bg_thread = None
        self.api_thread = None
        self.agent_thread = None
        self.mcp_thread = None
        self._services_ready = False  # 服务就绪状态
    
    def start_background_services(self):
        """启动后台服务 - 异步非阻塞"""
        # 启动后台任务管理器
        self.bg_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.bg_thread.start()
        logger.info(f"后台服务线程已启动: {self.bg_thread.name}")
    
    def _run_event_loop(self):
        """运行事件循环"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._init_background_services())
        logger.info("后台服务事件循环已启动")
    
    async def _init_background_services(self):
        """初始化后台服务 - 优化启动流程"""
        logger.info("正在启动后台服务...")
        try:
            # 标记服务就绪
            self._services_ready = True
            logger.info(f"任务管理器状态: running={task_manager.is_running}")
            
            # 保持事件循环活跃
            while True:
                await asyncio.sleep(3600)  # 每小时检查一次
        except Exception as e:
            logger.error(f"后台服务异常: {e}")
    
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
                'api': config.api_server.enabled and config.api_server.auto_start and 
                      self.check_port_available(config.api_server.host, config.api_server.port),
                'mcp': self.check_port_available("0.0.0.0", get_server_port("mcp_server")),
                'agent': self.check_port_available("0.0.0.0", get_server_port("agent_server")),
                # 'tts': self.check_port_available("0.0.0.0", config.tts.port)
            }
            
            # API服务器（可选）
            if port_checks['api']:
                api_thread = threading.Thread(target=self._start_api_server, daemon=True)
                threads.append(("API", api_thread))
                service_status['API'] = "准备启动"
            elif config.api_server.enabled and config.api_server.auto_start:
                print(f"⚠️  API服务器: 端口 {config.api_server.port} 已被占用，跳过启动")
                service_status['API'] = "端口占用"

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

            # TTS服务器
            # if port_checks['tts']:
            #     tts_thread = threading.Thread(target=self._start_tts_server, daemon=True)
            #     threads.append(("TTS", tts_thread))
            #     service_status['TTS'] = "准备启动"
            # else:
            #     print(f"⚠️  TTS服务器: 端口 {config.tts.port} 已被占用，跳过启动")
            #     service_status['TTS'] = "端口占用"
            
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

            print("-" * 30)
            print(f"🎉 服务启动完成: {len(threads)} 个服务正在运行")
            print("=" * 50)
            
        except Exception as e:
            print(f"❌ 并行启动服务异常: {e}")

    def _init_proxy_settings(self):
        """初始化代理设置：若不启用代理，则清空系统代理环境变量"""
        # 检测 applied_proxy 状态
        if not config.api.applied_proxy:  # 当 applied_proxy 为 False 时
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

    def _start_api_server(self):
        """内部API服务器启动方法"""
        try:
            print(f"   🚀 API服务器: 正在启动 on {config.api_server.host}:{config.api_server.port}...")

            # 使用异步方式启动，不阻塞当前线程
            uv_config = uvicorn.Config(
                "apiserver.api_server:app",
                host=config.api_server.host,
                port=config.api_server.port,
                log_level="info",  # 临时改为info以便看到uvicorn日志
                access_log=False,
                reload=False,
                ws_ping_interval=None,  # 禁用WebSocket ping
                ws_ping_timeout=None    # 禁用WebSocket ping超时
            )
            server = uvicorn.Server(uv_config)

            # 在新的事件循环中运行服务器
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(server.serve())
        except ImportError as e:
            print(f"   ❌ API服务器依赖缺失: {e}")
        except Exception as e:
            print(f"   ❌ API服务器启动失败: {e}")

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
    

# 延迟初始化 - 避免启动时阻塞
def _deffered_init_services():
    """延迟初始化服务 - 在需要时才初始化"""
    global service_manager
    if not hasattr(_deffered_init_services, '_initialized'):
        # 初始化服务管理器
        service_manager = ServiceManager()
        service_manager.start_background_services()
        
        # 显示系统状态
        # print("=" * 30)
        # print(f"GRAG状态: {'启用' if memory_manager.enabled else '禁用'}")
        # if memory_manager.enabled:
        #     stats = memory_manager.get_memory_stats()
        #     from summer_memory.quintuple_graph import get_graph, GRAG_ENABLED
        #     graph = get_graph()
        #     print(f"Neo4j连接: {'成功' if graph and GRAG_ENABLED else '失败'}")
        print("=" * 30)
        print(f'【{AI_NAME}】已启动')
        print("=" * 30)
        
        # 启动服务（并行异步）
        service_manager.start_all_servers()
        
        _deffered_init_services._initialized = True



# =============== 启动器部分 ===============
if __name__ == "__main__":
    # 系统环境检测
    print("🚀 正在启动智能体...")
    print("=" * 50)
    
    if not asyncio.get_event_loop().is_running():
        asyncio.set_event_loop(asyncio.new_event_loop())

    # 快速启动UI，后台服务延迟初始化
    app = QApplication(sys.argv)
    # icon_path = os.path.join(os.path.dirname(__file__), "ui", "img/window_icon.png")
    # app.setWindowIcon(QIcon(icon_path))

    # 集成控制台托盘功能
    # console_tray = integrate_console_tray()

    # 立即显示UI
    # win = ChatWindow()
    # win.setWindowTitle("NagaAgent")
    # win.show()
    window = ChatUI(chat_with_model, ai_name=AI_NAME)
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
    QTimer.singleShot(100, init_services_async)  # 100ms后初始化
    
    sys.exit(app.exec_())