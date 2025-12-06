import os
import json
import re
import logging
from litellm import OpenAI
import requests

from system.config import config

# 设置 LiteLLM 日志级别，减少调试输出
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from typing import Dict, List, Optional, Tuple, Any, Callable
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
EXTRACT_KEYWORDS_PROMPT = load_prompt_file("0_extract_keywords.txt", "关键词提取")
INTENT_ANALYSIS_PROMPT = load_prompt_file("1_intent_analyze.txt", "意图分析")
GENERATE_RESPONSE_PROMPT = load_prompt_file("2_generate_response.txt", "生成回复")
TOOL_CALL_PROMPT = load_prompt_file("2_tool_selection.txt", "工具调用")
MEMORY_CONTROL_PROMPT = load_prompt_file("3_memory_control.txt", "记忆控制")
COMPLETION_CHECK_PROMPT = load_prompt_file("3_completion_check.txt", "任务完成检查")


def _parse_json(text: str) -> Dict[str, Any]:
    """
    将文本转换为JSON对象，智能处理字符串值中的控制字符
    """
    try:
        # 首先清理markdown格式
        cleaned_text = text.replace("```json", "").replace("```", "").strip()
        
        # 尝试直接解析
        try:
            return json.loads(cleaned_text)
        except json.JSONDecodeError:
            # 如果直接解析失败，尝试智能修复字符串值中的控制字符
            fixed_text = _fix_json_string_values(cleaned_text)
            return json.loads(fixed_text)
            
    except json.JSONDecodeError as json_error:
        print(f"[错误] JSON解析失败: {json_error}")
        return {}


def _fix_json_string_values(json_text: str) -> str:
    """
    智能修复JSON中字符串值的控制字符问题
    只转义字符串值内的控制字符，不影响JSON结构
    """
    result = ""
    in_string = False
    escape_next = False
    i = 0
    
    while i < len(json_text):
        char = json_text[i]
        
        if escape_next:
            # 如果前一个字符是转义符，直接添加当前字符
            result += char
            escape_next = False
        elif char == '\\':
            # 遇到转义符
            result += char
            escape_next = True
        elif char == '"':
            # 遇到引号，切换字符串状态
            result += char
            in_string = not in_string
        elif in_string:
            # 在字符串内部，转义控制字符
            if char == '\n':
                result += '\\n'
            elif char == '\t':
                result += '\\t'
            elif char == '\r':
                result += '\\r'
            else:
                result += char
        else:
            # 在JSON结构中，保持原样
            result += char
        
        i += 1
    
    return result


def extract_keywords(message_to_proceed: List[Dict]) -> List[str]:
    """
    使用0_extract_keywords.txt提示词从消息中提取关键词
    参数：
        message_to_proceed: 处理过的消息列表（包含历史消息和当前消息）
    返回：
        关键词列表
    """
    if DEBUG_MODE:
        print(f"[DEBUG] 关键词提取接收到: ")
        for msg in message_to_proceed:            
            print(f"{msg.get('content', '')}")
    
    # 构建包含系统提示词的完整消息列表
    messages = [{"role": "system", "content": EXTRACT_KEYWORDS_PROMPT}] + message_to_proceed

    # 调用模型进行关键词提取
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        stream=False,
        temperature=0.3
    )

    full_response = response.choices[0].message.content
    if DEBUG_MODE:
        print(f"[DEBUG] 关键词提取原始响应: {repr(full_response)}")

    if not full_response:
        return []
    
    # 使用统一的JSON解析函数
    keywords_data = _parse_json(full_response)
    
    if isinstance(keywords_data, list):
        # 如果直接是列表格式
        valid_keywords = [kw for kw in keywords_data if kw and isinstance(kw, str) and kw.strip()]
        if DEBUG_MODE:
            print(f"[DEBUG] 提取到的关键词: {valid_keywords}")
        return valid_keywords
    elif isinstance(keywords_data, dict) and "keywords" in keywords_data:
        # 如果是包含keywords字段的字典格式
        keywords = keywords_data["keywords"]
        if isinstance(keywords, list):
            valid_keywords = [kw for kw in keywords if kw and isinstance(kw, str) and kw.strip()]
            if DEBUG_MODE:
                print(f"[DEBUG] 提取到的关键词: {valid_keywords}")
            return valid_keywords
    
    print(f"[错误] 响应格式不正确: {keywords_data}")
    return []


