import os
import json
import requests
from typing import Dict, List, Optional, Tuple

# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 读取配置
with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
    configjson = json.load(f)

OLLAMA_API_URL = configjson["api"]["local_api"]
MODEL = configjson["api"]["local_model"]

# 读取意图分析提示词
INTENT_PROMPT = ""
prompt_path = os.path.join(BASE_DIR, "system", "prompts", "intent_analyze.txt")
if os.path.exists(prompt_path):
    with open(prompt_path, "r", encoding="utf-8") as f:
        INTENT_PROMPT = f.read().strip()

def analyze_intent(messages: List[Dict]) -> Tuple[str, str]:
    """
    使用 LLM 分析最后一条用户消息的意图
    返回: (意图标签, 解释)
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
        # 构建分析请求
        analysis_messages = [
            {"role": "system", "content": INTENT_PROMPT},
            {"role": "user", "content": last_user_msg}
        ]

        # 调用模型分析意图（使用流式响应）
        response = requests.post(
            OLLAMA_API_URL,
            json={"model": MODEL, "messages": analysis_messages},
            stream=True  # 改为流式接收
        )
        
        if response.status_code == 200:
            # 收集完整响应
            full_response = ""
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line.decode('utf-8'))
                        if "message" in data and "content" in data["message"]:
                            full_response += data["message"]["content"]
                    except json.JSONDecodeError:
                        continue
            
            # 解析意图和解释
            intent = "unknown"
            explanation = ""
            
            for line in full_response.split("\n"):
                line = line.strip()
                if line.startswith("意图："):
                    intent = line.replace("意图：", "").strip()
                elif line.startswith("解释："):
                    explanation = line.replace("解释：", "").strip()
            
            print(f"用户意图识别为: {intent}")
            print(f"解释: {explanation}")
            return intent, explanation

    except Exception as e:
        print(f"[错误] 意图分析失败: {e}")
    
    return "unknown", "分析失败"