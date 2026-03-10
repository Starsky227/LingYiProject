#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆写入功能模块，负责汇总消息内容，并将有价值的信息储存以节点形式储存至neo4j。
调用方式：
    event_extractor.passive_event_extraction(
        recent_message=recent_messages,
        related_memory=related_memory
    )
passive_event_extraction 会自动调用 passive_memory_record 来记录记忆信息，
不会返回任何内容，自动将记忆节点上传至neo4j图谱中。
"""

import os
import sys
import json
import logging
from typing import Dict, Any, List
from collections import deque
from datetime import datetime
from openai import OpenAI

# 添加项目根目录到Python路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from system.config import config

# API 配置
API_KEY = config.memory_api.memory_record_api_key
API_URL = config.memory_api.memory_record_base_url
MODEL = config.memory_api.memory_record_model
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
        filename: 文件名（如 "memory_record.txt"）
        description: 文件描述（用于错误提示）
    Returns:
        文件内容字符串，失败时返回空字符串
    """
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt", filename)
    if not os.path.exists(prompt_path):
        error_msg = f"{description}提示词文件不存在: {prompt_path}"
        logger.error(error_msg)
        return ""
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            logger.error(f"{description}提示词文件为空: {prompt_path}")
        return content

MEMORY_RECORD_PROMPT = load_prompt_file("memory_record.txt", "记忆存储")
EVENT_EXTRACT_PROMPT = load_prompt_file("event_extract.txt", "事件提取")

logger = logging.getLogger(__name__)


class MemoryContext:
    """会话上下文管理器，用于记录对话历史和事件提取结果"""
    
    def __init__(self, session_id: str, max_messages: int = 25):
        self.session_id = session_id
        self.messages = deque(maxlen=max_messages)
        self.extracted_events = deque(maxlen=10)  # 记录提取的事件
        self.last_updated = None

    def add_message(self, role: str, content: str):
        """添加对话消息"""
        self.messages.append({
            "role": role,
            "content": content,
            "time": datetime.now().isoformat(),
        })
        self.last_updated = datetime.now()

    def add_extracted_event(self, event_data: Dict[str, Any]):
        """记录提取的事件数据"""
        self.extracted_events.append({
            "event_data": event_data,
            "session_id": self.session_id,
            "time": datetime.now().isoformat(),
        })
        self.last_updated = datetime.now()

    def get_context(self) -> List[Dict[str, Any]]:
        """获取对话上下文"""
        return list(self.messages)
    
    def get_extracted_events(self) -> List[Dict[str, Any]]:
        """获取提取的事件列表"""
        return list(self.extracted_events)

    def clear(self):
        """清空上下文"""
        self.messages.clear()
        self.extracted_events.clear()
        self.last_updated = None