def analyze_intent(message_to_proceed: List[Dict], relevant_memories: str) -> Tuple[str, str]:
    """
    使用intend_analyze.txt提示词分析最后一条消息的意图
    输入：消息记录
    返回: todoList
    """
    
    # 初步整理所有收到的文件
    input_messages = [{"role": "system", "content": f"[相关记忆]：\n{relevant_memories}"}] + message_to_proceed

    if DEBUG_MODE:
        print(f"[DEBUG] 意图分析接收到: ")
        for msg in input_messages:            
            print(f"{msg.get('content', '')}")

    # 构建包含系统提示词的完整消息列表
    messages = [{"role": "system", "content": INTENT_ANALYSIS_PROMPT}] + input_messages
        
    # 调用模型进行意图分析
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        stream=False,
        temperature=0.3
    )

    full_response = response.choices[0].message.content

    if DEBUG_MODE:
        print(f"[DEBUG] 意图分析模型回应: {full_response}")

    if not full_response:
        return "unknown", "[错误]模型未返回响应"

    # 提取为json格式
    if full_response.strip().startswith("```"):
        intent_data = _parse_json(full_response.strip())
        return (
            intent_data.get("IntentType", "unknown"),
            intent_data.get("TasksTodo", "[错误]模型未找到任务")
        )
    else:
        print(f"[错误] 意图分析模型回应: {full_response}")
        return ("unknown", "[错误] 模型回复格式错误")



def generate_response(message_to_proceed: List[Dict], todo_list: str, relevant_memories: str, work_history: str, on_response: Callable[[str], None] = None) -> str:
    """
    使用2_generate_response.txt提示词生成AI回复
    参数：
        message_to_proceed: 处理过的消息列表（包含历史消息和当前消息）
        todo_list: 任务列表
        relevant_memories: 相关记忆内容
        work_history: 工作历史记录
        on_response: 流式输出回调函数（可选）
    返回：
        完整的AI回答文本
    """
    # 初步整理所有收到的文件
    input_messages = [{"role": "system", "content": f"[任务列表]：\n{todo_list}"}] + [{"role": "system", "content": f"[工作记录]：\n{work_history}"}] + [{"role": "system", "content": f"[相关记忆]：\n{relevant_memories}"}] + message_to_proceed

    if DEBUG_MODE:
        print(f"[DEBUG] 输出回复接收到: ")
        for msg in input_messages:
            print(f"{msg.get("content", "")}")

    # 替换提示词中的占位符
    prompt = GENERATE_RESPONSE_PROMPT.replace("{AI_NAME}", AI_NAME).replace("{USERNAME}", USERNAME)

    # 构建包含系统提示词的完整消息列表
    messages = [{"role": "system", "content": prompt}] + input_messages

    try:
        # 使用流式输出
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=True,
            temperature=0.7
        )
        
        full_response = ""
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                chunk_text = chunk.choices[0].delta.content
                full_response += chunk_text
                # 通过回调函数发送流式文本块
                on_response(chunk_text)
        
        if DEBUG_MODE:
            print(f"[DEBUG] 生成回复完整响应: {full_response}")
        
        return full_response

    except Exception as e:
        error_msg = f"[错误] 生成回复失败: {e}"
        print(error_msg)
        on_response(error_msg)
        return error_msg


def tool_call(todo_list: str, tools_available: str) -> Dict[str, Any]:
    """
    使用tool_selection.txt提示词分析是否需要工具调用
    参数：
        messages: 对话历史
        todo_list： 任务列表
        tools_available: 可用工具信息
    返回：工具调用结果字典
    {
        "server_type": "mcp_server" | "agent_server",
        "service_name": "",
        "tool_name": "",
        "param_name": {},
        "task_type": "", // 仅当server_type为agent_server时
        "instruction": "", // 仅当server_type为agent_server时
        "parameters": {} // 仅当server_type为agent_server时
    }
    """
    input_messages = [{"role": "system", "content": f"[任务列表]：\n{todo_list}"}] + [{"role": "system", "content": tools_available}]    

    if DEBUG_MODE:
        print(f"[DEBUG] 意图分析接收到: ")
        for msg in input_messages:            
            print(f"{msg.get("content", "")}")

    # 构建包含系统提示词的完整消息列表
    messages = [{"role": "system", "content": TOOL_CALL_PROMPT}] + input_messages
    
    try:
        # 调用模型进行工具调用分析
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 工具调用原始响应: {full_response}")

    except Exception as e:
        print(f"[错误] 工具调用分析失败: {e}")
        return {"decision_reason": f"[错误] 模型未响应: {e}"}
    
    # 提取为json格式
    tool_call_data = _parse_json(full_response.strip())
    if DEBUG_MODE:
        print(f"[DEBUG] 成功解析工具调用JSON: {tool_call_data}")
    return tool_call_data
    
    
def memory_control(message_to_proceed: List[Dict], todo_list: str) -> Dict[str, Any]:
    """
    使用memory_control.txt提示词分析需要提取和记忆的关键词
    参数：
        message_to_proceed: 处理过的消息列表（包含历史消息和当前消息）
        todo_list： 任务列表
    返回：
    {
        "from_memory": ["A", "B", "C"],
        "to_memory": ["需要记录的内容A","需要记录的内容B"]
    }
    """
    
    # 将消息列表转换为字符串格式并进行扁平化处理
    message_text = "\n".join([msg.get("content", "") for msg in message_to_proceed if msg.get("content", "").strip()])
    flattened_text = ""
    flattened_text += message_text
    
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
            print(f"[DEBUG] 工具调用原始响应: {full_response}")

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
    
    # 提取为json格式
    if full_response.strip().startswith("```"):
        memory_data = _parse_json(full_response.strip())
        if DEBUG_MODE:
            print(f"[DEBUG] 成功解析工具调用JSON: {memory_data}")
    else:
        print(f"[错误] 意图分析模型回应: {full_response}")
        memory_data = {}
        
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



