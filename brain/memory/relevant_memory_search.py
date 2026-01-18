# -*- coding: utf-8 -*-
"""
相关记忆搜索模块 - 基于关键词提取和知识图谱查询来检索相关记忆
调用方法：
    recent_message = [{"role": "user", "content": f"[YYYY_MM_DDTHH:MM:SS:XX] <发言人> 发言内容"}]
        prompt默认为此格式但格式不严格要求。
    extract_result = extract_keyword_from_text(recent_message)
提取结果：
    提取出和主体相关的记忆节点，格式为：
    {noeds: [...], relationships: [...]}
"""
import os
import sys
import json
import re
import logging
import logging

# 添加项目根目录到Python路径（必须在导入项目模块之前）
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from openai import OpenAI
from system.config import config
from typing import Any, List, Dict, Optional

# API 配置
API_KEY = config.api.memory_record_api_key
API_URL = config.api.memory_record_base_url
MODEL = config.api.memory_record_model
EMB_API_KEY = config.api.embedding_api_key
EMB_API_URL = config.api.embedding_base_url
EMB_MODEL = config.api.embedding_model
DEBUG_MODE = config.system.debug

# 初始化 OpenAI 客户端
client = OpenAI(
    api_key=API_KEY,
    base_url=API_URL
)

logger = logging.getLogger(__name__)


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

KEYWORD_EXTRACT_PROMPT = load_prompt_file("keyword_extract.txt", "关键词提取")
MEMORY_FILTER_PROMPT = load_prompt_file("memory_filter.txt", "事件提取")

def generate_embedding(text: str) -> Optional[List[float]]:
    """
    使用embedding模型生成文本向量
    
    Args:
        text: 要计算向量的文本内容
        
    Returns:
        Optional[List[float]]: 计算得到的向量，失败返回None
    """
    if not text or not text.strip():
        logger.warning("Cannot generate embedding for empty text")
        return None
        
    try:
        logger.debug(f"Generating embedding for text: {text[:100]}...")
        response = client.embeddings.create(
            input=text,
            model=EMB_MODEL,
            dimensions=384,
        )
        
        if response and response.data and len(response.data) > 0:
            embedding = response.data[0].embedding
            logger.debug(f"Successfully generated embedding with dimension: {len(embedding)}")
            return embedding
        else:
            logger.error("Embedding API returned empty response")
            return None
            
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}")
        return None

