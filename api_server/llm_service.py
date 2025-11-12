# -*- coding: utf-8 -*-
"""
LLM 服务模块 - 提供与本地大模型的通信接口
支持流式响应和模型预加载
使用 OpenAI API 标准格式接入本地 Ollama
"""
import os
import sys
import json
import re
import datetime
from typing import List, Dict, Callable
from openai import OpenAI

# 添加项目根目录到路径
PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.memory.knowledge_graph_manager import relevant_memories_by_keywords
from mcpserver.mcp_manager import get_mcp_manager
from mcpserver.mcp_scheduler import get_mcp_scheduler
from system.config import config, is_neo4j_available
from system.background_analyzer import analyze_intent, tool_call, memory_control, task_completion_check, final_output

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



def get_recent_chat(messages: List[Dict], max_chars: int = 100, min_messages: int = 2) -> List[Dict]:
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


def extract_keywords_from_text(text: str) -> List[str]:
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
        import re
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


def message_to_logs(message: Dict) -> str:
    """
    将消息转换为日志格式
    Args:
        message: 消息对象 {"role": "user/assistant", "content": "..."}
        
    Returns:
        格式化的日志字符串，格式：时间 <发言者> 发言内容
        例：13:27:02 <星空> 早上好，帮我看看现在几点了
    """
    if not message:
        return ""
    
    try:
        role = message.get("role", "unknown")
        content = message.get("content", "").strip()
        
        if not content:
            return ""
        
        # 生成当前时间戳
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        # 将换行替换为空格以保持单行记录
        safe_content = content.replace("\r", " ").replace("\n", " ")
        
        return f"{timestamp} <{role}> {safe_content}"
        
    except Exception as e:
        print(f"[错误] 消息格式化失败: {e}")
        return ""


