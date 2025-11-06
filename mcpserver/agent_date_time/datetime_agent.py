# -*- coding: utf-8 -*-
"""
DateTime Agent - 系统时间获取 MCP 工具
提供当前时间获取、时间计算、格式转换等功能
"""
import datetime
import pytz
import calendar
from typing import Dict, Any, Optional
from dateutil.relativedelta import relativedelta
import json


class DateTimeAgent:
    """DateTime MCP Agent - 处理时间相关操作"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化 DateTime Agent
        
        Args:
            config: 配置字典
        """
        self.config = config or {}
        self.default_timezone = self.config.get('default_timezone', 'Asia/Shanghai')
        self.default_format = self.config.get('default_format', 'ISO')
        self.default_language = self.config.get('default_language', 'zh')
        
        # 中文星期映射
        self.weekday_zh = {
            0: '星期一', 1: '星期二', 2: '星期三', 3: '星期四',
            4: '星期五', 5: '星期六', 6: '星期日'
        }
        
        # 英文星期映射
        self.weekday_en = {
            0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday',
            4: 'Friday', 5: 'Saturday', 6: 'Sunday'
        }
    
    def get_current_time(self, format_type: str = None, timezone: str = None, language: str = None) -> Dict[str, Any]:
        """
        获取当前时间
        
        Args:
            format_type: 时间格式类型
            timezone: 时区
            language: 语言
            
        Returns:
            包含时间信息的字典
        """
        try:
            # 使用默认值
            format_type = format_type or self.default_format
            timezone = timezone or self.default_timezone
            language = language or self.default_language
            
            # 获取指定时区的当前时间
            if timezone:
                try:
                    tz = pytz.timezone(timezone)
                    now = datetime.datetime.now(tz)
                except pytz.exceptions.UnknownTimeZoneError:
                    # 如果时区无效，使用本地时间
                    now = datetime.datetime.now()
                    timezone = "本地时区"
            else:
                now = datetime.datetime.now()
                timezone = "本地时区"
            
            # 获取星期几
            weekday_num = now.weekday()
            if language == 'en':
                weekday = self.weekday_en[weekday_num]
            else:
                weekday = self.weekday_zh[weekday_num]
            
            # 根据格式类型返回不同格式的时间
            if format_type.upper() == 'ISO':
                formatted_time = now.isoformat()
            elif format_type.upper() == 'READABLE':
                if language == 'en':
                    formatted_time = now.strftime('%Y-%m-%d %H:%M:%S %Z')
                else:
                    formatted_time = now.strftime('%Y年%m月%d日 %H时%M分%S秒')
            elif format_type.upper() == 'TIMESTAMP':
                formatted_time = str(int(now.timestamp()))
            else:
                # 自定义格式
                try:
                    formatted_time = now.strftime(format_type)
                except (ValueError, TypeError):
                    formatted_time = now.isoformat()
            
            return {
                "status": "success",
                "message": "成功获取当前时间",
                "data": {
                    "current_time": formatted_time,
                    "timestamp": int(now.timestamp()),
                    "timezone": timezone,
                    "weekday": weekday,
                    "formatted_time": formatted_time,
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
    
    def calculate_time(self, operation: str, amount: int, unit: str, 
                      base_time: datetime.datetime = None) -> Dict[str, Any]:
        """
        时间计算
        
        Args:
            operation: 操作类型 (add/subtract)
            amount: 数量
            unit: 单位
            base_time: 基准时间，默认为当前时间
            
        Returns:
            计算结果
        """
        try:
            if base_time is None:
                base_time = datetime.datetime.now()
            
            # 单位映射
            unit_mapping = {
                'seconds': 'seconds',
                'minutes': 'minutes', 
                'hours': 'hours',
                'days': 'days',
                'weeks': 'weeks',
                'months': 'months',
                'years': 'years'
            }
            
            if unit not in unit_mapping:
                return {
                    "status": "error",
                    "message": f"不支持的时间单位: {unit}",
                    "error": f"Unsupported time unit: {unit}"
                }
            
            # 执行计算
            if operation.lower() == 'add':
                if unit in ['months', 'years']:
                    if unit == 'months':
                        result_time = base_time + relativedelta(months=amount)
                    else:  # years
                        result_time = base_time + relativedelta(years=amount)
                else:
                    delta_kwargs = {unit: amount}
                    result_time = base_time + datetime.timedelta(**delta_kwargs)
            elif operation.lower() == 'subtract':
                if unit in ['months', 'years']:
                    if unit == 'months':
                        result_time = base_time - relativedelta(months=amount)
                    else:  # years
                        result_time = base_time - relativedelta(years=amount)
                else:
                    delta_kwargs = {unit: amount}
                    result_time = base_time - datetime.timedelta(**delta_kwargs)
            else:
                return {
                    "status": "error",
                    "message": f"不支持的操作类型: {operation}",
                    "error": f"Unsupported operation: {operation}"
                }
            
            # 获取星期几
            weekday = self.weekday_zh[result_time.weekday()]
            
            return {
                "status": "success",
                "message": f"成功计算时间: {operation} {amount} {unit}",
                "data": {
                    "original_time": base_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "result_time": result_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "formatted_result": result_time.strftime('%Y年%m月%d日 %H时%M分%S秒'),
                    "timestamp": int(result_time.timestamp()),
                    "weekday": weekday,
                    "operation_summary": f"{operation} {amount} {unit}"
                }
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": f"时间计算失败: {str(e)}",
                "error": str(e)
            }
    
    def convert_time_format(self, input_time: str, output_format: str, 
                           input_format: str = None) -> Dict[str, Any]:
        """
        时间格式转换
        
        Args:
            input_time: 输入时间字符串
            output_format: 输出格式
            input_format: 输入格式（可选）
            
        Returns:
            转换结果
        """
        try:
            # 如果没有指定输入格式，尝试自动解析
            if input_format is None:
                # 尝试常见格式
                common_formats = [
                    '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%d',
                    '%Y/%m/%d %H:%M:%S',
                    '%Y/%m/%d',
                    '%Y年%m月%d日 %H时%M分%S秒',
                    '%Y年%m月%d日',
                    '%m/%d/%Y %H:%M:%S',
                    '%m/%d/%Y',
                    '%d/%m/%Y %H:%M:%S',
                    '%d/%m/%Y'
                ]
                
                parsed_time = None
                for fmt in common_formats:
                    try:
                        parsed_time = datetime.datetime.strptime(input_time, fmt)
                        break
                    except ValueError:
                        continue
                
                if parsed_time is None:
                    # 尝试使用 dateutil 解析
                    from dateutil import parser
                    try:
                        parsed_time = parser.parse(input_time)
                    except Exception:
                        return {
                            "status": "error",
                            "message": f"无法解析时间格式: {input_time}",
                            "error": f"Cannot parse time format: {input_time}"
                        }
            else:
                # 使用指定的输入格式
                parsed_time = datetime.datetime.strptime(input_time, input_format)
            
            # 转换为输出格式
            formatted_result = parsed_time.strftime(output_format)
            weekday = self.weekday_zh[parsed_time.weekday()]
            
            return {
                "status": "success",
                "message": "时间格式转换成功",
                "data": {
                    "original_time": input_time,
                    "converted_time": formatted_result,
                    "timestamp": int(parsed_time.timestamp()),
                    "weekday": weekday,
                    "year": parsed_time.year,
                    "month": parsed_time.month,
                    "day": parsed_time.day,
                    "hour": parsed_time.hour,
                    "minute": parsed_time.minute,
                    "second": parsed_time.second
                }
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": f"时间格式转换失败: {str(e)}",
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
                return self.get_current_time(
                    format_type=request.get('format'),
                    timezone=request.get('timezone'),
                    language=request.get('language')
                )
            elif tool_name == '时间计算':
                return self.calculate_time(
                    operation=request.get('operation'),
                    amount=request.get('amount'),
                    unit=request.get('unit')
                )
            elif tool_name == '时间格式转换':
                return self.convert_time_format(
                    input_time=request.get('input_time'),
                    output_format=request.get('output_format'),
                    input_format=request.get('input_format')
                )
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
    return ['pytz', 'python-dateutil']


# 测试代码
if __name__ == "__main__":
    agent = DateTimeAgent()
    
    # 测试获取当前时间
    print("=== 测试获取当前时间 ===")
    result = agent.process_request({"tool_name": "获取当前时间"})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 测试时间计算
    print("\n=== 测试时间计算 ===")
    result = agent.process_request({
        "tool_name": "时间计算",
        "operation": "add",
        "amount": 7,
        "unit": "days"
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 测试时间格式转换
    print("\n=== 测试时间格式转换 ===")
    result = agent.process_request({
        "tool_name": "时间格式转换",
        "input_time": "2025-11-04 14:30:00",
        "output_format": "%Y年%m月%d日 %H时%M分"
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))