class MemoryWriter:
    """记忆写入处理器"""
    
    def __init__(self, kg_manager=None, memory_context=None):
        """
        初始化记忆写入器
        Args:
            kg_manager: KnowledgeGraphManager实例，用于实际的数据库操作
            memory_context: MemoryContext实例，用于记录对话上下文
        """
        self.kg_manager = kg_manager
        self.memory_context = memory_context
    
    
    def passive_event_extraction(self, recent_message: List[Dict[str, Any]], related_memory: Dict[str, Any] = None) -> None:
        """
        阅读群消息，判断是否值得记忆，并提取需要记忆的事件信息
        
        Args:
            recent_message: 最近的消息列表，格式：[{"role": "user", "content": "...", "time": "..."}, ...]
            related_memory: 相关记忆节点，格式：{"nodes": [...], "relations": [...]}，为空时自动从temp_memory.json读取
        """

        # 准备输入数据
        input_messages = [{"role": "system", "content": EVENT_EXTRACT_PROMPT}]
        
        # 如果没有输入related_memory，则读取temp_memory.json文件中的相关记忆数据
        if related_memory is None:
            temp_memory_path = os.path.join(os.path.dirname(__file__), "memory_graph", "temp_memory.json")
            try:
                with open(temp_memory_path, "r", encoding="utf-8") as f:
                    related_memory = json.load(f)
                logger.debug("从temp_memory.json加载了相关记忆数据")
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning(f"无法加载temp_memory.json: {e}")
                related_memory = {"nodes": [], "relationships": []}

        # 处理related_memory，将字典格式转换为文本格式
        if isinstance(related_memory, dict):
            # 转换为文本格式
            related_memory_text = f"请参考以下相关记忆进行决策：\n{str(related_memory)}"
        else:
            related_memory_text = "\n暂无相关记忆，可直接进行处理。"
        input_messages.append({"role": "user", "content": related_memory_text})

        # 使用传入的 recent_message 参数构建消息段落
        if recent_message:
            # 构建消息段落
            messages_paragraph = "收到消息：\n"
            
            if len(recent_message) > 1:
                # 有多条消息，前面的作为历史消息
                for msg in recent_message[:-1]:
                    messages_paragraph += msg.get("content", "") + "\n"
                
                messages_paragraph += "\n【最新消息】\n"
                messages_paragraph += recent_message[-1].get("content", "")
            else:
                # 只有一条消息
                messages_paragraph = "【历史消息】\n暂无历史消息\n\n【最新消息】\n"
                messages_paragraph += recent_message[0].get("content", "")
        else:
            # 没有消息
            messages_paragraph = "【历史消息】\n暂无历史消息\n\n【最新消息】\n暂无消息"
        
        input_messages.append({"role": "user", "content": messages_paragraph})

        # 调用模型
        response = client.responses.create(
            model=MODEL,
            input=input_messages,
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
        )

        full_response = response.output_text

        if not full_response:
            logger.error("记忆存储模型未返回响应。")
            return
        
        # 提取输出为 json 格式
        try:
            # 尝试解析JSON响应
            if full_response.strip().startswith('```json'):
                # 移除markdown代码块标记
                json_start = full_response.find('[') if '[' in full_response[:full_response.find('{') + 1] else full_response.find('{')
                json_end = (full_response.rfind(']') + 1) if full_response.rfind(']') > full_response.rfind('}') else (full_response.rfind('}') + 1)
                json_content = full_response[json_start:json_end]
            else:
                json_content = full_response.strip()
            
            extract_events = json.loads(json_content)
            
            # 兼容单个dict和list格式
            if isinstance(extract_events, dict):
                extract_events = [extract_events]
            
            logger.debug(f"解析出的事件数据: {json.dumps(extract_events, ensure_ascii=False, indent=2)}")
                
        except json.JSONDecodeError as e:
            logger.error(f"无法解析模型返回的JSON格式: {e}")
            logger.error(f"原始响应: {full_response}")
            return
        except Exception as e:
            logger.error(f"Error processing response: {e}")
            return

        # 遍历事件列表，逐个记忆并将结果并入related_memory
        if not extract_events or not isinstance(extract_events, list):
            logger.debug("未提取到有效事件，跳过记忆记录")
            return
        
        for extract_event in extract_events:
            if not isinstance(extract_event, dict):
                continue
            if not extract_event.get('event'):
                logger.debug("事件字段为空，跳过记忆记录")
                continue
            if 'description' not in extract_event:
                logger.debug("缺少description字段，跳过记忆记录")
                continue
            
            logger.debug(f"提取到事件: {extract_event.get('event', '')}")
            
            # 调用记忆记录功能
            result = self.passive_memory_record(
                memory_to_record=extract_event,
                related_memory=related_memory,
            )
            
            # 将返回的nodes和relations并入related_memory，供下一个事件使用
            if result and isinstance(result, dict):
                if 'nodes' not in related_memory:
                    related_memory['nodes'] = []
                if 'relations' not in related_memory:
                    related_memory['relations'] = []
                related_memory['nodes'].extend(result.get('nodes', []))
                related_memory['relations'].extend(result.get('relations', []))

    
    def passive_memory_record(self, memory_to_record: Dict[str, Any], related_memory: Dict[str, Any]) -> None:
        """
        记录记忆信息，调用LLM生成结构化的记忆记录
        
        Args:
            memory_to_record: 需要记忆的信息，格式：{"event": "", "description": [...]}
            related_memory: 相关记忆节点，格式：{"nodes": [...], "relations": [...]}
            
        Returns:
            调整过的和新创建的所有node和relation {"nodes": [...], "relations": [...]}
            没有则输出{"nodes": [], "relations": []}
        """
        # 处理related_memory，将字典格式转换为文本格式
        if isinstance(related_memory, dict):
            # 转换为文本格式
            related_memory_text = f"请参考以下相关记忆进行决策：\n{str(related_memory)}"
        else:
            related_memory_text = "暂无相关记忆，可直接进行处理。"
        
        logger.debug(f"记忆存储接收到任务: {str(memory_to_record)}")
        
        related_memory_text = related_memory_text + "\n\n请评估并记忆以下事件：\n" + str(memory_to_record)

        # 准备输入数据
        input_messages = []

        # 添加系统prompt和当前的记忆信息
        input_messages.extend([
            {"role": "system", "content": MEMORY_RECORD_PROMPT},
            {"role": "user", "content": related_memory_text},
            {"role": "user", "content": str(memory_to_record)}
        ])

        # 调用模型
        response = client.responses.create(
            model=MODEL,
            input=input_messages,
            reasoning={"effort": "medium"},
            text={"verbosity": "low"},
        )

        full_response = response.output_text

        if not full_response:
            logger.error("记忆存储模型未返回响应。")
            return
        
        # 提取输出为 json 格式
        try:
            # 尝试解析JSON响应
            if full_response.strip().startswith('```json'):
                # 移除markdown代码块标记
                json_start = full_response.find('{')
                json_end = full_response.rfind('}') + 1
                json_content = full_response[json_start:json_end]
            else:
                json_content = full_response.strip()
            
            memory_data = json.loads(json_content)
                
        except json.JSONDecodeError as e:
            logger.error(f"无法解析模型返回的JSON格式: {e}")
            logger.error(f"原始响应: {full_response}")
            return
        except Exception as e:
            logger.error(f"Error processing response: {e}")
            return

        # 如果没有kg_manager实例，则仅返回解析的数据
        if not self.kg_manager:
            return

        return self.kg_manager.upload_memory_package(memory_data)
    
    def full_memory_record(self, message: str, related_memory: Dict[str, Any] = None) -> None:
        """
        完整的记忆记录流程。
        输入任意str，执行：搜索相关记忆 -> 事件提取 -> 记忆写入。
        每次调用相当于main中用户执行一轮记忆。
        """
        from brain.memory.search_memory import full_memory_search

        # 1. 搜索相关记忆（如果未提供）
        if related_memory is None:
            related_memory = full_memory_search(message, save_temp_memory=True)
        logger.debug(f"检索到 {len(related_memory.get('nodes', []))} 个相关记忆节点")

        # 2. 添加到会话上下文
        if self.memory_context:
            self.memory_context.add_message("user", message)
            recent_messages = self.memory_context.get_context()
        else:
            recent_messages = [{"role": "user", "content": message}]

        # 3. 事件提取 + 记忆写入
        self.passive_event_extraction(recent_messages, related_memory)