def chat_with_model(messages: List[Dict], on_response: Callable[[str], None]) -> str:
    """
    Args:
        messages: 对话历史 [{"role": "...", "content": "..."}]
        on_response: 接收模型流式输出的回调函数
    Returns:
        完整的模型回复文本
    """
    # 循坏外必要的记录
    mission_completed = False
    todo_list = ""
    tool_call_results = []
    relevant_memories = ""
    work_history = ""
    work_count = 0
    
    # 使用get_recent_chat限制聊天记录长度，避免上下文过长
    recent_messages = get_recent_chat(messages)
    # 检查是否有有效消息
    if not recent_messages or not recent_messages[-1].get("content", "").strip():
        print("[INFO] 检测到空白消息，跳过处理")
        return ""
    
    # 将信息分为最新消息和历史消息
    new_message = messages[-1] if messages else None
    history_messages = messages[:-1] if len(messages) > 1 else []
    
    # 消息扁平化处理
    message_to_proceed = ""
    
    # 处理历史消息
    message_to_proceed += "[历史消息]：\n"
    if history_messages:
        for msg in history_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "").strip()
            message_to_proceed += f"<{role}>{content}\n"
    
    # 处理当前消息
    message_to_proceed += "[当前消息]：\n"
    if new_message:
        role = new_message.get("role", "unknown")
        content = new_message.get("content", "").strip()
        message_to_proceed += f"<{role}>{content}\n"
    else:
        print("[INFO] 检测到空白消息，跳过处理")
        return ""
    
    # ======1.进行意图识别======
    intent_type, todo_list = analyze_intent(message_to_proceed)
    print(f"[思考] 识别到意图类型: {intent_type},\n[思考] 规划后续任务: {todo_list}")

    # 获取可用工具信息
    try:
        # 从mcp_registry获取所有可用工具
        from mcpserver.mcp_registry import get_all_services_info
        
        services_info = get_all_services_info()
        tools_available_list = []
        
        if DEBUG_MODE:
            print(f"[DEBUG] 找到 {len(services_info)} 个可用服务")
        
        # 构建工具信息文本
        for service_name, service_info in services_info.items():
            service_desc = f"## {service_name}\n"
            service_desc += f"描述: {service_info.get('description', '无描述')}\n"
            
            # 获取该服务的工具列表
            tools = service_info.get('available_tools', [])
            if tools:
                service_desc += "可用工具:\n"
                for tool in tools:
                    tool_name = tool.get('name', '')
                    tool_desc = tool.get('description', '')
                    tool_example = tool.get('example', '')
                    
                    service_desc += f"- **{tool_name}**: {tool_desc}\n"
                    if tool_example:
                        service_desc += f"  示例: {tool_example}\n"
            else:
                service_desc += "暂无可用工具\n"
            
            service_desc += "\n"
            tools_available_list.append(service_desc)
        
        # 组合成完整的工具描述文本
        if tools_available_list:
            tools_available = "[可用的mcp_agent]:\n" + "".join(tools_available_list)
        else:
            tools_available = "[可用的mcp_agent]:\n无可用工具"
            
        #if DEBUG_MODE:
        #    print(f"[DEBUG] 工具描述文本长度: {len(tools_available)} 字符")
        #    print(f"[DEBUG] 工具预览: {tools_available}...")
            
    except Exception as e:
        print(f"[Warning] 获取可用工具失败: {e}")
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        tools_available = "获取工具信息失败"
    
    while mission_completed is False:
        # ======2.进行工具调用======
        tool_result = tool_call(message_to_proceed, todo_list, tools_available)
        
        if DEBUG_MODE:
            print(f"[DEBUG] 完整工具调用结果: {tool_result}")
        
        # 处理工具调用结果（新的JSON格式：包含agent_call数组）
        tool_use = tool_result.get("tool_use", False)
        
        if tool_use:
            agent_calls = tool_result.get("agent_call", [])
            if DEBUG_MODE:
                print(f"[DEBUG] 找到 {len(agent_calls)} 个工具调用请求")
            
            # 直接使用MCP管理器，避免调度器的异步初始化问题
            mcp_manager = get_mcp_manager()
            
            # 遍历所有工具调用请求
            for i, agent_call in enumerate(agent_calls):
                agent_type = agent_call.get("agentType", "null")
                print(f"[工具执行] 处理第 {i+1} 个工具调用: {agent_type}")
                

                
                try:
                    if agent_type == "mcp_agent":
                        # 直接使用MCP管理器进行工具调用
                        service_name = agent_call.get("service_name", "")
                        tool_name = agent_call.get("tool_name", "")
                        args = {}
                        if service_name and tool_name:
                            param_name = agent_call.get("param_name")
                            if param_name is not None and param_name != "null":
                                if isinstance(param_name, dict):
                                    args = param_name
                            print(f"[工具执行] 正在调用MCP工具: {service_name}.{tool_name}:{args}")
                            
                            import asyncio
                            try:
                                tool_call_result = asyncio.run(mcp_manager.unified_call(service_name, tool_name, args))
                                print(f"[工具执行] MCP工具调用成功")
                                if DEBUG_MODE:
                                    print(f"[DEBUG] 工具执行结果: {tool_call_result}")
                            except Exception as exec_error:
                                tool_call_result = f"工具执行失败: {exec_error}"
                                print(f"[工具执行] MCP工具调用失败: {exec_error}")
                        else:
                            tool_call_result = "工具执行失败: MCP工具参数不完整"
                            print(f"[工具执行] 错误: 服务名或工具名缺失")
                        # 记录到 mcp 调用到工作日志 work history
                        work_count += 1
                        work_log_entry = f"{work_count}. 调用的具体工具{service_name}.{tool_name}:{args}，调用的结果{tool_call_result}。"
                        work_history += work_log_entry + "\n"

                    elif agent_type == "api_agent":
                        # 预留给其他类型的Agent调用
                        task_type = agent_call.get("task_type", "unknown")
                        tool_call_result = f"Api Agent任务调用暂未实现 - 任务类型: {task_type}"
                        print(f"[工具执行] Api Agent任务调用暂未实现: {task_type}")
                    
                    else:
                        tool_call_result = f"未知的代理类型: {agent_type}"
                        print(f"[工具执行] 错误: 未知的代理类型 {agent_type}")
                
                except Exception as e:
                    tool_call_result = f"工具调用异常: {str(e)}"
                    print(f"[工具执行] 工具调用异常: {e}")
                
                # 将结果添加到结果列表
                tool_call_results.append({
                    "agent_type": agent_type,
                    "result": tool_call_result,
                    "call_index": i + 1
                })


        # ======3.记忆调用======（neo4j启用时才有效）
        if is_neo4j_available():
            memory_control_result = memory_control(message_to_proceed, todo_list)
            
            # 提取记忆控制结果
            from_memory = memory_control_result.get("from_memory", [])
            to_memory = memory_control_result.get("to_memory", [])

            # 从用户最后一条消息中提取关键词
            user_keywords = []
            user_keywords = extract_keywords_from_text(new_message.get("content", "").strip())
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
        
        # ======4.工作审查======
        task_review = task_completion_check(message_to_proceed, todo_list, tool_call_results, relevant_memories, work_history)
        
        # 根据审查结果决定是否继续循环
        mission_completed = task_review.get("mission_completed", False)
        todo_list = task_review.get("todo_list", todo_list)  # 更新任务列表
        decision_reason = task_review.get("decision_reason", "")
        
        if DEBUG_MODE:
            print(f"[DEBUG] 任务审查结果: 完成={mission_completed}, 理由={decision_reason}")
            if not mission_completed:
                print(f"[DEBUG] 更新后的任务列表: {todo_list}")
        
        if mission_completed:
            print(f"[思考] 任务审查完成: {decision_reason}")
            break
        else:
            print(f"[思考] 任务继续执行: {decision_reason}")
            # 继续循环，使用更新后的任务列表

    # ======5.最终回答======
    # 将tool_call_results转换为纯文本格式，供final_output使用
    tool_results_text = ""
    if tool_call_results:
        tool_results_text = "工具执行结果：\n"
        for tool_result in tool_call_results:
            agent_type = tool_result.get("agent_type", "unknown")
            result = tool_result.get("result", "")
            tool_results_text += f"- {agent_type}: {result}\n"
    else:
        tool_results_text = "无工具执行结果"
    
    final_answer = final_output(message_to_proceed, tool_results_text, relevant_memories, work_history)

    return final_answer


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


# ============= 测试代码 =============

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