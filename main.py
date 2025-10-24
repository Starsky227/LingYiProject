import logging
import sys
import json
import os
from PyQt5.QtWidgets import QApplication
from ui.chat_ui import ChatUI

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory")
logger.setLevel(logging.INFO)

# 从 config 调取配置（保留在 main 用于 UI 显示等）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
    configjson = json.load(f)

AI_NAME = configjson["general"]["ai_name"]
LOCAL_MODEL = configjson["api"]["local_model"]
USERNAME = configjson["ui"]["username"]
print("AI name:", AI_NAME)

# 从独立服务模块导入模型交互函数与预加载函数
from api_server.llm_service import chat_with_model, preload_and_get_greeting

# =============== 启动器部分 ===============
def main():
    # 先预加载模型并获取问候语（阻塞）
    greeting = preload_and_get_greeting()

    app = QApplication(sys.argv)
    # 将 chat_with_model 函数与 AI 名传入 UI 界面
    window = ChatUI(chat_with_model, ai_name=AI_NAME)
    window.show()

    # 预加载完成后，在 UI 中显示模型的问候（主线程中操作）
    if greeting:
        window._append_text(f"{AI_NAME}: {greeting}\n")
        window.messages.append({"role": "assistant", "content": greeting})

    sys.exit(app.exec_())

if __name__ == "__main__":
    print("🧠 启动本地 Gemma3 聊天程序中...")
    main()
