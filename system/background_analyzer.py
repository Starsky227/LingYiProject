import os
import json
from litellm import OpenAI
import requests

from system.config import config
from typing import Dict, List, Optional, Tuple
from mcpserver.mcp_manager import get_mcp_manager

# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# API 配置
API_KEY = config.api.api_key
API_URL = config.api.base_url
MODEL = config.api.model
AI_NAME = config.system.ai_name
USERNAME = config.ui.username
DEBUG_MODE = config.system.debug

# 初始化 OpenAI 客户端
client = OpenAI(
    api_key=API_KEY,
    base_url=API_URL
)

# 读取意图分析提示词
INTENT_ANALYSIS_PROMPT = ""
prompt_path = os.path.join(BASE_DIR, "system", "prompts", "intent_analyze.txt")
if os.path.exists(prompt_path):
    with open(prompt_path, "r", encoding="utf-8") as f:
        INTENT_ANALYSIS_PROMPT = f.read().strip()

# 读取对话分析提示词
CONVERSATION_PROMPT = ""
prompt_path = os.path.join(BASE_DIR, "system", "prompts", "intent_analyze.txt")
if os.path.exists(prompt_path):
    with open(prompt_path, "r", encoding="utf-8") as f:
        CONVERSATION_PROMPT = f.read().strip()

# 读取任务规划提示词
TASK_PLANNER_PROMPT = ""
prompt_path = os.path.join(BASE_DIR, "system", "prompts", "task_planner.txt")
if os.path.exists(prompt_path):
    with open(prompt_path, "r", encoding="utf-8") as f:
        TASK_PLANNER_PROMPT = f.read().strip()

def analyze_intent(messages: List[Dict]) -> Tuple[str, str]:
    """
    使用intend_analyze.txt提示词分析最后一条消息的意图
    返回: (意图类型, 具体任务)
    """
    if not INTENT_ANALYSIS_PROMPT:
        return "unknown", "[错误] 意图分析提示词未加载，请检查 intent_analyze.txt 文件"

    # 将信息分为最新消息和历史消息
    new_message = messages[-1]
    history_messages = messages[:-1] if len(messages) > 1 else []
    
    # 消息扁平化处理
    flattened_text = ""
    
    # 处理历史消息
    flattened_text += "[历史消息]：\n"
    if history_messages:
        for msg in history_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "").strip()
            flattened_text += f"<{role}>{content}\n"
    
    # 处理当前消息
    flattened_text += "[当前消息]：\n"
    if new_message:
        role = new_message.get("role", "unknown")
        content = new_message.get("content", "").strip()
        flattened_text += f"<{role}>{content}\n"
    
    if DEBUG_MODE:
        print(f"[DEBUG] 意图分析接收到:\n{flattened_text}")
    
    try:
        # 调用模型分析意图
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": INTENT_ANALYSIS_PROMPT},
                {"role": "user", "content": flattened_text}
            ],
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 模型原始响应: {repr(full_response)}")
        
        if not full_response:
            return "unknown", "[错误]模型未返回响应"

        # 清理markdown格式
        json_text = full_response.strip()
        if json_text.startswith("```"):
            json_text = json_text.replace("```json", "").replace("```", "").strip()
        
        try:
            # 解析JSON响应
            intent_data = json.loads(json_text)
            if DEBUG_MODE:
                print(f"[DEBUG] 成功解析JSON: {intent_data}")
            return (
                intent_data.get("IntentType", "unknown"),
                intent_data.get("TasksTodo", "[错误]模型未找到任务")
            )
        except json.JSONDecodeError as json_error:
            if DEBUG_MODE:
                print(f"[DEBUG] JSON解析失败: {json_error}")
                print(f"[DEBUG] 尝试解析的文本: {repr(json_text)}")
            
            # 简单的文本提取作为备用
            intent_type = tasks_todo = "unknown"
            return intent_type, tasks_todo

    except Exception as e:
        print(f"[错误] 意图分析失败: {e}")
        return "unknown", "分析失败"


