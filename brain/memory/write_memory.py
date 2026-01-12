#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆写入功能模块
负责处理记忆的被动记录和测试功能
"""

import os
import sys
import json
import logging
from typing import Dict, Any, List
from collections import deque
from datetime import datetime
from litellm import OpenAI

# 添加项目根目录到Python路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from system.config import config

# API 配置
API_KEY = config.api.api_key
API_URL = config.api.base_url
MODEL = config.api.model
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
        error_msg = f"[错误] {description}提示词文件不存在: {prompt_path}"
        print(error_msg)
        return ""
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            print(f"[警告] {description}提示词文件为空: {prompt_path}")
        return content

MEMORY_RECORD_PROMPT = load_prompt_file("memory_record.txt", "记忆存储")
EVENT_EXTRACT_PROMPT = load_prompt_file("event_extract.txt", "事件提取")

logger = logging.getLogger(__name__)


class ConversationContext:
    """会话上下文管理器，用于记录对话历史和事件提取结果"""
    
    def __init__(self, session_id: str, max_messages: int = 40):
        self.session_id = session_id
        self.messages = deque(maxlen=max_messages)
        self.extracted_events = deque(maxlen=10)  # 记录提取的事件
        self.last_updated = None

    def add_message(self, role: str, content: str):
        """添加对话消息"""
        self.messages.append({
            "role": role,
            "content": content
        })
        self.last_updated = datetime.now()

    def add_extracted_event(self, event_data: Dict[str, Any]):
        """记录提取的事件数据"""
        self.extracted_events.append({
            "event_data": event_data,
            "session_id": self.session_id
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
    
    def __init__(self, kg_manager=None, conversation_context=None):
        """
        初始化记忆写入器
        Args:
            kg_manager: KnowledgeGraphManager实例，用于实际的数据库操作
            conversation_context: ConversationContext实例，用于记录对话上下文
        """
        self.kg_manager = kg_manager
        self.conversation_context = conversation_context
    
    
    def passive_event_extraction_from_message(self, event_text: Dict[str, Any], related_memory: Dict[str, Any]) -> Dict[str, Any]:
        """
        阅读群消息，判断是否值得记忆，并提取需要记忆的事件信息
        
        Args: 
            event_text：聊天历史记录，格式严格为：[{"role": "user", "content": f"[{timestamp}] <{name}> {content}"}, ...]
                注：即便是assistant的发言，也应以user的角色出现，因为这里的assistant并非AI本体，而是记忆系统。
            related_memory: 相关记忆节点，格式：{"nodes": [...], "relations": [...]}
        调用：passive_memory_record函数，输入
            memory_to_record: 需要记忆的信息，格式：{"event": "", "description": [...]}
        Returns：
            None
        """
        # 处理related_memory，将字典格式转换为文本格式
        if isinstance(related_memory, dict):
            # 转换为文本格式
            related_memory_text = f"【请参考以下相关记忆进行决策】\n{str(related_memory)}"
        else:
            related_memory_text = "【相关记忆】\n暂无相关记忆，可直接进行处理"
        
        # 确保event_text中所有的role都是"user"
        for message in event_text:
            message["role"] = "user"
        
        # 准备输入数据
        input_messages = [{"role": "system", "content": EVENT_EXTRACT_PROMPT}]
        
        # 添加处理过的事件extracted_event
        if self.conversation_context:
            extracted_events = self.conversation_context.get_extracted_events()
            for extracted_event in extracted_events:
                event_data = extracted_event.get('event_data', {})
                if event_data and 'event' in event_data:
                    input_messages.append({
                        "role": "assistant", 
                        "content": event_data['event']
                    })
                    print(f"[DEBUG] 添加已提取事件到输入: {event_data['event']}")
                
        input_messages.append({"role": "user", "content": related_memory_text})

        # 添加系统prompt和当前的记忆信息
        input_messages.extend(event_text)

        # 调用模型
        response = client.chat.completions.create(
            model=MODEL,
            messages=input_messages,
            stream=False,
            temperature=0.2
        )

        full_response = response.choices[0].message.content

        if not full_response:
            print(f"[错误] 记忆存储模型未返回响应。")
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
            
            extract_events = json.loads(json_content)
            
            # 检测并确保格式为列表
            if not isinstance(extract_events, list):
                if isinstance(extract_events, dict):
                    # 如果是单个字典，包装为列表
                    extract_events = [extract_events]
                    if DEBUG_MODE:
                        print(f"[DEBUG] 将单个字典转换为列表格式")
                else:
                    # 如果既不是列表也不是字典，设为空列表
                    extract_events = []
                    if DEBUG_MODE:
                        print(f"[DEBUG] 未识别的格式，设置为空列表: {type(extract_events)}")
            
            if DEBUG_MODE:
                print(f"[DEBUG] 解析出的记忆数据: {json.dumps(extract_events, ensure_ascii=False, indent=2)}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            print(f"[错误] 无法解析模型返回的JSON格式: {e}")
            print(f"[原始响应] {full_response}")
            return {"nodes": [], "relations": []}
        except Exception as e:
            logger.error(f"Error processing response: {e}")
            return []

        # 记录提取的事件数据到上下文中
        if self.conversation_context:
            for event in extract_events:
                self.conversation_context.add_extracted_event(event)
        
        # 如果有有效的事件数据，可以继续处理记忆记录
        if extract_events and len(extract_events) > 0:
            if DEBUG_MODE:
                print(f"[DEBUG] 提取到 {len(extract_events)} 个事件，准备进行记忆记录")
            
            # 对每个提取的事件调用记忆记录功能
            results = []
            for event in extract_events:
                if isinstance(event, dict) and 'event' in event and 'description' in event:
                    # 调用passive_memory_record进行实际的记忆存储
                    result = self.passive_memory_record_test(
                        work_history=[],  # 可以根据需要传入对话历史
                        memory_to_record=event,
                        related_memory=related_memory
                    )
                    results.append(result)
            
            return results
        else:
            if DEBUG_MODE:
                print("[DEBUG] 未提取到有效事件，跳过记忆记录")
            return []

    
    def passive_memory_record(self, work_history, memory_to_record: Dict[str, Any], related_memory: Dict[str, Any]) -> Dict[str, Any]:
        """
        记录记忆信息，调用LLM生成结构化的记忆记录
        
        Args:
            work_history: 历史记录，格式严格为：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
            memory_to_record: 需要记忆的信息，格式：{"event": "", "description": [...]}
            related_memory: 相关记忆节点，格式：{"nodes": [...], "relations": [...]}
            
        Returns:
            调整过的和新创建的所有node和relation {"nodes": [...], "relations": [...]}
            没有则输出{"nodes": [], "relations": []}
        """
        # 处理related_memory，将字典格式转换为文本格式
        if isinstance(related_memory, dict):
            # 转换为文本格式
            related_memory_text = f"【请参考以下相关记忆进行决策】\n{str(related_memory)}"
        else:
            related_memory_text = "【相关记忆】\n暂无相关记忆，可直接进行处理"
        
        if DEBUG_MODE:
            print(f"[DEBUG] 记忆存储接收到任务: {str(memory_to_record)}")
        
        # 准备输入数据
        input_messages = []
        
        # 如果有work_history，先添加到最前面
        if work_history and isinstance(work_history, list):
            input_messages.extend(work_history)
        
        # 添加系统prompt和当前的记忆信息
        input_messages.extend([
            {"role": "system", "content": MEMORY_RECORD_PROMPT},
            {"role": "user", "content": related_memory_text},
            {"role": "user", "content": str(memory_to_record)}
        ])

        # 调用模型
        response = client.chat.completions.create(
            model=MODEL,
            messages=input_messages,
            stream=False,
            temperature=0.1
        )

        full_response = response.choices[0].message.content

        if DEBUG_MODE:
            print(f"[DEBUG] 记忆存储模型回应: {full_response}")
        if not full_response:
            print(f"[DEBUG] 记忆存储模型未返回响应。")
            return None

        if not full_response:
            print(f"[错误] 记忆存储模型未返回响应。")
            return {"nodes": [], "relations": []}
        
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
            logger.error(f"Failed to parse JSON response: {e}")
            print(f"[错误] 无法解析模型返回的JSON格式: {e}")
            print(f"[原始响应] {full_response}")
            return {"nodes": [], "relations": []}
        except Exception as e:
            logger.error(f"Error processing response: {e}")
            return {"nodes": [], "relations": []}

        # 如果没有kg_manager实例，则仅返回解析的数据
        if not self.kg_manager:
            return memory_data

        # 读取nodes中的所有内容，记录在nodelist中
        nodes_list = memory_data.get("nodes", [])
        relations_list = memory_data.get("relations", [])
        
        if DEBUG_MODE:
            print(f"[DEBUG] 处理 {len(nodes_list)} 个节点和 {len(relations_list)} 个关系")

        try:
            with self.kg_manager.driver.session() as session:
                # 遍历nodelist，处理节点
                for node in nodes_list:
                    try:
                        node_id = node.get("nodeId")
                        node_type = node.get("nodeType")
                        node_info = node.get("nodeInfo", {})
                        
                        if not node_id or not node_type:
                            logger.warning(f"Node missing required fields: {node}")
                            continue
                        
                        # 检查节点是否已存在于图谱中
                        check_query = """
                        OPTIONAL MATCH (n) WHERE elementId(n) = $node_id
                        RETURN elementId(n) as existing_id
                        """
                        
                        check_result = session.run(check_query, node_id=node_id).single()
                        existing_node_id = check_result["existing_id"] if check_result else None
                        
                        if existing_node_id:
                            # 节点存在，调用modify_node修改节点属性
                            updates = {}
                            for key, value in node_info.items():
                                if key not in ["nodeId", "nodeType", "significance"]:  # 排除不应加入的字段
                                    updates[key] = value
                            # 处理updates字典
                            # 排除所有Time时间节点，该节点不遵从modify_node逻辑
                            if node_type == "Time":
                                updates = {}
                            
                            if updates:
                                result = self.kg_manager.modify_node(existing_node_id, updates)
                                if result:
                                    logger.info(f"Updated existing node: {node_id}")
                                else:
                                    logger.warning(f"Failed to update node: {node_id}")
                        else:
                            # 节点不存在，根据nodeType调用对应的create_xxx_node
                            new_node_id = None
                            
                            if node_type == "Time":
                                time_str = node_info.get("time_str", node_info.get("time", ""))
                                if time_str:
                                    new_node_id = self.kg_manager.create_time_node(session, time_str)
                                    
                            elif node_type == "Character":
                                name = node_info.get("character_name", node_info.get("name", ""))
                                importance = node_info.get("importance", 0.5)
                                trust = node_info.get("trust", 0.5)
                                context = node_info.get("context", "reality")
                                if name:
                                    new_node_id = self.kg_manager.create_character_node(session, name, importance, trust, context)
                                    
                            elif node_type == "Location":
                                name = node_info.get("location_name", node_info.get("name", ""))
                                context = node_info.get("context", "reality")
                                if name:
                                    new_node_id = self.kg_manager.create_location_node(session, name, context)
                                    
                            elif node_type == "Entity":
                                name = node_info.get("entity_name", node_info.get("name", ""))
                                importance = node_info.get("importance", 0.5)
                                context = node_info.get("context", "reality")
                                note = node_info.get("note", "无")
                                if name:
                                    new_node_id = self.kg_manager.create_entity_node(session, name, importance, context, note)
                            
                            if new_node_id:
                                logger.info(f"Created new {node_type} node: {node_id} -> {new_node_id}")
                                
                                # 更新当前节点的ID为实际的Neo4j节点ID
                                old_node_id = node["nodeId"]
                                node["nodeId"] = new_node_id
                                
                                # 更新relations_list中所有引用这个节点的关系
                                for relation in relations_list:
                                    if relation.get("startNode") == old_node_id:
                                        relation["startNode"] = new_node_id
                                    
                                    if relation.get("endNode") == old_node_id:
                                        relation["endNode"] = new_node_id
                            else:
                                logger.warning(f"Failed to create {node_type} node: {node_id}")
                                
                    except Exception as e:
                        logger.error(f"Error processing node {node}: {e}")
                        continue

                # 遍历relationlist，处理关系
                for relation in relations_list:
                    try:
                        relation_id = relation.get("relationId")
                        relation_type = relation.get("relationType", "single")
                        start_node_id = relation.get("startNode")
                        end_node_id = relation.get("endNode")
                        relation_info = relation.get("relationInfo", {})
                        
                        if not all([relation_id, start_node_id, end_node_id]):
                            logger.warning(f"Relation missing required fields: {relation}")
                            continue
                        
                        # 解析关系信息
                        predicate = relation_info.get("predicate", "CONNECTED_TO")
                        source = relation_info.get("source", "memory_record")
                        confidence = float(relation_info.get("confidence", 0.5))
                        evidence = relation_info.get("evidence", "")
                        
                        if not start_node_id or not end_node_id:
                            logger.warning(f"Could not resolve node IDs for relation {relation_id}: {start_node_id} -> {end_node_id}")
                            continue
                        
                        # 检查关系是否已存在
                        check_relation_query = """
                        OPTIONAL MATCH (a)-[r]->(b) 
                        WHERE elementId(a) = $start_id AND elementId(b) = $end_id 
                        AND (elementId(r) = $relation_id OR (r.custom_id IS NOT NULL AND r.custom_id = $relation_id))
                        RETURN elementId(r) as existing_relation_id
                        """
                        
                        check_relation_result = session.run(check_relation_query, 
                                                           start_id=start_node_id,
                                                           end_id=end_node_id,
                                                           relation_id=relation_id).single()
                        
                        existing_relation_id = check_relation_result["existing_relation_id"] if check_relation_result else None
                        
                        if existing_relation_id:
                            # 关系存在，调用modify_relation修改关系属性
                            result = self.kg_manager.modify_relation(existing_relation_id, predicate, source, confidence, relation_type, evidence)
                            if result:
                                logger.info(f"Updated existing relation: {relation_id}")
                            else:
                                logger.warning(f"Failed to update relation: {relation_id}")
                        else:
                            # 关系不存在，调用create_relation创建关系
                            new_relation_id = self.kg_manager.create_relation(
                                startNode_id=start_node_id,
                                endNode_id=end_node_id,
                                predicate=predicate,
                                source=source,
                                confidence=confidence,
                                directivity=relation_type,
                                evidence=evidence
                            )
                                
                    except Exception as e:
                        logger.error(f"Error processing relation {relation}: {e}")
                        continue
                
                logger.info(f"Memory record processing completed: {len(nodes_list)} nodes, {len(relations_list)} relations")
                
                # 收集处理结果
                processed_nodes = []
                processed_relations = []
                
                for node in nodes_list:
                    if node.get("nodeId"):
                        processed_nodes.append({
                            "nodeId": node["nodeId"],
                            "nodeType": node.get("nodeType"),
                            "action": "processed"
                        })
                
                for relation in relations_list:
                    if relation.get("relationId"):
                        processed_relations.append({
                            "relationId": relation.get("relationId"),
                            "startNode": relation.get("startNode"),
                            "endNode": relation.get("endNode"),
                            "predicate": relation.get("relationInfo", {}).get("predicate", "CONNECTED_TO"),
                            "action": "processed"
                        })
                
                return {"nodes": processed_nodes, "relations": processed_relations}
                
        except Exception as e:
            logger.error(f"Error during memory record processing: {e}")
            return {"nodes": [], "relations": []}

    def passive_memory_record_test(self, work_history, memory_to_record: Dict[str, Any], related_memory: Dict[str, Any]) -> None:
        """
        记录记忆信息，调用LLM生成结构化的记忆记录（测试版本）
        
        Args:
            work_history: 历史记录，格式严格为：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
            memory_to_record: 需要记忆的信息，格式：{"event": "", "description": [...]}
            related_memory: 相关记忆节点，格式：{"nodes": [...], "relations": [...]}
            
        Returns:
            None
        """
        # 处理related_memory，将字典格式转换为文本格式
        if isinstance(related_memory, dict):
            # 转换为文本格式
            related_memory_text = f"【请参考以下相关记忆进行决策】\n{str(related_memory)}"
        else:
            related_memory_text = "【相关记忆】\n暂无相关记忆，可直接进行处理"
        
        # 准备输入数据
        input_messages = []
        
        # 如果有work_history，先添加到最前面
        if work_history and isinstance(work_history, list):
            input_messages.extend(work_history)
        
        # 添加系统prompt和当前的记忆信息
        # 添加系统prompt和当前的记忆信息
        input_messages.extend([
            {"role": "system", "content": MEMORY_RECORD_PROMPT},
            {"role": "user", "content": related_memory_text},
            {"role": "user", "content": str(memory_to_record)}
        ])

        # 调用模型
        response = client.chat.completions.create(
            model=MODEL,
            messages=input_messages,
            stream=False,
            temperature=0.1
        )

        full_response = response.choices[0].message.content

        if DEBUG_MODE:
            print(f"[DEBUG] 记忆存储模型回应: {full_response}")
        if not full_response:
            print(f"[DEBUG] 记忆存储模型未返回响应。")
            return None
        
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
            
            return None

        except Exception as e:
            logger.error(f"Error during memory record processing: {e}")
            return None


def test_passive_memory_record():
    """测试被动记忆记录功能"""
    from brain.memory.knowledge_graph_manager import KnowledgeGraphManager
    
    # 初始化知识图谱管理器和记忆写入器
    kg_manager = KnowledgeGraphManager()
    memory_writer = MemoryWriter(kg_manager)
    
    # 测试数据
    work_history = []
    
    memory_to_record = {}
    
    related_memory = {
        "nodes": [],
        "relations": []
    }
    
    try:
        print("开始测试记忆写入功能...")
        print(f"事件: {memory_to_record['event']}")
        print(f"描述详情: {len(memory_to_record['description'])} 个属性")
        
        # 调用被测试的函数
        memory_writer.passive_memory_record_test(
            work_history=work_history,
            memory_to_record=memory_to_record,
            related_memory=related_memory
        )
        
        print("记忆写入测试完成!")
        
    except Exception as e:
        print(f"测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
    
def test_passive_event_extraction():
    """测试被动事件提取功能"""
    from brain.memory.knowledge_graph_manager import KnowledgeGraphManager
    
    # 初始化知识图谱管理器、会话上下文和记忆写入器
    kg_manager = KnowledgeGraphManager()
    session_context = ConversationContext("test_session_001")
    event_extractor = MemoryWriter(kg_manager, session_context)
    
    # 从 memory_frag_for_test.json 文件读取测试数据
    memory_frag_path = os.path.join(os.path.dirname(__file__), "memory_graph", "memory_frag_for_test.json")
    try:
        with open(memory_frag_path, "r", encoding="utf-8") as f:
            related_memory = json.load(f)
    except FileNotFoundError:
        print(f"[警告] 未找到测试数据文件: {memory_frag_path}")
        related_memory = {"nodes": [], "relations": []}
    except json.JSONDecodeError as e:
        print(f"[错误] 测试数据文件JSON解析失败: {e}")
        related_memory = {"nodes": [], "relations": []}
    
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
        print("请输入对话记录，格式：[时间戳] <角色> 对话内容")
        print("输入示例：[2026/1/7T16:01] <角色> 对话内容")
        print("回车键结束当前轮次输入")
        
        event_text = []
        while True:
            user_input = input("输入对话记录: ").strip()
            if user_input.lower() in ['quit', 'exit']:
                print("退出测试...")
                return
            if user_input == '':
                break
            # 将用户输入转换为所需格式
            event_text.append({"role": "user", "content": user_input})
        
        # 将消息添加到上下文中
        for msg in event_text:
            session_context.add_message(msg["role"], msg["content"])
        
        try:
            print("开始测试事件提取功能...")
            
            # 调用被测试的函数
            results = event_extractor.passive_event_extraction_from_message(
                event_text=event_text,
                related_memory=related_memory
            )
            
            print("事件提取测试完成!")
            
            # 显示结果
            if results:
                print(f"成功处理 {len(results)} 个事件记录结果")
            
            # 显示上下文中记录的事件
            extracted_events = session_context.get_extracted_events()
            if extracted_events:
                print(f"上下文中记录了 {len(extracted_events)} 个提取事件")
                for i, event in enumerate(extracted_events):
                    print(f"  事件 {i+1}: {event['event_data'].get('event', '未知事件')}")
            
            print()
            print("=" * 50)
            print()
            
        except Exception as e:
            print(f"测试过程中出现错误: {e}")
            import traceback
            traceback.print_exc()
            print()
        
        round_count += 1


def main():
    """测试事件提取功能"""
    test_passive_event_extraction()

    """测试记忆写入功能"""
    # test_passive_memory_record()


if __name__ == "__main__":
    main()