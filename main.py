import asyncio
import logging
import socket
import sys
import json
import os
import threading
from PyQt5.QtWidgets import QApplication
import uvicorn
from brain import task_manager
from system import config
from ui.chat_ui import ChatUI

# 获取当前脚本目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory")
logger.setLevel(logging.INFO)

# 服务后台管理器
class ServiceManager:
    """服务管理器 - 统一管理所有后台服务"""
    
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.bg_thread = None
        self.api_thread = None
        self.agent_thread = None
        self.tts_thread = None
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
        """并行启动所有服务：API(可选)、MCP、Agent、TTS - 优化版本"""
        print("🚀 正在并行启动所有服务...")
        print("=" * 50)
        threads = []
        service_status = {}  # 服务状态跟踪
        
        try:
            self._init_proxy_settings()
            # 预检查所有端口，减少重复检查
            port_checks = {
                'api': config.api_server.enabled and config.api_server.auto_start and 
                      self.check_port_available(config.api_server.host, config.api_server.port),
                'mcp': self.check_port_available("0.0.0.0", 8003),
                'agent': self.check_port_available("0.0.0.0", 8001),
                'tts': self.check_port_available("0.0.0.0", config.tts.port)
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
                print(f"⚠️  MCP服务器: 端口 8003 已被占用，跳过启动")
                service_status['MCP'] = "端口占用"
            
            # Agent服务器
            if port_checks['agent']:
                agent_thread = threading.Thread(target=self._start_agent_server, daemon=True)
                threads.append(("Agent", agent_thread))
                service_status['Agent'] = "准备启动"
            else:
                print(f"⚠️  Agent服务器: 端口 8001 已被占用，跳过启动")
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
            
            print("-" * 30)
            print(f"🎉 服务启动完成: {len(threads)} 个服务正在后台运行")
            print("=" * 50)
            
        except Exception as e:
            print(f"❌ 并行启动服务异常: {e}")

    def _start_api_server(self):
        """内部API服务器启动方法"""
        try:
            uvicorn.run(
                "apiserver.api_server:app",
                host=config.api_server.host,
                port=config.api_server.port,
                log_level="error",
                access_log=False,
                reload=False,
                ws_ping_interval=None,  # 禁用WebSocket ping
                ws_ping_timeout=None    # 禁用WebSocket ping超时
            )
        except ImportError as e:
            print(f"   ❌ API服务器依赖缺失: {e}")
        except Exception as e:
            print(f"   ❌ API服务器启动失败: {e}")
    
    def _start_mcp_server(self):
        """内部MCP服务器启动方法"""
        try:
            import uvicorn
            from mcpserver.mcp_server import app
            
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=8003,
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
            import uvicorn
            from agentserver.agent_server import app
            
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=8001,
                log_level="error",
                access_log=False,
                reload=False,
                ws_ping_interval=None,  # 禁用WebSocket ping
                ws_ping_timeout=None    # 禁用WebSocket ping超时
            )
        except Exception as e:
            print(f"   ❌ Agent服务器启动失败: {e}")
    
    def _init_memory_system(self):
        """初始化记忆系统"""
        try:
            if memory_manager and memory_manager.enabled:
                logger.info("夏园记忆系统已初始化")
            else:
                logger.info("夏园记忆系统已禁用")
        except Exception as e:
            logger.warning(f"记忆系统初始化失败: {e}")
    
    def _init_mcp_services(self):
        """初始化MCP服务系统"""
        try:
            # MCP服务现在由mcpserver独立管理，这里只需要记录日志
            logger.info("MCP服务系统由mcpserver独立管理")
        except Exception as e:
            logger.error(f"MCP服务系统初始化失败: {e}")
    
    
    def show_naga_portal_status(self):
        """显示NagaPortal配置状态（手动调用）"""
        try:
            if config.naga_portal.username and config.naga_portal.password:
                print(f"🌐 NagaPortal: 已配置账户信息")
                print(f"   地址: {config.naga_portal.portal_url}")
                print(f"   用户: {config.naga_portal.username[:3]}***{config.naga_portal.username[-3:] if len(config.naga_portal.username) > 6 else '***'}")
                
                # 获取并显示Cookie信息
                try:
                    from mcpserver.agent_naga_portal.portal_login_manager import get_portal_login_manager
                    login_manager = get_portal_login_manager()
                    status = login_manager.get_status()
                    cookies = login_manager.get_cookies()
                    
                    if cookies:
                        print(f"🍪 Cookie信息 ({len(cookies)}个):")
                        for name, value in cookies.items():
                            # 显示完整的cookie名称和值
                            print(f"   {name}: {value}")
                    else:
                        print(f"🍪 Cookie: 未获取到")
                    
                    user_id = status.get('user_id')
                    if user_id:
                        print(f"👤 用户ID: {user_id}")
                    else:
                        print(f"👤 用户ID: 未获取到")
                        
                    # 显示登录状态
                    if status.get('is_logged_in'):
                        print(f"✅ 登录状态: 已登录")
                    else:
                        print(f"❌ 登录状态: 未登录")
                        if status.get('login_error'):
                            print(f"   错误: {status.get('login_error')}")
                        
                except Exception as e:
                    print(f"🍪 状态获取失败: {e}")
            else:
                print(f"🌐 NagaPortal: 未配置账户信息")
                print(f"   如需使用NagaPortal功能，请在config.json中配置naga_portal.username和password")
        except Exception as e:
            print(f"🌐 NagaPortal: 配置检查失败 - {e}")




# 从 config 调取配置（保留在 main 用于 UI 显示等）

with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
    configjson = json.load(f)

AI_NAME = configjson["general"]["ai_name"]
LOCAL_MODEL = configjson["api"]["local_model"]
USERNAME = configjson["ui"]["username"]
print("AI name:", AI_NAME)

# 从独立服务模块导入模型交互函数与预加载函数
from api_server.llm_service import chat_with_model, preload_and_get_greeting

# =============== 启动器部分 ===============
def main():
    # 先预加载模型并获取问候语（阻塞）
    greeting = preload_and_get_greeting()

    app = QApplication(sys.argv)
    # 将 chat_with_model 函数与 AI 名传入 UI 界面
    window = ChatUI(chat_with_model, ai_name=AI_NAME)
    window.show()

    # 预加载完成后，在 UI 中显示模型的问候（主线程中操作）
    if greeting:
        window._append_text(f"{AI_NAME}: {greeting}\n")
        window.messages.append({"role": "assistant", "content": greeting})

    sys.exit(app.exec_())

if __name__ == "__main__":
    print("🧠 启动本地 Gemma3 聊天程序中...")
    main()
