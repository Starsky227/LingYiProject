import os
import json
from litellm import OpenAI
import requests

from system.config import config
from typing import Dict, List, Optional, Tuple, Any
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

def load_prompt_file(filename: str, description: str = "") -> str:
    """
    加载提示词文件的通用函数
    Args:
        filename: 文件名（如 "1_intent_analyze.txt"）
        description: 文件描述（用于错误提示）
    Returns:
        文件内容字符串，失败时返回空字符串
    """
    prompt_path = os.path.join(BASE_DIR, "system", "prompts", filename)
    if not os.path.exists(prompt_path):
        error_msg = f"[错误] {description}提示词文件不存在: {prompt_path}"
        print(error_msg)
        return ""
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            print(f"[警告] {description}提示词文件为空: {prompt_path}")
        return content

# 加载各类提示词文件
INTENT_ANALYSIS_PROMPT = load_prompt_file("1_intent_analyze.txt", "意图分析")
CONVERSATION_PROMPT = load_prompt_file("1_intent_analyze.txt", "作用未知")
TOOL_CALL_PROMPT = load_prompt_file("2_tool_selection.txt", "工具调用")
MEMORY_CONTROL_PROMPT = load_prompt_file("3_memory_control.txt", "记忆控制")
TASK_REVIEW_PROMPT = load_prompt_file("4_task_review.txt", "任务审查")

def analyze_intent(message_to_proceed: str) -> Tuple[str, str]:
    """
    使用intend_analyze.txt提示词分析最后一条消息的意图
    返回: (意图类型, 具体任务)
    """
    if not INTENT_ANALYSIS_PROMPT:
        return "unknown", "[错误] 意图分析提示词未加载，请检查 1_intent_analyze.txt 文件"
    
    if DEBUG_MODE:
        print(f"[DEBUG] 意图分析接收到:\n{message_to_proceed}")
    
    try:
        # 调用模型分析意图
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": INTENT_ANALYSIS_PROMPT},
                {"role": "user", "content": message_to_proceed}
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


def tool_call(message_to_proceed: str, todo_list: str, tools_available: str) -> Dict[str, Any]:
    """
    使用tool_selection.txt提示词分析是否需要工具调用
    参数：
        messages: 对话历史
        todo_list： 任务列表
        tools_available: 可用工具信息
    返回：工具调用结果字典
    [{
        "tool_use": true/false,
        "agentType": "",
        "service_name": "",
        "tool_name": "",
        "param_name": ""
        "decision_reason": ""
    }]
    """
    if not TOOL_CALL_PROMPT:
        return {
            "tool_use": False,
            "agentType": "null",
            "service_name": "null", 
            "tool_name": "null",
            "param_name": "null",
            "decision_reason": "[错误] 工具调用提示词未加载，请检查 tool_selection.txt 文件"
        }
    
    # 消息扁平化处理
    flattened_text = ""
    flattened_text += message_to_proceed
    
    # 添加任务列表
    flattened_text += f"[任务列表]：\n{todo_list if todo_list else '无任务'}\n"
    
    # 添加可用工具
    flattened_text += f"{tools_available}\n"

    if DEBUG_MODE:
        print(f"[DEBUG] 工具调用分析输入:\n{flattened_text}")
    
    try:
        # 调用模型进行工具调用分析
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": TOOL_CALL_PROMPT},
                {"role": "user", "content": flattened_text}
            ],
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 工具调用原始响应: {repr(full_response)}")

    except Exception as e:
        print(f"[错误] 工具调用分析失败: {e}")
        return {"decision_reason": f"[错误] 模型未响应: {e}"}
    
    # 清理响应文本
    json_text = full_response.strip()
    if json_text.startswith("```"):
        json_text = json_text.replace("```json", "").replace("```", "").strip()
    
    try:
        # 解析JSON响应
        tool_call_data = json.loads(json_text)
        if DEBUG_MODE:
            print(f"[DEBUG] 成功解析工具调用JSON: {tool_call_data}")
        return tool_call_data
        
    except json.JSONDecodeError as json_error:
        # 返回解析失败的结果
        return {"decision_reason": f"JSON解析失败: {json_error}"}
    
    
