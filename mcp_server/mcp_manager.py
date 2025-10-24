import os
import json
from typing import Dict, List

class MCPManager:
    def __init__(self):
        self.available_tools = {}
        self._load_config()

    def _load_config(self):
        """从 config.json 加载 MCP 工具配置"""
        try:
            # 获取项目根目录
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(project_root, "config.json")
            
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            # 从 mcp_agent 部分筛选启用的工具
            mcp_config = config.get("mcp_agent", {})
            self.available_tools = {
                name: details
                for name, details in mcp_config.items()
                if isinstance(details, dict) and details.get("enabled", False)
            }
            
            print(f"已加载 MCP 工具: {list(self.available_tools.keys())}")
            
        except Exception as e:
            print(f"[错误] 加载 MCP 配置失败: {e}")
            self.available_tools = {}

    def get_available_tools_description(self) -> str:
        """获取可用工具描述，用于拼接到提示词中"""
        if not self.available_tools:
            return "当前无可用工具"
        
        descriptions = []
        for name, details in self.available_tools.items():
            desc = details.get("description", "无描述")
            params = details.get("parameters", {})
            param_desc = ", ".join(f"{k}: {v}" for k, v in params.items())
            descriptions.append(f"- {name}: {desc}\n  参数: {param_desc}")
            
        return "\n".join(descriptions)

    def is_tool_available(self, tool_name: str) -> bool:
        """检查工具是否可用"""
        return tool_name in self.available_tools

# 创建全局单例
mcp_manager = MCPManager()