# -*- coding: utf-8 -*-
"""
LLM 服务模块 - 提供与本地大模型的通信接口
支持流式响应和模型预加载
使用 OpenAI API 标准格式接入本地 Ollama
"""
import asyncio
import datetime
import json
import os
import re
import sys
import traceback
from typing import List, Dict, Callable
from openai import OpenAI

# 添加项目根目录到路径
PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.memory.knowledge_graph_manager import relevant_memories_by_keywords
from brain.memory.quintuples_extractor import record_messages_to_memories
from mcpserver.mcp_manager import get_mcp_manager
from mcpserver.mcp_registry import get_all_services_info, get_service_statistics
from system.config import config, is_neo4j_available
from system.background_analyzer import analyze_intent, extract_keywords, generate_response, tool_call, memory_control, task_completion_check, final_output


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

# 读取 system 提示词（system/prompts/personality.txt）并替换 {AI_NAME} 和 {USERNAME}
SYSTEM_PROMPT = None
_personality_path = os.path.join(PROJECT_ROOT, "system", "prompts", "personality.txt")
if os.path.exists(_personality_path):
    try:
        with open(_personality_path, "r", encoding="utf-8") as f:
            raw = f.read()
        SYSTEM_PROMPT = raw.replace("{AI_NAME}", AI_NAME).replace("{USERNAME}", USERNAME).strip()
    except Exception as e:
        print(f"[警告] 读取 personality.txt 失败: {e}")
        SYSTEM_PROMPT = None



def get_recent_messages(messages: List[Dict], max_chars: int = 100, min_messages: int = 2) -> List[Dict]:
    """
    获取当前对话的最近上下文
    Args:
        messages: 当前对话消息列表 [{"role": "user/assistant", "content": "..."}]
        max_chars: 最大字符数，默认100字
        min_messages: 最少消息数，默认2条
        
    Returns:
        最近的消息列表 [{"role": "...", "content": "..."}]
    """
    if not messages:
        return []
    
    try:
        # 过滤掉空消息
        valid_messages = []
        for msg in messages:
            content = msg.get("content", "").strip()
            if content:
                valid_messages.append(msg)
        
        if not valid_messages:
            return []
        
        # 选择合适的消息数量和字符长度
        selected_messages = []
        total_chars = 0
        
        # 特殊情况：如果只有1条消息，直接返回它
        if len(valid_messages) == 1:
            return valid_messages
        
        # 从最新的消息开始往前选择
        for msg in reversed(valid_messages):
            content = msg.get("content", "")
            message_chars = len(content)
            
            # 如果还没达到最少消息数，无论字符数多少都要添加
            if len(selected_messages) < min_messages:
                selected_messages.insert(0, msg)
                total_chars += message_chars
            else:
                # 已达到最少消息数，检查是否超过字符限制
                if total_chars + message_chars > max_chars:
                    break
                selected_messages.insert(0, msg)  # 插入到开头保持时间顺序
                total_chars += message_chars
        
        # 确保至少有最少消息数（但不超过实际消息数量）
        if len(selected_messages) < min_messages and len(valid_messages) >= min_messages:
            selected_messages = valid_messages[-min_messages:]
        elif len(selected_messages) == 0:
            # 兜底：如果没有选择任何消息，至少返回最后一条
            selected_messages = [valid_messages[-1]]
        
        return selected_messages
        
    except Exception as e:
        print(f"[错误] 获取最近对话上下文失败: {e}")
        return []


