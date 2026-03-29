#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识图谱管理器：
- 负责将记忆数据写入 Neo4j 数据库
- 提供统一的图谱操作接口
- 支持批量写入和单条写入
- create：创建各类节点/关系，modify：修改节点/关系，delete：删除节点/关系
- set：通过五元组进行的创建，暂未启用
"""

import os
import re
import sys
import json
import logging
from dataclasses import asdict
from datetime import datetime

# 获取项目根目录
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from openai import OpenAI
from system.config import config
from typing import List, Dict, Any, Optional

# API 配置
API_KEY = config.memory_api.embedding_api_key
API_URL = config.memory_api.embedding_base_url
MODEL = config.memory_api.embedding_model
AI_NAME = config.system.ai_name
USERNAME = config.system.user_name
DEBUG_MODE = config.system.debug

# 初始化 OpenAI 客户端
client = OpenAI(api_key=API_KEY, base_url=API_URL)


def load_prompt_file(filename: str, description: str = "") -> str:
    """
    加载提示词文件的通用函数
    Args:
        filename: 文件名（如 "1_intent_analyze.txt"）
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
            logger.warning(f"{description}提示词文件为空: {prompt_path}")
        return content


MEMORY_RECORD_PROMPT = load_prompt_file("memory_record.txt", "记忆存储")


try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, AuthError, TransientError
except ImportError:
    print("Neo4j driver not installed. Please install with: pip install neo4j")
    sys.exit(1)

from system.system_checker import is_neo4j_available

logger = logging.getLogger(__name__)
logging.getLogger("neo4j").setLevel(logging.WARNING)