def memory_control(message_to_proceed: str, todo_list: str) -> Dict[str, Any]:
    """
    使用memory_control.txt提示词分析需要提取和记忆的关键词
    参数：
        message_to_proceed: 处理过的消息文本（包含历史消息和当前消息）
        todo_list： 任务列表
    返回：
    {
        "from_memory": ["A", "B", "C"],
        "to_memory": ["需要记录的内容A","需要记录的内容B"]
    }
    """
    
    # 消息扁平化处理
    flattened_text = ""
    flattened_text += message_to_proceed
    
    # 添加任务列表
    flattened_text += f"[任务列表]：\n{todo_list}\n"

    if DEBUG_MODE:
        print(f"[DEBUG] 记忆控制分析输入:\n{flattened_text}")
    
    try:
        # 调用模型进行记忆控制分析
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": MEMORY_CONTROL_PROMPT},
                {"role": "user", "content": flattened_text}
            ],
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 记忆控制原始响应: {repr(full_response)}")

        if not full_response:
            return {
                "from_memory": [],
                "to_memory": [],
                "error": "[错误] 模型未返回响应"
            }

    except Exception as e:
        print(f"[错误] 记忆控制分析失败: {e}")
        return {
            "from_memory": [],
            "to_memory": [],
            "error": f"[错误] 模型调用失败: {e}"
        }
    
    # 清理响应文本
    json_text = full_response.strip()
    if json_text.startswith("```"):
        json_text = json_text.replace("```json", "").replace("```", "").strip()
    
    try:
        # 解析JSON响应
        memory_data = json.loads(json_text)
        if DEBUG_MODE:
            print(f"[DEBUG] 成功解析记忆控制JSON: {memory_data}")
        
        # 确保返回的数据格式正确
        result = {
            "from_memory": [],
            "to_memory": []
        }
        
        # 处理from_memory字段
        if "from_memory" in memory_data:
            from_memory = memory_data["from_memory"]
            if isinstance(from_memory, (list, set)):
                result["from_memory"] = list(from_memory)
            elif isinstance(from_memory, str):
                # 如果是字符串，尝试分割
                result["from_memory"] = [from_memory] if from_memory else []
        
        # 处理to_memory字段
        if "to_memory" in memory_data:
            to_memory = memory_data["to_memory"]
            if isinstance(to_memory, (list, set)):
                result["to_memory"] = list(to_memory)
            elif isinstance(to_memory, str):
                # 如果是字符串，尝试分割
                result["to_memory"] = [to_memory] if to_memory else []
        
        if DEBUG_MODE:
            print(f"[DEBUG] 记忆控制结果 - 从记忆搜索: {result['from_memory']}")
            print(f"[DEBUG] 记忆控制结果 - 记入记忆: {result['to_memory']}")
        
        return result
        
    except json.JSONDecodeError as json_error:
        if DEBUG_MODE:
            print(f"[DEBUG] 记忆控制JSON解析失败: {json_error}")
            print(f"[DEBUG] 尝试解析的文本: {repr(json_text)}")
        
        # 返回解析失败的结果
        return {
            "from_memory": [],
            "to_memory": [],
            "error": f"JSON解析失败: {json_error}"
        }


