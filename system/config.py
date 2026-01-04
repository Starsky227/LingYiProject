import os
import json
from pathlib import Path
from typing import Callable, List

from pydantic import BaseModel, Field, field_validator


# 配置变更监听器
_config_listeners: List[Callable] = []

def add_config_listener(callback: Callable):
    """添加配置变更监听器"""
    _config_listeners.append(callback)

def remove_config_listener(callback: Callable):
    """移除配置变更监听器"""
    if callback in _config_listeners:
        _config_listeners.remove(callback)

def notify_config_changed():
    """通知所有监听器配置已变更"""
    for listener in _config_listeners:
        try:
            listener()
        except Exception as e:
            print(f"配置监听器执行失败: {e}")

def setup_environment():
    """设置环境变量解决兼容性问题"""
    env_vars = {
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1", 
        "OPENBLAS_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0.0",
        "PYTORCH_ENABLE_MPS_FALLBACK": "1"
    }
    for key, value in env_vars.items():
        os.environ.setdefault(key, value)

def load_config():
    """加载配置"""
    config_path = str(Path(__file__).parent.parent / "config.json")

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            print("配置加载成功")
            return LingyiConfig(**config)
        except Exception as e:
            print(f"加载配置时出错: {e}")
            return LingyiConfig()
    else:
        print(f"配置文件不存在: {config_path}")
        return LingyiConfig()


class SystemConfig(BaseModel):
    """系统基础配置"""
    version: str = Field(default="0.0.1", description="系统版本号")
    ai_name: str = Field(default="铃依", description="AI助手名称")
    base_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent, description="项目根目录")
    log_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "brain" / "memory" / "logs", description="日志目录")
    debug: bool = Field(default=True, description="是否启用调试模式")
    log_level: str = Field(default="INFO", description="日志级别")

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v):
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            print(f'日志级别 {v} 无效，使用默认值 INFO')
            return 'INFO'
        return v.upper()
    
class APIConfig(BaseModel):
    """API相关配置"""
    api_key: str = Field(default="ollama", description="API密钥")
    base_url: str = Field(default="http://localhost:11434/v1", description="API基础URL")
    model: str = Field(default="gemma3:4b", description="模型名称")
    memory_record_api_key: str = Field(default=None, description="记忆记录API密钥，未设置时使用api_key")
    memory_record_base_url: str = Field(default=None, description="记忆记录API基础URL，未设置时使用base_url")
    memory_record_model: str = Field(default=None, description="记忆记录模型名称，未设置时使用model")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 如果记忆记录相关配置未设置，则使用默认API配置
        if self.memory_record_api_key is None:
            self.memory_record_api_key = self.api_key
        if self.memory_record_base_url is None:
            self.memory_record_base_url = self.base_url
        if self.memory_record_model is None:
            self.memory_record_model = self.model


class APIServerConfig(BaseModel):
    """API服务器相关配置"""
    enabled: bool = Field(default=True, description="是否启用API服务器")
    host: str = Field(default="127.0.0.1", description="API服务器主机")
    port: int = Field(default=8000, description="API服务器端口")
    auto_start: bool = Field(default=True, description="是否自动启动API服务器")

class AgentServerConfig(BaseModel):
    """Agent服务器相关配置"""
    enabled: bool = Field(default=True, description="是否启用Agent服务器")
    host: str = Field(default="127.0.0.1", description="Agent服务器主机")
    port: int = Field(default=8001, description="Agent服务器端口")
    auto_start: bool = Field(default=True, description="是否自动启动Agent服务器")

class MCPServerConfig(BaseModel):
    """MCP服务器相关配置"""
    enabled: bool = Field(default=True, description="是否启用MCP服务器")
    host: str = Field(default="127.0.0.1", description="MCP服务器主机")
    port: int = Field(default=8003, description="MCP服务器端口")
    auto_start: bool = Field(default=True, description="是否自动启动MCP服务器")

class GRAGConfig(BaseModel):
    """GRAG知识图谱记忆系统配置"""
    enabled: bool = Field(default=False, description="是否启用GRAG记忆系统")
    auto_extract: bool = Field(default=False, description="是否自动提取对话中的五元组")
    context_length: int = Field(default=5, ge=1, le=20, description="记忆上下文长度")
    similarity_threshold: float = Field(default=0.6, ge=0.0, le=1.0, description="记忆检索相似度阈值")
    neo4j_uri: str = Field(default="neo4j://127.0.0.1:7687", description="Neo4j连接URI")
    neo4j_user: str = Field(default="neo4j", description="Neo4j用户名")
    neo4j_password: str = Field(default="your_password", description="Neo4j密码")
    neo4j_database: str = Field(default="neo4j", description="Neo4j数据库名")
    extraction_timeout: int = Field(default=12, ge=1, le=60, description="知识提取超时时间（秒）")
    extraction_retries: int = Field(default=2, ge=0, le=5, description="知识提取重试次数")
    base_timeout: int = Field(default=15, ge=5, le=120, description="基础操作超时时间（秒）")

