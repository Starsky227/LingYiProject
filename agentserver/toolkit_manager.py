#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工具包管理器 - 管理agentserver的工具包
"""

import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .tools import FileEditToolkit, AsyncBaseToolkit, ToolkitConfig

logger = logging.getLogger(__name__)


class ToolkitManager:
    """工具包管理器"""
    
    def __init__(self, config_dir: str = "agentserver/configs"):
        self.config_dir = Path(config_dir)
        self.toolkits: Dict[str, AsyncBaseToolkit] = {}
        self.toolkit_configs: Dict[str, Dict[str, Any]] = {}
        
        # 注册可用的工具包类型
        self.toolkit_types = {
            "file_edit": FileEditToolkit,
        }
        
        # 加载配置
        self._load_configs()
    
    def _load_configs(self):
        """加载工具包配置"""
        if not self.config_dir.exists():
            logger.warning(f"配置目录不存在: {self.config_dir}")
            return
            
        for config_file in self.config_dir.glob("*.yaml"):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                toolkit_name = config.get('name', config_file.stem)
                self.toolkit_configs[toolkit_name] = config
                logger.info(f"加载工具包配置: {toolkit_name}")
            except Exception as e:
                logger.error(f"加载配置文件失败 {config_file}: {e}")
    
    def get_toolkit(self, name: str) -> Optional[AsyncBaseToolkit]:
        """获取工具包实例"""
        if name in self.toolkits:
            return self.toolkits[name]
            
        if name not in self.toolkit_configs:
            logger.error(f"工具包配置不存在: {name}")
            return None
            
        config_data = self.toolkit_configs[name]
        toolkit_type = config_data.get('mode', 'builtin')
        
        if toolkit_type == 'builtin':
            toolkit_class = self.toolkit_types.get(name)
            if not toolkit_class:
                logger.error(f"未找到工具包类型: {name}")
                return None
                
            toolkit_config = ToolkitConfig(
                config=config_data.get('config', {}),
                name=name
            )
            toolkit_config.activated_tools = config_data.get('activated_tools')
            
            toolkit = toolkit_class(toolkit_config)
            self.toolkits[name] = toolkit
            logger.info(f"创建工具包实例: {name}")
            return toolkit
        else:
            logger.error(f"不支持的工具包模式: {toolkit_type}")
            return None
    
    def get_all_tools(self) -> List[Dict[str, Any]]:
        """获取所有工具包的工具列表"""
        all_tools = []
        for toolkit_name in self.toolkit_configs.keys():
            toolkit = self.get_toolkit(toolkit_name)
            if toolkit:
                tools = toolkit.get_tools_list()
                for tool in tools:
                    tool['toolkit'] = toolkit_name
                all_tools.extend(tools)
        return all_tools
    
    async def call_tool(self, toolkit_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """调用工具"""
        toolkit = self.get_toolkit(toolkit_name)
        if not toolkit:
            return f"工具包不存在: {toolkit_name}"
            
        try:
            return await toolkit.call_tool(tool_name, arguments)
        except Exception as e:
            logger.error(f"调用工具失败 {toolkit_name}.{tool_name}: {e}")
            return f"调用工具失败: {str(e)}"
    
    def list_toolkits(self) -> List[str]:
        """列出所有可用的工具包"""
        return list(self.toolkit_configs.keys())
    
    def get_toolkit_info(self, name: str) -> Optional[Dict[str, Any]]:
        """获取工具包信息"""
        if name not in self.toolkit_configs:
            return None
            
        config = self.toolkit_configs[name]
        toolkit = self.get_toolkit(name)
        
        info = {
            "name": name,
            "mode": config.get('mode', 'builtin'),
            "config": config.get('config', {}),
            "tools": []
        }
        
        if toolkit:
            info["tools"] = toolkit.get_tools_list()
            
        return info


# 全局工具包管理器实例
toolkit_manager = ToolkitManager()
