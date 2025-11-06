# -*- coding: utf-8 -*-
"""
相关记忆搜索模块 - 基于关键词提取和知识图谱查询来检索相关记忆
"""
import os
import sys
import json
import re
from typing import List, Dict, Optional

# 添加项目根目录到路径
PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from system.config import config
from openai import OpenAI

# 初始化 OpenAI 客户端用于关键词提取
client = OpenAI(
    api_key=config.api.api_key,
    base_url=config.api.base_url
)


def extract_keywords_with_model(user_question: str, recent_context: List[str] = None) -> List[str]:
    """
    使用模型提取关键词并查询知识图谱
    
    Args:
        user_question: 用户问题
        recent_context: 最近的上下文消息列表
        
    Returns:
        提取的关键词列表
    """
    try:
        context_str = "\n".join(recent_context) if recent_context else "无上下文"
        prompt = (
            f"基于以下上下文和用户问题，提取与知识图谱相关的关键词（如人物、物体、关系、实体类型），"
            f"仅以列表的形式返回核心关键词，避免无关词。返回 JSON 格式的关键词列表：\n"
            f"上下文：\n{context_str}\n"
            f"问题：{user_question}\n"
            f"输出格式：```json\n[]\n```"
        )
        
        response = client.chat.completions.create(
            model=config.api.model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            temperature=0.3,  # 降低温度以获得更一致的结果
        )
        
        content = response.choices[0].message.content
        # 调试输出模型返回的原始内容，便于排查格式或解析问题
        print(f"[关键词模型输出] {content!r}")
        if not content:
            return []
        
        # 提取JSON部分
        json_match = re.search(r'```json\s*(\[.*?\])\s*```', content, re.DOTALL)
        if json_match:
            try:
                keywords = json.loads(json_match.group(1))
                # 确保返回的是字符串列表
                return [str(keyword).strip() for keyword in keywords if keyword and str(keyword).strip()]
            except json.JSONDecodeError as e:
                print(f"[关键词提取] JSON解析失败: {e}")
                return []
        else:
            # 如果没有找到JSON格式，尝试从文本中提取
            lines = content.split('\n')
            keywords = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith(('基于', '关键词', '提取', '输出')):
                    # 移除标点符号和数字前缀
                    clean_line = re.sub(r'^\d+[\.\)]\s*', '', line)
                    clean_line = re.sub(r'[^\w\s]', '', clean_line).strip()
                    if clean_line:
                        keywords.append(clean_line)
            return keywords[:10]  # 限制关键词数量
            
    except Exception as e:
        print(f"[关键词提取错误] {e}")
        # 回退到简单的关键词提取
        return extract_simple_keywords(user_question)


def extract_simple_keywords(text: str) -> List[str]:
    """
    简单的关键词提取（作为备用方案）
    
    Args:
        text: 输入文本
        
    Returns:
        关键词列表
    """
    # 移除标点符号并分词
    words = re.findall(r'\b\w+\b', text.lower())
    # 过滤掉常见的停用词和短词
    stop_words = {'的', '了', '在', '是', '我', '你', '他', '她', '它', '我们', '你们', '他们', 
                 '这', '那', '这个', '那个', '什么', '怎么', '为什么', '哪里', 'when', 'what', 
                 'how', 'why', 'where', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at',
                 '有', '会', '能', '可以', '应该', '需要', '想要', '希望', '觉得', '认为'}
    keywords = [word for word in words if len(word) > 1 and word not in stop_words]
    return keywords[:5]  # 限制关键词数量


