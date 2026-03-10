# -*- coding: utf-8 -*-
"""
相关记忆搜索模块 - 基于关键词提取和知识图谱查询来检索相关记忆
调用方法：
    recent_message = "[YYYY_MM_DDTHH:MM:SS:XX] <发言人> 发言内容"
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

from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager, KnowledgeGraphManager
from system.system_checker import is_neo4j_available
from openai import OpenAI
from system.config import config
from typing import Any, List, Dict, Optional

# API 配置
API_KEY = config.memory_api.memory_record_api_key
API_URL = config.memory_api.memory_record_base_url
MODEL = config.memory_api.memory_record_model
EMB_API_KEY = config.memory_api.embedding_api_key
EMB_API_URL = config.memory_api.embedding_base_url
EMB_MODEL = config.memory_api.embedding_model
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
        error_msg = f"{description}提示词文件不存在: {prompt_path}"
        logger.error(error_msg)
        return ""
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            logger.error(f"{description}提示词文件为空: {prompt_path}")
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
        
        # 使用Neo4j原生向量索引搜索（需要预先创建向量索引）
        with kg_manager.driver.session() as session:
            matches = []
            seen_ids = set()
            
            # 遍历所有向量索引查询，合并结果
            for index_name, _label in KnowledgeGraphManager.VECTOR_INDEX_DEFINITIONS:
                try:
                    query = """
                    CALL db.index.vector.queryNodes($index_name, $top_k, $query_embedding)
                    YIELD node, score
                    WHERE score > 0.5
                    RETURN elementId(node) as id, node.name as name, score as similarity
                    """
                    results = session.run(query, index_name=index_name, top_k=top_k, query_embedding=query_embedding)
                    
                    for record in results:
                        node_id = record["id"]
                        if node_id not in seen_ids:
                            seen_ids.add(node_id)
                            matches.append({
                                "id": node_id,
                                "name": record["name"],
                                "similarity": record["similarity"]
                            })
                except Exception as idx_e:
                    logger.warning(f"向量索引 {index_name} 查询失败: {idx_e}")
                    continue
            
            # 按相似度降序排列，取 top_k
            matches.sort(key=lambda x: x["similarity"], reverse=True)
            return matches[:top_k]
            
    except Exception as e:
        logger.error(f"Embedding搜索失败: {e}")
        return []


def extract_keyword_from_text(recent_message: str) -> Dict[str, Any]:
    """
    提取关键词供后续使用。

    输入：
        recent_message：最近消息文本
    输出：
        {"summary": "...", "keywords": ["关键词1", "关键词2", ...]}
    """
    # 准备输入数据
    input_messages = [{"role": "system", "content": KEYWORD_EXTRACT_PROMPT}]
    input_messages.append({"role": "user", "content": recent_message})

    logger.debug("模型思考中……")
    
    # 调用模型
    response = client.responses.create(
        model=MODEL,
        input=input_messages,
        reasoning={"effort": "low"},
        text={"verbosity": "low"}
    )

    full_response = response.output_text

    if not full_response:
        logger.error("关键词提取模型未返回响应。")
        return {"summary": [], "keywords": []}
    
    # 提取输出为 json 格式
    try:
        # 尝试解析JSON响应
        json_content = full_response.strip()
        
        result = json.loads(json_content)
        
        logger.debug(f"提取的关键词: {json.dumps(result, ensure_ascii=False, indent=2)}")
    
    except json.JSONDecodeError as e:
        logger.error(f"无法解析模型返回的JSON格式: {e}")
        logger.error(f"原始响应: {full_response}")
        return {"summary": [], "keywords": []}
    except Exception as e:
        return {"summary": [], "keywords": []}
    
    return result


def _filter_related_nodes(nodes: Dict[str, Any], summary: str) -> Dict[str, Any]:
    """
    让AI对每个节点进行判断，是否为当前话题需要的记忆信息
    
    输入：
    {
        "node_id": {
            "ids": {
                "node_id": reached_node_id,
                "relation_id": rel_id
            },
            "display": "(当前节点名) -[关系名]-> (连接节点名)"  # 方向根据实际关系确定
        }
    }
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
        evaluation_data.append(node_data["display"])
    
    # 将简洁的关系描述列表转为str
    evaluation_content = json.dumps(evaluation_data, ensure_ascii=False, indent=2)
    
    # 将该内容加入input_message
    input_messages.append({
        "role": "user", 
        "content": f"话题: {summary}\n\n需要筛选的记忆数据:\n{evaluation_content}"
    })

    logger.debug(f"模型收到话题: {summary}，待筛选的记忆数据： {evaluation_content}")
    
    # 调用模型，最多重试3次以确保输出长度匹配
    memory_filter = None
    max_retries = 3
    
    for attempt in range(1, max_retries + 1):
        try:
            response = client.responses.create(
                model=MODEL,
                input=input_messages,
                reasoning={"effort": "low"},
                text={"verbosity": "low"},
            )

            full_response = response.output_text

            if not full_response:
                logger.warning(f"记忆筛选模型未返回响应 (第{attempt}次尝试)")
                continue
            
            # 提取输出为 json 格式
            json_content = full_response.strip()
            parsed_filter = json.loads(json_content)
            
            # 检查长度是否匹配
            if isinstance(parsed_filter, list) and len(parsed_filter) == len(ids_list):
                memory_filter = parsed_filter
                logger.debug(f"记忆筛选结果长度匹配 (第{attempt}次尝试)")
                break
            else:
                expected = len(ids_list)
                actual = len(parsed_filter) if isinstance(parsed_filter, list) else "非列表"
                logger.warning(f"记忆筛选结果长度不匹配: 期望{expected}, 实际{actual} (第{attempt}次尝试)")
                
        except json.JSONDecodeError as e:
            logger.warning(f"无法解析模型返回的JSON格式 (第{attempt}次尝试): {e}")
        except Exception as e:
            logger.warning(f"记忆筛选调用异常 (第{attempt}次尝试): {e}")
    
    # 如果重试全部失败，使用全部为True的列表作为兜底
    if memory_filter is None:
        logger.warning(f"记忆筛选{max_retries}次重试均失败，使用全部保留策略")
        memory_filter = [True] * len(ids_list)

    # 根据memory_filter结果筛选，将nodes中对应的内容保留/抹去
    filtered_nodes = []
    filtered_relationships = []
    filtered_evaluation_data = []
    
    # memory_filter已保证为长度匹配的布尔值列表
    for i, keep_node in enumerate(memory_filter):
        if keep_node:  # 如果AI判断该节点相关，则保留
            node_ids = ids_list[i]
            filtered_nodes.append(node_ids["node_id"])
            filtered_relationships.append(node_ids["relation_id"])
            filtered_evaluation_data.append(evaluation_data[i])
    
    logger.debug(f"增加关联记忆: {filtered_evaluation_data}")
    
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
        "importance", "created_at", "last_updated", "trust", "embedding",
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
        
        # 批量查询nodes_to_read连接的所有节点和关系
        nodes_to_add = {}
        relations_to_add = {}
        memories_to_be_viewed = {}
        
        query = """
        UNWIND $node_ids AS nid
        MATCH (root) WHERE elementId(root) = nid
        OPTIONAL MATCH (root)-[r]-(connected)
        RETURN 
            elementId(root) as root_id, labels(root) as root_labels, properties(root) as root_properties,
            elementId(connected) as connected_id, labels(connected) as connected_labels, properties(connected) as connected_properties,
            elementId(r) as rel_id, type(r) as rel_type, 
            elementId(startNode(r)) as rel_start, elementId(endNode(r)) as rel_end,
            properties(r) as rel_properties,
            startNode(r).name as start_node_name, endNode(r).name as end_node_name
        """
        
        connected_nodes = session.run(query, node_ids=nodes_to_read)
        
        # 收集遇到的Time节点，后续沿下游展开
        time_node_ids_to_expand = []
        
        for reached_node in connected_nodes:
            # 将查询结果存储到nodes_to_add和relations_to_add中以便后续处理
            reached_node_id = reached_node["connected_id"]
            if reached_node_id and reached_node_id not in all_nodes:
                connected_labels = reached_node["connected_labels"] or []
                rel_id = reached_node["rel_id"]
                rel_data = {
                    "id": rel_id,
                    "type": reached_node["rel_type"],
                    "start_node": reached_node["rel_start"],
                    "end_node": reached_node["rel_end"],
                    "properties": dict(reached_node["rel_properties"]) if reached_node["rel_properties"] else {}
                }
                node_data = {
                    "id": reached_node_id,
                    "labels": connected_labels,
                    "properties": _remove_embedding(dict(reached_node["connected_properties"]) if reached_node["connected_properties"] else {})
                }
                
                if "Time" in connected_labels:
                    # 时间节点直接并入all_nodes，不参与AI筛选
                    all_nodes[reached_node_id] = node_data
                    all_relationships[rel_id] = rel_data
                    time_node_ids_to_expand.append(reached_node_id)
                else:
                    # 非时间节点走正常筛选流程
                    nodes_to_add[reached_node_id] = node_data
                    relations_to_add[rel_id] = rel_data
                    # 将节点关系转换为简洁的方向性文字版交由AI审阅
                    root_name = reached_node["root_properties"].get("name", "Unknown") if reached_node["root_properties"] else "Unknown"
                    connected_name = reached_node["connected_properties"].get("name", "Unknown") if reached_node["connected_properties"] else "Unknown"
                    rel_type = reached_node["rel_type"]
                    
                    if reached_node["rel_start"] == reached_node["root_id"]:
                        display = f"({root_name}) -[{rel_type}]-> ({connected_name})"
                    else:
                        display = f"({root_name}) <-[{rel_type}]- ({connected_name})"
                    
                    memories_to_be_viewed[reached_node_id] = {
                        "ids": {
                            "node_id": reached_node_id,
                            "relation_id": reached_node["rel_id"]
                        },
                        "display": display
                    }
        
        # 沿Time节点向下游展开：查找指向Time节点的节点(child Time或关联的非Time节点)
        expanded_time_ids = set()
        while time_node_ids_to_expand:
            current_time_ids = [tid for tid in time_node_ids_to_expand if tid not in expanded_time_ids]
            if not current_time_ids:
                break
            for tid in current_time_ids:
                expanded_time_ids.add(tid)
            
            # 查询指向这些Time节点的所有节点 (downstream)-[r]->(time)
            time_downstream_query = """
            UNWIND $time_ids AS tid
            MATCH (downstream)-[r]->(t) WHERE elementId(t) = tid
            RETURN 
                elementId(t) as time_id, t.name as time_name,
                elementId(downstream) as ds_id, labels(downstream) as ds_labels, properties(downstream) as ds_properties,
                elementId(r) as rel_id, type(r) as rel_type,
                elementId(startNode(r)) as rel_start, elementId(endNode(r)) as rel_end,
                properties(r) as rel_properties
            """
            time_results = session.run(time_downstream_query, time_ids=current_time_ids)
            time_node_ids_to_expand = []
            
            for tr in time_results:
                ds_id = tr["ds_id"]
                if ds_id in all_nodes or ds_id in nodes_to_add:
                    continue
                ds_labels = tr["ds_labels"] or []
                ds_rel_id = tr["rel_id"]
                ds_rel_data = {
                    "id": ds_rel_id,
                    "type": tr["rel_type"],
                    "start_node": tr["rel_start"],
                    "end_node": tr["rel_end"],
                    "properties": dict(tr["rel_properties"]) if tr["rel_properties"] else {}
                }
                ds_node_data = {
                    "id": ds_id,
                    "labels": ds_labels,
                    "properties": _remove_embedding(dict(tr["ds_properties"]) if tr["ds_properties"] else {})
                }
                
                if "Time" in ds_labels:
                    # 子Time节点：直接并入，继续展开
                    all_nodes[ds_id] = ds_node_data
                    all_relationships[ds_rel_id] = ds_rel_data
                    time_node_ids_to_expand.append(ds_id)
                else:
                    # 非Time下游节点：加入待AI筛选
                    nodes_to_add[ds_id] = ds_node_data
                    relations_to_add[ds_rel_id] = ds_rel_data
                    ds_name = tr["ds_properties"].get("name", "Unknown") if tr["ds_properties"] else "Unknown"
                    time_name = tr["time_name"] or "Unknown"
                    display = f"({ds_name}) -[{tr['rel_type']}]-> ({time_name})"
                    
                    memories_to_be_viewed[ds_id] = {
                        "ids": {
                            "node_id": ds_id,
                            "relation_id": ds_rel_id
                        },
                        "display": display
                    }
        
        logger.info(f"新发现节点: {len(nodes_to_add)}个 (另有{len(expanded_time_ids)}个时间节点直接并入)")
        
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
        
        logger.debug(f"筛选后保留 {len(outer_node_ids)} 个节点，{len(filtered_relation_ids)} 个关系")
        
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


