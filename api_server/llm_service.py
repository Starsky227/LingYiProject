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

from brain.memory.quintuples_extractor import record_memories
from brain.memory.relevant_memory_search import query_relevant_memories
from mcpserver.mcp_manager import get_mcp_manager
from system.config import config
from system.background_analyzer import analyze_intent, plan_tasks

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
    
    # 使用get_recent_chat限制聊天记录长度，避免上下文过长
    recent_messages = get_recent_chat(messages)
    # 检查是否有有效消息
    if not recent_messages or not recent_messages[-1].get("content", "").strip():
        print("[INFO] 检测到空白消息，跳过处理")
        return ""
    
    # 进行意图识别
    intent_type, tasks_todo = analyze_intent(recent_messages)
    print(f"[思考] 识别到意图类型: {intent_type}, 任务: {tasks_todo}")

    # 进行任务分解
    todo_list = plan_tasks(recent_messages, tasks_todo, tools_available)
    print(f"[思考] 规划后续任务: \n{todo_list}")

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
            tools_available = "# 可用工具和服务\n\n" + "".join(tools_available_list)
        else:
            tools_available = "当前无可用工具和服务"
            
        if DEBUG_MODE:
            print(f"[DEBUG] 工具描述文本长度: {len(tools_available)} 字符")
            print(f"[DEBUG] 工具预览: {tools_available}...")
            
    except Exception as e:
        print(f"[Warning] 获取可用工具失败: {e}")
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        tools_available = "获取工具信息失败"
    
    # 进行工具调用

    try:
        relevant_memories = ""
        if messages and messages[-1].get("content"):
            latest_message = messages[-1].get("content", "").strip()
            latest_role = messages[-1].get("role", "unknown")
            if latest_message:
                # 提取最近的上下文（最近3条消息的内容）
                recent_context = []
                for msg in messages[-3:]:
                    if msg.get("content"):
                        recent_context.append(msg["content"])
                
                relevant_memories = query_relevant_memories(latest_message, recent_context)
                if relevant_memories:
                    print(f"[记忆] 基于 {latest_role} 的消息查询到相关记忆")
        
        # 组织要发送的消息：system prompt -> memory context -> intent 提示 -> 对话历史
        payload_messages = []
        
        # 添加系统提示词
        if SYSTEM_PROMPT:
            payload_messages.append({"role": "system", "content": SYSTEM_PROMPT})
        
        # 添加记忆上下文
        if relevant_memories:
            memory_context = f"""你可以参考以下相关的历史记忆信息来回复：

{relevant_memories}

请基于这些记忆信息和当前对话来提供更准确、个性化的回复。如果记忆信息与当前对话内容相关，可以自然地引用它们。"""
            payload_messages.append({"role": "system", "content": memory_context})
            print(f"[记忆] 找到 {len(relevant_memories.split('\\n')) - 1} 条相关记忆")
        
        # 添加意图分析结果
#        if intent and intent != "unknown":
#            intent_prompt = f"""当前对话意图分析：
#- 意图类型：{intent}
#- 具体任务：{tasks_todo}
#请基于这个意图来调整你的回复风格和内容重点。"""
#            payload_messages.append({"role": "system", "content": intent_prompt})
        
        # 添加对话历史（使用筛选后的最近消息）
        payload_messages.extend(recent_messages)
        
        # 使用 OpenAI API 格式调用 Ollama（流式）
        stream = client.chat.completions.create(
            model=MODEL,
            messages=payload_messages,
            stream=True,
            temperature=0.7,
        )
        
        full_reply = ""
        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                content = chunk.choices[0].delta.content
                full_reply += content
                on_response(content)
        
        # 将完整对话记录为记忆（包含AI回复）
        if full_reply.strip():
            # 创建包含AI回复的完整消息列表（使用筛选后的消息以控制长度）
            updated_messages = recent_messages + [{"role": "assistant", "content": full_reply.strip()}]
            try:
                task_id = record_memories(updated_messages, source="api_server", max_messages=6)
                if task_id:
                    print(f"[记忆] 已提交记忆提取任务: {task_id}")
            except Exception as e:
                print(f"[记忆错误] 记忆提取失败: {e}")
        
        return full_reply
        
    except Exception as e:
        error_msg = f"通信异常: {str(e)}"
        print(f"[错误] {error_msg}")
        try:
            on_response(f"\n[错误] {error_msg}\n")
        except:
            pass
        return ""


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