def query_relevant_memories(message_content: str, recent_context: List[str] = None, max_results: int = 5) -> str:
    """
    根据消息内容查询相关的记忆信息
    
    Args:
        message_content: 任何角色的消息内容
        recent_context: 最近的上下文消息列表
        max_results: 最大返回结果数
        
    Returns:
        格式化的记忆信息字符串
    """
    try:
        # 首先检查全局Neo4j可用性
        from system.config import is_neo4j_available
        if not is_neo4j_available():
            return ""
            
        kg_manager = get_knowledge_graph_manager()
        if not kg_manager.connected:
            return ""
        
        # 使用模型提取关键词
        keywords = extract_keywords_with_model(message_content, recent_context)
        
        if not keywords:
            print("[记忆查询] 未提取到关键词")
            return ""
        
        print(f"[记忆查询] 提取的关键词: {keywords}")
        
        memories = []
        
        with kg_manager.driver.session() as session:
            # 查询实体节点
            for keyword in keywords[:3]:  # 限制关键词数量避免查询过多
                # 查询包含关键词的实体
                entity_query = """
                MATCH (e:Entity)
                WHERE toLower(e.name) CONTAINS $keyword
                RETURN e.name as entity, 
                       e.source as source,
                       e.importance as importance,
                       e.confidence as confidence
                ORDER BY e.importance DESC, e.confidence DESC
                LIMIT 3
                """
                
                result = session.run(entity_query, keyword=keyword.lower())
                for record in result:
                    entity_info = {
                        'type': 'entity',
                        'name': record['entity'],
                        'source': record['source'],
                        'importance': record.get('importance', 0.5),
                        'confidence': record.get('confidence', 0.5)
                    }
                    memories.append(entity_info)
            
            # 查询相关的五元组关系
            relation_query = """
            MATCH (s:Entity)-[r]->(o:Entity)
            WHERE toLower(s.name) CONTAINS $keyword OR toLower(o.name) CONTAINS $keyword
            RETURN s.name as subject,
                   type(r) as relation_type,
                   r.action as action,
                   o.name as object,
                   r.time as time,
                   r.location as location,
                   r.importance as importance,
                   r.source as source
            ORDER BY r.importance DESC, r.confidence DESC
            LIMIT 5
            """
            
            for keyword in keywords[:2]:
                result = session.run(relation_query, keyword=keyword.lower())
                for record in result:
                    relation_info = {
                        'type': 'relation',
                        'subject': record['subject'],
                        'action': record.get('action', record['relation_type']),
                        'object': record['object'],
                        'time': record.get('time'),
                        'location': record.get('location'),
                        'importance': record.get('importance', 0.5),
                        'source': record.get('source', 'unknown')
                    }
                    memories.append(relation_info)
        
        # 格式化记忆信息
        if not memories:
            print("[记忆查询] 未找到相关记忆")
            return ""
        
        memory_text = "相关记忆信息：\n"
        for i, memory in enumerate(memories[:max_results], 1):
            if memory['type'] == 'entity':
                memory_text += f"{i}. 实体：{memory['name']} (来源：{memory['source']})\n"
            elif memory['type'] == 'relation':
                time_str = f" 时间：{memory['time']}" if memory['time'] else ""
                location_str = f" 地点：{memory['location']}" if memory['location'] else ""
                memory_text += f"{i}. 关系：{memory['subject']} {memory['action']} {memory['object']}{time_str}{location_str} (来源：{memory['source']})\n"
        
        print(f"[记忆查询] 找到 {len(memories)} 条相关记忆")
        return memory_text
        
    except Exception as e:
        print(f"[记忆查询错误] {e}")
        return ""


def test_memory_search():
    """测试记忆搜索功能"""
    print("测试记忆搜索功能...")
    
    # 测试关键词提取
    test_question = "你还记得昨天我们聊过的关于Python编程的内容吗？"
    test_context = ["我想学习Python", "Python是一种编程语言", "可以用来做数据分析"]
    
    print(f"测试问题: {test_question}")
    print(f"测试上下文: {test_context}")
    
    keywords = extract_keywords_with_model(test_question, test_context)
    print(f"提取的关键词: {keywords}")
    
    # 测试记忆查询
    memories = query_relevant_memories(test_question, test_context)
    print(f"查询结果:\n{memories}")


if __name__ == "__main__":
    test_memory_search()