def _extract_nodes_by_keyword(kg_manager, keywords: List[str], summary: str = "") -> Dict[str, Any]:
    """
    根据输入的关键词列表提取相应的节点及信息。
    
    Args:
        kg_manager: KnowledgeGraphManager实例
        keywords: 预定义的关键词列表
        summary: 话题摘要，用于对模糊匹配的节点进行AI筛选
        
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
                    # 2. 如果精确匹配没有结果，使用向量索引进行语义匹配
                    logger.debug(f"无法精准匹配 '{keyword}', 进行embedding模糊匹配")
                    
                    # 生成关键词的embedding向量
                    keyword_embedding = generate_embedding(keyword)
                    
                    if keyword_embedding:
                        # 使用Neo4j原生向量索引进行语义匹配
                        semantic_matches_all = []
                        for index_name, _label in KnowledgeGraphManager.VECTOR_INDEX_DEFINITIONS:
                            try:
                                semantic_match_query = """
                                CALL db.index.vector.queryNodes($index_name, 5, $keyword_embedding)
                                YIELD node, score
                                WHERE score > 0.6
                                RETURN elementId(node) as id, labels(node) as labels, 
                                       properties(node) as properties, score as similarity
                                """
                                idx_results = session.run(
                                    semantic_match_query, 
                                    index_name=index_name, 
                                    keyword_embedding=keyword_embedding
                                )
                                semantic_matches_all.extend(list(idx_results))
                            except Exception as idx_e:
                                logger.warning(f"向量索引 {index_name} 查询失败: {idx_e}")
                                continue
                        
                        # 按相似度排序取前5
                        semantic_matches_all.sort(key=lambda r: r["similarity"], reverse=True)
                        semantic_matches = semantic_matches_all[:5]
                        
                        if semantic_matches:
                            # 收集候选节点，准备交由AI筛选
                            candidate_nodes = {}
                            candidate_data = {}
                            for record in semantic_matches:
                                node_id = record["id"]
                                if node_id not in nodes_dict:
                                    node_name = record["properties"].get("name", "Unknown") if record["properties"] else "Unknown"
                                    similarity = record["similarity"]
                                    logger.debug(f"  - Matched '{node_name}' with similarity {similarity:.3f}")
                                    candidate_nodes[node_id] = {
                                        "id": node_id,
                                        "labels": record["labels"] or [],
                                        "properties": _remove_embedding(dict(record["properties"]) if record["properties"] else {})
                                    }
                                    candidate_data[node_id] = {
                                        "ids": {"node_id": node_id, "relation_id": None},
                                        "display": node_name
                                    }
                            
                            # 如果有summary则通过AI筛选，否则全部保留
                            if summary and candidate_data:
                                filtered_result = _filter_related_nodes(candidate_data, summary)
                                filtered_ids = filtered_result.get("filtered_node_ids", [])
                                for nid in filtered_ids:
                                    if nid in candidate_nodes:
                                        nodes_dict[nid] = candidate_nodes[nid]
                                logger.debug(f"模糊匹配筛选: {len(candidate_nodes)}个候选 -> {len(filtered_ids)}个保留")
                            else:
                                nodes_dict.update(candidate_nodes)
                        else:
                            logger.info(f"No semantic matches found for keyword: '{keyword}'")
                    else:
                        logger.warning(f"Failed to generate embedding for keyword: '{keyword}'")
            
            # 转换为列表格式
            nodes_list = list(nodes_dict.values())
            outer_node_ids = [node["id"] for node in nodes_list]
            node_names = [node["properties"].get("name", "") for node in nodes_list]
            
            logger.info(f"找到起点记忆：{node_names}")
            
            return {
                "nodes": nodes_list,
                "relationships": [],
                "outer_node_ids": outer_node_ids
            }
            
    except Exception as e:
        logger.error(f"Failed to extract keywords '{keywords}': {e}")
        return {"nodes": [], "relationships": [], "outer_node_ids": []}


def get_relevant_memories(keywords: List[str], summary: str = "", max_results: int = 50, save_temp_memory: bool = False) -> Dict[str, Any]:
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
        if not is_neo4j_available():
            return {"nodes": [], "relationships": []}
            
        kg_manager = get_knowledge_graph_manager()
        if not kg_manager.connected:
            return {"nodes": [], "relationships": []}
        
        if not keywords:
            logger.error("[记忆查询] 关键词列表为空")
            return {"nodes": [], "relationships": []}
        
        logger.debug(f"[记忆查询] 使用{len(keywords)}个关键词。")

        # 自我硬编码关键词，确保至少有一个关键词能匹配到自我相关的记忆节点。
        # keywords.append("自我")

        # 第一步：提取基础节点
        with kg_manager.driver.session() as session:
            nodes_data = _extract_nodes_by_keyword(kg_manager, keywords, summary)
            
            if not nodes_data.get("nodes"):
                logger.error("[记忆查询] 未找到匹配的节点")
                return {"nodes": [], "relationships": []}
            
            logger.debug(f"[记忆查询] 初始找到 {len(nodes_data['nodes'])} 个节点")
            
            # 第二步：反复扩展连接的节点，直到达到最大数量限制
            expansion_rounds = 0
            max_expansion_rounds = 3  # 限制扩展轮数避免过度查询
            
            while (len(nodes_data.get("nodes", [])) < max_results and 
                   nodes_data.get("outer_node_ids") and 
                   expansion_rounds < max_expansion_rounds):
                
                expansion_rounds += 1
                logger.debug(f"[记忆查询] 第 {expansion_rounds} 轮扩展，当前节点数: {len(nodes_data['nodes'])}")
                
                # 扩展连接的节点
                nodes_data = _expand_memory_grabbed(session, nodes_data, summary)
                
                # 如果节点数量超过限制，截断到最大数量
                if len(nodes_data.get("nodes", [])) > max_results:
                    nodes_data["nodes"] = nodes_data["nodes"][:max_results]
                    nodes_data["outer_node_ids"] = []  # 停止进一步扩展
                    logger.debug(f"[记忆查询] 节点数量超过限制，截断到 {max_results} 个")
                    break
            
            # 直接返回节点和关系数据
            result = {
                "nodes": nodes_data.get("nodes", []),
                "relationships": nodes_data.get("relationships", [])
            }
            
            logger.debug(f"[记忆查询] 成功返回 {len(result['nodes'])} 个节点，{len(result['relationships'])} 个关系")
            
            # 保存结果到临时文件供调试查看
            if save_temp_memory:
                try:
                    memory_frag_path = os.path.join(os.path.dirname(__file__), "memory_graph", "temp_memory.json")
                    os.makedirs(os.path.dirname(memory_frag_path), exist_ok=True)
                    with open(memory_frag_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    logger.debug(f"[记忆查询] 结果已保存到: {memory_frag_path}")
                except Exception as save_e:
                    logger.warning(f"[警告] 保存结果到文件失败: {save_e}")
            
            return result
        
    except Exception as e:
        logger.error(f"[错误] 基于关键词的记忆查询失败: {e}")
        return {"nodes": [], "relationships": []}

def full_memory_search(message: str, save_temp_memory: bool = False) -> Dict[str, Any]:
    """
    直接从输入文本进行完整的记忆搜索流程，包含关键词提取和相关记忆查询。
    
    Args:
        message: 输入文本消息
        save_temp_memory: 是否保存临时记忆结果
        
    Returns Dict[str, Any]:
        {"nodes": [...], "relationships": [...]}
    """
    extract_result = extract_keyword_from_text(message)
    keywords = extract_result.get("keywords", [])
    summary = extract_result.get("summary", "")
    
    logger.info(f"从输入消息提取到关键词: {keywords}, 摘要: {summary}")
    
    relevant_memories = get_relevant_memories(keywords, summary, save_temp_memory=save_temp_memory)
    
    return relevant_memories


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_message = "小明喜欢吃香蕉。"
    result = full_memory_search(test_message, save_temp_memory=True)
    print(result)