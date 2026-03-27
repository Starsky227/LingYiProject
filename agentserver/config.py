#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent Server配置文件
提供任务调度器和服务器配置管理
"""

from dataclasses import dataclass

# ============ 服务器配置 ============

# 从主配置读取端口
try:
    from system.config import get_server_port
    AGENT_SERVER_PORT = get_server_port("agent_server")
except ImportError:
    AGENT_SERVER_PORT = 8001  # 回退默认值

# ============ 任务调度器配置 ============

@dataclass
class TaskSchedulerConfig:
    """任务调度器配置"""
    # 记忆管理阈值
    max_steps: int = 15                    # 最大保存步骤数
    compression_threshold: int = 7          # 压缩触发阈值
    keep_last_steps: int = 4               # 压缩后保留的详细步骤数
    
    # 显示相关阈值
    key_facts_compression_limit: int = 5    # 压缩提示中的关键事实数量
    key_facts_summary_limit: int = 10       # 摘要中的关键事实数量
    compressed_memory_summary_limit: int = 3 # 任务摘要中的压缩记忆数量
    compressed_memory_global_limit: int = 2  # 全局摘要中的压缩记忆数量
    key_findings_display_limit: int = 3     # 关键发现显示数量
    failed_attempts_display_limit: int = 3  # 失败尝试显示数量
    
    # 输出长度限制
    output_summary_length: int = 256        # 关键事实中的输出摘要长度
    step_output_display_length: int = 512   # 步骤显示中的输出长度
    
    # 性能配置
    enable_auto_compression: bool = True    # 是否启用自动压缩
    compression_timeout: int = 30           # 压缩超时时间（秒）
    max_compression_retries: int = 3        # 最大压缩重试次数

# 默认任务调度器配置实例
DEFAULT_TASK_SCHEDULER_CONFIG = TaskSchedulerConfig()

# ============ 全局配置管理 ============

@dataclass
class AgentServerConfig:
    """Agent服务器全局配置"""
    # 服务器配置
    host: str = "0.0.0.0"
    port: int = None
    
    # 子模块配置
    task_scheduler: TaskSchedulerConfig = None
    
    # 日志配置
    log_level: str = "INFO"
    enable_debug_logs: bool = False
    
    def __post_init__(self):
        if self.port is None:
            self.port = AGENT_SERVER_PORT
        if self.task_scheduler is None:
            self.task_scheduler = DEFAULT_TASK_SCHEDULER_CONFIG

# 全局配置实例
config = AgentServerConfig()

# ============ 配置访问函数 ============

def get_task_scheduler_config() -> TaskSchedulerConfig:
    """获取任务调度器配置"""
    return config.task_scheduler

def update_config(**kwargs):
    """更新配置"""
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            raise ValueError(f"未知配置项: {key}")