def plan_tasks(messages: List[Dict], tasks_todo: str) -> str:
    """
    使用task_planner.txt提示词规划任务步骤
    返回：任务规划结果文本字符串
    """
    if not TASK_PLANNER_PROMPT:
        return "[错误] 任务规划提示词未加载，请检查 task_planner.txt 文件"
    
    # 将信息分为最新消息和历史消息
    new_message = messages[-1] if messages else None
    history_messages = messages[:-1] if len(messages) > 1 else []
    
    # 消息扁平化处理
    flattened_text = ""
    
    # 处理历史消息
    flattened_text += "[历史消息]：\n"
    if history_messages:
        for msg in history_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "").strip()
            flattened_text += f"<{role}>{content}\n"
    
    # 处理当前消息
    flattened_text += "[当前消息]：\n"
    if new_message:
        role = new_message.get("role", "unknown")
        content = new_message.get("content", "").strip()
        flattened_text += f"<{role}>{content}\n"
    
    # 添加当前任务
    flattened_text += f"[当前任务]：\n{tasks_todo}\n"

    if DEBUG_MODE:
        print(f"[DEBUG] 任务规划输入:\n{flattened_text}")
    
    try:
        # 调用模型进行任务规划
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": TASK_PLANNER_PROMPT},
                {"role": "user", "content": flattened_text}
            ],
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 任务规划原始响应: {repr(full_response)}")
        
        if not full_response:
            return "[错误] 模型未返回响应，请重试"
        
        # 直接返回模型的文本响应
        task_plan_result = full_response.strip()
        
        if DEBUG_MODE:
            print(f"[DEBUG] 任务规划结果: {task_plan_result}")
        
        return task_plan_result
    
    except Exception as e:
        print(f"[错误] 任务规划失败: {e}")
        return f"[错误] 任务规划调用失败: {e}，请检查网络连接或重试"