def search_nodes_by_embedding(text: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    通过embedding向量相似度搜索节点
    
    Args:
        text: 要搜索的文本
        top_k: 返回最相似的前k个节点
        
    Returns:
        List[Dict[str, Any]]: 匹配的节点列表，包含id, name和相似度分数
    """
    try:
        from system.config import is_neo4j_available
        if not is_neo4j_available():
            logger.warning("Neo4j不可用")
            return []
            
        kg_manager = get_knowledge_graph_manager()
        if not kg_manager.connected:
            logger.warning("知识图谱未连接")
            return []
        
        # 生成查询文本的embedding
        query_embedding = generate_embedding(text)
        if not query_embedding:
            logger.error("无法生成embedding向量")
            return []
        
        # 使用Neo4j的向量相似度搜索
        with kg_manager.driver.session() as session:
            # 查询所有有embedding的节点，计算余弦相似度
            query = """
            MATCH (n)
            WHERE n.embedding IS NOT NULL
            WITH n, 
                 reduce(dot = 0.0, i IN range(0, size(n.embedding)-1) | 
                     dot + n.embedding[i] * $query_embedding[i]) as dot_product,
                 sqrt(reduce(sum = 0.0, i IN range(0, size(n.embedding)-1) | 
                     sum + n.embedding[i] * n.embedding[i])) as norm1,
                 sqrt(reduce(sum = 0.0, i IN range(0, size($query_embedding)-1) | 
                     sum + $query_embedding[i] * $query_embedding[i])) as norm2
            WITH n, dot_product / (norm1 * norm2) as similarity
            WHERE similarity > 0.5
            RETURN elementId(n) as id, n.name as name, similarity
            ORDER BY similarity DESC
            LIMIT $top_k
            """
            
            results = session.run(query, query_embedding=query_embedding, top_k=top_k)
            
            matches = []
            for record in results:
                matches.append({
                    "id": record["id"],
                    "name": record["name"],
                    "similarity": record["similarity"]
                })
            
            return matches
            
    except Exception as e:
        logger.error(f"Embedding搜索失败: {e}")
        return []


def extract_keyword_from_text(recent_message: Dict[str, Any]) -> Dict[str, Any]:
    """
    提取关键词供后续使用。

    输入：
        recent_message：最近消息，格式严格为：[{"role": "user", "content": f"[{timestamp}] <{name}> {content}"}, ...]
    输出：
        ["关键词1", "关键词2", ...]
    """
    # 确保event_text中所有的role都是"user"
    for message in recent_message:
        message["role"] = "user"
    
    # 准备输入数据
    input_messages = [{"role": "system", "content": KEYWORD_EXTRACT_PROMPT}]
    input_messages.extend(recent_message)

    if DEBUG_MODE:
        print(f"[DEBUG] 模型思考中……")
    
    # 调用模型
    response = client.responses.create(
        model=MODEL,
        input=input_messages,
        reasoning={"effort": "low"},
        text={"verbosity": "low"}
    )

    full_response = response.output_text

    if not full_response:
        print(f"[错误] 关键词提取模型未返回响应。")
        return {"summary": [], "keywords": []}
    
    # 提取输出为 json 格式
    try:
        # 尝试解析JSON响应
        json_content = full_response.strip()
        
        result = json.loads(json_content)
        
        if DEBUG_MODE:
            print(f"[DEBUG] 提取的关键词: {json.dumps(result, ensure_ascii=False, indent=2)}")
    
    except json.JSONDecodeError as e:
        print(f"[错误] 无法解析模型返回的JSON格式: {e}")
        print(f"[原始响应] {full_response}")
        return {"summary": [], "keywords": []}
    except Exception as e:
        return {"summary": [], "keywords": []}
    
    return result


def _filter_related_nodes(nodes: Dict[str, Any], summary: str) -> Dict[str, Any]:
    """
    让AI对每个节点进行判断，是否为当前话题需要的记忆信息
    
    输入：
    [
        "node_id":{
            "ids": {
                "node_id": reached_node_id,
                "relation_id": reached_node["rel_id"]
            },
            "relation": {
                "type": reached_node["rel_type"],
                "from": reached_node["start_node_name"],
                "to": reached_node["end_node_name"]
            },
            "node_to_evaluate": {node properties}
        }
    ]
    """
    if not nodes:
        return {"nodes": [], "relationships": []}
    
    # 准备输入数据
    input_messages = [{"role": "system", "content": MEMORY_FILTER_PROMPT}]

    # 将nodes中的ids，和relation+node_to_evaluate分作两个list储存，确保顺序对应。
    ids_list = []
    evaluation_data = []
    
    for node_id, node_data in nodes.items():
        ids_list.append(node_data["ids"])
        evaluation_data.append({
            "relation": node_data["relation"],
            "node_to_evaluate": node_data["node_to_evaluate"]
        })
    
    # 将relation+node_to_evaluate的部分以[{"relation": {}, "node_to_evaluate": {}}, ...]的格式转为str
    evaluation_content = json.dumps(evaluation_data, ensure_ascii=False, indent=2)
    
    # 将该内容加入input_message
    input_messages.append({
        "role": "user", 
        "content": f"话题: {summary}\n\n需要筛选的记忆数据:\n{evaluation_content}"
    })

    if DEBUG_MODE:
        print(f"[DEBUG] 模型收到 {len(evaluation_data)} 条待筛选的记忆数据。")
    
    # 调用模型
    response = client.responses.create(
        model=MODEL,
        input=input_messages,
        reasoning={"effort": "low"},
        text={"verbosity": "low"},
    )

    full_response = response.output_text

    if not full_response:
        print(f"[错误] 记忆筛选模型未返回响应。")
        return {"nodes": [], "relationships": []}
    
    # 提取输出为 json 格式
    try:
        # 尝试解析JSON响应
        json_content = full_response.strip()
        
        memory_filter = json.loads(json_content)
    
    except json.JSONDecodeError as e:
        print(f"[错误] 无法解析模型返回的JSON格式: {e}")
        print(f"[原始响应] {full_response}")
        return {"nodes": [], "relationships": []}
    except Exception as e:
        return {"nodes": [], "relationships": []}

    # 根据memory_filter结果筛选，将nodes中对应的内容保留/抹去
    filtered_nodes = []
    filtered_relationships = []
    filtered_evaluation_data = []
    
    # memory_filter应该是一个布尔值列表，对应每个节点是否保留
    if isinstance(memory_filter, list) and len(memory_filter) == len(ids_list):
        for i, keep_node in enumerate(memory_filter):
            if keep_node:  # 如果AI判断该节点相关，则保留
                node_ids = ids_list[i]
                # 根据node_id和relation_id从原始数据中获取完整信息
                # 这里需要从调用方传入的nodes_to_add和relations_to_add中获取
                filtered_nodes.append(node_ids["node_id"])
                filtered_relationships.append(node_ids["relation_id"])
                filtered_evaluation_data.append(evaluation_data[i]["node_to_evaluate"].get("name"))
    
    if DEBUG_MODE:
        print(f"[DEBUG] 增加关联记忆: {filtered_evaluation_data}")
    
    return {
        "filtered_node_ids": filtered_nodes,
        "filtered_relation_ids": filtered_relationships
    }



def _filter_node_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    """过滤节点属性，隐去指定的系统属性"""

    if not properties:
        return {}
    
    # 需要隐去的属性列表
    hidden_properties = {
        "significance", "importance", "created_at", "last_updated", "trust", "embedding",
    }
    
    # 返回过滤后的属性
    filtered_props = {}
    for key, value in properties.items():
        if key not in hidden_properties:
            filtered_props[key] = value
    
    return filtered_props


def _remove_embedding(properties: Dict[str, Any]) -> Dict[str, Any]:
    """只移除节点的embedding属性，保留其他所有属性"""
    
    if not properties:
        return {}
    
    # 创建属性副本并移除embedding
    filtered_props = dict(properties)
    filtered_props.pop("embedding", None)
    
    return filtered_props


def _expand_memory_grabbed(session, nodes_data: Dict[str, Any], summary: str = "") -> Dict[str, Any]:
    """
    根据根据节点id提取节点内容。
    输入：
    nodes_data->来自上一次该函数的产出。
    输出：
    Dict[str, Any]:
        {"nodes": [所有的节点字典],
        "relationships": [节点字典对应的联系],
        "outer_node_ids": [新增的节点字典]}
    """
    if not nodes_data:
        return {"nodes": [], "relationships": [], "outer_node_ids": []}
    
    try:
        # 存储所有节点和关系
        all_nodes_list = nodes_data.get("nodes", [])
        all_relationships_list = nodes_data.get("relationships", [])
        nodes_to_read = nodes_data.get("outer_node_ids", [])
        
        # 转换为字典格式便于去重和查找
        all_nodes = {node["id"]: node for node in all_nodes_list}
        all_relationships = {rel["id"]: rel for rel in all_relationships_list}
        
        # 如果没有新节点需要读取，直接返回原数据
        if not nodes_to_read:
            return {
                "nodes": all_nodes_list,
                "relationships": all_relationships_list,
                "outer_node_ids": []
            }
        
        # 查询nodes_to_read连接的所有节点和关系
        nodes_to_add = {}
        relations_to_add = {}
        memories_to_be_viewed = {}
        for node_id in nodes_to_read:
            # 查询该节点及其所有连接的节点和关系
            query = """
            MATCH (root) WHERE elementId(root) = $node_id
            OPTIONAL MATCH (root)-[r]-(connected)
            RETURN 
                elementId(root) as root_id, labels(root) as root_labels, properties(root) as root_properties,
                elementId(connected) as connected_id, labels(connected) as connected_labels, properties(connected) as connected_properties,
                elementId(r) as rel_id, type(r) as rel_type, 
                elementId(startNode(r)) as rel_start, elementId(endNode(r)) as rel_end,
                properties(r) as rel_properties,
                startNode(r).name as start_node_name, endNode(r).name as end_node_name
            """
            
            connected_nodes = session.run(query, node_id=node_id)
            
            for reached_node in connected_nodes:
                # 将查询结果存储到nodes_to_add和relations_to_add中以便后续处理
                reached_node_id = reached_node["connected_id"]
                if reached_node_id and reached_node_id not in all_nodes:
                    nodes_to_add[reached_node_id] = {
                        "id": reached_node_id,
                        "labels": reached_node["connected_labels"] or [],
                        "properties": _remove_embedding(dict(reached_node["connected_properties"]) if reached_node["connected_properties"] else {})
                    }
                    rel_id = reached_node["rel_id"]
                    relations_to_add[rel_id] = {
                        "id": rel_id,
                        "type": reached_node["rel_type"],
                        "start_node": reached_node["rel_start"],
                        "end_node": reached_node["rel_end"],
                        "properties": dict(reached_node["rel_properties"]) if reached_node["rel_properties"] else {}
                    }
                    # 将节点关系转换为文字版交由AI审阅，省略不需要的属性
                    memories_to_be_viewed[reached_node_id] = {
                        "ids": {
                            "node_id": reached_node_id,
                            "relation_id": reached_node["rel_id"]
                        },
                        "relation": {
                            "type": reached_node["rel_type"],
                            "from": reached_node["start_node_name"],
                            "to": reached_node["end_node_name"]
                        },
                        "node_to_evaluate": _filter_node_properties(reached_node["connected_properties"])
                    }
        
        print(f"新发现节点: {len(nodes_to_add)}个")
        
        # 根据summary筛选相关节点
        if summary and memories_to_be_viewed:
            filtered_nodes_relation = _filter_related_nodes(memories_to_be_viewed, summary)
            filtered_node_ids = filtered_nodes_relation.get("filtered_node_ids", [])
            filtered_relation_ids = filtered_nodes_relation.get("filtered_relation_ids", [])
        else:
            # 如果没有summary，保留所有节点
            filtered_node_ids = list(nodes_to_add.keys())
            filtered_relation_ids = list(relations_to_add.keys())
        
        # 根据筛选出的node_id和relation_id从nodes_to_add和relations_to_add中取出对应的内容加入all_nodes和all_relationships
        outer_node_ids = []
        for node_id in filtered_node_ids:
            if node_id in nodes_to_add:
                all_nodes[node_id] = nodes_to_add[node_id]
                outer_node_ids.append(node_id)
        
        for relation_id in filtered_relation_ids:
            if relation_id in relations_to_add:
                all_relationships[relation_id] = relations_to_add[relation_id]
        
        if DEBUG_MODE:
            print(f"[DEBUG] 筛选后保留 {len(outer_node_ids)} 个节点，{len(filtered_relation_ids)} 个关系")
        
        # 转换为列表格式
        nodes_list = list(all_nodes.values())
        relationships_list = list(all_relationships.values())

        return {
            "nodes": nodes_list,
            "relationships": relationships_list,
            "outer_node_ids": outer_node_ids
        }
        
    except Exception as e:
        logger.error(f"Failed to extract connected nodes: {e}")
        return {"nodes": [], "relationships": [], "outer_node_ids": []}


def _extract_nodes_by_keyword(kg_manager, keywords: List[str]) -> Dict[str, Any]:
    """
    根据输入的关键词列表提取相应的节点及信息。
    
    Args:
        kg_manager: KnowledgeGraphManager实例
        text: 输入文本
        keywords: 预定义的关键词列表
        
    Returns Dict[str, Any]:
        {"nodes": [所有的节点字典],
        "relationships": [节点字典对应的联系],
        "outer_node_ids": [新增的节点字典]}
    """
    if not kg_manager._ensure_connection():
        logger.error("Cannot extract keywords: No Neo4j connection")
        return {"nodes": [], "relationships": [], "outer_node_ids": []}
    
    if not keywords:
        logger.error("No keywords provided for extraction")
        return {"nodes": [], "relationships": [], "outer_node_ids": []}
    
    try:
        nodes_dict = {}  # 使用字典避免重复节点
        
        with kg_manager.driver.session() as session:
            for keyword in keywords:
                if not keyword or not keyword.strip():
                    continue
                    
                keyword = keyword.strip()
                logger.debug(f"Searching for keyword: {keyword}")
                
                # 1. 对于每个关键词首先尝试精确匹配 - 查找名称完全匹配的节点
                exact_match_query = """
                MATCH (n)
                WHERE n.name = $keyword
                RETURN elementId(n) as id, labels(n) as labels, properties(n) as properties
                """
                
                exact_results = session.run(exact_match_query, keyword=keyword)
                exact_matches = list(exact_results)
                
                if exact_matches:
                    logger.debug(f"Found {len(exact_matches)} exact matches for '{keyword}'")
                    for record in exact_matches:
                        node_id = record["id"]
                        if node_id not in nodes_dict:
                            nodes_dict[node_id] = {
                                "id": node_id,
                                "labels": record["labels"] or [],
                                "properties": _remove_embedding(dict(record["properties"]) if record["properties"] else {})
                            }
                else:
                    # 2. 如果精确匹配没有结果，使用embedding进行语义匹配
                    if DEBUG_MODE:
                        print(f"无法精准匹配 '{keyword}', 进行embedding模糊匹配")
                    
                    # 生成关键词的embedding向量
                    keyword_embedding = generate_embedding(keyword)
                    
                    if keyword_embedding:
                        # 使用embedding向量相似度进行语义匹配
                        semantic_match_query = """
                        MATCH (n)
                        WHERE n.embedding IS NOT NULL
                        WITH n, 
                             reduce(dot = 0.0, i IN range(0, size(n.embedding)-1) | 
                                 dot + n.embedding[i] * $keyword_embedding[i]) as dot_product,
                             sqrt(reduce(sum = 0.0, i IN range(0, size(n.embedding)-1) | 
                                 sum + n.embedding[i] * n.embedding[i])) as norm1,
                             sqrt(reduce(sum = 0.0, i IN range(0, size($keyword_embedding)-1) | 
                                 sum + $keyword_embedding[i] * $keyword_embedding[i])) as norm2
                        WITH n, dot_product / (norm1 * norm2) as similarity
                        WHERE similarity > 0.6
                        RETURN elementId(n) as id, labels(n) as labels, properties(n) as properties, similarity
                        ORDER BY similarity DESC
                        LIMIT 5
                        """
                        
                        semantic_results = session.run(semantic_match_query, keyword_embedding=keyword_embedding)
                        semantic_matches = list(semantic_results)
                        
                        if semantic_matches:
                            for record in semantic_matches:
                                node_id = record["id"]
                                if node_id not in nodes_dict:
                                    node_name = record["properties"].get("name", "Unknown") if record["properties"] else "Unknown"
                                    similarity = record["similarity"]
                                    if DEBUG_MODE:
                                        print(f"  - Matched '{node_name}' with similarity {similarity:.3f}")
                                    nodes_dict[node_id] = {
                                        "id": node_id,
                                        "labels": record["labels"] or [],
                                        "properties": _remove_embedding(dict(record["properties"]) if record["properties"] else {})
                                    }
                        else:
                            print(f"No semantic matches found for keyword: '{keyword}'")
                    else:
                        logger.warning(f"Failed to generate embedding for keyword: '{keyword}'")
            
            # 转换为列表格式
            nodes_list = list(nodes_dict.values())
            outer_node_ids = [node["id"] for node in nodes_list]
            node_names = [node["properties"].get("name", "") for node in nodes_list]
            
            if DEBUG_MODE: 
                print(f"找到起点记忆：{node_names}")
            
            return {
                "nodes": nodes_list,
                "relationships": [],
                "outer_node_ids": outer_node_ids
            }
            
    except Exception as e:
        logger.error(f"Failed to extract keywords '{keywords}': {e}")
        return {"nodes": [], "relationships": [], "outer_node_ids": []}


def get_relevant_memories(keywords: List[str], summary: str = "", max_results: int = 50) -> Dict[str, Any]:
    """
    通过_extract_keywords获得基础节点数据。
    而后通过反复调用_expand_memory_grabbed获取相关联的节点和关系。
    当总节点数超过max_results时停止，输出最多max_results个节点的记忆信息字符串。

    Args:
        keywords: 已提取的关键词列表
        max_results: 最大返回结果数
        
    Returns Dict[str, Any]:
        {"nodes": [
            {"id": "节点ID",
            "labels": ["节点标签1", "节点标签2"],
            "properties": {property1: value1, property2: value2}}
        ],
        "relationships": [
            {"id": "关系ID",
            "type": "关系类型",
            "start_node": "起始节点ID",
            "end_node": "结束节点ID",
            "properties": {property1: value1, property2: value2}}
        ]}
    """
    try:
        # 首先检查全局Neo4j可用性
        from system.config import is_neo4j_available
        if not is_neo4j_available():
            return {"nodes": [], "relationships": []}
            
        kg_manager = get_knowledge_graph_manager()
        if not kg_manager.connected:
            return {"nodes": [], "relationships": []}
        
        if not keywords:
            logger.error("[记忆查询] 关键词列表为空")
            return {"nodes": [], "relationships": []}
        
        if DEBUG_MODE:
            print(f"[记忆查询] 使用{len(keywords)}个关键词。")

        keywords.append("自我")

        # 第一步：提取基础节点
        with kg_manager.driver.session() as session:
            nodes_data = _extract_nodes_by_keyword(kg_manager, keywords)
            
            if not nodes_data.get("nodes"):
                logger.error("[记忆查询] 未找到匹配的节点")
                return {"nodes": [], "relationships": []}
            
            if DEBUG_MODE:
                print(f"[记忆查询] 初始找到 {len(nodes_data['nodes'])} 个节点")
            
            # 第二步：反复扩展连接的节点，直到达到最大数量限制
            expansion_rounds = 0
            max_expansion_rounds = 3  # 限制扩展轮数避免过度查询
            
            while (len(nodes_data.get("nodes", [])) < max_results and 
                   nodes_data.get("outer_node_ids") and 
                   expansion_rounds < max_expansion_rounds):
                
                expansion_rounds += 1
                if DEBUG_MODE:
                    print(f"[记忆查询] 第 {expansion_rounds} 轮扩展，当前节点数: {len(nodes_data['nodes'])}")
                
                # 扩展连接的节点
                nodes_data = _expand_memory_grabbed(session, nodes_data, summary)
                
                # 如果节点数量超过限制，截断到最大数量
                if len(nodes_data.get("nodes", [])) > max_results:
                    nodes_data["nodes"] = nodes_data["nodes"][:max_results]
                    nodes_data["outer_node_ids"] = []  # 停止进一步扩展
                    if DEBUG_MODE:
                        print(f"[记忆查询] 节点数量超过限制，截断到 {max_results} 个")
                    break
            
            # 直接返回节点和关系数据
            result = {
                "nodes": nodes_data.get("nodes", []),
                "relationships": nodes_data.get("relationships", [])
            }
            
            if DEBUG_MODE:
                print(f"[记忆查询] 成功返回 {len(result['nodes'])} 个节点，{len(result['relationships'])} 个关系")
            return result
        
    except Exception as e:
        logger.error(f"[错误] 基于关键词的记忆查询失败: {e}")
        return {"nodes": [], "relationships": []}




if __name__ == "__main__":
    test_message = [{"role": "user", "content": f"[2026_1_12T20:30] <星空> 玲依你知道我的出生日期吗？"}]

    extract_result = extract_keyword_from_text(test_message)
    test_summary = extract_result.get("summary", [])
    test_keywords = extract_result.get("keywords", [])

    result = get_relevant_memories(test_keywords, test_summary)

    # 保存result到json文件供调试查看
    try:
        memory_frag_path = os.path.join(os.path.dirname(__file__), "memory_graph", "memory_frag_for_test.json")
        os.makedirs(os.path.dirname(memory_frag_path), exist_ok=True)
        with open(memory_frag_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.debug(f"[记忆查询] 结果已保存到: {memory_frag_path}")
    except Exception as save_e:
        logger.warning(f"[警告] 保存结果到文件失败: {save_e}")
    