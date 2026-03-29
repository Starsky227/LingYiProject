#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent Server配置文件
提供服务器端口配置
"""

# 从主配置读取端口
try:
    from system.config import get_server_port
    AGENT_SERVER_PORT = get_server_port("agent_server")
except ImportError:
    AGENT_SERVER_PORT = 8001  # 回退默认值