def analyze_intent_with_tools(messages: List[Dict]) -> Tuple[str, str, Dict]:
    """
    分析最后一条消息的意图并返回工具调用信息
    返回: (意图, 解释, 工具调用信息)
    """
    # 获取最后一条消息
    last_message = None
    if messages:
        last_message = messages[-1]
    
    # 获取除最后一条外的历史消息
    history_messages = messages[:-1] if len(messages) > 1 else []
    
    # 消息扁平化处理
    flattened_text = ""
    
    # 处理历史消息
    if history_messages:
        flattened_text += "【历史消息】：\n"
        for msg in history_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "").strip()
            if content:
                flattened_text += f"<{role}>{content}\n"
    
    # 处理当前消息
    if last_message:
        flattened_text += "【当前消息】：\n"
        role = last_message.get("role", "unknown")
        content = last_message.get("content", "").strip()
        if content:
            flattened_text += f"<{role}>{content}\n"
    
    # 获取最后一条消息的内容（用于后续处理）
    last_user_msg = last_message.get("content", "") if last_message else ""
    
    if not last_user_msg:
        return "unknown", "无消息内容", {}

    try:
        # 准备提示词（替换对话内容和可用工具）
        try:
            # 从mcp_registry获取详细的服务信息
            from mcpserver.mcp_registry import get_all_services_info, MCP_REGISTRY
            
            services_info = get_all_services_info()
            available_tools_list = []
            
            # 构建详细的工具信息
            for service_name, service_info in services_info.items():
                service_desc = {
                    "service_name": service_name,
                    "description": service_info.get('description', ''),
                    "display_name": service_info.get('display_name', service_name),
                    "tools": []
                }
                
                # 获取该服务的工具列表
                tools = service_info.get('available_tools', [])
                for tool in tools:
                    tool_info = {
                        "name": tool.get('name', ''),
                        "description": tool.get('description', ''),
                        "example": tool.get('example', ''),
                        "input_schema": tool.get('input_schema', {})
                    }
                    service_desc["tools"].append(tool_info)
                
                available_tools_list.append(service_desc)
            
            # 格式化为可读的文本供AI理解
            if available_tools_list:
                available_tools_desc = "## 可用工具和服务\n\n"
                for service in available_tools_list:
                    available_tools_desc += f"### {service['service_name']}\n"
                    available_tools_desc += f"描述: {service['description']}\n"
                    
                    if service['tools']:
                        available_tools_desc += "可用工具:\n"
                        for tool in service['tools']:
                            available_tools_desc += f"- **{tool['name']}**: {tool['description']}\n"
                            if tool['example']:
                                available_tools_desc += f"  示例: {tool['example']}\n"
                    available_tools_desc += "\n"
            else:
                available_tools_desc = "当前无可用工具"
                
            if DEBUG_MODE:
                print(f"[DEBUG] 找到 {len(available_tools_list)} 个可用服务")
                print(f"[DEBUG] 工具描述长度: {len(available_tools_desc)} 字符")
                
        except Exception as e:
            if DEBUG_MODE:
                print(f"[DEBUG] 获取可用工具失败: {e}")
                import traceback
                traceback.print_exc()
            available_tools_desc = "获取工具信息失败"
        
        prompt = CONVERSATION_PROMPT.replace(
            "{conversation}", flattened_text
        ).replace(
            "{available_tools}", available_tools_desc
        )

        # 构建分析请求
        analysis_messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": flattened_text}
        ]

        # 调用模型分析意图
        response = client.chat.completions.create(
            model=MODEL,
            messages=analysis_messages,
            stream=False,
            temperature=0.7
        )
        
        if response:
            # 获取模型输出
            full_response = response.choices[0].message.content
            
            # 解析意图分析结果
            intent = "unknown"
            explanation = ""
            tool_call_info = {}
            
            try:
                response_text = full_response.strip()
                
                # 如果返回空对象，表示没有可执行任务
                if response_text == "｛｝" or response_text == "{}":
                    intent = "chat"
                    explanation = "普通对话，无需工具调用"
                else:
                    # 处理非标准的中文大括号
                    json_text = response_text.replace("｛", "{").replace("｝", "}")
                    
                    try:
                        tool_call_data = json.loads(json_text)
                        
                        # 提取工具调用信息
                        agent_type = tool_call_data.get("agentType", "")
                        service_name = tool_call_data.get("service_name", "")
                        tool_name = tool_call_data.get("tool_name", "")
                        user_intent = tool_call_data.get("user_intent", "")
                        
                        if agent_type and (service_name or tool_name):
                            if agent_type == "mcp":
                                intent = f"mcp_call_{service_name}_{tool_name}"
                                explanation = f"MCP工具调用: {service_name}.{tool_name}"
                            elif agent_type == "agent":
                                task_type = tool_call_data.get("task_type", "")
                                intent = f"agent_call_{task_type}"
                                explanation = f"Agent任务调用: {task_type}"
                            else:
                                intent = f"tool_call_{agent_type}"
                                explanation = f"工具调用: {agent_type}"
                            
                            if user_intent:
                                explanation += f" - {user_intent}"
                            
                            tool_call_info = tool_call_data
                        else:
                            intent = "chat"
                            explanation = "对话内容，未识别到明确的工具调用"
                            
                    except json.JSONDecodeError:
                        if "agentType" in response_text:
                            intent = "tool_call_detected"
                            explanation = "检测到工具调用请求，但格式解析失败"
                        else:
                            intent = "chat"
                            explanation = "普通对话内容"
                
            except Exception as parse_error:
                print(f"[警告] 意图解析出错: {parse_error}")
                intent = "unknown"
                explanation = "意图解析失败"
            
            print(f"用户意图识别为: {intent}")
            print(f"解释: {explanation}")
            if tool_call_info:
                print(f"工具调用信息: {tool_call_info}")
            
            return intent, explanation, tool_call_info

    except Exception as e:
        print(f"[错误] 意图分析失败: {e}")
    
    return "unknown", "分析失败", {}

