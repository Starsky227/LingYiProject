from system.config import config


class LingYiCore:
    """AI 模型客户端"""

    def __init__(self, prompt) -> None:
        self.prompt = prompt

    
    def process_incoming_information(self, information: str) -> str:
        """处理输入信息，生成回复"""
        return information