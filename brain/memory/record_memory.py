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
    加载提示词文件的通用函数，支持从任意位置导入调用。
    Args:
        filename: 相对于项目根目录的路径（如 "brain/memory/prompt/memory_record.txt"）
                  或仅文件名（如 "memory_record.txt"，将在当前文件同级的 prompt 目录下查找）
        description: 文件描述（用于错误提示）
    Returns:
        文件内容字符串，失败时返回空字符串
    """
    # 优先尝试基于当前文件同级的 prompt 目录
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt", filename)
    # 如果当前文件同级的 prompt 目录下找不到，回退到项目根目录
    if not os.path.exists(prompt_path):
        prompt_path = os.path.join(project_root, filename)
    
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


class MemoryWriter:
    """记忆写入处理器"""
    
    def __init__(self, kg_manager=None, memory_record_prompt: str = None, event_extract_prompt: str = None, max_event_history: int = 25):
        """
        初始化记忆写入器
        Args:
            kg_manager: KnowledgeGraphManager实例，用于实际的数据库操作
            memory_record_prompt: 自定义记忆存储提示词内容，为None时使用默认prompt
            event_extract_prompt: 自定义事件提取提示词内容，为None时使用默认prompt
            max_event_history: 已提取事件的最大记录数
        """
        self.kg_manager = kg_manager
        self.memory_record_prompt = memory_record_prompt if memory_record_prompt else MEMORY_RECORD_PROMPT
        self.event_extract_prompt = event_extract_prompt if event_extract_prompt else EVENT_EXTRACT_PROMPT
        self._extracted_events: deque = deque(maxlen=max_event_history)  # 记录已提取的事件，避免重复
    
    
    def passive_event_extraction(self, message: str, related_memory: Dict[str, Any] = None) -> None:
        """
        阅读群消息，判断是否值得记忆，并提取需要记忆的事件信息
        
        Args:
            message: 当前消息内容

            related_memory: 相关记忆节点，格式：{"nodes": [...], "relations": [...]}，为空时自动从temp_memory.json读取
        """

        # 准备输入数据
        input_messages = [{"role": "system", "content": self.event_extract_prompt}]
        
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

        # 获取已提取过的event列表，避免重复提取和记录
        if self._extracted_events:
            existing_events = list(self._extracted_events)
            existing_events_text = f"以下是之前已经提取过的事件（请勿重复提取）:\n{json.dumps(existing_events, ensure_ascii=False, indent=2)}"
            input_messages.append({"role": "user", "content": existing_events_text})

        # 加入待处理的文本
        input_messages.append({"role": "user", "content": f"需要处理的新消息：\n{message}"})

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
            
            extracted_events = json.loads(json_content)
            
            # 兼容单个dict和list格式
            if isinstance(extracted_events, dict):
                extracted_events = [extracted_events]
            
            logger.debug(f"解析出的事件数据: {json.dumps(extracted_events, ensure_ascii=False, indent=2)}")
                
        except json.JSONDecodeError as e:
            logger.error(f"无法解析模型返回的JSON格式: {e}")
            logger.error(f"原始响应: {full_response}")
            return
        except Exception as e:
            logger.error(f"Error processing response: {e}")
            return

        # 遍历事件列表，逐个记忆并将结果并入related_memory
        if not extracted_events or not isinstance(extracted_events, list):
            logger.debug("未提取到有效事件，跳过记忆记录")
            return
        
        for extracted_event in extracted_events:
            if not isinstance(extracted_event, dict):
                continue
            if not extracted_event.get('event'):
                logger.debug("事件字段为空，跳过记忆记录")
                continue
            if 'description' not in extracted_event:
                logger.debug("缺少description字段，跳过记忆记录")
                continue
            
            logger.debug(f"提取到事件: {extracted_event.get('event', '')}")
            
            # 记录已提取的事件，避免后续重复
            self._extracted_events.append(extracted_event.get('event'))
            
            # 调用记忆记录功能
            result = self.passive_memory_record(
                memory_to_record=extracted_event,
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
            {"role": "system", "content": self.memory_record_prompt},
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

        # 1. 搜索相关记忆（如果未提供），搜索时使用当前上下文（不含本条消息）
        if related_memory is None:
            related_memory = full_memory_search(message, save_temp_memory=True)
        logger.debug(f"检索到 {len(related_memory.get('nodes', []))} 个相关记忆节点")

        # 2. 事件提取 + 记忆写入
        self.passive_event_extraction(message, related_memory)

def main():
    """测试被动事件提取功能"""
    from brain.memory.knowledge_graph_manager import KnowledgeGraphManager
    
    # 初始化知识图谱管理器和记忆写入器
    kg_manager = KnowledgeGraphManager()
    event_extractor = MemoryWriter(kg_manager)
    
    print("=== 事件提取功能测试 ===")
    print("输入 'quit' 或 'exit' 退出测试")
    print()
    
    round_count = 1
    
    while True:
        print(f"--- 第 {round_count} 轮测试 ---")
        print(f"当前已提取事件数: {len(event_extractor._extracted_events)}")
        
        user_input = input("输入对话记录: ").strip()
        if user_input.lower() in ['quit', 'exit']:
            print("退出测试...")
            return
        
        try:
            print("开始测试事件提取功能...")
            event_extractor.passive_event_extraction(message=user_input)
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