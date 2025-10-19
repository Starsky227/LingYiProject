import sys
import requests
import json
import os
from PyQt5.QtWidgets import QApplication
from ui.chat_ui import ChatUI

# 从config调取配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
    configjson = json.load(f)

OLLAMA_API_URL = configjson["api"]["local_api"]
MODEL = configjson["api"]["local_model"]
AI_NAME = configjson["general"]["AI_name"]
print(OLLAMA_API_URL, MODEL, AI_NAME)

# 读取 system 提示词（system/prompts/personality.txt）
SYSTEM_PROMPT = None
_personality_path = os.path.join(BASE_DIR, "system", "prompts", "personality.txt")
if os.path.exists(_personality_path):
    try:
        with open(_personality_path, "r", encoding="utf-8") as f:
            SYSTEM_PROMPT = f.read().strip()https://github.com/Starsky227/LingYiProject.git
    except Exception as e:
        print(f"[警告] 无法读取系统提示词: {_personality_path} - {e}")
else:
    print(f"[提示] 未找到系统提示词文件: {_personality_path}")

# =============== 模型交互部分 ===============
def chat_with_model(messages, on_response):
    """
    发送消息给 Ollama 并流式输出回复（调用 on_response 进行流式显示）
    """
    try:
        # 在发送前将 system 提示词插入到消息开头（如果存在）
        payload_messages = []
        if SYSTEM_PROMPT:
            payload_messages.append({"role": "system", "content": SYSTEM_PROMPT})
        # 追加当前对话消息（不修改原 messages 变量）
        payload_messages.extend(messages)

        response = requests.post(
            OLLAMA_API_URL,
            json={"model": MODEL, "messages": payload_messages},
            stream=True,
        )

        full_reply = ""
        for line in response.iter_lines():
            if line:
                data = json.loads(line.decode("utf-8"))
                # 如果当前行包含消息内容，则提取并通过回调发送给 UI
                if "message" in data and "content" in data["message"]:
                    content = data["message"]["content"]
                    full_reply += content
                    # 通知 UI 收到一段流式输出
                    on_response(content)

        return full_reply
    except Exception as e:
        # 出现异常时通过回调发送错误信息，便于 UI 展示
        on_response(f"\n[错误] 无法连接到 Ollama: {e}\n")
        return ""


# =============== 启动器部分 ===============
def main():
    app = QApplication(sys.argv)
    # 将 chat_with_model 函数与 AI 名传入 UI 界面
    window = ChatUI(chat_with_model, ai_name=AI_NAME)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    print("🧠 启动本地 Gemma3 聊天程序中...")
    main()