def _extract_keywords_from_text(text: str) -> List[str]:
    """
    从文本中提取关键词
    Args:
        text: 输入文本
    Returns:
        关键词列表
    """
    if not text or not text.strip():
        return []
    
    try:
        # 去除标点符号，保留中英文、数字和空格
        clean_text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)
        # 分词（按空格和标点分割）
        words = re.split(r'\s+', clean_text)
        
        # 定义停用词（常用词汇）
        stop_words = {
            # 中文停用词
            '的', '是', '在', '有', '和', '了', '我', '你', '他', '她', '它', '这', '那', '一个', '什么', '怎么', '为什么', 
            '可以', '能够', '应该', '需要', '想要', '希望', '觉得', '认为', '知道', '看到', '听到', '说', '做', '去',
            '来', '会', '要', '把', '被', '给', '让', '使', '对', '向', '从', '到', '于', '为了', '因为', '所以',
            '但是', '不过', '然而', '而且', '或者', '还是', '就是', '也是', '不是', '没有', '不会', '不能',
            # 英文停用词
            'the', 'is', 'are', 'was', 'were', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
            'of', 'with', 'by', 'from', 'as', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
            'will', 'would', 'could', 'should', 'may', 'might', 'can', 'must', 'shall', 'this', 'that', 'these',
            'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'us', 'them', 'my', 'your',
            'his', 'her', 'its', 'our', 'their', 'what', 'when', 'where', 'why', 'how', 'who', 'which'
        }
        
        # 过滤关键词
        keywords = []
        for word in words:
            word = word.strip().lower()
            # 过滤条件：长度大于1，不是停用词，不是纯数字
            if (len(word) > 1 and 
                word not in stop_words and 
                not word.isdigit() and
                word.isalnum()):  # 只保留字母数字组合
                keywords.append(word)
        
        # 去重并保持原始大小写（取第一次出现的形式）
        seen = set()
        unique_keywords = []
        for word in keywords:
            if word not in seen:
                seen.add(word)
                unique_keywords.append(word)
        
        return unique_keywords[:10]  # 最多返回10个关键词
        
    except Exception as e:
        print(f"[错误] 关键词提取失败: {e}")
        return []


def _handle_message_reply_task(task_number: int, message_to_proceed: List[Dict], todo_list: str, relevant_memories: str, work_history: str, on_response: Callable[[str], None]) -> tuple[str, str]:
    """
    处理消息回复任务
    Args:
        task_number: 当前任务编号
        current_task_content: 任务内容描述
        message_to_proceed: 处理过的消息文本
        todo_list: 任务列表
        relevant_memories: 相关记忆
        work_history: 工作历史
        on_response: 流式输出回调函数
    Returns:
        tuple[更新后的工作历史, 生成的回复内容]
    """
    message_replied = generate_response(message_to_proceed, todo_list, relevant_memories, work_history, on_response)
    # 记录工作日志
    updated_work_history = work_history + f"{task_number}. [消息回复] {message_replied}\n"
    return updated_work_history, message_replied


def _handle_tool_call_task_execution(task_number: int, todo_list: str, tools_available: str, work_history: str) -> str:
    """
    处理工具调用任务执行
    Args:
        task_number: 当前任务编号
        current_task_content: 任务内容描述
        todo_list: 任务列表
        tools_available: 可用工具信息
        work_history: 工作历史
    Returns:
        更新后的工作历史
    """
    tool_result = tool_call(todo_list, tools_available)
    
    if DEBUG_MODE:
        print(f"[DEBUG] 完整工具调用结果: {tool_result}")
    
    # 处理工具调用结果（新的JSON格式：直接返回单个工具调用对象）
    if tool_result.get("server_type"):
        if DEBUG_MODE:
            print(f"[DEBUG] 检测到工具调用请求: {tool_result.get('server_type')}")
        
        # 直接使用MCP管理器，避免调度器的异步初始化问题
        mcp_manager = get_mcp_manager()
        
        # 处理单个工具调用
        agent_call = tool_result
        server_type = agent_call.get("server_type", "null")
        print(f"[工具执行] 处理工具调用: {server_type}")

        try:
            tool_call_result = ""  # 初始化结果变量
            if server_type == "mcp_server":
                # 直接使用MCP管理器进行工具调用
                service_name = agent_call.get("service_name", "")
                tool_name = agent_call.get("tool_name", "")
                
                # 检查service_name是否已注册
                stats = get_service_statistics()
                registered_services = stats.get('registered_services', [])
                if service_name not in registered_services:
                    tool_call_result = f"service name未注册，service name需要在[{', '.join(registered_services)}]里选择"
                    print(f"[工具执行] 错误: {tool_call_result}")
                elif not service_name or not tool_name:
                    tool_call_result = "工具执行失败: MCP工具参数不完整"
                    print(f"[工具执行] 错误: 服务名或工具名缺失")
                else:
                    args = {}
                    param_name = agent_call.get("param_name")
                    if param_name is not None and param_name != "null":
                        if isinstance(param_name, dict):
                            args = param_name
                    print(f"[工具执行] 正在调用MCP工具: {service_name}.{tool_name}")

                    try:
                        # 使用路由机制调用，将args作为独立参数传递而不是打包在字典中
                        tool_call_result = asyncio.run(mcp_manager.unified_call(service_name, tool_name, args))
                        print(f"[工具执行] MCP工具调用成功")
                        if DEBUG_MODE:
                            print(f"[DEBUG] 工具执行结果: {tool_call_result}")
                    except Exception as exec_error:
                        tool_call_result = f"工具执行失败: {exec_error}"
                        print(f"[工具执行] MCP工具调用失败: {exec_error}")
                # 记录到 mcp 调用到工作日志 work history
                args_str = f":{args}" if args else ""
                work_log_entry = f"{task_number}. 调用工具{service_name}.{tool_name}{args_str}，调用结果：{tool_call_result}。"
                work_history += work_log_entry + "\n"

            elif server_type == "agent_server":
                # 预留给其他类型的Agent调用
                task_type = agent_call.get("task_type", "unknown")
                tool_call_result = f"Agent Server任务调用暂未实现 - 任务类型: {task_type}"
                print(f"[工具执行] Agent Server任务调用暂未实现: {task_type}")
                work_history += f"{task_number}. [工具调用] 错误！Agent Server服务暂未实装，无法使用。\n"
            
            elif server_type == "none":
                work_history += f"{task_number}. [工具调用] 错误！没有可以完成此任务的工具。\n"
            else:
                tool_call_result = f"未知的服务器类型: {server_type}"
                print(f"[工具执行] 错误: 未知的服务器类型 {server_type}")
                work_history += f"{task_number}. [工具调用] 错误！未知的服务器类型，无法使用。\n"
            
        except Exception as e:
            tool_call_result = f"工具调用异常: {str(e)}"
            print(f"[工具执行] 工具调用异常: {e}")
            work_history += f"{task_number}. [工具调用] 错误！{tool_call_result}\n"
    
    return work_history


