# config.py - 简化配置系统
"""
NagaAgent 框架的配置系统 - 基于Pydantic实现类型安全和验证
支持配置热更新和变更通知
"""
import os
import socket
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime

from pydantic import BaseModel, Field, field_validator
from charset_normalizer import from_path
import json5  # 支持带注释的JSON解析

# ========== 服务器端口配置 - 统一管理 ==========
class ServerPortsConfig(BaseModel):
    """服务器端口配置 - 统一管理所有服务器端口"""
    # 主API服务器
    api_server: int = Field(default=8000, ge=1, le=65535, description="API服务器端口")
    
    # 智能体服务器
    agent_server: int = Field(default=8001, ge=1, le=65535, description="智能体服务器端口")
    
    # MCP工具服务器
    mcp_server: int = Field(default=8003, ge=1, le=65535, description="MCP工具服务器端口")

# 全局服务器端口配置实例
server_ports = ServerPortsConfig()

def get_server_port(server_name: str) -> int:
    """获取指定服务器的端口号"""
    return getattr(server_ports, server_name, None)

def get_all_server_ports() -> Dict[str, int]:
    """获取所有服务器端口配置"""
    return {
        "api_server": server_ports.api_server,
        "agent_server": server_ports.agent_server,
        "mcp_server": server_ports.mcp_server,
    }

# 配置变更监听器
_config_listeners: List[Callable] = []

# 为了向后兼容，提供AI_NAME常量
def get_ai_name() -> str:
    """获取AI名称"""
    return config.system.ai_name

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

class SystemConfig(BaseModel):
    """系统基础配置"""
    version: str = Field(default="4.0.0", description="系统版本号")
    ai_name: str = Field(default="AI助手", description="AI助手名称")
    user_name: str = Field(default="用户", description="默认用户名")
    base_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent, description="项目根目录")
    log_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "data", description="日志目录")
    stream_mode: bool = Field(default=True, description="全局流式开关：开启则 UI 气泡按句子流式出现 + TTS 按句子推送；关闭则整段一次性输出")
    debug: bool = Field(default=False, description="是否启用调试模式")
    log_level: str = Field(default="INFO", description="日志级别")

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v):
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f'日志级别必须是以下之一: {valid_levels}')
        return v.upper()

class APIConfig(BaseModel):
    """API服务配置"""
    api_key: str = Field(default="sk-placeholder-key-not-set", description="API密钥")
    base_url: str = Field(default="https://api.openai.com/v1", description="API基础URL")
    model: str = Field(default="gpt-5.4-mini-2026-03-17", description="使用的模型名称")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数")
    max_tokens: int = Field(default=10000, ge=1, le=32768, description="最大token数")
    max_history_rounds: int = Field(default=100, ge=1, le=200, description="最大历史轮数")
    max_history_days: int = Field(default=3, ge=1, le=30, description="加载历史上下文的天数")
    applied_proxy: bool = Field(default=False, description="是否应用代理")

class MemorySystemConfig(BaseModel):
    """记忆系统配置"""
    memory_record_api_key: str = Field(default="sk-placeholder-key-not-set", description="记忆记录API密钥")
    memory_record_base_url: str = Field(default="https://api.openai.com/v1", description="记忆记录API基础URL")
    memory_record_model: str = Field(default="gpt-5-nano-2025-08-07", description="记忆记录使用的模型")
    embedding_api_key: str = Field(default="sk-placeholder-key-not-set", description="向量分析API密钥")
    embedding_base_url: str = Field(default="https://api.openai.com/v1", description="向量分析API基础URL")
    embedding_model: str = Field(default="text-embedding-3-small", description="向量分析使用的模型")
    memory_decay_factor: float = Field(default=0.8, ge=0.0, le=1.0, description="记忆衰减因子，范围0-1，值越小衰减越快,1不衰减")

class VisionAPIConfig(BaseModel):
    """图像分析API配置"""
    enabled: bool = Field(default=True, description="是否启用图像分析API")
    vision_api_key: str = Field(default="sk-placeholder-key-not-set", description="图像分析API密钥")
    vision_base_url: str = Field(default="https://api.openai.com/v1", description="图像分析API基础URL")
    vision_model: str = Field(default="gpt-image-1.5-2025-12-16", description="图像分析使用的模型")