class UIConfig(BaseModel):
    """UI相关配置"""
    username: str = Field(default="用户", description="默认用户名")
    text_size: int = Field(default=10, description="UI文本大小")
    image_name: str = Field(default="LingYi_img.png", description="UI使用的图片名称")

class Crawl4AIConfig(BaseModel):
    """Crawl4AI相关配置"""
    headless: bool = Field(default=True, description="是否启用无头模式")
    timeout: int = Field(default=30000, description="超时时间（毫秒）")
    user_agent: str = Field(default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36", description="用户代理")
    viewport_width: int = Field(default=1920, description="视口宽度")
    viewport_height: int = Field(default=1080, description="视口高度")

class LingyiConfig(BaseModel):
    """取自naga的主配置加载"""
    system: SystemConfig = Field(default_factory=SystemConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    api_server: APIServerConfig = Field(default_factory=APIServerConfig)
    agent_server: AgentServerConfig = Field(default_factory=AgentServerConfig)
    grag: GRAGConfig = Field(default_factory=GRAGConfig)
    mcp_server: MCPServerConfig = Field(default_factory=MCPServerConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    crawl4ai: Crawl4AIConfig = Field(default_factory=Crawl4AIConfig)

    def __init__(self, **kwargs):
        setup_environment()
        super().__init__(**kwargs)
        self.system.log_dir.mkdir(parents=True, exist_ok=True)  # 确保递归创建日志目录


# 全局Neo4j连接状态管理
_neo4j_connection_tested = False
_neo4j_connection_available = False

def is_neo4j_available() -> bool:
    """检查Neo4j是否可用（带缓存）"""
    global _neo4j_connection_tested, _neo4j_connection_available
    
    if not config.grag.enabled:
        return False
    
    # 如果已经测试过，直接返回缓存结果
    if _neo4j_connection_tested:
        return _neo4j_connection_available
    
    # 首次测试连接
    _neo4j_connection_available = check_neo4j_connection()
    _neo4j_connection_tested = True
    
    if not _neo4j_connection_available:
        print("⚠️  Neo4j数据库连接失败，GRAG记忆系统将被禁用")
    
    return _neo4j_connection_available

def reset_neo4j_connection_status():
    """重置Neo4j连接状态（用于配置重载时）"""
    global _neo4j_connection_tested, _neo4j_connection_available
    _neo4j_connection_tested = False
    _neo4j_connection_available = False

config = load_config()

def reload_config() -> LingyiConfig:
    """重新加载配置"""
    global config
    reset_neo4j_connection_status()  # 重置Neo4j连接状态
    config = load_config()
    notify_config_changed()
    return config

def hot_reload_config() -> LingyiConfig:
    """热更新配置 - 重新加载配置并通知所有模块"""
    global config
    old_config = config
    reset_neo4j_connection_status()  # 重置Neo4j连接状态
    config = load_config()
    notify_config_changed()
    print(f"配置已热更新: {old_config.system.version} -> {config.system.version}")
    return config

def get_config() -> LingyiConfig:
    """获取当前配置"""
    return config

def check_neo4j_connection() -> bool:
    """检查Neo4j数据库连接状态"""
    if not config.grag.enabled:
        return False
    
    try:
        from neo4j import GraphDatabase
        from neo4j.exceptions import ServiceUnavailable, AuthError
        
        uri = config.grag.neo4j_uri
        user = config.grag.neo4j_user
        password = config.grag.neo4j_password
        database = config.grag.neo4j_database
        
        driver = GraphDatabase.driver(
            uri, 
            auth=(user, password),
            database=database,
            connection_acquisition_timeout=5  # 5秒超时
        )
        
        # 测试连接
        with driver.session() as session:
            result = session.run("RETURN 1 as test")
            test_value = result.single()["test"]
            driver.close()
            return test_value == 1
            
    except (AuthError, ServiceUnavailable, Exception):
        return False

# 初始化时打印配置信息
if config.system.debug:
    print(f"Lingyi {config.system.version} 配置已加载")
    print(f"API服务器: {'启用' if config.api_server.enabled else '禁用'} ({config.api_server.host}:{config.api_server.port})")
    print(f"Agent服务器: {'启用' if config.agent_server.enabled else '禁用'} ({config.agent_server.host}:{config.agent_server.port})")
    print(f"MCP服务器: {'启用' if config.mcp_server.enabled else '禁用'} ({config.mcp_server.host}:{config.mcp_server.port})")
    
    # 检查GRAG记忆系统连接状态
    if config.grag.enabled:
        neo4j_connected = is_neo4j_available()
        connection_status = "已连接" if neo4j_connected else "连接失败，已禁用"
        print(f"GRAG记忆系统: 启用 - Neo4j数据库{connection_status}")
    else:
        print(f"GRAG记忆系统: 禁用")

import logging
logger = logging.getLogger(__name__)