def _initialize_tools_available() -> str:
    """
    初始化可用工具信息
    Returns:
        工具描述文本字符串
    """
    tools_available = ""
    try:
        # 从mcp_registry获取所有可用工具
        services_info = get_all_services_info()
        tools_available_list = []
        
        if DEBUG_MODE:
            print(f"[DEBUG] 找到 {len(services_info)} 个可用服务")
        
        # 构建工具信息文本
        for service_name, service_info in services_info.items():
            service_desc = f"## 服务名称：{service_name}\n"
            service_desc += f"描述: {service_info.get('description', '无描述')}\n"
            
            # 获取该服务的工具列表
            tools = service_info.get('available_tools', [])
            if tools:
                service_desc += "可用工具:\n"
                for tool in tools:
                    tool_name = tool.get('name', '')
                    tool_desc = tool.get('description', '')
                    tool_example = tool.get('example', '')
                    
                    service_desc += f"- **工具名称：{tool_name}**: {tool_desc}\n"
                    if tool_example:
                        service_desc += f"  示例: {tool_example}\n"
            else:
                service_desc += "暂无可用工具\n"
            
            service_desc += "\n"
            tools_available_list.append(service_desc)
        
        # 组合成完整的工具描述文本
        if tools_available_list:
            tools_available += "[可用的mcp server]:\n" + "".join(tools_available_list)
        else:
            tools_available += "[可用的mcp server]:\n无可用工具\n"
            
    except Exception as e:
        print(f"[Warning] 获取可用工具失败: {e}")
        if DEBUG_MODE:
            traceback.print_exc()
        tools_available = "获取工具信息失败"

    tools_available += "[可用的agent server]:\n无可用工具\n"
    return tools_available