def task_completion_check(message_to_proceed: List[Dict], todo_list: str, relevant_memories: str, work_history: str) -> Dict[str, Any]:
    """
    使用3_completion_check.txt提示词检查任务是否完成
    参数：
        message_to_proceed: 处理过的消息列表（包含历史消息和当前消息）
        todo_list: 任务列表
        relevant_memories: 相关记忆内容
        work_history: 工作历史记录
    返回：
    {
        "mission_completed": true/false,
        "TodoList": "更新后的任务列表"
    }
    """
    
    # 将消息列表转换为字符串格式并构建输入文本
    message_text = "\n".join([msg.get("content", "") for msg in message_to_proceed if msg.get("content", "").strip()])
    flattened_text = ""
    flattened_text += message_text
    flattened_text += f"[任务列表]：\n{todo_list if todo_list else '无任务'}\n"
    flattened_text += f"[相关记忆]：\n{relevant_memories if relevant_memories else '无相关记忆'}\n"
    flattened_text += f"[工作记录]：\n{work_history}"

    if DEBUG_MODE:
        print(f"[DEBUG] 任务审查接收到:\n{flattened_text}")
    
    try:
        # 调用模型进行任务完成检查
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": COMPLETION_CHECK_PROMPT},
                {"role": "user", "content": flattened_text}
            ],
            stream=False,
            temperature=0.3
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 任务审查原始响应: {(full_response)}")

        if not full_response:
            return {
                "mission_continue": False,
                "TodoList": ""
            }

    except Exception as e:
        print(f"[错误] 任务审查分析失败: {e}")
        return {
            "mission_continue": False,
            "TodoList": ""
        }
    
    # 提取为json格式
    if full_response.strip().startswith("```"):
        review_data = _parse_json(full_response.strip())
    else:
        print(f"[错误] 任务审查模型回应: {full_response}")
        review_data = {}
    
    # 确保返回的数据格式正确
    mission_continue = review_data.get("mission_continue", False)
    todo_list = review_data.get("TodoList", "")
    
    # 如果任务完成（mission_continue=false），清空TodoList
    if not mission_continue:
        todo_list = ""
    
    result = {
        "mission_continue": mission_continue,
        "TodoList": todo_list
    }
    
    if DEBUG_MODE:
        print(f"[DEBUG] 任务完成检查结果 - 任务继续: {result['mission_continue']}")
        print(f"[DEBUG] 任务完成检查结果 - 更新任务列表: {result['TodoList']}")
    
    return result


def final_output(message_to_proceed: List[Dict], tool_results_text: str, relevant_memories: str, work_history: str) -> str:
    """
    使用5_final_output.txt提示词生成最终回答
    参数：
        message_to_proceed: 处理过的消息列表（包含历史消息和当前消息）
        tool_results_text: 工具调用结果的纯文本格式
        relevant_memories: 相关记忆内容
        memories_recorded: 已存入记忆的内容列表
    返回：
        最终的AI回答文本
    """
    if not GENERATE_RESPONSE_PROMPT:
        return "[错误] 最终输出提示词未加载，请检查 5_final_output.txt 文件"
    
    # 替换提示词中的占位符
    prompt = GENERATE_RESPONSE_PROMPT.replace("{AI_NAME}", AI_NAME).replace("{USERNAME}", USERNAME)
    
    # 将消息列表转换为字符串格式
    message_text = "\n".join([msg.get("content", "") for msg in message_to_proceed if msg.get("content", "").strip()])
    
    # 构建输入文本
    flattened_text = ""
    
    # 添加消息内容
    flattened_text += message_text
    
    # 添加工具输出
    flattened_text += f"[工具输出]：\n{tool_results_text if tool_results_text else '无工具调用'}\n"
    
    # 添加相关记忆
    flattened_text += f"[相关记忆]：\n{relevant_memories if relevant_memories else '无记忆提取'}\n"
    
    # 添加工作记录
    flattened_text += f"[工作记录]：\n{work_history}"

    if DEBUG_MODE:
        print(f"[DEBUG] 最终输出分析输入:\n{flattened_text}")
    
    try:
        # 调用模型生成最终回答
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": flattened_text}
            ],
            stream=False,
            temperature=0.7
        )
        
        full_response = response.choices[0].message.content
        if DEBUG_MODE:
            print(f"[DEBUG] 最终输出原始响应: {repr(full_response)}")

        if not full_response:
            return "[错误] 模型未返回响应"

        # 返回最终回答
        final_answer = full_response.strip()
        
        return final_answer

    except Exception as e:
        print(f"[错误] 最终输出生成失败: {e}")
        return f"[错误] 最终回答生成失败: {e}"