class AgentServerConfig(BaseModel):
    """智能体服务器配置"""
    enabled: bool = Field(default=True, description="是否启用智能体服务器")
    agent_api_key: str = Field(default="sk-placeholder-key-not-set", description="智能体服务器API密钥")
    agent_base_url: str = Field(default="https://api.openai.com/v1", description="智能体服务器基础URL")
    agent_model: str = Field(default="gpt-5.4-mini-2026-03-17", description="智能体服务器使用的模型")
    agent_max_tokens: int = Field(default=4096, ge=1, description="智能体最大输出token数")
    agent_temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="智能体温度参数")


class APIServerConfig(BaseModel):
    """API服务器配置"""
    enabled: bool = Field(default=True, description="是否启用API服务器")
    host: str = Field(default="127.0.0.1", description="API服务器主机")
    port: int = Field(default_factory=lambda: server_ports.api_server, description="API服务器端口")
    auto_start: bool = Field(default=True, description="启动时自动启动API服务器")

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

class WebAgentConfig(BaseModel):
    """Web代理配置"""
    use_proxy: bool = Field(default=False, description="是否使用代理")
    http_proxy: Optional[str] = Field(default=None, description="HTTP代理地址（如 http://127.0.0.1:8080）")
    https_proxy: Optional[str] = Field(default=None, description="HTTPS代理地址（如 https://127.0.0.1:8080）")


class STTConfig(BaseModel):
    """语音输入配置。"""
    mic_device: Optional[int] = Field(
        default=None,
        description="麦克风设备索引。None 表示自动选择物理麦克风；"
                    "可通过运行 python -m sounddevice 查看可用设备列表"
    )
    whisper_model_size: str = Field(default="small", description="faster-whisper 模型大小")
    whisper_device: str = Field(default="cpu", description="faster-whisper 推理设备")
    whisper_compute_type: str = Field(default="int8", description="faster-whisper compute type")
    language: str = Field(default="zh", description="语音识别语言")

    # ---- VAD 调参 ----
    # 这些字段从 vad_voice_input._VadConfig 抬到这里，让用户可以在 config.json 里调。
    # sample_rate / channels / frame_ms 强依赖 Silero VAD（512 samples @ 16 kHz），
    # 所以不开放为可调字段，仍写死在 _VadConfig 里。
    vad_pre_roll_ms: int = Field(
        default=320, ge=0, le=2000,
        description="语音段前预录时长（毫秒）。32 ms/帧 → 默认 10 帧",
    )
    vad_end_silence_ms: int = Field(
        default=700, ge=200, le=3000,
        description="句末静音判定阈值（毫秒）。短=反应快易切句，长=完整但延迟高",
    )
    vad_min_speech_ms: int = Field(
        default=320, ge=64, le=2000,
        description="语音段最短长度，低于此值丢弃（防止咳嗽/单音节噪声触发）",
    )
    vad_speech_confirm_ms: int = Field(
        default=1000, ge=100, le=5000,
        description="持续语音超过此时长才算\"用户真的开口\"，触发 TTS 打断",
    )
    vad_max_speech_ms: int = Field(
        default=15000, ge=2000, le=60000,
        description="单段语音最长时长，超过强制切句送 ASR",
    )
    vad_threshold: float = Field(
        default=0.65, ge=0.1, le=0.95,
        description="Silero VAD speech-probability 阈值。环境吵则调高",
    )


class TTSConfig(BaseModel):
    """TTS 语音合成配置。"""
    enabled: bool = Field(default=True, description="是否启用语音输出")
    backend: str = Field(default="qwen", description="TTS 后端：qwen（本地 Qwen3-TTS）或 stepfun（StepFun 实时 TTS API）")
    stream: bool = Field(default=True, description="是否启用流式语音合成（边生成边说）。关闭则等完整回复后再整句播放")
    # StepFun 实时 TTS API 配置
    tts_api_key: str = Field(default="", description="StepFun API Key")
    tts_voice_id: str = Field(default="qingchunshaonv", description="StepFun 音色 ID，如 qingchunshaonv / cixiangnvsheng 等")
    # Qwen3-TTS 本地模型配置
    model_path: str = Field(
        default="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        description="模型ID或本地模型目录"
    )
    model_local_dir: str = Field(
        default="data/cache/models/Qwen3-TTS-12Hz-0.6B-Base",
        description="setup.ps1 下载后的本地模型目录"
    )
    device: str = Field(default="cpu", description="推理设备，当前推荐 cpu")
    dtype: str = Field(default="float32", description="torch dtype：float32 / bfloat16 / float16")
    default_voice: str = Field(default="", description="声源名称（0.6B-Base 使用 LingYiVoice.wav 声源）")
    default_speed: float = Field(default=1.0, ge=0.5, le=2.0, description="播放语速")
    default_language: str = Field(default="Auto", description="默认语言：Auto / Chinese / English 等")
    max_new_tokens: int = Field(default=2048, ge=256, le=8192, description="生成音频 token 上限")
    local_files_only: bool = Field(default=False, description="是否仅使用本地模型文件")
    playback_chunk_ms: int = Field(default=120, ge=20, le=1000, description="播放分块大小（毫秒）")