def chat_with_model(messages: List[Dict], on_response: Callable[[str], None]) -> str:
    """
    这里是和AI的私聊模块。

    输入:
        messages: 对话历史 [{"role": USERNAME/assistant, "content": content无格式， "timestamp": ISO格式时间戳YYYY_MM_DDTHH:MM:SS.XX}]
        on_response: 接收模型流式输出的回调函数
    
    输出:
        完整的模型回复文本
    
    特殊说明:
        内容会在这里进行整合，将时间戳等加入文本。之后的content格式会变成[YYYY_MM_DDTHH:MM:SS.XX] <USERNAME> content。
        故get_recent_messages只能在当前环境使用，否则时间戳和用户名会被加入文本计数。
    """
    # 循坏外必要的记录
    mission_continue = True
    todo_list = ""
    task_number = 0
    tools_available = ""    # 开始为空值，首次加载（不管有没有内容都会写入标题[工具列表]，以此来避免重复加载
    relevant_memories = ""
    work_history = ""
    full_response = []
    
    # 使用get_recent_chat限制聊天记录长度，避免上下文过长
    recent_messages = get_recent_messages(messages)

    # 检查是否有有效消息
    if not recent_messages or not recent_messages[-1].get("content", "").strip():
        print("[INFO] 检测到空白消息，跳过处理")
        return ""
    
    # 消息格式化处理 - 直接使用recent_messages中的时间戳
    message_to_proceed = []
    
    # 处理所有recent_messages
    for msg in recent_messages:
        role = msg.get("role", "unknown")
        if role != "assistant":
            role = "user"
        name = msg.get("role", "unknown")
        if name == "assistant":
            name = AI_NAME
        content = msg.get("content", "").strip()

        timestamp = msg.get("timestamp")
        formatted_content = f"[{timestamp}] <{name}> {content}"
        message_to_proceed.append({"role": role, "content": formatted_content})

    # 在这里对用户的发言内容进行记忆
    input_memory_id = record_messages_to_memories(message_to_proceed, USERNAME)

    # ======首先查询记忆======
    # 从用户最后一条消息中提取关键词
    keywords = []
    user_keywords = []
    last_message = recent_messages[-1] if recent_messages else None
    if last_message:
        user_keywords = _extract_keywords_from_text(last_message.get("content", "").strip())
    assistant_keywords = extract_keywords(message_to_proceed)
    keywords = list(set(user_keywords + assistant_keywords))
    if is_neo4j_available():
        print(f"[记忆查询] 提取的关键词: {keywords}")
        relevant_memories = "neo4j链接错误，无法查询记忆。"
    else:
        relevant_memories = "neo4j记忆库未连接，无法查询记忆。"


    # ======进行意图识别======
    intent_type, todo_list = analyze_intent(message_to_proceed, relevant_memories)
    print(f"[思考] 识别到意图类型: {intent_type},\n[思考] 规划后续任务: {todo_list}")

    
    # ======解析任务列表======
    while mission_continue:
        task_number += 1
        current_task_type = "unknown"
        current_task_content = ""
        current_task_line = ""
        
        if todo_list and todo_list.strip():
            # 按行分割任务列表
            task_lines = [line.strip() for line in todo_list.split('\n') if line.strip()]
            
            # 检查是否存在第task_number行任务
            if task_number <= len(task_lines):
                current_task_line = task_lines[task_number - 1]  # 转换为0基索引
                print(f"[执行] 执行第{task_number}个任务: {current_task_line}")
                
                # 解析任务类型和内容
                # 匹配格式：数字. 任务类型（任务描述）
                task_pattern = r'^\d+\.\s*([^（(]+)[（(]([^）)]*)[）)]'
                match = re.match(task_pattern, current_task_line)
                if match:
                    current_task_type = match.group(1).strip()
                    current_task_content = match.group(2).strip()
                else:
                    # 备用解析：简单提取可能的任务类型关键词
                    print(f"[错误] todo list格式不规范，当前输出{current_task_line}")
                    break
            else:
                print(f"[错误] 没有第{task_number}个任务")
                mission_continue = False
                break
        else:
            print(f"[错误] 任务列表为空，无任务")
            mission_continue = False
            break

        # ======依照列表工作======
        if current_task_type == "消息回复":
            print(f"[执行] 处理消息回复任务: {current_task_content}")
            work_history, message_replied = _handle_message_reply_task(
                task_number, message_to_proceed, 
                todo_list, relevant_memories, work_history, on_response
            )
            # 将message replied添加到full_response中，作为后续任务的上下文
            current_timestamp = datetime.datetime.now().isoformat(timespec='centiseconds')
            formatted_reply = f"[{current_timestamp}] <{AI_NAME}> {message_replied}"
            full_response.append({"role": "assistant", "content": formatted_reply})


    # =========================================================================================================todo
        elif current_task_type == "记忆调整":
            print(f"[执行] 处理记忆调整任务: {current_task_content}")
            if is_neo4j_available():
                # TODO: 实现记忆调整逻辑
                # 可能包括：
                # - 解析需要调整的记忆内容
                # - 执行记忆更新操作
                # - 验证调整结果
                print(f"占位符，你不应看到这个信息")
            else:
                print(f"[警告] Neo4j 未启用，跳过记忆调整任务")
                work_history += f"{task_number}. [记忆调整] 错误！记忆库未连接，无法进行记忆调整任务\n"
        
        elif current_task_type == "记忆查找":
            print(f"[执行] 处理记忆查找任务: {current_task_content}")
            if is_neo4j_available():
                # TODO: 实现记忆调整逻辑
                # 可能包括：
                # - 解析需要调整的记忆内容
                # - 执行记忆更新操作
                # - 验证调整结果
                print(f"占位符，你不应看到这个信息")
            else:
                print(f"[警告] Neo4j 未启用，跳过记忆查找任务")
                work_history += f"{task_number}. [记忆查找] 错误！记忆库未连接，无法进行记忆查找任务\n"
        
        elif current_task_type == "历史记录":
            print(f"[执行] 处理历史记录任务: {current_task_content}")
            work_history += f"{task_number}. [历史记录] 错误！历史记录功能未实装\n"

        elif current_task_type == "工具调用":
            if tools_available == "":
                # 初始化可用工具信息
                tools_available = _initialize_tools_available()
            
            # 执行工具调用
            print(f"[执行] 处理工具调用任务: {current_task_content}")
            work_history = _handle_tool_call_task_execution(
                task_number, todo_list, tools_available, work_history
            )

            
        elif current_task_type == "数据查询":
            print(f"[执行] 处理数据查询任务: {current_task_content}")
            work_history += f"{task_number}. [数据查询] 错误！数据查询功能未实装\n"
            # TODO: 实现数据查询逻辑
            # 可能包括：
            # - 从数据库查询信息
            # - 搜索相关记忆
            # - 格式化查询结果
            
        elif current_task_type == "文件操作":
            print(f"[执行] 处理文件操作任务: {current_task_content}")
            work_history += f"{task_number}. [文件操作] 错误！文件操作功能未实装\n"
            # TODO: 实现文件操作逻辑
            # 可能包括：
            # - 读取文件
            # - 写入文件
            # - 文件搜索
            
        else:
            print(f"[执行] 未识别的任务类型 '{current_task_type}'，执行默认流程")
            # 执行原有的工具调用流程作为默认处理
            work_history += f"{task_number}. [{current_task_type}] 错误！任务类型输入错误，需要从列表中选择具体任务\n"

        # ======标记工作完成======
        # 在todo_list中标记当前任务为已完成
        if todo_list and todo_list.strip() and current_task_line:
            completed_task_line = current_task_line + " *已完成*"
            # 更新todo_list，替换当前任务行
            task_lines = todo_list.split('\n')
            if task_number <= len(task_lines):
                task_lines[task_number - 1] = completed_task_line
                todo_list = '\n'.join(task_lines)
                print(f"[执行] 任务 {task_number} 标记为已完成")

        # ======审核工作进度======
        print(f"[审核] 进行任务完成检查，当前任务进度{task_number}")
        check_result = task_completion_check(message_to_proceed, todo_list, relevant_memories, work_history)
        mission_continue = check_result.get("mission_continue", True)
        new_todo_list = check_result.get("TodoList", "")
        
        # 处理任务列表更新逻辑
        if new_todo_list and new_todo_list.strip():
            # 将当前任务列表按行分割
            current_task_lines = [line.strip() for line in todo_list.split('\n') if line.strip()]
            new_task_lines = [line.strip() for line in new_todo_list.split('\n') if line.strip()]
            
            # 保留当前任务编号之前的已完成任务
            updated_task_lines = []
            for i in range(min(task_number, len(current_task_lines))):
                updated_task_lines.append(current_task_lines[i])
            
            # 添加新任务列表中的任务（从task_number位置开始）
            for i, new_line in enumerate(new_task_lines):
                # 重新调整任务序号，从当前task_number开始编号
                adjusted_task_number = task_number + i + 1
                
                # 使用正则表达式替换行首的序号
                # 匹配行首的数字+点号格式（如"1. "、"2. "等）
                pattern = r'^\d+\.\s*'
                if re.match(pattern, new_line):
                    # 替换为新的序号
                    adjusted_line = re.sub(pattern, f'{adjusted_task_number}. ', new_line)
                else:
                    # 如果没有匹配到序号格式，直接添加序号
                    adjusted_line = f'{adjusted_task_number}. {new_line}'
                
                updated_task_lines.append(adjusted_line)
            
            # 更新任务列表
            todo_list = '\n'.join(updated_task_lines)
            
            if DEBUG_MODE:
                print(f"[DEBUG] - 更新后任务列表: \n{todo_list}")
        
        if DEBUG_MODE:
            print(f"[DEBUG] 任务完成检查结果: 完成={not mission_continue}")
            print(f"[DEBUG] 更新后的任务列表: {todo_list}")
    
    # ======确保最后向用户汇报结果======
    # 检查最后一个任务是否为消息回复，如果不是则追加消息回复任务
    if work_history.strip():
        # 从工作历史中提取最后一个任务类型
        work_lines = [line.strip() for line in work_history.split('\n') if line.strip()]
        last_work_line = work_lines[-1]
        # 检查最后一行工作记录是否包含[消息回复]
        if "[消息回复]" not in last_work_line:
            print(f"[执行] 最后任务类型不是消息回复，追加消息回复任务")
            task_number += 1
            todo_list += f"\n{task_number}. 消息回复（请总结并回复用户）"
            work_history, message_replied = _handle_message_reply_task(
                task_number, "请总结并回复用户", message_to_proceed, 
                todo_list, relevant_memories, work_history, on_response
            )
            full_response += message_replied + "\n"
    else:
        # 如果工作历史为空，直接添加消息回复
        print(f"[执行] 工作历史为空，追加消息回复任务")
        task_number += 1
        todo_list += f"\n{task_number}. 消息回复（请总结并回复用户）"
        work_history, message_replied = _handle_message_reply_task(
            task_number, "请总结并回复用户", message_to_proceed, 
            todo_list, relevant_memories, work_history, on_response
        )
        full_response += message_replied + "\n"

    return full_response


    # ======3.记忆调用======（neo4j启用时才有效）
    if is_neo4j_available():
        memory_control_result = memory_control(message_to_proceed, todo_list)
        
        # 提取记忆控制结果
        from_memory = memory_control_result.get("from_memory", [])
        to_memory = memory_control_result.get("to_memory", [])

        # 从用户最后一条消息中提取关键词
        user_keywords = []
        user_keywords = _extract_keywords_from_text(new_message.get("content", "").strip())
        if DEBUG_MODE:
            print(f"[DEBUG] 从用户消息中提取的关键词: {user_keywords}")
            print(f"[DEBUG] AI分析的记忆搜索关键词: {from_memory}")
            print(f"[DEBUG] 需要记录的记忆: {to_memory}")

        # 合并用户关键词和AI分析的关键词
        search_from_memory = []
        if user_keywords:
            search_from_memory.extend(user_keywords)
        if from_memory:
            search_from_memory.extend(from_memory)
        # 去重
        search_from_memory = list(set(search_from_memory))
        # 查询相关记忆
        relevant_memories = relevant_memories_by_keywords(search_from_memory)
        if DEBUG_MODE:
            print(f"[DEBUG] 最终的记忆搜索关键词组: {search_from_memory}")
            print(f"[DEBUG] 查询到的相关记忆内容: {(relevant_memories)}")
    else: 
        relevant_memories = "GRAG记忆系统未连接"
    
        


