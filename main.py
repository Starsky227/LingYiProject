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
# 从独立服务模块导入模型交互函数与预加载函数
from api_server.llm_service import chat_with_model, preload_and_get_greeting

# 获取当前脚本目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置日志 - 屏蔽 httpx 的 INFO 级别日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory")
logger.setLevel(logging.INFO)

# 屏蔽 httpx 和 openai 的 INFO 日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# 加载配置
config = config.load_config()
USERNAME = config.ui.username
AI_NAME = config.system.ai_name
print(f"配置加载完成 - AI名称: {AI_NAME}, 用户名: {USERNAME}")
print(f"使用本地模型: {config.api.model} @ {config.api.base_url}")

# 服务后台管理器
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
    
    def _start_api_server(self):
        """启动API服务器"""
        try:
            # 这里可以添加Api服务器的启动逻辑
            print("🔄 API服务器: 正在启动...")
            # 示例：启动Api服务)
        except Exception as e:
            print(f"❌ API服务器启动失败: {e}")

    def _start_mcp_server(self):
        """启动MCP服务器"""
        try:
            # from mcpserver.mcp_manager import start_mcp_server
            print("🔄 MCP服务器: 正在启动...")
            # start_mcp_server()
        except Exception as e:
            print(f"❌ MCP服务器启动失败: {e}")
    
    def _start_agent_server(self):
        """启动Agent服务器"""
        try:
            # 这里可以添加Agent服务器的启动逻辑
            print("🔄 Agent服务器: 正在启动...")
            # 示例：启动Agent服务
            pass
        except Exception as e:
            print(f"❌ Agent服务器启动失败: {e}")
    
    def start_all_servers(self):
        """并行启动所有服务：MCP、Agent"""
        print("🚀 正在并行启动所有服务...")
        print("=" * 50)
        threads = []
        service_status = {}  # 服务状态跟踪
        
        try:
            # 预检查所有端口，减少重复检查
            port_checks = {
                'api': config.api_server.enabled and config.api_server.auto_start and 
                      self.check_port_available(config.api_server.host, config.api_server.port),
                'mcp': self.check_port_available("0.0.0.0", 8003),
                'agent': self.check_port_available("0.0.0.0", 8001),
                'tts': self.check_port_available("0.0.0.0", config.tts.port)
            }

            # API服务器
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
                print(f"⚠️  MCP服务器: 端口 {config.mcp_server.port} 已被占用或未启用，跳过启动")
                service_status['MCP'] = "端口占用/未启用"
            
            # Agent服务器
            if port_checks['agent']:
                agent_thread = threading.Thread(target=self._start_agent_server, daemon=True)
                threads.append(("Agent", agent_thread))
                service_status['Agent'] = "准备启动"
            else:
                print(f"⚠️  Agent服务器: 端口 {config.agent_server.port} 已被占用或未启用，跳过启动")
                service_status['Agent'] = "端口占用/未启用"
            
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



# =============== 启动器部分 ===============
def main():
    print("🧠 启动本地 Gemma3 聊天程序中...")
    
    # 启动后台服务管理器
    service_mgr = ServiceManager()
    service_mgr.start_background_services()
    service_mgr.start_all_servers()
    
    # 先预加载模型并获取问候语（阻塞）
    print("🔄 正在预加载模型...")
    greeting = preload_and_get_greeting()
    
    if greeting:
        print(f"✅ 模型预加载完成，问候语: {greeting[:50]}...")
    else:
        print("⚠️  模型预加载未返回问候语")

    # 启动 UI
    app = QApplication(sys.argv)
    window = ChatUI(chat_with_model, ai_name=AI_NAME)
    window.show()

    # 预加载完成后，在 UI 中显示模型的问候（主线程中操作）
    if greeting:
        window._append_text(f"{AI_NAME}: {greeting}\n")
        window.messages.append({"role": "assistant", "content": greeting})

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