class UIConfig(BaseModel):
    """用户界面配置"""
    text_size: str = Field(default="10", description="文本大小")
    image_name: str = Field(default="LingYi_img.png", description="AI头像文件名")

class ScreenOCRConfig(BaseModel):
    """屏幕文字提取配置"""
    enabled: bool = Field(default=False, description="是否启用屏幕文字提取")
    region: list[int] = Field(default=[0, 0, 800, 200], description="截屏区域 [left, top, width, height]")
    interval: float = Field(default=0.5, ge=0.1, le=5.0, description="截屏检测间隔（秒）")
    hash_threshold: int = Field(default=5, ge=1, le=30, description="dHash 汉明距离阈值，超过此值认为画面有变化")
    stable_count: int = Field(default=2, ge=1, le=10, description="连续多少次相同才认为文字已稳定")
    lang: str = Field(default="ch", description="PaddleOCR 语言：ch / en / japan 等")

class QQConfig(BaseModel):
    """QQ相关配置"""
    bot_qq: int = Field(default=None, description="机器人QQ号")
    master_qq: int = Field(default=None, description="主人QQ号")
    speak_freq: int = Field(default=30, ge=1, le=3600, description="两次回应之间的间隔（秒）")
    onebot_ws_url: str = Field(default="http://127.0.0.1:6099", description="OneBot WebSocket URL")
    onebot_token: str = Field(default="", description="OneBot Token")
    group_whitelist: List[int] = Field(default_factory=list, description="群聊白名单，填入允许机器人响应的群号")
    group_blacklist: List[int] = Field(default_factory=list, description="群聊黑名单，当白名单为空时，黑名单生效")
    private_whitelist: List[int] = Field(default_factory=list, description="私聊白名单，填入允许机器人响应的QQ号")
    private_blacklist: List[int] = Field(default_factory=list, description="私聊黑名单，当白名单为空时，黑名单生效")