def preload_model(timeout_sec: int = 30) -> bool:
    """
    预加载模型
    
    Args:
        timeout_sec: 超时时间（秒）
        
    Returns:
        是否成功加载模型
    """
    print("模型加载中……", flush=True)
    
    try:
        # 使用简单的同步调用进行预热，避免触发完整的聊天流程
        test_response = call_model_sync("测试", "这是模型预热测试，请简单回复确认。")
        
        if test_response and not test_response.startswith("[错误]"):
            print("✅ 模型加载完成", flush=True)
            return True
        else:
            print(f"❌ 模型测试失败: {test_response}", flush=True)
            return False
            
    except Exception as e:
        print(f"❌ 模型加载失败: {e}", flush=True)
        return False


def call_model_sync(prompt: str, system_prompt: str = None) -> str:
    """
    同步调用模型（非流式）
    
    Args:
        prompt: 用户提示
        system_prompt: 系统提示（可选）
        
    Returns:
        模型完整回复
    """
    try:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=False,
            temperature=0.7,
        )
        
        return response.choices[0].message.content
            
    except Exception as e:
        return f"[错误] {str(e)}"


# ============ 测试代码 ============
def test_chat():
    """测试聊天功能"""
    print("测试 LLM 服务...测试字段：你好，请介绍一下你自己")
    print(f"API: {API_URL}")
    print(f"模型: {MODEL}")
    print("-" * 50)
    
    # 测试预加载
    model_loaded = preload_model()
    print(f"模型加载状态: {'成功' if model_loaded else '失败'}")
    print("-" * 50)
    
    # 测试对话
    messages = [{"role": "user", "content": "你好，请介绍一下你自己"}]
    
    def on_chunk(chunk):
        print(chunk, end="", flush=True)
    
    print("模型回复: ", end="", flush=True)
    reply = chat_with_model(messages, on_chunk)
    print("\n" + "-" * 50)
    print(f"完整回复长度: {len(reply)} 字符")


if __name__ == "__main__":
    test_chat()