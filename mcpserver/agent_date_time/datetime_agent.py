# -*- coding: utf-8 -*-
"""
DateTime Agent - 系统时间读取 MCP 工具
提供当前系统时间读取功能
"""
import datetime
from typing import Dict, Any
import json


class DateTimeAgent:
    """DateTime MCP Agent - 获取当前时间"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化 DateTime Agent
        
        Args:
            config: 配置字典
        """
        self.config = config or {}
        self.default_timezone = self.config.get('default_timezone', 'Asia/Shanghai')
        self.default_format = self.config.get('default_format', 'readable')
        
        # 中文星期映射
        self.weekday_zh = {
            0: '星期一', 1: '星期二', 2: '星期三', 3: '星期四',
            4: '星期五', 5: '星期六', 6: '星期日'
        }
        

    
    def get_current_time(self) -> Dict[str, Any]:
        """
        获取当前系统时间
        
        Returns:
            包含时间信息的字典
        """
        try:
            # 获取本地系统时间
            now = datetime.datetime.now()
            
            # 获取星期几
            weekday_num = now.weekday()
            weekday = self.weekday_zh[weekday_num]
            
            # 使用可读格式
            formatted_time = now.strftime('%Y年%m月%d日 %H时%M分%S秒')
            
            return {
                "status": "success",
                "message": "成功获取当前系统时间",
                "data": {
                    "current_time": formatted_time,
                    "timestamp": int(now.timestamp()),
                    "weekday": weekday,
                    "year": now.year,
                    "month": now.month,
                    "day": now.day,
                    "hour": now.hour,
                    "minute": now.minute,
                    "second": now.second
                }
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": f"获取时间失败: {str(e)}",
                "error": str(e)
            }

    
    def process_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理 MCP 请求
        
        Args:
            request: 请求参数
            
        Returns:
            处理结果
        """
        try:
            tool_name = request.get('tool_name', '')
            
            if tool_name == '获取当前时间':
                return self.get_current_time()
            else:
                return {
                    "status": "error",
                    "message": f"未知的工具名称: {tool_name}",
                    "error": f"Unknown tool name: {tool_name}"
                }
                
        except Exception as e:
            return {
                "status": "error",
                "message": f"处理请求失败: {str(e)}",
                "error": str(e)
            }

    async def handle_handoff(self, data: Dict[str, Any]) -> str:
        """
        标准MCP handoff接口
        
        Args:
            data: handoff数据，包含tool_name和其他参数
            
        Returns:
            JSON字符串格式的处理结果
        """
        try:
            # 调用process_request处理请求
            result = self.process_request(data)
            # 返回JSON字符串
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            error_result = {
                "status": "error",
                "message": f"Handoff处理失败: {str(e)}",
                "error": str(e)
            }
            return json.dumps(error_result, ensure_ascii=False)

    async def 获取当前时间(self) -> str:
        """
        直接工具调用方法 - 供MCP管理器直接调用
        
        Returns:
            JSON字符串格式的时间结果
        """
        result = self.get_current_time()
        return json.dumps(result, ensure_ascii=False)


def create_datetime_agent(config: Dict[str, Any] = None) -> DateTimeAgent:
    """
    创建 DateTime Agent 实例
    
    Args:
        config: 配置参数
        
    Returns:
        DateTimeAgent 实例
    """
    return DateTimeAgent(config)


def validate_agent_config(config: Dict[str, Any]) -> bool:
    """
    验证 Agent 配置
    
    Args:
        config: 配置字典
        
    Returns:
        是否有效
    """
    # DateTime agent 不需要特殊配置验证
    return True


def get_agent_dependencies() -> list:
    """
    获取 Agent 依赖
    
    Returns:
        依赖列表
    """
    return []


# 测试代码
if __name__ == "__main__":
    agent = DateTimeAgent()
    
    # 测试获取当前系统时间
    print("=== 测试获取当前系统时间 ===")
    result = agent.process_request({"tool_name": "获取当前时间"})
    print(json.dumps(result, ensure_ascii=False, indent=2))