class LingYiConfig(BaseModel):
    """LingYi主配置类"""
    system: SystemConfig = Field(default_factory=SystemConfig)
    main_api: APIConfig = Field(default_factory=APIConfig)
    memory_api: MemorySystemConfig = Field(default_factory=MemorySystemConfig)
    vision_api: VisionAPIConfig = Field(default_factory=VisionAPIConfig)
    agent_api: AgentServerConfig = Field(default_factory=AgentServerConfig)
    api_server: APIServerConfig = Field(default_factory=APIServerConfig)
    grag: GRAGConfig = Field(default_factory=GRAGConfig)
    web_agent: WebAgentConfig = Field(default_factory=WebAgentConfig)

    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    screen_ocr: ScreenOCRConfig = Field(default_factory=ScreenOCRConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    qq_config: QQConfig = Field(default_factory=QQConfig)
    window: Any = Field(default=None)

    model_config = {
        "extra": "ignore",  # 保留原配置：忽略未定义的字段
        "arbitrary_types_allowed": True,  # 允许非标准类型
        "json_schema_extra": {
            "exclude": ["window"]  # 序列化到 config.json 时排除 window 字段
        }
    }
    def __init__(self, **kwargs):
        setup_environment()
        super().__init__(**kwargs)
        self.system.log_dir.mkdir(parents=True, exist_ok=True)  # 确保递归创建日志目录


# 全局配置实例
def load_config():
    """加载配置"""
    config_path = str(Path(__file__).parent.parent / "config.json")

    if os.path.exists(config_path):
        try:
            # 使用Charset Normalizer自动检测编码
            charset_results = from_path(config_path)
            if charset_results:
                best_match = charset_results.best()
                if best_match:
                    detected_encoding = best_match.encoding
                    print(f"检测到配置文件编码: {detected_encoding}")

                    # 使用检测到的编码直接打开文件，然后使用json5读取
                    with open(config_path, 'r', encoding=detected_encoding) as f:
                        # 使用json5解析支持注释的JSON
                        try:
                            config_data = json5.load(f)
                        except Exception as json5_error:
                            print(f"json5解析失败: {json5_error}")
                            print("尝试使用标准JSON库解析（将忽略注释）...")
                            # 回退到标准JSON库，但需要先去除注释
                            f.seek(0)  # 重置文件指针
                            content = f.read()
                            # 去除注释行
                            lines = content.split('\n')
                            cleaned_lines = []
                            for line in lines:
                                # 移除行内注释（#后面的内容）
                                if '#' in line:
                                    line = line.split('#')[0].rstrip()
                                if line.strip():  # 只保留非空行
                                    cleaned_lines.append(line)
                            cleaned_content = '\n'.join(cleaned_lines)
                            config_data = json.loads(cleaned_content)
                    return LingYiConfig(**config_data)
                else:
                    print(f"警告：无法检测 {config_path} 的编码")
            else:
                print(f"警告：无法检测 {config_path} 的编码")

            # 如果自动检测失败，回退到原来的方法
            print("使用回退方法加载配置")
            with open(config_path, 'r', encoding='utf-8') as f:
                # 使用json5解析支持注释的JSON
                config_data = json5.load(f)
            return LingYiConfig(**config_data)

        except Exception as e:
            print(f"警告：加载 {config_path} 失败: {e}")
            print("使用默认配置")
            return LingYiConfig()
    else:
        print(f"警告：配置文件 {config_path} 不存在，使用默认配置")

    return LingYiConfig()

config = load_config()

def reload_config() -> LingYiConfig:
    """重新加载配置"""
    global config
    config = load_config()
    notify_config_changed()
    return config

def hot_reload_config() -> LingYiConfig:
    """热更新配置 - 重新加载配置并通知所有模块"""
    global config
    old_config = config
    config = load_config()
    notify_config_changed()
    print(f"配置已热更新: {old_config.system.version} -> {config.system.version}")
    return config

def get_config() -> LingYiConfig:
    """获取当前配置"""
    return config

# 初始化时打印配置信息
if config.system.debug:
    print(f"LingYi {config.system.version} 配置已加载")
    print(f"API服务器: {'启用' if config.api_server.enabled else '禁用'} ({config.api_server.host}:{config.api_server.port})")
    print(f"GRAG记忆系统: {'启用' if config.grag.enabled else '禁用'}")

# 启动时设置用户显示名：优先config.json，其次系统用户名 #
try:
    # 检查 config.json 中的 user_name 是否为空白或未填写
    if not config.ui.user_name or not config.ui.user_name.strip():
        # 如果是，则尝试获取系统登录用户名并覆盖
        config.ui.user_name = os.getlogin()
except Exception:
    # 获取系统用户名失败时，将保留默认值 "用户" 或 config.json 中的空值
    pass

# 向后兼容的AI_NAME常量
AI_NAME = config.system.ai_name

import logging
logger = logging.getLogger(__name__)


def save_screen_ocr_region(region: list[int]) -> None:
    """将屏幕 OCR 区域保存到 config.json（保留注释和格式）。

    通过正则替换 "region": [...] 行，不破坏其余内容。
    同时更新内存中的 config 对象。
    """
    import re
    config.screen_ocr.region = list(region)

    config_path = str(Path(__file__).parent.parent / "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 匹配 "region": [数字, 数字, 数字, 数字] 及其后可能的注释
        new_region_str = f'"region": [{region[0]}, {region[1]}, {region[2]}, {region[3]}]'
        content_new = re.sub(
            r'"region"\s*:\s*\[[\d\s,]+\]',
            new_region_str,
            content,
            count=1,
        )

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content_new)

        logger.info(f"[Config] 屏幕 OCR 区域已保存: {region}")
    except Exception as e:
        logger.error(f"[Config] 保存屏幕 OCR 区域失败: {e}")