class KnowledgeGraphManager:
    """知识图谱管理器"""

    def __init__(self):
        self.driver = None
        self.connected = False
        self._connect()

    def _connect(self) -> bool:
        """连接到 Neo4j 数据库"""
        if not config.grag.enabled:
            logger.warning("GRAG is disabled, skipping Neo4j connection")
            return False

        # 使用全局连接状态检查，避免重复连接尝试
        if not is_neo4j_available():
            logger.warning("Neo4j connection unavailable, skipping connection attempt")
            return False

        try:
            uri = config.grag.neo4j_uri
            user = config.grag.neo4j_user
            password = config.grag.neo4j_password
            database = config.grag.neo4j_database

            logger.info(f"Connecting to Neo4j at {uri}")

            self.driver = GraphDatabase.driver(
                uri,
                auth=(user, password),
                database=database,
                max_connection_lifetime=30 * 60,  # 30 minutes
                max_connection_pool_size=50,
                connection_acquisition_timeout=10,  # 10 seconds
                connection_timeout=5,  # 5 seconds
            )

            # 测试连接
            with self.driver.session() as session:
                result = session.run("RETURN 1 as test")
                test_value = result.single()["test"]
                if test_value == 1:
                    self.connected = True
                    logger.info("Successfully connected to Neo4j")
                    # 确保向量索引已创建
                    self._ensure_vector_indexes()
                    # 每日检查点：快照 + 记忆衰退
                    self.daily_checkpoint()
                    return True

        except AuthError as e:
            logger.error(f"Neo4j authentication failed: {e}")
        except ServiceUnavailable as e:
            logger.error(f"Neo4j service unavailable: {e}")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")

        self.connected = False
        return False

    def disconnect(self):
        """断开数据库连接"""
        if self.driver:
            self.driver.close()
            self.connected = False
            logger.info("Disconnected from Neo4j")

    def _ensure_connection(self) -> bool:
        """确保数据库连接可用"""
        # 首先检查全局连接状态
        if not is_neo4j_available():
            return False

        if not self.connected:
            return self._connect()
        return True

    # 向量索引定义：(索引名, 标签名)
    VECTOR_INDEX_DEFINITIONS = [
        ("character_embedding_index", "Character"),
        ("location_embedding_index", "Location"),
        ("entity_embedding_index", "Entity"),
        ("time_embedding_index", "Time"),
    ]
    VECTOR_EMBEDDING_DIMENSION = 384
    VECTOR_SIMILARITY_FUNCTION = "cosine"

    def _ensure_vector_indexes(self):
        """
        确保所有需要的向量索引已创建。
        在连接成功后调用，如果索引已存在则跳过。
        """
        if not self.driver:
            return

        try:
            with self.driver.session() as session:
                # 获取已存在的索引
                existing_indexes = set()
                result = session.run("SHOW INDEXES YIELD name RETURN name")
                for record in result:
                    existing_indexes.add(record["name"])

                for index_name, label in self.VECTOR_INDEX_DEFINITIONS:
                    if index_name not in existing_indexes:
                        create_index_query = (
                            f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS "
                            f"FOR (n:{label}) ON (n.embedding) "
                            f"OPTIONS {{indexConfig: {{"
                            f"`vector.dimensions`: {self.VECTOR_EMBEDDING_DIMENSION}, "
                            f"`vector.similarity_function`: '{self.VECTOR_SIMILARITY_FUNCTION}'"
                            f"}}}}"
                        )
                        session.run(create_index_query)
                        logger.info(f"Created vector index: {index_name} for label :{label}")
                    else:
                        logger.debug(f"Vector index already exists: {index_name}")

        except Exception as e:
            logger.warning(f"Failed to ensure vector indexes (non-fatal): {e}")

    def _generate_embedding(self, text: str) -> Optional[List[float]]:
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
                model=MODEL,
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
    
    def create_node(
        self,
        session,
        name: str,
        node_type: str,
        time_str: list[str],
        time_type: str,
        trust: float = 0.5,
        context: str = "reality现实",
        note: str = "无",
    ) -> Optional[str]:
        """
        创建节点，通过此节点时可以创建多种节点类型。
        """
        if node_type == "Time":
            return self.create_time_node(session, time_str, time_type, context)
        elif node_type == "Character":
            return self.create_character_node(session, name, trust, context, note)
        elif node_type == "Location":
            return self.create_location_node(session, name, context, note)
        elif node_type == "Entity":
            return self.create_entity_node(session, name, context, note)
        else:
            logger.error(f"Unsupported node type: {node_type}")
            return None


    def create_time_node(self, session, time_str: list[str], time_type: str = "static", context: str = "reality现实") -> Optional[str]:
        """
        根据时间组件列表创建对应的时间节点和层次关系。
        列表中第一个元素为最上层（最粗粒度），最后一个为最具体。
        例如: ["2001年", "02月", "03日", "04点", "56分", "07秒"]

        Args:
            session: Neo4j session
            time_str: 时间组件列表，如 ["2001年", "02月", "03日"]
            time_type: 时间类型，"static" 或 "recurring"
            context: 上下文环境

        Returns:
            Optional[str]: 创建的最具体时间节点ID，失败返回None
        """
        if not time_str:
            return None
        logger.info(f"Creating time node for: {time_str}")

        try:
            most_specific_node_id = None
            prev_node_name = None
            prev_cumulative = None

            for i, component in enumerate(time_str):
                component = component.strip()
                if not component:
                    continue

                # name: 当前组件文本
                name = component
                # cumulative_name: 从上层到当前层的累积文本，用于embedding
                cumulative_name = "".join(c.strip() for c in time_str[:i + 1])

                # 先查询是否已存在相同 time_str+context 的节点且已有 embedding
                existing = session.run(
                    """
                    MATCH (t:Time {time_str: $time_str, context: $context})
                    RETURN elementId(t) as node_id, t.embedding IS NOT NULL as has_embedding
                    """,
                    time_str=cumulative_name,
                    context=context,
                ).single()

                if existing and existing["has_embedding"]:
                    # 节点已存在且有 embedding，直接复用
                    current_node_id = existing["node_id"]
                    most_specific_node_id = current_node_id
                else:
                    # 节点不存在或缺少 embedding，生成后写入
                    embedding = self._generate_embedding(cumulative_name)

                    result = session.run(
                        """
                        MERGE (t:Time {time_str: $time_str, context: $context})
                        SET t.name = $name,
                            t.node_type = 'Time',
                            t.time_type = $time_type,
                            t.embedding = $embedding
                        RETURN elementId(t) as node_id
                        """,
                        name=name,
                        time_str=cumulative_name,
                        context=context,
                        time_type=time_type,
                        embedding=embedding,
                    )
                    record = result.single()
                    if not record:
                        continue

                    current_node_id = record["node_id"]
                    most_specific_node_id = current_node_id

                # 创建当前节点到上层节点的 BELONGS_TO 关系
                if prev_cumulative is not None:
                    session.run(
                        """
                        MATCH (child:Time {time_str: $child_time_str, context: $context})
                        MATCH (parent:Time {time_str: $parent_time_str, context: $context})
                        MERGE (child)-[:BELONGS_TO]->(parent)
                        """,
                        child_time_str=cumulative_name,
                        parent_time_str=prev_cumulative,
                        context=context,
                    )

                prev_node_name = name
                prev_cumulative = cumulative_name

            if most_specific_node_id:
                logger.debug(f"Created hierarchical time node with ID: {most_specific_node_id}")
                return most_specific_node_id
            else:
                logger.warning(f"No specific node created for time: {time_str}")
                return None

        except Exception as e:
            logger.error(f"Failed to create time node '{time_str}': {e}")
            return None

    
    def create_character_node(
        self,
        session,
        name: str,
        trust: float = 0.5,
        context: str = "reality",
        note: str = "",
    ) -> Optional[str]:
        """
        创建角色节点

        Args:
            session: Neo4j session
            name: 角色名称
            trust: 信任度 (默认0.5)
            context: 上下文环境 (默认"reality")
            note: 备注信息 (默认空字符串)
        Returns:
            Optional[str]: 创建的角色节点ID，失败返回None
        """
        if not name or not name.strip():
            logger.warning("Character name cannot be empty")
            return None

        name = name.strip()
        logger.debug(f"Creating character node for: {name}")

        try:
            current_time = datetime.now().isoformat()

            character_embedding = self._generate_embedding(name)

            result = session.run(
                """
                CREATE (c:Character {name: $name})
                SET c.created_at = $created_at,
                    c.node_type = 'Character',
                    c.trust = $trust,
                    c.context = $context,
                    c.last_updated = $last_updated,
                    c.embedding = $embedding,
                    c.note = $note
                RETURN elementId(c) as node_id
            """,
                name=name,
                trust=trust,
                context=context,
                created_at=current_time,
                last_updated=current_time,
                embedding=character_embedding,
                note=note,
            )

            record = result.single()
            if record:
                node_id = record["node_id"]
                logger.debug(f"Created character node '{name}' with ID: {node_id}")
                return node_id
            else:
                logger.error(
                    f"No result returned when creating character node '{name}'"
                )
                return None

        except Exception as e:
            logger.error(f"Failed to create character node '{name}': {e}")
            return None

    def create_location_node(
        self, session, name: str, context: str = "reality", note: str = ""
    ) -> Optional[str]:
        """
        创建地点节点

        Args:
            session: Neo4j session
            name: 地点名称
            context: 上下文环境 (默认"reality")
            note: 备注信息 (默认空字符串)
        Returns:
            Optional[str]: 创建的地点节点ID，失败返回None
        """
        if not name or not name.strip():
            logger.warning("Location name cannot be empty")
            return None

        name = name.strip()
        logger.debug(f"Creating location node for: {name}")

        try:
            current_time = datetime.now().isoformat()

            location_embedding = self._generate_embedding(name)

            result = session.run(
                """
                CREATE (l:Location {name: $name})
                SET l.node_type = 'Location',
                    l.context = $context,
                    l.created_at = $created_at,
                    l.last_updated = $last_updated,
                    l.embedding = $embedding,
                    l.note = $note
                RETURN elementId(l) as node_id
            """,
                name=name,
                context=context,
                created_at=current_time,
                last_updated=current_time,
                embedding=location_embedding,
                note=note,
            )

            record = result.single()
            if record:
                node_id = record["node_id"]
                logger.debug(f"Created location node '{name}' with ID: {node_id}")
                return node_id
            else:
                logger.error(f"No result returned when creating location node '{name}'")
                return None

        except Exception as e:
            logger.error(f"Failed to create location node '{name}': {e}")
            return None

    def create_entity_node(
        self,
        session,
        name: str,
        context: str = "reality",
        note: str = "无",
    ) -> Optional[str]:
        """
        创建实体节点（事件，物品，概念等）

        Args:
            session: Neo4j session
            name: 实体名称
            context: 上下文环境 (默认"reality")
            note: 备注 (默认"无")

        Returns:
            Optional[str]: 创建的实体节点ID，失败返回None
        """
        if not name or not name.strip():
            logger.warning("Entity name cannot be empty")
            return None

        name = name.strip()
        logger.debug(f"Creating entity node for: {name}")

        try:
            current_time = datetime.now().isoformat()

            entity_embedding = self._generate_embedding(name)

            result = session.run(
                """
                CREATE (e:Entity {name: $name})
                SET e.created_at = $created_at,
                    e.node_type = 'Entity',
                    e.context = $context,
                    e.note = $note,
                    e.last_updated = $last_updated,
                    e.embedding = $embedding
                RETURN elementId(e) as node_id
            """,
                name=name,
                context=context,
                note=note,
                created_at=current_time,
                last_updated=current_time,
                embedding=entity_embedding,
            )

            record = result.single()
            if record:
                node_id = record["node_id"]
                logger.debug(f"Created entity node '{name}' with ID: {node_id}")
                return node_id
            else:
                logger.error(f"No result returned when creating entity node '{name}'")
                return None

        except Exception as e:
            logger.error(f"Failed to create entity node '{name}': {e}")
            return None

    def create_relation(
        self,
        startNode_id: str,
        endNode_id: str,
        predicate: str,
        source: str,
        confidence: float = 0.5,
        importance: float = 0.5,
        directivity: str = "single",
        evidence: str = "",
    ) -> Optional[str]:
        """
        在两个节点之间创建关系

        Args:
            startNode_id: 第一个节点的Neo4j元素ID
            endNode_id: 第二个节点的Neo4j元素ID
            predicate: 关系谓词/类型
            source: 关系来源
            confidence: 置信度 (默认0.5)
            importance: 重要程度 (默认0.5)
            directivity: 关系方向 (默认'single'，'bidirectional'表示双向)
            evidence: 关系证据（为什么设置这个置信度，选填）

        Returns:
            Optional[str]: 成功返回关系ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot connect nodes: No Neo4j connection")
            return None

        if not all([startNode_id, endNode_id, predicate, source]):
            logger.error("Missing required parameters for connecting nodes")
            return None

        try:
            with self.driver.session() as session:
                # 首先验证两个节点是否存在
                validate_query = """
                OPTIONAL MATCH (a) WHERE elementId(a) = $startNode_id
                OPTIONAL MATCH (b) WHERE elementId(b) = $endNode_id
                RETURN a IS NOT NULL as a_exists, b IS NOT NULL as b_exists,
                       a.name as a_name, b.name as b_name,
                       labels(a) as a_labels, labels(b) as b_labels
                """
                validation_result = session.run(
                    validate_query, startNode_id=startNode_id, endNode_id=endNode_id
                ).single()

                if not validation_result["a_exists"]:
                    logger.error(f"Node A with ID '{startNode_id}' not found")
                    return None

                if not validation_result["b_exists"]:
                    logger.error(f"Node B with ID '{endNode_id}' not found")
                    return None

                # 准备关系描述
                direction_desc = (
                    f"{validation_result['a_name']} -> {validation_result['b_name']}"
                )
                if directivity == "bidirectional":
                    direction_desc = f"{validation_result['a_name']} <-> {validation_result['b_name']}"

                # 处理关系类型名称，确保符合Neo4j关系类型命名规范
                predicate_safe = predicate.replace(" ", "_").replace("-", "_").upper()
                if not predicate_safe.replace("_", "").isalnum():
                    predicate_safe = "CONNECTED_TO"  # 回退到通用关系类型

                # 检测相同位置有没有同名关系
                check_existing_query = """
                MATCH (a) WHERE elementId(a) = $startNode_id
                MATCH (b) WHERE elementId(b) = $endNode_id
                OPTIONAL MATCH (a)-[r]->(b) WHERE r.predicate = $predicate
                RETURN elementId(r) as existing_relation_id, r.predicate as existing_predicate, type(r) as relation_type
                """

                existing_result = session.run(
                    check_existing_query,
                    startNode_id=startNode_id,
                    endNode_id=endNode_id,
                    predicate=predicate,
                ).single()

                # 如果已存在相同关系，直接调用modify_relation修改并返回ID
                if existing_result and existing_result["existing_relation_id"]:
                    logger.info(
                        f"Relation already exists with ID: {existing_result['existing_relation_id']}"
                    )
                    relationship_id = self.modify_relation(
                        existing_result["existing_relation_id"],
                        predicate,
                        source,
                        confidence,
                        directivity,
                        evidence,
                        importance=importance,
                    )
                    return relationship_id

                # 否则正常创建关系
                current_time = datetime.now().isoformat()
                relationship_id = None

                # 创建正向关系
                forward_query = f"""
                MATCH (source) WHERE elementId(source) = $startNode_id
                MATCH (target) WHERE elementId(target) = $endNode_id
                CREATE (source)-[r:{predicate_safe}]->(target)
                SET r.created_at = $current_time,
                    r.last_updated = $current_time,
                    r.predicate = $predicate,
                    r.source = [$source],
                    r.confidence = $confidence,
                    r.importance = $importance,
                    r.significance = $significance,
                    r.evidence = $evidence
                RETURN elementId(r) as forward_relationship_id
                """

                forward_result = session.run(
                    forward_query,
                    startNode_id=startNode_id,
                    endNode_id=endNode_id,
                    predicate=predicate,
                    source=source,
                    confidence=confidence,
                    importance=importance,
                    significance=1,
                    evidence=evidence,
                    current_time=current_time,
                )

                forward_record = forward_result.single()
                if forward_record:
                    relationship_id = forward_record["forward_relationship_id"]
                    logger.debug(f"Created relationship")

                if directivity == "bidirectional":
                    # 创建反向关系
                    backward_query = f"""
                    MATCH (source) WHERE elementId(source) = $endNode_id
                    MATCH (target) WHERE elementId(target) = $startNode_id
                    CREATE (source)-[r:{predicate_safe}]->(target)
                    SET r.created_at = $current_time,
                        r.last_updated = $current_time,
                        r.predicate = $predicate,
                        r.source = [$source],
                        r.confidence = $confidence,
                        r.importance = $importance,
                        r.significance = $significance,
                        r.evidence = $evidence
                    RETURN elementId(r) as backward_relationship_id
                    """

                    backward_result = session.run(
                        backward_query,
                        startNode_id=startNode_id,
                        endNode_id=endNode_id,
                        predicate=predicate,
                        source=source,
                        confidence=confidence,
                        importance=importance,
                        significance=1,
                        evidence=evidence,
                        current_time=current_time,
                    )

                    backward_record = backward_result.single()
                    if backward_record:
                        logger.debug(f"Created backward relationship")

                if relationship_id:
                    logger.info(f"Successfully connected nodes: {direction_desc}")
                    return relationship_id
                else:
                    logger.error("Failed to create relationship")
                    return None

        except Exception as e:
            logger.error(
                f"Failed to connect nodes '{startNode_id}' and '{endNode_id}': {e}"
            )
            return None

    def modify_node(self, node_id: str, updates: dict) -> Optional[str]:
        """
        用于从客户端直接修改节点的属性。

        Args:
            node_id: Neo4j节点ID
            updates: 包含需要更新的属性的字典，键为属性名，值为新的属性值

        Returns:
            Optional[str]: 修改成功返回节点ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot modify node: No Neo4j connection")
            return None

        if not node_id or not node_id.strip():
            logger.error("Node ID cannot be empty")
            return None

        if not updates or not isinstance(updates, dict):
            logger.error("Updates must be a non-empty dictionary")
            return None

        try:
            with self.driver.session() as session:
                # 首先获取节点当前信息进行验证
                check_query = """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN labels(n) as node_labels, n.name as node_name, n.node_type as node_type, 
                       n.context as node_context, properties(n) as current_properties
                """

                check_result = session.run(check_query, node_id=node_id).single()

                if not check_result:
                    logger.error(f"Node with ID '{node_id}' not found")
                    return None

                current_node_type = check_result["node_type"]
                # 如果nodeType = Time，则拒绝修改
                if current_node_type == "Time":
                    logger.warning(
                        f"Cannot modify Time node '{node_id}' - Time nodes are read-only"
                    )
                    return "InvalidModification"

        except Exception as e:
            logger.error(f"Failed to validate node '{node_id}': {e}")
            return None

        try:
            with self.driver.session() as session:
                # 检查节点是否存在
                check_query = """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN labels(n) as node_labels, n.name as node_name, properties(n) as current_properties
                """

                check_result = session.run(check_query, node_id=node_id).single()

                if not check_result:
                    logger.error(f"Node with ID '{node_id}' not found")
                    return None

                # 获取当前节点的所有属性
                current_properties = check_result["current_properties"] or {}

                # 添加当前时间戳到更新中
                updates["last_updated"] = datetime.now().isoformat()

                # 定义需要保护的系统属性（不会被删除）
                protected_properties = {
                    "created_at",
                    "last_updated",
                    "node_type",
                    "context",
                    "name",
                    "id",
                    "elementId",
                    "labels",
                }

                # 检查是否需要更新节点标签
                new_node_type = updates.get("node_type")
                current_labels = check_result["node_labels"]

                # 如果需要更新节点类型，先处理标签更新
                if new_node_type:
                    # 定义业务相关的标签
                    business_labels = ["Entity", "Character", "Location"]

                    # 移除现有的业务标签
                    for label in business_labels:
                        if label in current_labels:
                            remove_label_query = f"""
                            MATCH (n) WHERE elementId(n) = $node_id
                            REMOVE n:{label}
                            """
                            session.run(remove_label_query, node_id=node_id)
                            logger.debug(f"Removed label '{label}' from node {node_id}")

                    # 添加新的业务标签
                    if new_node_type in business_labels:
                        add_label_query = f"""
                        MATCH (n) WHERE elementId(n) = $node_id
                        SET n:{new_node_type}
                        """
                        session.run(add_label_query, node_id=node_id)
                        logger.debug(f"Added label '{new_node_type}' to node {node_id}")

                    # 确保node_type属性和标签一致
                    updates["node_type"] = new_node_type

                # 找出需要删除的属性（当前存在但不在updates中且不是保护属性）
                properties_to_remove = []
                for prop_name in current_properties.keys():
                    if (
                        prop_name not in updates
                        and prop_name not in protected_properties
                        and prop_name != "nodeType"
                    ):  # nodeType会转换为node_type
                        properties_to_remove.append(prop_name)

                # 删除不需要的属性
                if properties_to_remove:
                    remove_props_query = f"""
                    MATCH (n) WHERE elementId(n) = $node_id
                    REMOVE {", ".join([f"n.{prop}" for prop in properties_to_remove])}
                    """
                    session.run(remove_props_query, node_id=node_id)
                    logger.debug(
                        f"Removed properties {properties_to_remove} from node {node_id}"
                    )

                # 构建SET子句
                set_clauses = []
                params = {"node_id": node_id}

                for key, value in updates.items():
                    # 跳过nodeType，因为已经处理为node_type
                    if key == "nodeType":
                        continue

                    # 验证属性名（避免注入攻击）
                    if not key.replace("_", "").isalnum():
                        logger.warning(f"Skipping invalid property name: {key}")
                        continue

                    param_key = f"prop_{key}"
                    set_clauses.append(f"n.{key} = ${param_key}")
                    params[param_key] = value

                if not set_clauses:
                    logger.warning("No valid properties to update")
                    return None

                # 执行更新
                update_query = f"""
                MATCH (n) WHERE elementId(n) = $node_id
                SET {", ".join(set_clauses)}
                RETURN properties(n) as updated_properties, labels(n) as updated_labels
                """

                result = session.run(update_query, **params)
                updated_record = result.single()

                if updated_record:
                    updated_labels = updated_record["updated_labels"]
                    updated_properties = updated_record["updated_properties"]
                    logger.info(
                        f"Successfully updated node {node_id} with labels: {updated_labels}"
                    )

                    # 重新计算并更新embedding向量
                    try:
                        # 根据节点类型决定使用哪个字段生成embedding
                        embedding_text = None
                        if 'Time' in updated_labels:
                            # Time节点使用time字段
                            embedding_text = updated_properties.get('time')
                        else:
                            # 其他节点使用name字段
                            embedding_text = updated_properties.get('name')
                        
                        # 生成新的embedding
                        if embedding_text:
                            new_embedding = self._generate_embedding(embedding_text)
                            
                            if new_embedding:
                                # 更新embedding到数据库
                                update_embedding_query = """
                                MATCH (n) WHERE elementId(n) = $node_id
                                SET n.embedding = $embedding
                                """
                                session.run(update_embedding_query, node_id=node_id, embedding=new_embedding)
                                logger.debug(f"Successfully updated embedding for node {node_id}")
                            else:
                                logger.warning(f"Failed to generate embedding for node {node_id}")
                    except Exception as embed_error:
                        logger.warning(f"Failed to update embedding for node {node_id}: {embed_error}")

                    return node_id
                else:
                    logger.error("Failed to update node")
                    return None

        except Exception as e:
            logger.error(f"Failed to modify node '{node_id}': {e}")
            return None

    def _reverse_relation_direction(self, relation_id: str) -> str:
        """令关系的指向反转"""
        if not self._ensure_connection():
            logger.error("Cannot reverse relation direction: No Neo4j connection")
            return None

        if not relation_id or not relation_id.strip():
            logger.error("Relation ID cannot be empty")
            return None

        try:
            with self.driver.session() as session:
                # 查询原关系的所有信息
                query_relation = """
                MATCH (start)-[r]->(end) WHERE elementId(r) = $relation_id
                RETURN elementId(start) as start_node_id, elementId(end) as end_node_id,
                       type(r) as rel_type_name, properties(r) as rel_properties,
                       start.name as start_name, end.name as end_name
                """

                result = session.run(query_relation, relation_id=relation_id).single()

                if not result:
                    logger.error(f"Relation with ID '{relation_id}' not found")
                    return None

                # 获取关系信息
                start_node_id = result["start_node_id"]
                end_node_id = result["end_node_id"]
                rel_type_name = result["rel_type_name"]
                rel_properties = result["rel_properties"] or {}
                start_name = result["start_name"]
                end_name = result["end_name"]

                # 检查是否为受保护的关系类型
                if rel_type_name == "BELONGS_TO":
                    logger.warning(
                        f"Cannot reverse BELONGS_TO relation - protected relation type"
                    )
                    return None

                # 删除原关系并创建反向关系的事务
                reverse_query = f"""
                MATCH (start) WHERE elementId(start) = $start_node_id
                MATCH (end) WHERE elementId(end) = $end_node_id
                MATCH (start)-[old_r:{rel_type_name}]->(end) WHERE elementId(old_r) = $relation_id
                WITH start, end, old_r, properties(old_r) as old_props
                DELETE old_r
                CREATE (end)-[new_r:{rel_type_name}]->(start)
                SET new_r = old_props
                RETURN elementId(new_r) as new_relation_id
                """

                reverse_result = session.run(
                    reverse_query,
                    start_node_id=start_node_id,
                    end_node_id=end_node_id,
                    relation_id=relation_id,
                )

                reverse_record = reverse_result.single()

                if reverse_record:
                    new_relation_id = reverse_record["new_relation_id"]
                    logger.info(
                        f"Successfully reversed relation direction: {start_name} -> {end_name} became {end_name} -> {start_name}"
                    )

                    return new_relation_id
                else:
                    logger.error("Failed to reverse relation direction")
                    return None

        except Exception as e:
            logger.error(
                f"Failed to reverse relation direction for '{relation_id}': {e}"
            )
            return None

    def modify_relation(
        self,
        relation_id: str,
        predicate: str,
        source: str,
        confidence: float = 0.5,
        directivity: str = "to_endNode",
        evidence: str = "",
        importance: float = None,
    ) -> Optional[str]:
        """
        修改关系的属性

        Args:
            relation_id: Neo4j关系ID
            predicate: 关系谓词/类型
            source: 关系来源
            confidence: 置信度 (默认0.5)
            directivity: 关系方向 (输入'to_endNode', 'to_startNode', 'bidirectional'表示双向, 
                            输出时会自动调整为'single'或'bidirectional')
            evidence: 关系证据（为什么设置这个置信度，选填）
            importance: 重要程度 (0-1)，如果提供则更新
        Returns:
            Optional[str]: 修改成功返回关系ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot modify relation: No Neo4j connection")
            return None

        if not relation_id or not relation_id.strip():
            logger.error("Relation ID cannot be empty")
            return None

        if not all([predicate, source]):
            logger.error(
                "Missing required parameters: predicate and source cannot be empty"
            )
            return None

        try:
            with self.driver.session() as session:
                # 检查关系是否存在并获取节点信息
                check_query = """
                MATCH (a)-[r]->(b) WHERE elementId(r) = $relation_id
                RETURN elementId(a) as source_node_id, elementId(b) as target_node_id,
                       a.name as source_name, b.name as target_name,
                       type(r) as rel_type_name, properties(r) as current_properties
                """

                check_result = session.run(
                    check_query, relation_id=relation_id
                ).single()

                if not check_result:
                    logger.error(f"Relation with ID '{relation_id}' not found")
                    return None

                start_node = check_result["source_node_id"]
                end_node = check_result["target_node_id"]
                source_name = check_result["source_name"]
                target_name = check_result["target_name"]
                rel_type_name = check_result["rel_type_name"]
                current_properties = check_result["current_properties"]
                # 如果关系类型是BELONGS_TO，拒绝修改
                if rel_type_name == "BELONGS_TO":
                    logger.warning(
                        f"Cannot modify BELONGS_TO relation with ID '{relation_id}' - BELONGS_TO relations are protected"
                    )
                    return None

                # 更新现有关系的属性
                current_time = datetime.now().isoformat()

                # 处理source合并和置信度计算
                current_source = (
                    current_properties.get("source") if current_properties else []
                )
                current_confidence = (
                    current_properties.get("confidence", 0.5)
                    if current_properties
                    else 0.5
                )

                # 将字符串格式的source转换为list（向后兼容）
                if isinstance(current_source, str):
                    if current_source:
                        current_source = [
                            s.strip() for s in current_source.split(",") if s.strip()
                        ]
                    else:
                        current_source = []
                elif not isinstance(current_source, list):
                    current_source = []

                # 合并source列表
                source_list = current_source.copy() if current_source else []
                if source not in source_list:
                    source_list.append(source)

                    # 计算新的置信度：new_confidence = (1-(1-old_confidence)*(1-confidence/2))
                    try:
                        current_confidence = float(current_confidence)
                        new_confidence = 1 - (1 - current_confidence) * (1 - confidence / 2)
                        # 确保置信度在0-1范围内
                        new_confidence = max(0.0, min(1.0, new_confidence))
                    except (ValueError, TypeError):
                        new_confidence = confidence
                else:
                    # source已存在，不更新置信度
                    new_confidence = current_confidence

                if directivity == "to_startNode":
                    relation_id = self._reverse_relation_direction(relation_id)

                if directivity != "bidirectional":
                    directivity = "single"

                # 处理importance和significance
                current_importance = (
                    current_properties.get("importance", 0.5)
                    if current_properties
                    else 0.5
                )

                new_importance = importance if importance is not None else current_importance
                new_significance = 1

                update_query = """
                MATCH ()-[r]-() WHERE elementId(r) = $relation_id
                SET r.predicate = $predicate,
                    r.source = $source_list,
                    r.confidence = $new_confidence,
                    r.importance = $importance,
                    r.significance = $significance,
                    r.evidence = $evidence,
                    r.last_updated = $current_time
                RETURN elementId(r) as updated_relation_id
                """

                update_result = session.run(
                    update_query,
                    relation_id=relation_id,
                    predicate=predicate,
                    source_list=source_list,
                    new_confidence=new_confidence,
                    importance=new_importance,
                    significance=new_significance,
                    evidence=evidence,
                    directivity=directivity,
                    current_time=current_time,
                )

                update_record = update_result.single()

                if update_record:
                    logger.info(
                        f"Successfully updated relation {relation_id} between {source_name} and {target_name}"
                    )

                    return relation_id
                else:
                    logger.error(f"Failed to update relation {relation_id}")
                    return None

        except Exception as e:
            logger.error(f"Failed to modify relation '{relation_id}': {e}")
            return None
        
    def collide_nodes(self, node_id_1: str, node_id_2: str) -> Optional[str]:
        """
        将所选的两个节点，合并成一个节点。
        节点2将被删除，其上的所有关系迁移至节点1。

        Args:
            node_id_1: 第一个节点的Neo4j元素ID
            node_id_2: 第二个节点的Neo4j元素ID

        Returns:
            Optional[str]: 合并后的节点ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot collide nodes: No Neo4j connection")
            return None

        if not all([node_id_1, node_id_2]):
            logger.error("Node IDs cannot be empty")
            return None

        try:
            with self.driver.session() as session:
                # 查询两个节点的信息
                query = """
                MATCH (n) WHERE elementId(n) IN [$node_id_1, $node_id_2]
                RETURN elementId(n) as node_id, n.name as name, n.node_type as node_type,
                       n.context as context, properties(n) as properties, labels(n) as labels
                """

                result = session.run(query, node_id_1=node_id_1, node_id_2=node_id_2)
                nodes_info = {record["node_id"]: record for record in result}

                if len(nodes_info) != 2:
                    logger.error("One or both nodes not found for collision")
                    return None

                node1_info = nodes_info[node_id_1]
                node2_info = nodes_info[node_id_2]

                # 将节点2的出关系迁移到节点1（跳过指向节点1的自环）
                session.run(
                    """
                    MATCH (n2)-[r]->(target) WHERE elementId(n2) = $node_id_2
                      AND elementId(target) <> $node_id_1
                    MATCH (n1) WHERE elementId(n1) = $node_id_1
                    WITH n1, target, type(r) as rel_type, properties(r) as rel_props, r
                    CALL apoc.create.relationship(n1, rel_type, rel_props, target) YIELD rel
                    DELETE r
                    """,
                    node_id_1=node_id_1,
                    node_id_2=node_id_2,
                )

                # 将节点2的入关系迁移到节点1（跳过来自节点1的自环）
                session.run(
                    """
                    MATCH (source)-[r]->(n2) WHERE elementId(n2) = $node_id_2
                      AND elementId(source) <> $node_id_1
                    MATCH (n1) WHERE elementId(n1) = $node_id_1
                    WITH n1, source, type(r) as rel_type, properties(r) as rel_props, r
                    CALL apoc.create.relationship(source, rel_type, rel_props, n1) YIELD rel
                    DELETE r
                    """,
                    node_id_1=node_id_1,
                    node_id_2=node_id_2,
                )

                # 删除节点2上剩余的关系（节点1和节点2之间的直接关系）及节点2本身
                session.run(
                    """
                    MATCH (n2) WHERE elementId(n2) = $node_id_2
                    DETACH DELETE n2
                    """,
                    node_id_2=node_id_2,
                )

                logger.info(
                    f"Successfully collided nodes: '{node2_info['name']}' merged into '{node1_info['name']}' (ID: {node_id_1})"
                )
                return node_id_1

        except Exception as e:
            logger.error(f"Failed to collide nodes '{node_id_1}' and '{node_id_2}': {e}")
            return None

    def ensure_node_exists(
        self,
        session=None,
        *,
        node_type: str,
        name: str = "",
        time_str: Optional[list[str]] = None,
        time_type: str = "static",
        trust: float = 0.5,
        context: str = "reality现实",
        note: str = "无",
    ) -> Optional[str]:
        """如果节点已存在则返回其 ID，否则创建节点并返回新 ID。

        查重规则:
        - Character / Location / Entity: 使用 `name` + `node_type` + `context` 查重

        注意:
        - Time 节点具有层级与聚合语义，`ensure_node_exists` 不应也不会用于 Time 节点

        Args:
            session: 可选 Neo4j session/transaction；不传则函数内部自行创建 session
            node_type: 节点类型（Time/Character/Location/Entity）
            name: 非 Time 节点名称
            time_str: Time 节点时间组件列表
            time_type: Time 节点类型（static/recurring）
            trust: Character 信任度
            context: 节点语境
            note: 节点备注

        Returns:
            Optional[str]: 已存在或新建节点的 elementId，失败返回 None
        """
        if not self._ensure_connection():
            logger.error("Cannot ensure node exists: No Neo4j connection")
            return None

        node_type = (node_type or "").strip()
        if not node_type:
            logger.error("Cannot ensure node exists: node_type is required")
            return None

        if node_type == "Time":
            logger.warning("ensure_node_exists is not applicable to Time nodes")
            return None

        time_str = time_str or []

        def _find_existing_node(tx) -> Optional[str]:
            normalized_name = (name or "").strip()
            if not normalized_name:
                logger.warning(f"Cannot ensure {node_type} node: name is empty")
                return None

            record = tx.run(
                """
                MATCH (n {name: $name, node_type: $node_type, context: $context})
                RETURN elementId(n) as node_id
                ORDER BY n.last_updated DESC
                LIMIT 1
                """,
                name=normalized_name,
                node_type=node_type,
                context=context,
            ).single()
            return record["node_id"] if record else None

        def _create_node(tx) -> Optional[str]:
            create_name = (name or "").strip()
            return self.create_node(
                session=tx,
                name=create_name,
                node_type=node_type,
                time_str=None,
                time_type=time_type,
                trust=trust,
                context=context,
                note=note,
            )

        try:
            if session is not None:
                existing_node_id = _find_existing_node(session)
                if existing_node_id:
                    return existing_node_id
                return _create_node(session)

            with self.driver.session() as local_session:
                existing_node_id = _find_existing_node(local_session)
                if existing_node_id:
                    return existing_node_id
                return _create_node(local_session)
        except Exception as e:
            logger.error(f"Failed to ensure node exists ({node_type}): {e}")
            return None

    def ensure_relation_exists(
        self,
        *,
        start_node_id: str,
        end_node_id: str,
        predicate: str,
        source: str,
        confidence: float = 0.5,
        importance: float = 0.5,
        directivity: str = "single",
        evidence: str = "",
    ) -> Optional[str]:
        """如果关系已存在则复用并更新，否则创建关系并返回关系 ID。

        该方法复用 `create_relation` 的幂等逻辑：
        当起止节点间存在同 predicate 关系时，`create_relation` 会转为 `modify_relation`。
        """
        if not all([start_node_id, end_node_id, predicate, source]):
            logger.error("Cannot ensure relation exists: missing required parameters")
            return None

        try:
            return self.create_relation(
                startNode_id=start_node_id,
                endNode_id=end_node_id,
                predicate=predicate,
                source=source,
                confidence=confidence,
                importance=importance,
                directivity=directivity,
                evidence=evidence,
            )
        except Exception as e:
            logger.error(
                f"Failed to ensure relation exists ({start_node_id}-{predicate}->{end_node_id}): {e}"
            )
            return None

    def memory_decay(self, decay_factor: float = 0.8) -> None:
        """
        记忆衰退机制
        对于所有的关系，如果其有significance参数，设置其
        significance = old_significance * (decay_factor + importance * (1 - decay_factor))
        如果该关系的significance衰退到0.1以下，则删除该关系

        孤立节点清理
        对于所有的时间节点，如果没有指向该节点的关系，则删除该节点
        对于所有其余节点，如果既没有指向该节点的关系，也没有关系从该节点触发，则删除该节点
        """
        if not self._ensure_connection():
            logger.error("Cannot perform memory decay: No Neo4j connection")
            return

        try:
            with self.driver.session() as session:
                # 1. 衰退所有有significance的关系
                result = session.run(
                    """
                    MATCH ()-[r]->()
                    WHERE r.significance IS NOT NULL AND r.importance IS NOT NULL
                    SET r.significance = r.significance * ($decay_factor + r.importance * (1 - $decay_factor))
                    RETURN count(r) as updated_count
                    """,
                    decay_factor=decay_factor,
                )
                decay_count = result.single()["updated_count"]
                logger.info(f"Memory decay applied to {decay_count} relationships (decay_factor={decay_factor})")

                # 2. 基于时间精细度的关系梯度提升
                # 不同精细度的时间节点有不同的significance阈值，低于阈值则提升至上一级时间节点
                granularity_thresholds = {
                    "秒": 0.9,
                    "分": 0.75,
                    "点": 0.55,
                    "日": 0.35,
                    "月": 0.2,
                }

                promoted_count = 0

                # 获取所有连接Time节点的非BELONGS_TO关系
                result = session.run(
                    """
                    MATCH (a)-[r]->(b)
                    WHERE r.significance IS NOT NULL
                      AND (a:Time OR b:Time)
                      AND type(r) <> 'BELONGS_TO'
                    RETURN elementId(r) as rel_id, elementId(a) as start_id, elementId(b) as end_id,
                           type(r) as rel_type,
                           labels(a) as start_labels, labels(b) as end_labels,
                           a.name as start_name, b.name as end_name,
                           r.significance as significance
                    """
                )
                time_rels = list(result)

                for record in time_rels:
                    rel_id = record["rel_id"]
                    significance = record["significance"]
                    start_labels = record["start_labels"] or []
                    end_labels = record["end_labels"] or []

                    # 确定时间节点和另一端节点
                    if "Time" in end_labels:
                        time_node_id = record["end_id"]
                        other_node_id = record["start_id"]
                        time_name = record["end_name"]
                        is_time_at_end = True
                    elif "Time" in start_labels:
                        time_node_id = record["start_id"]
                        other_node_id = record["end_id"]
                        time_name = record["start_name"]
                        is_time_at_end = False
                    else:
                        continue

                    if not time_name:
                        continue

                    # 根据时间节点名称后缀判断精细度，非标准精细度（周、轮次等）不处理
                    threshold = None
                    for suffix, thresh in granularity_thresholds.items():
                        if time_name.endswith(suffix):
                            threshold = thresh
                            break

                    if threshold is None or significance >= threshold:
                        continue

                    # 查找上一级时间节点
                    parent_result = session.run(
                        """
                        MATCH (t:Time)-[:BELONGS_TO]->(parent:Time)
                        WHERE elementId(t) = $time_node_id
                        RETURN elementId(parent) as parent_id, parent.name as parent_name
                        """,
                        time_node_id=time_node_id,
                    )
                    parent_record = parent_result.single()

                    if not parent_record:
                        continue

                    parent_id = parent_record["parent_id"]
                    parent_name = parent_record["parent_name"]

                    # 将关系迁移至上一级时间节点，significance重置为1
                    if is_time_at_end:
                        promote_result = session.run(
                            """
                            MATCH (other) WHERE elementId(other) = $other_id
                            MATCH (parent:Time) WHERE elementId(parent) = $parent_id
                            MATCH ()-[old_r]->() WHERE elementId(old_r) = $rel_id
                            WITH other, parent, old_r, properties(old_r) as old_props, type(old_r) as old_type
                            DELETE old_r
                            WITH other, parent, old_props, old_type
                            CALL apoc.create.relationship(other, old_type, old_props, parent) YIELD rel
                            RETURN elementId(rel) as new_rel_id
                            """,
                            other_id=other_node_id,
                            parent_id=parent_id,
                            rel_id=rel_id,
                        )
                    else:
                        promote_result = session.run(
                            """
                            MATCH (other) WHERE elementId(other) = $other_id
                            MATCH (parent:Time) WHERE elementId(parent) = $parent_id
                            MATCH ()-[old_r]->() WHERE elementId(old_r) = $rel_id
                            WITH other, parent, old_r, properties(old_r) as old_props, type(old_r) as old_type
                            DELETE old_r
                            WITH other, parent, old_props, old_type
                            CALL apoc.create.relationship(parent, old_type, old_props, other) YIELD rel
                            RETURN elementId(rel) as new_rel_id
                            """,
                            other_id=other_node_id,
                            parent_id=parent_id,
                            rel_id=rel_id,
                        )

                    if promote_result.single():
                        promoted_count += 1
                        logger.debug(
                            f"Promoted relation from time '{time_name}' to '{parent_name}' (significance={significance:.2f})"
                        )

                if promoted_count > 0:
                    logger.info(f"Promoted {promoted_count} relationships to parent time nodes")

                # 3. 删除significance低于0.1的剩余关系
                result = session.run(
                    """
                    MATCH ()-[r]->()
                    WHERE r.significance IS NOT NULL AND r.significance < 0.1
                      AND type(r) <> 'BELONGS_TO'
                    DELETE r
                    RETURN count(r) as deleted_count
                    """
                )
                deleted_rels = result.single()["deleted_count"]
                if deleted_rels > 0:
                    logger.info(f"Deleted {deleted_rels} relationships with significance < 0.1")

                # 4. 清理孤立时间节点（没有入关系的Time节点）
                result = session.run(
                    """
                    MATCH (t:Time)
                    WHERE NOT EXISTS { MATCH ()-[]->(t) }
                    DETACH DELETE t
                    RETURN count(t) as deleted_count
                    """
                )
                deleted_time = result.single()["deleted_count"]
                if deleted_time > 0:
                    logger.info(f"Deleted {deleted_time} orphaned Time nodes")

                # 5. 清理其余孤立节点（既无入关系也无出关系的非Time节点）
                result = session.run(
                    """
                    MATCH (n)
                    WHERE NOT n:Time
                      AND NOT EXISTS { MATCH (n)-[]-() }
                    DELETE n
                    RETURN count(n) as deleted_count
                    """
                )
                deleted_other = result.single()["deleted_count"]
                if deleted_other > 0:
                    logger.info(f"Deleted {deleted_other} orphaned non-Time nodes")

        except Exception as e:
            logger.error(f"Failed to apply memory decay: {e}")

    def _find_node(
        self,
        session,
        node_name: str,
        node_type: str,
        time: str,
        location: str,
        signature_node: str,
        context: str,
    ) -> Optional[str]:
        """
        查找匹配的客体节点

        Args:
            session: Neo4j session
            node_name: 节点名称
            node_type: 节点类型
            time: 事件发生的时间（选填）
            location: 事件发生的地点（选填）
            signature_node: 相连的特征节点（选填）

        Returns:
            Optional[str]: 找到的节点ID，找不到返回None
        """
        try:
            current_time = datetime.now()

            # 第1步：查找所有同名节点
            same_name_nodes_query = """
            MATCH (n {name: $name})
            OPTIONAL MATCH (n)-[:HAPPENED_AT]->(t:Time)
            OPTIONAL MATCH (n)-[:HAPPENED_IN]->(l:Location)
            RETURN elementId(n) as node_id, n.last_updated as last_updated,
                   labels(n) as node_labels, n.node_type as node_type, n.context as node_context,
                   t.time as node_time, l.name as node_location
            """

            same_name_results = session.run(same_name_nodes_query, name=node_name)
            candidates = []

            for record in same_name_results:
                candidates.append(
                    {
                        "node_id": record["node_id"],
                        "last_updated": record["last_updated"],
                        "node_labels": record["node_labels"],
                        "node_type": record["node_type"],
                        "node_time": record["node_time"],
                        "node_location": record["node_location"],
                        "node_context": record["node_context"],
                    }
                )

            if not candidates:
                # 没有找到同名节点
                return None

            # 第2步：移除不符合类型（node_type）和语境（context）的节点
            if node_type:  # 只有提供了node_type才进行类型过滤
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate["node_type"] == node_type
                    and context in candidate["node_context"]
                ]
                if not candidates:
                    return None

            # 第3步：移除不符合时间条件的节点
            if time:  # 只有提供了time才进行时间过滤
                # 优先选择有明确时间匹配的节点
                time_matched_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate["node_time"] == time
                ]

                # 如果有明确时间匹配的节点，就只使用这些节点
                if time_matched_candidates:
                    candidates = time_matched_candidates
                else:
                    # 如果没有明确匹配的，才考虑无时间信息的节点
                    candidates = [
                        candidate
                        for candidate in candidates
                        if not candidate["node_time"]
                    ]
                # 如果也无法匹配，返回None
                if not candidates:
                    return None

            # 第4步：移除不符合地点条件的节点
            if location:  # 只有提供了location才进行地点过滤
                location_matched_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate["node_location"] == location
                ]

                # 如果有明确地点匹配的节点，就只使用这些节点
                if location_matched_candidates:
                    candidates = location_matched_candidates
                else:
                    # 如果没有明确匹配的，才考虑无地点信息的节点
                    candidates = [
                        candidate
                        for candidate in candidates
                        if not candidate["node_location"]
                    ]
                # 如果也无法匹配，返回None
                if not candidates:
                    return None

            # 如果只有一个候选节点，直接返回（通常只应该有一个候选节点）
            if len(candidates) == 1:
                return candidates[0]["node_id"]

            # 第5步：如果仍有多个候选，使用特征节点进行匹配，查询每个候选是否与特征节点相连。
            if signature_node:
                signature_matched_candidates = []
                for candidate in candidates:
                    signature_check_query = """
                    MATCH (n) WHERE elementId(n) = $node_id
                    MATCH (s {name: $signature_name})
                    RETURN EXISTS( (n)-[]->(s) OR (s)-[]->(n) ) as is_connected
                    """
                    signature_result = session.run(
                        signature_check_query,
                        node_id=candidate["node_id"],
                        signature_name=signature_node,
                    ).single()
                    if signature_result and signature_result["is_connected"]:
                        signature_matched_candidates.append(candidate)

                # 如果有与特征节点相连的候选，使用这些候选；否则继续使用原有候选
                if signature_matched_candidates:
                    candidates = signature_matched_candidates

            # 再检查一下节点数量
            if len(candidates) == 1:
                return candidates[0]["node_id"]

            # 第6步：如果仍有多个候选节点，按last_updated排序选择最近更新的
            def parse_datetime(dt_str):
                if not dt_str:
                    return datetime.min
                try:
                    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                except:
                    return datetime.min

            # 计算与当前时间的距离
            for candidate in candidates:
                last_updated_dt = parse_datetime(candidate["last_updated"])
                candidate["time_distance"] = abs(
                    (current_time - last_updated_dt).total_seconds()
                )

            # 按时间距离排序，选择最近更新的节点
            candidates.sort(key=lambda x: x["time_distance"])

            # 由于last_updated基于时间且绝对不会重复，直接返回最接近当前时间的节点
            return candidates[0]["node_id"]

        except Exception as e:
            logger.error(f"Failed to find object node '{node_name}': {e}")
            return None

    def get_statistics(self) -> Dict[str, Any]:
        """获取知识图谱统计信息"""
        if not self._ensure_connection():
            return {"error": "No Neo4j connection"}

        try:
            with self.driver.session() as session:
                # 获取节点统计
                entity_count = session.run(
                    "MATCH (n:Entity) RETURN count(n) as count"
                ).single()["count"]
                time_count = session.run(
                    "MATCH (n:Time) RETURN count(n) as count"
                ).single()["count"]
                location_count = session.run(
                    "MATCH (n:Location) RETURN count(n) as count"
                ).single()["count"]

                # 获取关系统计
                triple_rels = session.run(
                    "MATCH ()-[r]->() WHERE r.predicate IS NOT NULL AND r.action IS NULL RETURN count(r) as count"
                ).single()["count"]
                quintuple_rels = session.run(
                    "MATCH ()-[r]->() WHERE r.action IS NOT NULL RETURN count(r) as count"
                ).single()["count"]

                return {
                    "nodes": {
                        "entities": entity_count,
                        "times": time_count,
                        "locations": location_count,
                        "total": entity_count + time_count + location_count,
                    },
                    "relationships": {
                        "triples": triple_rels,
                        "quintuples": quintuple_rels,
                        "total": triple_rels + quintuple_rels,
                    },
                }

        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return {"error": str(e)}

    def delete_node_or_relation(self, element_ids: List[str]) -> Dict[str, Any]:
        """
        根据Neo4j元素ID批量删除节点或关系

        Args:
            element_ids: Neo4j元素ID列表（节点ID或关系ID）

        Returns:
            {
              "success": bool,
              "error": Optional[str],
              "deleted_nodes": int,
              "deleted_relationships": int,
            }
        """
        if not self._ensure_connection():
            logger.error("Cannot delete element: No Neo4j connection")
            return {
                "success": False,
                "error": "No Neo4j connection",
                "deleted_nodes": 0,
                "deleted_relationships": 0,
            }

        if not element_ids:
            return {
                "success": False,
                "error": "Element ID list cannot be empty",
                "deleted_nodes": 0,
                "deleted_relationships": 0,
            }

        # 累计统计
        total_deleted_nodes = 0
        total_deleted_relationships = 0
        failed_items = []

        try:
            with self.driver.session() as session:
                for element_id in element_ids:
                    if not element_id or not element_id.strip():
                        failed_items.append("Empty element ID")
                        continue
                    
                    try:
                        # 首先检查元素是否存在以及类型
                        check_query = """
                        OPTIONAL MATCH (n) WHERE elementId(n) = $element_id
                        OPTIONAL MATCH ()-[r]-() WHERE elementId(r) = $element_id
                        RETURN 
                            CASE WHEN n IS NOT NULL THEN 'node' ELSE null END as node_type,
                            CASE WHEN r IS NOT NULL THEN 'relationship' ELSE null END as rel_type,
                            CASE WHEN n IS NOT NULL THEN labels(n) ELSE null END as node_labels,
                            CASE WHEN n IS NOT NULL THEN n.name ELSE null END as node_name,
                            CASE WHEN r IS NOT NULL THEN type(r) ELSE null END as rel_type_name
                        """

                        check_result = session.run(check_query, element_id=element_id).single()

                        if not check_result or (
                            not check_result["node_type"] and not check_result["rel_type"]
                        ):
                            failed_items.append(f"Element '{element_id}' not found")
                            continue

                        # 删除节点（会自动删除相关关系）
                        if check_result["node_type"]:
                            # 获取删除前的关系数量
                            rel_count_query = """
                            MATCH (n)-[r]-() WHERE elementId(n) = $element_id
                            RETURN count(r) as rel_count
                            """
                            rel_count = session.run(
                                rel_count_query, element_id=element_id
                            ).single()["rel_count"]

                            # 删除节点（DETACH DELETE 会同时删除所有相关关系）
                            delete_query = """
                            MATCH (n) WHERE elementId(n) = $element_id
                            DETACH DELETE n
                            RETURN count(n) as deleted_count
                            """

                            result = session.run(delete_query, element_id=element_id)
                            deleted_count = result.single()["deleted_count"]

                            if deleted_count > 0:
                                total_deleted_nodes += 1
                                total_deleted_relationships += rel_count
                                logger.info(
                                    f"Successfully deleted node {element_id} and {rel_count} related relationships"
                                )
                            else:
                                failed_items.append(f"Node '{element_id}' deletion failed")

                        # 删除关系
                        elif check_result["rel_type"]:
                            # 删除关系
                            delete_query = """
                            MATCH ()-[r]-() WHERE elementId(r) = $element_id
                            DELETE r
                            RETURN count(r) as deleted_count
                            """

                            result = session.run(delete_query, element_id=element_id)
                            deleted_count = result.single()["deleted_count"]

                            if deleted_count > 0:
                                total_deleted_relationships += 1
                                logger.info(f"Successfully deleted relationship {element_id}")
                            else:
                                failed_items.append(f"Relationship '{element_id}' deletion failed")
                    
                    except Exception as item_error:
                        failed_items.append(f"Element '{element_id}': {str(item_error)}")
                        logger.error(f"Failed to delete element '{element_id}': {item_error}")

                # 构建返回结果
                if total_deleted_nodes > 0 or total_deleted_relationships > 0:
                    message = f"成功删除 {total_deleted_nodes} 个节点和 {total_deleted_relationships} 个关系"
                    if failed_items:
                        message += f"，{len(failed_items)} 个项目失败"
                    
                    return {
                        "success": True,
                        "error": None if not failed_items else f"{len(failed_items)} items failed",
                        "deleted_nodes": total_deleted_nodes,
                        "deleted_relationships": total_deleted_relationships,
                        "message": message,
                        "failed_items": failed_items if failed_items else None,
                    }
                else:
                    return {
                        "success": False,
                        "error": "No elements were deleted: " + "; ".join(failed_items[:3]),
                        "deleted_nodes": 0,
                        "deleted_relationships": 0,
                        "failed_items": failed_items,
                    }

        except Exception as e:
            logger.error(f"Failed to delete elements: {e}")
            return {
                "success": False,
                "error": str(e),
                "deleted_nodes": total_deleted_nodes,
                "deleted_relationships": total_deleted_relationships,
            }

    def _filter_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
        """过滤节点属性，隐去指定的系统属性"""

        if not properties:
            return {}
        
        # 需要隐去的属性列表
        hidden_properties = {
            "last_updated", "embedding", "significance",
        }
        
        # 返回过滤后的属性
        filtered_props = {}
        for key, value in properties.items():
            if key not in hidden_properties:
                filtered_props[key] = value
        
        return filtered_props

    def downloaod_memory(self, elements: Dict[str, Any]) -> bool:
        """
        从Neo4j查询指定的节点和关系，过滤后保存到日志文件
        
        输入：
        {
            "nodes_ids": [],      # 节点ID列表
            "relation_ids": [],   # 关系ID列表
        }
        
        Returns:
            bool: 操作是否成功
        """
        if not self._ensure_connection():
            logger.error("Cannot save memory: No Neo4j connection")
            return False
        
        # 验证输入格式
        if not isinstance(elements, dict):
            logger.error("Invalid input format: elements must be a dictionary")
            return False
        
        nodes_ids = elements.get("nodes_ids", [])
        relation_ids = elements.get("relation_ids", [])
        
        if not isinstance(nodes_ids, list) or not isinstance(relation_ids, list):
            logger.error("Invalid input format: nodes_ids and relation_ids must be lists")
            return False
        
        try:
            with self.driver.session() as session:
                # 查询指定的节点
                new_nodes = []
                if nodes_ids:
                    logger.info(f"Loading {len(nodes_ids)} nodes from Neo4j...")
                    
                    for node_id in nodes_ids:
                        node_query = """
                        MATCH (n)
                        WHERE elementId(n) = $node_id
                        RETURN elementId(n) as id, labels(n) as labels, properties(n) as properties
                        """
                        node_result = session.run(node_query, node_id=node_id)
                        node_record = node_result.single()
                        
                        if node_record:
                            node = {
                                "id": str(node_record["id"]),
                                "labels": node_record["labels"],
                                "properties": dict(node_record["properties"]),
                            }
                            new_nodes.append(node)
                        else:
                            logger.warning(f"Node with ID '{node_id}' not found, skipping")
                
                # 查询指定的关系
                new_relationships = []
                valid_node_ids = set(node["id"] for node in new_nodes)
                
                if relation_ids:
                    logger.info(f"Loading {len(relation_ids)} relationships from Neo4j...")
                    
                    for relation_id in relation_ids:
                        rel_query = """
                        MATCH (a)-[r]->(b)
                        WHERE elementId(r) = $relation_id
                        RETURN elementId(r) as id, type(r) as type, 
                               elementId(a) as start_node, elementId(b) as end_node, 
                               properties(r) as properties
                        """
                        rel_result = session.run(rel_query, relation_id=relation_id)
                        rel_record = rel_result.single()
                        
                        if rel_record:
                            start_node = str(rel_record["start_node"])
                            end_node = str(rel_record["end_node"])
                            
                            # 检查关系的起始节点和结束节点是否都在nodes_ids中
                            if start_node in valid_node_ids and end_node in valid_node_ids:
                                relationship = {
                                    "id": str(rel_record["id"]),
                                    "type": rel_record["type"],
                                    "start_node": start_node,
                                    "end_node": end_node,
                                    "properties": dict(rel_record["properties"]),
                                }
                                new_relationships.append(relationship)
                            else:
                                logger.warning(
                                    f"Relationship '{relation_id}' has invalid nodes "
                                    f"(start: {start_node in valid_node_ids}, end: {end_node in valid_node_ids}), skipping"
                                )
                        else:
                            logger.warning(f"Relationship with ID '{relation_id}' not found, skipping")
                
                # 将过滤后的结果保存到日志文件
                try:
                    log_dir = os.path.join(project_root, "data", "memory_graph")
                    os.makedirs(log_dir, exist_ok=True)
                    
                    log_filename = f"{datetime.now().strftime('%Y%m%d')}.jsonl"
                    log_file = os.path.join(log_dir, log_filename)
                    
                    # 过滤节点属性
                    filtered_nodes = []
                    for node in new_nodes:
                        filtered_nodes.append({
                            "id": node["id"],
                            "labels": node["labels"],
                            "properties": KnowledgeGraphManager._filter_properties(node.get("properties", {})),
                        })
                    
                    # 过滤关系属性
                    filtered_relationships = []
                    for rel in new_relationships:
                        filtered_relationships.append({
                            "id": rel["id"],
                            "type": rel["type"],
                            "start_node": rel["start_node"],
                            "end_node": rel["end_node"],
                            "properties": KnowledgeGraphManager._filter_properties(rel.get("properties", {})),
                        })
                    
                    log_entry = {
                        "nodes": filtered_nodes,
                        "relationships": filtered_relationships,
                        "timestamp": datetime.now().isoformat(),
                    }
                    
                    with open(log_file, "w", encoding="utf-8") as f:
                        f.write(json.dumps(log_entry, ensure_ascii=False, indent=2) + "\n")
                    
                    logger.info(f"记忆保存日志已写入: {log_file}")
                except Exception as e:
                    logger.warning(f"记忆保存日志写入失败（不影响主流程）: {e}")
                
                logger.info(f"已处理 {len(new_nodes)} 个节点和 {len(new_relationships)} 个关系")
                
                # 统计被过滤的关系数量
                filtered_count = len(relation_ids) - len(new_relationships)
                if filtered_count > 0:
                    logger.warning(f"已过滤 {filtered_count} 个无效关系（节点不在保存列表中）")
                
                return True
        
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            logger.error(f"保存记忆失败: {e}")
            return False

    def upload_memory(self, elements: Dict[str, Any]) -> bool:
        """
        将节点和关系数据加载到Neo4j数据库
        数据格式与 downloaod_memory 保存的格式一致：
        {
            "nodes": [{"id": "...", "labels": [...], "properties": {...}}, ...],
            "relationships": [{"id": "...", "type": "...", "start_node": "...", "end_node": "...", "properties": {...}}, ...],
            "timestamp": "..."
        }
        采用合并策略：通过ID匹配更新已存在的项目，创建新项目
        
        Args:
            elements: 包含nodes和relationships的字典
        
        Returns:
            bool: 操作是否成功
        """
        if not self._ensure_connection():
            logger.error("Cannot upload memory: No Neo4j connection")
            return False
        
        if not isinstance(elements, dict):
            logger.error("Invalid input: elements must be a dictionary")
            return False
        
        try:
            # 提取节点和关系数据
            nodes_to_upload = elements.get("nodes", [])
            all_relationships = elements.get("relationships", [])
            
            if not nodes_to_upload and not all_relationships:
                logger.warning("没有节点和关系数据")
                return True
            
            # 构建关系字典，用于后续ID重映射
            relationships_list = list(all_relationships)
            
            logger.info(f"从文件加载: {len(nodes_to_upload)} 个节点, {len(relationships_list)} 个关系")
            
            with self.driver.session() as session:
                # 上传所有节点
                added_count = 0
                updated_count = 0
                
                for node in nodes_to_upload:
                    old_node_id = node["id"]
                    labels = node.get("labels", [])
                    properties = node.get("properties", {})
                    
                    # 检查Neo4j中是否存在该ID的节点
                    check_query = """
                    MATCH (n)
                    WHERE elementId(n) = $node_id
                    RETURN elementId(n) as id, labels(n) as existing_labels
                    """
                    check_result = session.run(check_query, node_id=old_node_id)
                    existing_node = check_result.single()
                    
                    if existing_node:
                        # 节点已存在，更新属性和标签
                        existing_labels = existing_node["existing_labels"]
                        
                        update_query = """
                        MATCH (n)
                        WHERE elementId(n) = $node_id
                        SET n += $properties
                        RETURN elementId(n) as id
                        """
                        session.run(update_query, node_id=old_node_id, properties=properties)
                        
                        # 处理标签：添加缺失的标签，移除多余的标签
                        labels_to_add = [lbl for lbl in labels if lbl not in existing_labels]
                        labels_to_remove = [lbl for lbl in existing_labels if lbl not in labels]
                        
                        if labels_to_add:
                            add_labels_query = f"""
                            MATCH (n)
                            WHERE elementId(n) = $node_id
                            SET n:{":".join(labels_to_add)}
                            """
                            session.run(add_labels_query, node_id=old_node_id)
                        
                        if labels_to_remove:
                            for label in labels_to_remove:
                                remove_label_query = f"""
                                MATCH (n)
                                WHERE elementId(n) = $node_id
                                REMOVE n:{label}
                                """
                                session.run(remove_label_query, node_id=old_node_id)
                        
                        updated_count += 1
                        logger.info(f"Updated node: {properties.get('name', 'Unknown')} (id: {old_node_id})")
                    else:
                        # 节点不存在，创建新节点并获取Neo4j生成的ID
                        labels_str = ":".join(labels) if labels else "Entity"
                        create_query = f"""
                        CREATE (n:{labels_str})
                        SET n = $properties
                        RETURN elementId(n) as id
                        """
                        create_result = session.run(create_query, properties=properties)
                        created_record = create_result.single()
                        
                        if created_record:
                            new_node_id = created_record["id"]
                            
                            # 更新所有关系中引用该节点的ID
                            for rel in relationships_list:
                                if rel.get("start_node") == old_node_id:
                                    rel["start_node"] = new_node_id
                                if rel.get("end_node") == old_node_id:
                                    rel["end_node"] = new_node_id
                            
                            added_count += 1
                            logger.info(f"Created node: {properties.get('name', 'Unknown')} (old_id: {old_node_id}, new_id: {new_node_id})")
                
                # 上传所有关系（先验证节点存在性）
                valid_relationships = []
                for rel in relationships_list:
                    start_node_id = rel.get("start_node")
                    end_node_id = rel.get("end_node")
                    
                    check_nodes_query = """
                    MATCH (a), (b)
                    WHERE elementId(a) = $start_id AND elementId(b) = $end_id
                    RETURN elementId(a) as start_id, elementId(b) as end_id
                    """
                    check_result = session.run(check_nodes_query, start_id=start_node_id, end_id=end_node_id)
                    
                    if check_result.single():
                        valid_relationships.append(rel)
                    else:
                        logger.warning(
                            f"关系 '{rel.get('id')}' 的节点不存在于Neo4j中 "
                            f"(start: {start_node_id}, end: {end_node_id}), 跳过"
                        )
                
                rel_added_count = 0
                rel_updated_count = 0
                
                for rel in valid_relationships:
                    old_rel_id = rel["id"]
                    rel_type = rel.get("type", "RELATED_TO")
                    start_node_id = rel.get("start_node")
                    end_node_id = rel.get("end_node")
                    properties = rel.get("properties", {})
                    
                    # 检查Neo4j中是否存在该ID的关系
                    check_rel_query = """
                    MATCH ()-[r]->()
                    WHERE elementId(r) = $rel_id
                    RETURN elementId(r) as id, type(r) as existing_type
                    """
                    check_rel_result = session.run(check_rel_query, rel_id=old_rel_id)
                    existing_rel = check_rel_result.single()
                    
                    if existing_rel:
                        # 关系已存在，更新属性
                        update_rel_query = """
                        MATCH ()-[r]->()
                        WHERE elementId(r) = $rel_id
                        SET r += $properties
                        RETURN elementId(r) as id
                        """
                        update_result = session.run(update_rel_query, rel_id=old_rel_id, properties=properties)
                        
                        if update_result.single():
                            rel_updated_count += 1
                            logger.info(f"Updated relationship: {rel_type} (id: {old_rel_id})")
                    else:
                        # 关系不存在，创建新关系
                        create_rel_query = f"""
                        MATCH (a), (b)
                        WHERE elementId(a) = $start_id AND elementId(b) = $end_id
                        CREATE (a)-[r:{rel_type}]->(b)
                        SET r = $properties
                        RETURN elementId(r) as id
                        """
                        create_rel_result = session.run(
                            create_rel_query, 
                            start_id=start_node_id, 
                            end_id=end_node_id, 
                            properties=properties
                        )
                        created_rel_record = create_rel_result.single()
                        
                        if created_rel_record:
                            rel_added_count += 1
                            logger.info(f"Created relationship: {rel_type} (old_id: {old_rel_id}, new_id: {created_rel_record['id']})")
                
                logger.info("记忆已上传到Neo4j")
                logger.info(f"  节点: 新增 {added_count} 个, 更新 {updated_count} 个")
                logger.info(f"  关系: 新增 {rel_added_count} 个, 更新 {rel_updated_count} 个")
                
                skipped_rels = len(relationships_list) - len(valid_relationships)
                if skipped_rels > 0:
                    logger.warning(f"跳过 {skipped_rels} 个关系（节点不存在）")
                
                return True
        
        except Exception as e:
            logger.error(f"上传记忆失败: {e}")
            return False

    def clear_all_memory(self) -> bool:
        """清空Neo4j中的全部记忆节点，彻底格式化记忆，无法回退

        警告：此操作将永久删除Neo4j数据库中的所有节点和关系！
        需要用户输入'yes'确认才能执行。

        Returns:
            bool: 操作是否成功
        """
        # 显示严重警告
        print("\n" + "=" * 60)
        print("⚠️  严重警告：记忆完全清空操作 ⚠️")
        print("=" * 60)
        print("该操作将会：")
        print("1. 清空Neo4j数据库中的全部节点和关系")
        print("2. 彻底格式化所有记忆数据")
        print("3. 此操作无法回退，数据将永久丢失")
        print("4. 影响范围：所有实体、时间、地点节点及其关系")
        print("=" * 60)

        # 要求用户确认
        try:
            user_input = input(
                "如果您确定要执行此操作，请输入 'yes'（区分大小写）: "
            ).strip()

            if user_input != "yes":
                print("操作已取消。")
                logger.info("clear_all_memory operation cancelled by user")
                return False

        except KeyboardInterrupt:
            print("\n操作已取消。")
            logger.info(
                "clear_all_memory operation cancelled by user (KeyboardInterrupt)"
            )
            return False
        except Exception as e:
            print(f"\n输入错误：{e}")
            logger.error(f"Input error in clear_all_memory: {e}")
            return False

        # 执行清空操作
        logger.info("正在清空Neo4j数据库...")

        if not self._ensure_connection():
            logger.error("Cannot clear all memory: No Neo4j connection")
            logger.error("无法连接到Neo4j数据库")
            return False

        try:
            with self.driver.session() as session:
                # 获取清空前的统计信息
                stats_before = self.get_statistics()
                if "error" not in stats_before:
                    total_nodes = stats_before.get("nodes", {}).get("total", 0)
                    total_rels = stats_before.get("relationships", {}).get("total", 0)
                    logger.info(f"清空前统计：{total_nodes} 个节点，{total_rels} 个关系")

                # 删除所有关系和节点
                result = session.run("MATCH (n) DETACH DELETE n")

                logger.info("Neo4j数据库已完全清空")
                logger.warning(
                    "All Neo4j memory data has been cleared by clear_all_memory function"
                )

                # 确认清空结果
                stats_after = self.get_statistics()
                if "error" not in stats_after:
                    remaining_nodes = stats_after.get("nodes", {}).get("total", 0)
                    remaining_rels = stats_after.get("relationships", {}).get(
                        "total", 0
                    )
                    logger.info(
                        f"清空后确认：{remaining_nodes} 个节点，{remaining_rels} 个关系"
                    )

                return True

        except Exception as e:
            logger.error(f"Failed to clear all memory: {e}")
            logger.error(f"清空操作失败：{e}")
            return False

    def download_neo4j_data(self) -> bool:
        """检查Neo4j连接并将数据下载到neo4j_memory.json文件

        Returns:
            bool: 操作是否成功
        """
        # 检查Neo4j连接
        if not self._ensure_connection():
            logger.error("Cannot load Neo4j data: No Neo4j connection available")
            logger.error("无法连接到Neo4j数据库")
            return False

        logger.info("Neo4j连接正常，正在同步数据...")
        logger.info("Neo4j connection established, starting data download")

        try:
            # 确保目录存在
            neo4j_memory_dir = os.path.join(os.path.dirname(__file__), "memory_graph")
            os.makedirs(neo4j_memory_dir, exist_ok=True)

            neo4j_memory_file = os.path.join(neo4j_memory_dir, "neo4j_memory.json")

            with self.driver.session() as session:
                # 加载所有节点
                logger.info("正在下载节点数据...")
                nodes_query = """
                MATCH (n)
                RETURN elementId(n) as id, labels(n) as labels, properties(n) as properties
                """
                nodes_result = session.run(nodes_query)
                nodes = []

                for record in nodes_result:
                    node = {
                        "id": str(record["id"]),
                        "labels": record["labels"],
                        "properties": dict(record["properties"]),
                    }
                    nodes.append(node)

                # 加载所有关系
                logger.info("正在下载关系数据...")
                relationships_query = """
                MATCH (a)-[r]->(b)
                RETURN elementId(r) as id, type(r) as type, elementId(a) as start_node, elementId(b) as end_node, properties(r) as properties
                """
                relationships_result = session.run(relationships_query)
                relationships = []

                for record in relationships_result:
                    relationship = {
                        "id": str(record["id"]),
                        "type": record["type"],
                        "start_node": str(record["start_node"]),
                        "end_node": str(record["end_node"]),
                        "properties": dict(record["properties"]),
                    }
                    relationships.append(relationship)

                # 构建数据结构
                neo4j_data = {
                    "nodes": nodes,
                    "relationships": relationships,
                    "metadata": {
                        "source": "neo4j",
                        "neo4j_uri": config.grag.neo4j_uri,
                        "neo4j_database": config.grag.neo4j_database,
                    },
                    "updated_at": __import__("datetime").datetime.now().isoformat(),
                }

                # 保存到文件（覆盖模式）
                with open(neo4j_memory_file, "w", encoding="utf-8") as f:
                    json.dump(neo4j_data, f, ensure_ascii=False, indent=2)

                logger.info(f"Neo4j数据已保存到: {neo4j_memory_file}")
                logger.info(f"下载统计: {len(nodes)} 个节点, {len(relationships)} 个关系")

                logger.info(
                    f"Neo4j data successfully downloaded to {neo4j_memory_file}: {len(nodes)} nodes, {len(relationships)} relationships"
                )
                return True

        except Exception as e:
            logger.error(f"Failed to load Neo4j data: {e}")
            logger.error(f"Neo4j数据下载失败: {e}")
            return False

    def upload_memory_package(self, memory_data: Dict[str, Any]) -> Dict[str, Any]:
        """将一组节点和关系批量写入Neo4j图谱。
        
        对于每个节点：检查是否已存在，存在则修改，不存在则创建。
        对于每个关系：检查是否已存在，存在则修改，不存在则创建。
        节点ID映射会自动更新到关系引用中。
        
        Args:
            memory_data: 包含 nodes 和 relations 的字典
            
        Returns:
            {"nodes": [...], "relations": [...]} 处理结果
        """
        if not self._ensure_connection():
            logger.warning("Neo4j未连接，跳过记忆上传")
            return {"nodes": [], "relations": []}

        nodes_list = memory_data.get("nodes", [])
        relations_list = memory_data.get("relations", [])
        
        logger.debug(f"记忆存储接收到数据: {json.dumps(memory_data, ensure_ascii=False, indent=2)}")
        logger.info(f"处理 {len(nodes_list)} 个节点和 {len(relations_list)} 个关系")
        
        try:
            with self.driver.session() as session:
                tx = session.begin_transaction()
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
                        
                        check_result = tx.run(check_query, node_id=node_id).single()
                        existing_node_id = check_result["existing_id"] if check_result else None
                        
                        if existing_node_id:
                            # 节点存在，调用modify_node修改节点属性
                            updates = {}
                            for key, value in node_info.items():
                                if key not in ["nodeId", "nodeType", "significance"]:  # 排除不应加入的字段
                                    updates[key] = value
                            # 排除所有Time时间节点，该节点不遵从modify_node逻辑，直接创建新节点
                            if node_type == "Time":
                                time_value = node_info.get("time", [])
                                new_node_id = self.create_node(
                                    session=tx,
                                    name="",
                                    node_type=node_type,
                                    time_str=time_value,
                                    time_type=node_info.get("time_type", "static"),
                                    trust=0.5,
                                    context=node_info.get("context", "reality现实"),
                                    note="无"
                                )
                                if new_node_id:
                                    logger.info(f"Created new Time node: {node_id} -> {new_node_id}")
                                    old_node_id = node["nodeId"]
                                    node["nodeId"] = new_node_id
                                    for relation in relations_list:
                                        if relation.get("startNode") == old_node_id:
                                            relation["startNode"] = new_node_id
                                        if relation.get("endNode") == old_node_id:
                                            relation["endNode"] = new_node_id
                                else:
                                    logger.warning(f"Failed to create Time node: {node_id}")
                                continue
                            
                            if updates:
                                result = self.modify_node(existing_node_id, updates)
                                if result and result != "InvalidModification":
                                    logger.info(f"Updated existing node: {node_id}")
                                elif result == "InvalidModification":
                                    # 修改被拒绝，回退到创建新节点
                                    logger.warning(f"Modify rejected for node {node_id}, falling back to create new node")
                                    fallback_time = node_info.get("time_str", node_info.get("time", []))
                                    if isinstance(fallback_time, str):
                                        fallback_time = [t.strip() for t in fallback_time.split(",") if t.strip()] if fallback_time else []
                                    new_node_id = self.create_node(
                                        session=tx,
                                        name=node_info.get("character_name", node_info.get("location_name", node_info.get("entity_name", node_info.get("name", "")))),
                                        node_type=node_type,
                                        time_str=fallback_time,
                                        time_type=node_info.get("time_type", "static"),
                                        trust=node_info.get("trust", 0.5),
                                        context=node_info.get("context", "reality现实"),
                                        note=node_info.get("note", "无")
                                    )
                                    if new_node_id:
                                        logger.info(f"Fallback created new {node_type} node: {node_id} -> {new_node_id}")
                                        old_node_id = node["nodeId"]
                                        node["nodeId"] = new_node_id
                                        for relation in relations_list:
                                            if relation.get("startNode") == old_node_id:
                                                relation["startNode"] = new_node_id
                                            if relation.get("endNode") == old_node_id:
                                                relation["endNode"] = new_node_id
                                    else:
                                        logger.warning(f"Fallback create also failed for {node_type} node: {node_id}")
                                else:
                                    logger.warning(f"Failed to update node: {node_id}")
                        else:
                            # 节点不存在，调用create_node创建节点
                            create_time = node_info.get("time_str", node_info.get("time", []))
                            if isinstance(create_time, str):
                                create_time = [t.strip() for t in create_time.split(",") if t.strip()] if create_time else []
                            new_node_id = self.create_node(
                                session=tx,
                                name=node_info.get("character_name", node_info.get("location_name", node_info.get("entity_name", node_info.get("name", "")))),
                                node_type=node_type,
                                time_str=create_time,
                                time_type=node_info.get("time_type", "static"),
                                trust=node_info.get("trust", 0.5),
                                context=node_info.get("context", "reality现实"),
                                note=node_info.get("note", "无")
                            )
                            
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

                # 先提交节点事务，确保新节点对后续 create_relation/modify_relation 的独立 session 可见
                tx.commit()
                logger.info(f"节点事务已提交: {len(nodes_list)} 个节点")

                # 遍历relationlist，处理关系（使用独立 session，节点已持久化）
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
                        importance = float(relation_info.get("importance", 0.5))
                        evidence = relation_info.get("evidence", "")
                        
                        if not start_node_id or not end_node_id:
                            logger.warning(f"Could not resolve node IDs for relation {relation_id}: {start_node_id} -> {end_node_id}")
                            continue
                        
                        # 检查关系是否已存在
                        check_relation_query = """
                        OPTIONAL MATCH (a)-[r]->(b) 
                        WHERE elementId(a) = $start_id AND elementId(b) = $end_id 
                        AND elementId(r) = $relation_id
                        RETURN elementId(r) as existing_relation_id
                        """
                        
                        check_relation_result = session.run(check_relation_query, 
                                                       start_id=start_node_id,
                                                       end_id=end_node_id,
                                                       relation_id=relation_id).single()
                        
                        existing_relation_id = check_relation_result["existing_relation_id"] if check_relation_result else None
                        
                        if existing_relation_id:
                            # 关系存在，调用modify_relation修改关系属性
                            result = self.modify_relation(existing_relation_id, predicate, source, confidence, relation_type, evidence, importance=importance)
                            if result:
                                logger.info(f"Updated existing relation: {relation_id}")
                            else:
                                logger.warning(f"Failed to update relation: {relation_id}")
                        else:
                            # 关系不存在，调用create_relation创建关系
                            new_relation_id = self.create_relation(
                                startNode_id=start_node_id,
                                endNode_id=end_node_id,
                                predicate=predicate,
                                source=source,
                                confidence=confidence,
                                importance=importance,
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
                
                logger.info(f"记忆记录处理完成: {len(nodes_list)} 个节点, {len(relations_list)} 个关系")
                return {"nodes": processed_nodes, "relations": processed_relations}
                
        except Exception as e:
            # 事务未提交时，session 关闭会自动回滚未提交的事务
            logger.error(f"记忆记录处理出错，事务已回滚: {e}")
            return {"nodes": [], "relations": []}

    def note_memory_used(self, relation_ids: list[str]) -> bool:
        """
        记忆被调用时触发，将记忆的significance调整至1。
        若importance不高于0.95，importance增加0.01
        """
        if not self._ensure_connection():
            logger.error("Cannot note memory used: No Neo4j connection")
            return False

        if not relation_ids:
            return True

        try:
            with self.driver.session() as session:
                result = session.run(
                    """
                    UNWIND $rel_ids AS rid
                    MATCH ()-[r]->()
                    WHERE elementId(r) = rid AND r.significance IS NOT NULL
                    SET r.significance = 1.0,
                        r.importance = CASE
                            WHEN r.importance IS NOT NULL AND r.importance <= 0.95
                            THEN r.importance + 0.01
                            ELSE r.importance
                        END
                    RETURN count(r) as updated_count
                    """,
                    rel_ids=relation_ids,
                )
                updated = result.single()["updated_count"]
                logger.debug(f"记忆调用标记完成: {updated}/{len(relation_ids)} 个关系已更新")
                return True

        except Exception as e:
            logger.error(f"记忆调用标记失败: {e}")
            return False

    def _get_checkpoint_date(self) -> Optional[str]:
        """
        从Neo4j中读取全局checkpoint日期字符串（格式 YYYYMMDD）。
        checkpoint 存储在一个 (:SystemMeta {key: 'daily_checkpoint'}) 节点上。

        Returns:
            Optional[str]: checkpoint日期字符串，不存在或失败返回None
        """
        if not self._ensure_connection():
            return None
        try:
            with self.driver.session() as session:
                result = session.run(
                    "MATCH (m:SystemMeta {key: 'daily_checkpoint'}) RETURN m.value AS value"
                ).single()
                return result["value"] if result else None
        except Exception as e:
            logger.error(f"Failed to get checkpoint date: {e}")
            return None

    def _set_checkpoint_date(self, date_str: str) -> bool:
        """
        在Neo4j中写入/更新全局checkpoint日期。

        Args:
            date_str: 日期字符串（格式 YYYYMMDD）
        Returns:
            bool: 是否成功
        """
        if not self._ensure_connection():
            return False
        try:
            with self.driver.session() as session:
                session.run(
                    """
                    MERGE (m:SystemMeta {key: 'daily_checkpoint'})
                    SET m.value = $date_str, m.updated_at = $now
                    """,
                    date_str=date_str,
                    now=datetime.now().isoformat(),
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set checkpoint date: {e}")
            return False

    def daily_checkpoint(self) -> bool:
        """
        每日检查点：
        1. 读取Neo4j中保存的checkpoint日期
        2. 若与当前日期不一致，先将全部节点和关系导出到
           data/memory_graph/<checkpoint日期>.jsonl
        3. 执行一次 memory_decay
        4. 更新checkpoint为当前日期

        Returns:
            bool: 是否执行了衰退流程（日期一致时返回False表示无需操作）
        """
        if not self._ensure_connection():
            logger.error("Cannot run daily checkpoint: No Neo4j connection")
            return False

        today_str = datetime.now().strftime("%Y%m%d")
        checkpoint_date = self._get_checkpoint_date()

        if checkpoint_date == today_str:
            logger.debug("Daily checkpoint already done for today, skipping")
            return False

        logger.info(f"Daily checkpoint triggered (last={checkpoint_date}, today={today_str})")

        # ---- 1. 导出当前全部节点和关系到本地 ----
        try:
            with self.driver.session() as session:
                nodes_result = session.run(
                    "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties"
                )
                nodes = []
                for record in nodes_result:
                    props = dict(record["properties"])
                    props.pop("embedding", None)  # 去掉embedding大字段
                    nodes.append({
                        "id": str(record["id"]),
                        "labels": record["labels"],
                        "properties": props,
                    })

                rels_result = session.run(
                    """
                    MATCH (a)-[r]->(b)
                    RETURN elementId(r) AS id, type(r) AS type,
                           elementId(a) AS start_node, elementId(b) AS end_node,
                           properties(r) AS properties
                    """
                )
                relationships = []
                for record in rels_result:
                    relationships.append({
                        "id": str(record["id"]),
                        "type": record["type"],
                        "start_node": str(record["start_node"]),
                        "end_node": str(record["end_node"]),
                        "properties": dict(record["properties"]),
                    })

            # 写入文件
            save_date = checkpoint_date if checkpoint_date else today_str
            log_dir = os.path.join(project_root, "data", "memory_graph")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f"{save_date}.jsonl")

            log_entry = {
                "nodes": nodes,
                "relationships": relationships,
                "timestamp": datetime.now().isoformat(),
            }
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False, indent=2) + "\n")

            logger.info(f"Checkpoint snapshot saved: {log_file} ({len(nodes)} nodes, {len(relationships)} relationships)")

        except Exception as e:
            logger.error(f"Failed to export snapshot during daily checkpoint: {e}")
            # 导出失败仍继续衰退，避免记忆无限膨胀

        # ---- 2. 执行记忆衰退 ----
        self.memory_decay()

        # ---- 3. 更新checkpoint日期 ----
        self._set_checkpoint_date(today_str)
        logger.info(f"Daily checkpoint completed, checkpoint updated to {today_str}")
        return True


# 全局实例
_kg_manager = None


def get_knowledge_graph_manager() -> KnowledgeGraphManager:
    """获取知识图谱管理器单例"""
    global _kg_manager
    if _kg_manager is None:
        _kg_manager = KnowledgeGraphManager()
    return _kg_manager

def clear_all_memory_interactive() -> bool:
    """便捷函数：交互式清空Neo4j中的全部记忆数据"""
    manager = get_knowledge_graph_manager()
    return manager.clear_all_memory()


def load_neo4j_data_to_file() -> bool:
    """便捷函数：检查Neo4j连接并将数据下载到neo4j_memory.json文件"""
    manager = get_knowledge_graph_manager()
    return manager.download_neo4j_data()