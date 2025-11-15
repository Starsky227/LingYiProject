# mcp_support.py - MCP服务支持模块
import json
import os
import importlib
import inspect
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

# 全局MCP服务池和缓存
MCP_REGISTRY = {}  # 全局MCP服务池
MANIFEST_CACHE = {}  # 缓存manifest信息

def load_manifest_file(manifest_path: Path) -> Optional[Dict[str, Any]]:
    """加载manifest文件"""
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        sys.stderr.write(f"加载manifest文件失败 {manifest_path}: {e}\n")
        return None

def create_agent_instance(manifest: Dict[str, Any]) -> Optional[Any]:
    """根据manifest创建agent实例"""
    try:
        entry_point = manifest.get('entryPoint', {})
        module_name = entry_point.get('module')
        class_name = entry_point.get('class')
        
        if not module_name or not class_name:
            sys.stderr.write(f"manifest缺少entryPoint信息: {manifest.get('name', 'unknown')}\n")
            return None
            
        # 动态导入模块
        module = importlib.import_module(module_name)
        agent_class = getattr(module, class_name)
        
        # 创建实例
        instance = agent_class()
        return instance
        
    except Exception as e:
        sys.stderr.write(f"创建agent实例失败 {manifest.get('name', 'unknown')}: {e}\n")
        return None

def scan_and_register_mcp_agents(mcp_dir: str = 'mcpserver') -> list:
    """扫描目录中的JSON元数据文件，注册MCP类型的agent"""
    d = Path(mcp_dir)
    registered_agents = []
    
    # 扫描所有agent-manifest.json文件
    for manifest_file in d.glob('**/agent-manifest.json'):
        try:
            # 加载manifest
            manifest = load_manifest_file(manifest_file)
            if not manifest:
                continue
                
            agent_type = manifest.get('agentType')
            
            # 智能推导服务名称：优先使用name，其次displayName，最后使用目录名
            agent_name = manifest.get('displayName')

            # 根据agentType进行分类处理
            if agent_type == 'mcp':
                # MCP类型：注册到MCP_REGISTRY
                MANIFEST_CACHE[agent_name] = manifest
                agent_instance = create_agent_instance(manifest)
                if agent_instance:
                    MCP_REGISTRY[agent_name] = agent_instance
                    registered_agents.append(agent_name)
                    sys.stderr.write(f"注册MCP服务: {agent_name}\n")
                    
            # 只处理MCP类型，其他类型可以在未来扩展
                    
        except Exception as e:
            sys.stderr.write(f"处理manifest文件失败 {manifest_file}: {e}\n")
            continue
    
    return registered_agents