def task_completion_check(message_to_proceed: str, todo_list: str, tool_call_results: List[Dict], relevant_memories: str, memories_recorded: List[str]) -> Dict[str, Any]:
    """
    使用4_task_review提示词检查任务是否完成
    参数：
        message_to_proceed: 处理过的消息文本（包含历史消息和当前消息）
        todo_list: 任务列表
        tool_call_results: 工具调用结果列表
        relevant_memories: 相关记忆内容
        memories_recorded: 已存入记忆的内容列表
    返回：
    {
        "mission_completed": true/false,
        "todo_list": "更新后的任务列表",
        "decision_reason": "推理理由"
    }
    """
    if not TASK_REVIEW_PROMPT:
        return {
            "mission_completed": False,
            "todo_list": todo_list,
            "decision_reason": "[错误] 任务审查提示词未加载，请检查 4_task_review 文件"
        }
    
    # 构建输入文本
    flattened_text = ""
    
    # 添加消息内容
    flattened_text += message_to_proceed
    
    # 添加任务列表
    flattened_text += f"[任务列表]：\n{todo_list if todo_list else '无任务'}\n"
    
    # 添加工具输出
    flattened_text += "[工具输出]：\n"
    if tool_call_results:
        for i, tool_result in enumerate(tool_call_results):
            agent_type = tool_result.get("agent_type", "unknown")
            result = tool_result.get("result", "")
            call_index = tool_result.get("call_index", i + 1)
            flattened_text += f"工具调用 {call_index} ({agent_type})：{result}\n"
    else:
        flattened_text += "无工具调用\n"
    
    # 添加相关记忆
    flattened_text += f"[相关记忆]：\n{relevant_memories if relevant_memories else '无相关记忆'}\n"
    
    # 添加记忆存储
    flattened_text += "[记忆存储]：\n"
    if memories_recorded:
        for memory in memories_recorded:
            flattened_text += f"- {memory}\n"
    else:
        flattened_text += "无新存入记忆\n"

    if DEBUG_MODE:
        print(f"[DEBUG] 任务审查分析输入:\n{flattened_text}")
    
    try:
        # 调用模型进行任务完成检查
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": TASK_REVIEW_PROMPT},
                {"role": "user", "content": flattened_text}
            ],
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 任务审查原始响应: {repr(full_response)}")

        if not full_response:
            return {
                "mission_completed": False,
                "todo_list": todo_list,
                "decision_reason": "[错误] 模型未返回响应"
            }

    except Exception as e:
        print(f"[错误] 任务审查分析失败: {e}")
        return {
            "mission_completed": False,
            "todo_list": todo_list,
            "decision_reason": f"[错误] 模型调用失败: {e}"
        }
    
    # 清理响应文本
    json_text = full_response.strip()
    if json_text.startswith("```"):
        json_text = json_text.replace("```json", "").replace("```", "").strip()
    
    try:
        # 解析JSON响应
        review_data = json.loads(json_text)
        if DEBUG_MODE:
            print(f"[DEBUG] 成功解析任务审查JSON: {review_data}")
        
        # 确保返回的数据格式正确
        result = {
            "mission_completed": review_data.get("mission_completed", False),
            "todo_list": review_data.get("todo_list", todo_list),
            "decision_reason": review_data.get("decision_reason", "未提供理由")
        }
        
        if DEBUG_MODE:
            print(f"[DEBUG] 任务审查结果 - 任务完成: {result['mission_completed']}")
            print(f"[DEBUG] 任务审查结果 - 更新任务: {result['todo_list']}")
            print(f"[DEBUG] 任务审查结果 - 决策理由: {result['decision_reason']}")
        
        return result
        
    except json.JSONDecodeError as json_error:
        if DEBUG_MODE:
            print(f"[DEBUG] 任务审查JSON解析失败: {json_error}")
            print(f"[DEBUG] 尝试解析的文本: {repr(json_text)}")
        
        # 返回解析失败的结果
        return {
            "mission_completed": False,
            "todo_list": todo_list,
            "decision_reason": f"JSON解析失败: {json_error}"
        }


def analyze_intent_with_tools(messages: List[Dict]) -> Tuple[str, str, Dict]:
    """
    分析最后一条消息的意图并返回工具调用信息
    返回: (意图, 解释, 工具调用信息)
    参数：
        messages: 对话历史
        todo_list： 任务列表
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

