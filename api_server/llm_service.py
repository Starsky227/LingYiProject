# -*- coding: utf-8 -*-
"""
LLM 服务模块 - 提供与本地大模型的通信接口
支持流式响应和模型预加载
使用 OpenAI API 标准格式接入本地 Ollama
"""
import os
import sys
import json
import datetime
from typing import List, Dict, Callable
from openai import OpenAI

# 添加项目根目录到路径
PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from system.config import config
# from system.intent_analyzer import analyze_intent
from brain.memory.quintuples_extractor import record_memories

# API 配置
API_KEY = config.api.api_key
API_URL = config.api.base_url
MODEL = config.api.model
AI_NAME = config.system.ai_name
USERNAME = config.ui.username

# 初始化 OpenAI 客户端（指向本地 Ollama）
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


def write_chat_log(sender: str, text: str):
    """将单条对话追加到 logs/chat_logs/chat_logs_YYYY_MM_DD.txt"""
    try:
        # 日志目录位于项目根的 logs/chat_logs
        logs_dir = os.path.normpath(os.path.join(PROJECT_ROOT, "logs", "chat_logs"))
        os.makedirs(logs_dir, exist_ok=True)
        filename = datetime.datetime.now().strftime("chat_logs_%Y_%m_%d.txt")
        path = os.path.join(logs_dir, filename)
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        # 将换行替换为空格以保持单行记录
        safe_text = text.replace("\r", " ").replace("\n", " ")
        line = f"{ts} <{sender}> {safe_text}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        # 写日志失败不影响主流程，打印到控制台以便排查
        print(f"[日志错误] 无法写入聊天日志: {e}")


def chat_with_model(messages: List[Dict], on_response: Callable[[str], None]) -> str:
    """
    将消息发送到 Ollama，并以流式方式处理返回
    
    Args:
        messages: 对话历史 [{"role": "user/assistant", "content": "..."}]
        on_response: 接收模型流式输出的回调函数
        
    Returns:
        完整的模型回复文本
    """
    # 记录最新的用户消息到日志
    if messages and messages[-1].get("role") == "user":
        user_content = messages[-1].get("content", "").strip()
        if user_content:
            write_chat_log(USERNAME, user_content)
    try:
        # 先进行意图识别
        # intent, explanation = analyze_intent(messages)
        
        # 组织要发送的消息：system prompt -> intent 提示 -> 对话历史
        payload_messages = []
        
        # 添加系统提示词
        if SYSTEM_PROMPT:
            payload_messages.append({"role": "system", "content": SYSTEM_PROMPT})
        
        # 添加意图分析结果
#        if intent and intent != "unknown":
#            intent_prompt = f"""当前对话意图分析：
#- 意图：{intent}
#- 解释：{explanation}
#请基于这个意图来调整你的回复风格和内容重点。"""
#            payload_messages.append({"role": "system", "content": intent_prompt})
        
        # 添加对话历史
        payload_messages.extend(messages)
        
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
        
        # 记录AI回复到日志
        if full_reply.strip():
            write_chat_log(AI_NAME, full_reply.strip())
            
            # 将完整对话记录为记忆（包含AI回复）
            # 创建包含AI回复的完整消息列表
            updated_messages = messages + [{"role": "assistant", "content": full_reply.strip()}]
            try:
                task_id = record_memories(updated_messages, max_messages=6)
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


def preload_and_get_greeting(timeout_sec: int = 30) -> str:
    """
    预加载模型并生成问候语
    
    Args:
        timeout_sec: 超时时间（秒）
        
    Returns:
        模型生成的问候语，失败时返回空字符串
    """
    print("模型加载中……", flush=True)
    
    prompt = f"请用一句简短的中文向用户打招呼，称呼用户为「{USERNAME}」。保持礼貌、简洁。"
    messages = [{"role": "user", "content": prompt}]
    parts = []
    
    def on_chunk(chunk: str):
        """收集流式片段"""
        parts.append(chunk)
    
    try:
        # 调用模型以完成预热并获取完整回复
        reply = chat_with_model(messages, on_chunk)
        if reply and reply.strip():
            return reply.strip()
        return "".join(parts).strip()
    except Exception as e:
        print(f"[警告] 预加载失败: {e}", flush=True)
        return ""


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
    greeting = preload_and_get_greeting()
    print(f"问候语: {greeting}")
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