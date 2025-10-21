import os
import json
import requests
from system.intent_analyzer import analyze_intent

# 计算项目根目录（api_server 的上一级）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(BASE_DIR, ".."))

# 从 config 读取配置
config_path = os.path.join(PROJECT_ROOT, "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    configjson = json.load(f)

OLLAMA_API_URL = configjson.get("api", {}).get("local_api", "")
MODEL = configjson.get("api", {}).get("local_model", "")
AI_NAME = configjson.get("general", {}).get("AI_name", "AI")
USERNAME = configjson.get("ui", {}).get("username", "用户")

# 读取 system 提示词（system/prompts/personality.txt）
SYSTEM_PROMPT = None
_personality_path = os.path.join(PROJECT_ROOT, "system", "prompts", "personality.txt")
if os.path.exists(_personality_path):
    try:
        with open(_personality_path, "r", encoding="utf-8") as f:
            raw = f.read()
        # 做占位符替换（支持 {AI_NAME}）
        SYSTEM_PROMPT = raw.replace("{AI_NAME}", AI_NAME).strip()
    except Exception:
        SYSTEM_PROMPT = None
else:
    SYSTEM_PROMPT = None


def chat_with_model(messages, on_response):
    """
    将消息发送到 Ollama，并以流式方式处理返回
    在发送前先进行一轮意图识别，将识别到的意图作为额外的 system 提示插入
    """
    try:
        # 先进行意图识别
        intent, explanation = analyze_intent(messages)
        
        # 组织要发送的消息：system prompt -> intent 提示 -> 对话历史
        payload_messages = []
        if SYSTEM_PROMPT:
            payload_messages.append({"role": "system", "content": SYSTEM_PROMPT})
        
        # 将意图分析结果作为 system 提示
        intent_prompt = f"""当前对话意图分析：
- 意图：{intent}
- 解释：{explanation}
请基于这个意图来调整你的回复风格和内容重点。"""
        
        payload_messages.append({"role": "system", "content": intent_prompt})
        payload_messages.extend(messages)

        response = requests.post(
            OLLAMA_API_URL,
            json={"model": MODEL, "messages": payload_messages},
            stream=True,
        )

        full_reply = ""
        for line in response.iter_lines():
            if not line:
                continue
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception:
                chunk = line.decode("utf-8", errors="ignore")
                full_reply += chunk
                on_response(chunk)
                continue

            # 如果当前行包含消息内容，则提取并通过回调发送给 UI
            if isinstance(data, dict) and "message" in data and "content" in data["message"]:
                content = data["message"]["content"]
                full_reply += content
                on_response(content)

        return full_reply
    except Exception as e:
        # 出现异常时通过回调发送错误信息，便于 UI 展示
        try:
            on_response(f"\n[错误] 无法连接到 Ollama: {e}\n")
        except Exception:
            pass
        return ""
    

def preload_and_get_greeting(timeout_sec: int = 30) -> str:
    """
    在显示 UI 之前触发一次模型调用以预热并让模型生成一条简短问候语。
    返回模型生成的问候字符串（失败时返回空字符串）。
    """
    # 提示用户模型正在加载
    print(" 🔄 模型加载中……首次加载可能需要1-2分钟")
    
    prompt = f"请用一句简短的中文向用户打招呼，称呼用户为「{USERNAME}」。保持礼貌、简洁。"
    messages = [{"role": "user", "content": prompt}]
    parts = []

    def on_chunk(chunk: str):
        # 收集流式片段（如果 chat_with_model 有流式回调）
        parts.append(chunk)

    try:
        # 阻塞地调用模型以完成预热并拿到完整回复
        reply = chat_with_model(messages, on_chunk)
        print(" ✅ 模型加载完成，可以开始聊天了！")
        if reply and reply.strip():
            return reply.strip()
        return "".join(parts).strip()
    except Exception:
        return ""