def main():
    """测试被动事件提取功能"""
    from brain.memory.knowledge_graph_manager import KnowledgeGraphManager
    
    # 初始化知识图谱管理器、会话上下文和记忆写入器
    kg_manager = KnowledgeGraphManager()
    session_context = MemoryContext("test_session_001")
    event_extractor = MemoryWriter(kg_manager, session_context)
    
    print("=== 事件提取功能测试 ===")
    print(f"会话ID: {session_context.session_id}")
    print("输入 'quit' 或 'exit' 退出测试")
    print()
    
    round_count = 1
    
    while True:
        print(f"--- 第 {round_count} 轮测试 ---")
        
        # 显示当前上下文状态
        current_messages = session_context.get_context()
        current_events = session_context.get_extracted_events()
        print(f"当前历史消息数: {len(current_messages)}")
        print(f"当前已提取事件数: {len(current_events)}")
        
        # 从用户输入获取对话记录
        print("请输入对话记录，建议格式：[时间戳] <角色> 对话内容")
        print("输入示例：[2026/1/7T16:01] <角色> 对话内容")
        print("回车键结束当前轮次输入")
        
        event_text = []
        user_input = input("输入对话记录: ").strip()
        if user_input.lower() in ['quit', 'exit']:
            print("退出测试...")
            return
        # 将用户输入转换为所需格式
        event_text.append({"role": "user", "content": user_input})
        
        # 将消息添加到上下文中
        for msg in event_text:
            session_context.add_message(msg["role"], msg["content"])
        
        try:
            print("开始测试事件提取功能...")
            
            # 从 session_context 获取消息历史
            recent_messages = session_context.get_context()
            
            # 调用被测试的函数
            event_extractor.passive_event_extraction(recent_message=recent_messages)
            
            print("事件提取测试完成!")
            
            print()
            print("=" * 50)
            print()
            
        except Exception as e:
            print(f"测试过程中出现错误: {e}")
            import traceback
            traceback.print_exc()
            print()
        
        round_count += 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main()