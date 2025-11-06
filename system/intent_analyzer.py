import os
import json
from litellm import OpenAI
import requests

from system.config import config
from typing import Dict, List, Optional, Tuple
from mcpserver import mcp_manager

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
INTENT_PROMPT = ""
prompt_path = os.path.join(BASE_DIR, "system", "prompts", "intent_analyze.txt")
if os.path.exists(prompt_path):
    with open(prompt_path, "r", encoding="utf-8") as f:
        INTENT_PROMPT = f.read().strip()

# 读取对话分析提示词
CONVERSATION_PROMPT = ""
prompt_path = os.path.join(BASE_DIR, "system", "prompts", "conversation_analyzer_prompt.txt")
if os.path.exists(prompt_path):
    with open(prompt_path, "r", encoding="utf-8") as f:
        CONVERSATION_PROMPT = f.read().strip()

def analyze_intent(messages: List[Dict]) -> Tuple[str, str]:
    """
    使用intend_analyze.txt提示词分析最后一条消息的意图
    返回: (意图类型, 具体任务)
    """
    # 获取最后一条用户消息
    last_user_msg = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content")
            break
    
    if not last_user_msg:
        return "unknown", "无用户消息"

    try:
        # 调用模型分析意图
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": INTENT_PROMPT},
                {"role": "user", "content": last_user_msg}
            ],
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if not full_response:
            return "unknown", "模型未返回响应"

        # 清理markdown格式
        json_text = full_response.strip()
        if json_text.startswith("```"):
            json_text = json_text.replace("```json", "").replace("```", "").strip()
        
        try:
            # 解析JSON响应
            intent_data = json.loads(json_text)
            return (
                intent_data.get("IntentType", "unknown"),
                intent_data.get("TasksTodo", "无具体任务")
            )
        except json.JSONDecodeError:
            # 简单的文本提取作为备用
            intent_type = tasks_todo = "unknown"
            for line in full_response.split('\n'):
                if '"IntentType"' in line and ':' in line:
                    intent_type = line.split(':')[1].strip().strip('"').strip(',')
                elif '"TasksTodo"' in line and ':' in line:
                    tasks_todo = line.split(':')[1].strip().strip('"').strip(',')
            return intent_type, tasks_todo

    except Exception as e:
        print(f"[错误] 意图分析失败: {e}")
        return "unknown", "分析失败"


def analyze_intent_with_tools(messages: List[Dict]) -> Tuple[str, str, Dict]:
    """
    分析最后一条消息的意图并返回工具调用信息
    返回: (意图, 解释, 工具调用信息)
    """
    # 获取最后一条用户消息
    last_user_msg = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content")
            break
    
    if not last_user_msg:
        return "unknown", "无用户消息", {}

    try:
        # 准备提示词（替换对话内容和可用工具）
        prompt = CONVERSATION_PROMPT.replace(
            "{conversation}", last_user_msg
        ).replace(
            "{available_tools}", mcp_manager.get_available_tools_description()
        )

        # 构建分析请求
        analysis_messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": last_user_msg}
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
