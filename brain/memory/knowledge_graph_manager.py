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
from litellm import OpenAI
from system.config import config
from typing import List, Dict, Any, Optional

# 获取项目根目录
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# API 配置
API_KEY = config.api.embedding_api_key
API_URL = config.api.embedding_base_url
MODEL = config.api.embedding_model
AI_NAME = config.system.ai_name
USERNAME = config.ui.username
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
        error_msg = f"[错误] {description}提示词文件不存在: {prompt_path}"
        print(error_msg)
        return ""

    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            print(f"[警告] {description}提示词文件为空: {prompt_path}")
        return content


MEMORY_RECORD_PROMPT = load_prompt_file("memory_record.txt", "记忆存储")


# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, AuthError, TransientError
except ImportError:
    print("Neo4j driver not installed. Please install with: pip install neo4j")
    sys.exit(1)

from system.config import config, is_neo4j_available

logger = logging.getLogger(__name__)


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
                connection_acquisition_timeout=30,  # 30 seconds
            )

            # 测试连接
            with self.driver.session() as session:
                result = session.run("RETURN 1 as test")
                test_value = result.single()["test"]
                if test_value == 1:
                    self.connected = True
                    logger.info("Successfully connected to Neo4j")
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

    def create_time_node(self, session, time_str: str) -> Optional[str]:
        """
        创建时间节点并建立层次关系
        自动根据时间完整性确定类型：没有年份为recurring，有年份为static
        标准格式：XXXX年XX月XX日XX点XX分XX秒XX

        Args:
            session: Neo4j session
            time_str: 时间字符串

        Returns:
            Optional[str]: 创建的最具体时间节点ID，失败返回None
        """
        if not time_str:
            return None
        logger.debug(f"Creating time node for: {time_str}")

        try:
            # 解析时间组件
            year_match = re.search(r"(\d{4})年", time_str)
            month_match = re.search(r"(\d{1,2})月", time_str)
            day_match = re.search(r"(\d{1,2})日", time_str)
            hour_match = re.search(r"(\d{1,2})点", time_str)
            sub_hour_match = re.search(r"点(.+)", time_str)

            # 自动确定时间类型：没有年份为recurring，有年份为static
            if not year_match:
                time_type = "recurring"
            else:
                time_type = "static"

            most_specific_node_id = None

            # 如果有年份，创建年份节点
            if year_match:
                year = year_match.group(1)
                year_name = f"{year}年"
                if time_type == "recurring":
                    year_name = f"[re]{year}年"

                # 创建年份节点 - 只记录到年份
                year_time_str = f"{year}年"  # 只记录年份
                
                # 生成embedding（使用time字段）
                year_embedding = self._generate_embedding(year_time_str)
                
                result = session.run(
                    """
                    MERGE (y:Time:Year {name: $year_name, time: $year_time_str})
                    SET y.node_type = 'Time',
                        y.type = $time_type,
                        y.embedding = $embedding
                    RETURN elementId(y) as node_id
                """,
                    year_name=year_name,
                    year_time_str=year_time_str,
                    time_type=time_type,
                    embedding=year_embedding,
                )
                record = result.single()
                if record:
                    most_specific_node_id = record["node_id"]

            if month_match:
                # 如果有月份
                if month_match:
                    month = month_match.group(1)
                    month = str(int(month))
                    month_name = f"{month}月"
                    if time_type == "recurring":
                        month_name = f"[re]{month}月"

                    # 创建月份节点 - 记录到年月或只记录月（recurring类型）
                    if time_type == "recurring":
                        month_time_str = f"{month}月"  # recurring类型只记录月份
                    else:
                        month_time_str = f"{year}年{month}月"  # static类型记录年月

                    # 生成embedding（使用time字段）
                    month_embedding = self._generate_embedding(month_time_str)

                    result = session.run(
                        """
                        MERGE (m:Time:Month {name: $month_name, time: $month_time_str})
                        SET m.node_type = 'Time',
                            m.type = $time_type,
                            m.embedding = $embedding
                        RETURN elementId(m) as node_id
                    """,
                        month_name=month_name,
                        month_time_str=month_time_str,
                        time_type=time_type,
                        embedding=month_embedding,
                    )
                    record = result.single()
                    if record:
                        most_specific_node_id = record["node_id"]

                    # 创建月份到年份的层次关系（年份节点在同一函数中已创建）
                    if year_match:
                        # 直接创建层次关系，无需检查存在性（年份节点刚刚创建）
                        session.run(
                            """
                            MATCH (m:Time:Month {name: $month_name, time: $month_time_str})
                            MATCH (y:Time:Year {name: $year_name, time: $year_time_str})
                            MERGE (m)-[r:BELONGS_TO]->(y)
                            SET r.hierarchy_type = 'month_to_year'
                        """,
                            month_name=month_name,
                            month_time_str=month_time_str,
                            year_name=year_name,
                            year_time_str=year_time_str,
                        )

                # 如果有日期
                if day_match:
                    day = day_match.group(1)
                    day = str(int(day))
                    day_name = f"{day}日"
                    if time_type == "recurring":
                        day_name = f"[re]{day}日"

                    # 创建日期节点
                    day_time_str = f"{day}日"
                    if month_match:
                        day_time_str = f"{month}月{day}日"
                        if year_match:
                            day_time_str = f"{year}年{month}月{day}日"

                    # 生成embedding（使用time字段）
                    day_embedding = self._generate_embedding(day_time_str)

                    result = session.run(
                        """
                        MERGE (d:Time:Day {name: $day_name, time: $day_time_str})
                        SET d.node_type = 'Time',
                            d.type = $time_type,
                            d.embedding = $embedding
                        RETURN elementId(d) as node_id
                    """,
                        day_name=day_name,
                        day_time_str=day_time_str,
                        time_type=time_type,
                        embedding=day_embedding,
                    )
                    record = result.single()
                    if record:
                        most_specific_node_id = record["node_id"]

                    # 创建日期到月份的层次关系（月份节点在同一函数中已创建）
                    if month_match:
                        # 直接创建层次关系，无需检查存在性（月份节点刚刚创建）
                        session.run(
                            """
                            MATCH (d:Time:Day {name: $day_name, time: $day_time_str})
                            MATCH (m:Time:Month {name: $month_name, time: $month_time_str})
                            MERGE (d)-[r:BELONGS_TO]->(m)
                            SET r.hierarchy_type = 'day_to_month'
                        """,
                            day_name=day_name,
                            day_time_str=day_time_str,
                            month_name=month_name,
                            month_time_str=month_time_str,
                        )

                # 如果没有具体日期，尝试解析"第X个星期X"格式
                elif not day_match:
                    # 匹配"第X个星期X"、"第X周星期X"等格式
                    week_pattern = re.search(
                        r"第(\d{1,2})[个周]?星期([一二三四五六七日天1234567])", time_str
                    )
                    if week_pattern:
                        week_number = week_pattern.group(1)
                        weekday = week_pattern.group(2)

                        # 将数字转换为中文
                        weekday_map = {
                            "1": "一",
                            "2": "二",
                            "3": "三",
                            "4": "四",
                            "5": "五",
                            "6": "六",
                            "7": "七",
                            "天": "日",
                        }
                        weekday = weekday_map.get(weekday, weekday)

                        day_name = f"第{week_number}周星期{weekday}"
                        if time_type == "recurring":
                            day_name = f"[re]第{week_number}周星期{weekday}"

                        # 创建周日期节点 - 根据类型记录对应精度
                        day_time_str = f"第{week_number}周星期{weekday}"
                        if month_match:
                            day_time_str = f"{month}月第{week_number}周星期{weekday}"
                            if year_match:
                                day_time_str = (
                                    f"{year}年{month}月第{week_number}周星期{weekday}"
                                )

                        # 生成embedding（使用time字段）
                        weekday_embedding = self._generate_embedding(day_time_str)

                        result = session.run(
                            """
                            MERGE (d:Time:WeekDay {name: $day_name, time: $day_time_str})
                            SET d.node_type = 'Time',
                                d.type = $time_type,
                                d.week_number = $week_number,
                                d.weekday = $weekday,
                                d.embedding = $embedding
                            RETURN elementId(d) as node_id
                        """,
                            day_name=day_name,
                            day_time_str=day_time_str,
                            time_type=time_type,
                            week_number=week_number,
                            weekday=weekday,
                            embedding=weekday_embedding,
                        )
                        record = result.single()
                        if record:
                            most_specific_node_id = record["node_id"]

                        # 创建WeekDay到月份的层次关系（月份节点在同一函数中已创建）
                        if month_match:
                            # 直接创建层次关系，无需检查存在性（月份节点刚刚创建）
                            session.run(
                                """
                                MATCH (d:Time:WeekDay {name: $day_name, time: $day_time_str})
                                MATCH (m:Time:Month {name: $month_name, time: $month_time_str})
                                MERGE (d)-[r:BELONGS_TO]->(m)
                                SET r.hierarchy_type = 'weekday_to_month'
                            """,
                                day_name=day_name,
                                day_time_str=day_time_str,
                                month_name=month_name,
                                month_time_str=month_time_str,
                            )

            # 如果有小时（在月份处理之后）
            if hour_match:
                hour = hour_match.group(1)
                hour = str(int(hour))
                hour_name = f"{hour}点"
                if time_type == "recurring":
                    hour_name = f"[re]{hour}点"

                # 创建小时节点 - 根据类型和已有信息记录对应精度
                hour_time_str = f"{hour}点"
                if day_match:
                    hour_time_str = f"{day}日{hour}点"
                    if month_match:
                        hour_time_str = f"{month}月{day}日{hour}点"
                        if year_match:
                            hour_time_str = f"{year}年{month}月{day}日{hour}点"

                # 生成embedding（使用time字段）
                hour_embedding = self._generate_embedding(hour_time_str)

                result = session.run(
                    """
                    MERGE (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                    SET h.node_type = 'Time',
                        h.type = $time_type,
                        h.embedding = $embedding
                    RETURN elementId(h) as node_id
                """,
                    hour_name=hour_name,
                    hour_time_str=hour_time_str,
                    time_type=time_type,
                    embedding=hour_embedding,
                )
                record = result.single()
                if record:
                    most_specific_node_id = record["node_id"]

                # 创建小时到日期的层次关系（日期节点在同一函数中已创建）
                if day_match:
                    day_name_for_check = f"{day_match.group(1)}日"
                    # 直接创建层次关系，无需检查存在性（日期节点刚刚创建）
                    # 需要构建对应的day_time_str
                    day_time_str_for_relation = f"{day_match.group(1)}日"
                    if month_match:
                        day_time_str_for_relation = f"{month}月{day_match.group(1)}日"
                        if year_match:
                            day_time_str_for_relation = (
                                f"{year}年{month}月{day_match.group(1)}日"
                            )

                    session.run(
                        """
                        MATCH (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                        MATCH (d:Time:Day {name: $day_name, time: $day_time_str})
                        MERGE (h)-[r:BELONGS_TO]->(d)
                        SET r.hierarchy_type = 'hour_to_day'
                    """,
                        hour_name=hour_name,
                        hour_time_str=hour_time_str,
                        day_name=day_name_for_check,
                        day_time_str=day_time_str_for_relation,
                    )
                elif not day_match:
                    # 如果没有具体日期但有周日期，检查WeekDay节点
                    week_pattern = re.search(
                        r"第(\d{1,2})[个周]?星期([一二三四五六七日天1234567])", time_str
                    )
                    if week_pattern:
                        week_number = week_pattern.group(1)
                        weekday = week_pattern.group(2)
                        weekday_map = {
                            "1": "一",
                            "2": "二",
                            "3": "三",
                            "4": "四",
                            "5": "五",
                            "6": "六",
                            "7": "七",
                            "天": "日",
                        }
                        weekday = weekday_map.get(weekday, weekday)
                        weekday_name = f"第{week_number}周星期{weekday}"

                        # 直接创建层次关系，WeekDay节点已在同一函数中创建
                        # 需要构建对应的weekday_time_str
                        weekday_time_str = f"第{week_number}周星期{weekday}"
                        if month_match:
                            weekday_time_str = (
                                f"{month}月第{week_number}周星期{weekday}"
                            )
                            if year_match:
                                weekday_time_str = (
                                    f"{year}年{month}月第{week_number}周星期{weekday}"
                                )

                        session.run(
                            """
                            MATCH (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                            MATCH (d:Time:WeekDay {name: $day_name, time: $weekday_time_str})
                            MERGE (h)-[r:BELONGS_TO]->(d)
                            SET r.hierarchy_type = 'hour_to_weekday'
                        """,
                            hour_name=hour_name,
                            hour_time_str=hour_time_str,
                            day_name=weekday_name,
                            weekday_time_str=weekday_time_str,
                        )

            # 如果有子小时单位，创建独立的SubHour节点
            if sub_hour_match:
                # 提取小时后的所有时间单位
                sub_hour_content = sub_hour_match.group(1).strip()
                sub_hour = sub_hour_content
                sub_hour_name = sub_hour
                if time_type == "recurring":
                    sub_hour_name = f"[re]{sub_hour}"

                # 创建SubHour节点
                sub_hour_time_str = time_str

                # 生成embedding（使用time字段）
                subhour_embedding = self._generate_embedding(sub_hour_time_str)

                result = session.run(
                    """
                    MERGE (sh:Time:SubHour {name: $sub_hour_name, time: $sub_hour_time_str})
                    SET sh.node_type = 'Time',
                        sh.type = $time_type,
                        sh.embedding = $embedding
                    RETURN elementId(sh) as node_id
                """,
                    sub_hour_name=sub_hour_name,
                    sub_hour_time_str=sub_hour_time_str,
                    time_type=time_type,
                    embedding=subhour_embedding,
                )
                record = result.single()
                if record:
                    most_specific_node_id = record["node_id"]

                # 创建SubHour到Hour的层次关系（Hour节点在同一函数中已创建）
                if hour_match:
                    hour_name_for_check = f"{hour_match.group(1)}点"
                    # 直接创建层次关系，无需检查存在性（Hour节点刚刚创建）
                    session.run(
                        """
                        MATCH (sh:Time:SubHour {name: $sub_hour_name, time: $sub_hour_time_str})
                        MATCH (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                        MERGE (sh)-[r:BELONGS_TO]->(h)
                        SET r.hierarchy_type = 'subhour_to_hour'
                    """,
                        sub_hour_name=sub_hour_name,
                        sub_hour_time_str=sub_hour_time_str,
                        hour_name=hour_name_for_check,
                        hour_time_str=hour_time_str,
                    )

            if most_specific_node_id:
                logger.debug(
                    f"Created hierarchical time node with ID: {most_specific_node_id}"
                )
                return most_specific_node_id
            else:
                logger.warning(f"No specific node created for time: {time_str}")
                return None

        except Exception as e:
            logger.error(f"Failed to create time node '{time_str}': {e}")
            # 回退到创建通用时间节点
            try:
                # 生成embedding（使用time字段）
                fallback_embedding = self._generate_embedding(time_str)
                
                result = session.run(
                    """
                    MERGE (t:Time {name: $time, time: $time})
                    SET t.node_type = 'Time',
                        t.type = $time_type,
                        t.embedding = $embedding
                    RETURN elementId(t) as node_id
                """,
                    time=time_str,
                    time_type=time_type,
                    embedding=fallback_embedding,
                )
                record = result.single()
                if record:
                    return record["node_id"]
                return None
            except Exception as fallback_e:
                logger.error(f"Failed to create fallback time node: {fallback_e}")
                return None

    def create_node(
        self,
        session,
        name: str,
        node_type: str,
        importance: float = 0.5,
        trust: float = 0.5,
        context: str = "reality",
        note: str = "无",
    ) -> Optional[str]:
        """
        创建节点，可能创建多种节点类型时通过此节点。
        """
        if node_type == "Character":
            return self.create_character_node(session, name, importance, trust, context)
        elif node_type == "Location":
            return self.create_location_node(session, name, context)
        elif node_type == "Entity":
            return self.create_entity_node(session, name, importance, context, note)
        else:
            logger.error(f"Unsupported node type: {node_type}")
            return None

    def create_character_node(
        self,
        session,
        name: str,
        importance: float = 0.5,
        trust: float = 0.5,
        context: str = "reality",
    ) -> Optional[str]:
        """
        创建角色节点

        Args:
            session: Neo4j session
            name: 角色名称
            importance: 重要程度 (默认0.5)
            trust: 信任度 (默认0.5)
            context: 上下文环境 (默认"reality")

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

            # 生成embedding（使用name字段）
            character_embedding = self._generate_embedding(name)

            result = session.run(
                """
                CREATE (c:Character {name: $name})
                SET c.created_at = $created_at,
                    c.node_type = 'Character',
                    c.importance = $importance,
                    c.trust = $trust,
                    c.context = $context,
                    c.last_updated = $last_updated,
                    c.significance = 1,
                    c.embedding = $embedding
                RETURN elementId(c) as node_id
            """,
                name=name,
                importance=importance,
                trust=trust,
                context=context,
                created_at=current_time,
                last_updated=current_time,
                embedding=character_embedding,
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
        self, session, name: str, context: str = "reality"
    ) -> Optional[str]:
        """
        创建地点节点

        Args:
            session: Neo4j session
            name: 地点名称
            context: 上下文环境 (默认"reality")

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

            # 生成embedding（使用name字段）
            location_embedding = self._generate_embedding(name)

            result = session.run(
                """
                CREATE (l:Location {name: $name})
                SET l.node_type = 'Location',
                    l.context = $context,
                    l.created_at = $created_at,
                    l.last_updated = $last_updated,
                    l.embedding = $embedding
                RETURN elementId(l) as node_id
            """,
                name=name,
                context=context,
                created_at=current_time,
                last_updated=current_time,
                embedding=location_embedding,
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
        importance: float = 0.5,
        context: str = "reality",
        note: str = "无",
    ) -> Optional[str]:
        """
        创建实体节点（事件，物品，概念等）

        Args:
            session: Neo4j session
            name: 实体名称
            importance: 重要程度 (默认0.5)
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

            # 生成embedding（使用name字段）
            entity_embedding = self._generate_embedding(name)

            result = session.run(
                """
                CREATE (e:Entity {name: $name})
                SET e.created_at = $created_at,
                    e.node_type = 'Entity',
                    e.importance = $importance,
                    e.context = $context,
                    e.note = $note,
                    e.last_updated = $last_updated,
                    e.significance = 1,
                    e.embedding = $embedding
                RETURN elementId(e) as node_id
            """,
                name=name,
                importance=importance,
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

    def modify_node(
        self, node_id: str, updates: dict, call: str = "passive"
    ) -> Optional[str]:
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
                current_node_name = check_result["node_name"]
                current_node_context = check_result["node_context"]

                # 如果nodeType = Time，则拒绝修改
                if current_node_type == "Time":
                    logger.warning(
                        f"Cannot modify Time node '{node_id}' - Time nodes are read-only"
                    )
                    return None

                if call == "passive":
                    # 被动更改的情况下，如果检测到name，context，nodeType和数据库中不一致，则拒绝并return
                    if "name" in updates and updates["name"] != current_node_name:
                        logger.warning(
                            f"Passive modification rejected: name mismatch for node '{node_id}'"
                        )
                        return None

                    if (
                        "context" in updates
                        and updates["context"] != current_node_context
                    ):
                        logger.warning(
                            f"Passive modification rejected: context mismatch for node '{node_id}'"
                        )
                        return None

                    if (
                        "node_type" in updates
                        and updates["node_type"] != current_node_type
                    ):
                        logger.warning(
                            f"Passive modification rejected: nodeType mismatch for node '{node_id}'"
                        )
                        return None

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
                    "significance",
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

                    # 更新修改的节点
                    self.update_node(
                        node_id, significance=0.99, Increase_importance=False
                    )

                    # 如果当前节点是Location类型，删除不应该有的属性
                    if "Location" in updated_labels:
                        location_cleanup_query = """
                        MATCH (n) WHERE elementId(n) = $node_id
                        REMOVE n.importance, n.significance
                        """
                        session.run(location_cleanup_query, node_id=node_id)
                        logger.debug(
                            f"Cleaned up importance and significance properties from Location node {node_id}"
                        )

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

                    # 更新相关节点的significance
                    self.update_node(
                        start_node_id, significance=0.99, Increase_importance=True
                    )
                    self.update_node(
                        end_node_id, significance=0.99, Increase_importance=True
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
        call: str = "passive",
    ) -> Optional[str]:
        """
        修改关系的属性

        Args:
            relation_id: Neo4j关系ID
            predicate: 关系谓词/类型
            source: 关系来源
            confidence: 置信度 (默认0.5)
            directivity: 关系方向 (默认'to_endNode'，'bidirectional'表示双向)
            evidence: 关系证据（为什么设置这个置信度，选填）
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
                current_predicate = (
                    current_properties.get("predicate") if current_properties else None
                )

                # 如果关系类型是BELONGS_TO，拒绝修改
                if rel_type_name == "BELONGS_TO":
                    logger.warning(
                        f"Cannot modify BELONGS_TO relation with ID '{relation_id}' - BELONGS_TO relations are protected"
                    )
                    return None

                if call == "passive":
                    # 被动更改的情况下，如果检测到predicate和数据库中不一致，则拒绝并return
                    if predicate != current_predicate:
                        logger.warning(
                            f"Cannot modify predicate in passive call mode: current='{current_predicate}', requested='{predicate}'"
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
                        new_confidence = 1 - (1 - current_confidence) * (
                            1 - confidence / 2
                        )
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

                update_query = """
                MATCH ()-[r]-() WHERE elementId(r) = $relation_id
                SET r.predicate = $predicate,
                    r.source = $source_list,
                    r.confidence = $new_confidence,
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

    def set_entity_time(
        self,
        node_id: str,
        time_str: str,
        source: str = "unknown",
        confidence: float = 0.5,
    ) -> Optional[str]:
        """
        为节点设置时间属性：（未使用
        Entity--HAPPENED_AT->Time

        Args:
            node_id: Neo4j节点ID
            time_str: 时间字符串

        Returns:
            Optional[str]: 设置成功返回节点ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot set node time: No Neo4j connection")
            return None

        if not node_id or not node_id.strip():
            logger.error("Node ID cannot be empty")
            return None

        if not time_str or not time_str.strip():
            logger.error("Time string cannot be empty")
            return None

        try:
            with self.driver.session() as session:
                # 检查节点是否存在且为Entity类型
                check_query = """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN n.name as node_name, labels(n) as node_labels
                """

                check_result = session.run(check_query, node_id=node_id).single()

                if not check_result:
                    logger.error(f"Node with ID '{node_id}' not found")
                    return None

                node_labels = check_result["node_labels"]
                if "Entity" not in node_labels:
                    logger.error(
                        f"Only Entity nodes can have time attributes. Node {node_id} has labels: {node_labels}"
                    )
                    return None

                # 删除已有的时间关联（覆盖逻辑）
                session.run(
                    """
                    MATCH (n) WHERE elementId(n) = $node_id
                    OPTIONAL MATCH (n)-[r:HAPPENED_AT]->(t:Time)
                    DELETE r
                """,
                    node_id=node_id,
                )

                # 创建时间节点
                time_node_id = self.create_time_node(session, time_str)

                if not time_node_id:
                    logger.error(f"Failed to create time node for '{time_str}'")
                    return None

                # 将时间节点与目标节点关联
                relation_id = self.create_relation(
                    startNode_id=node_id,
                    endNode_id=time_node_id,
                    predicate="HAPPENED_AT",
                    source=source,
                    confidence=confidence,
                    directivity="single",
                )

                if not relation_id:
                    logger.error("Failed to create HAPPENED_AT relationship")
                    return None

                logger.info(f"Set time '{time_str}' for Entity node {node_id}")

                # 更新实体节点
                self.update_node(node_id, significance=0.99, Increase_importance=True)

                return node_id

        except Exception as e:
            logger.error(f"Failed to set time for node '{node_id}': {e}")
            return None

    def set_location(
        self,
        node_id: str,
        location: str,
        source: str = "unknown",
        context: str = "reality",
    ) -> Optional[str]:
        """
        为节点设置地点属性，如：（暂未使用
        (Entity)-[HAPPENED_IN]->(Location)

        Args:
            node_id: Neo4j节点ID
            location: 地点名称

        Returns:
            Optional[str]: 设置成功返回node_id，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot set node location: No Neo4j connection")
            return None

        if not node_id or not node_id.strip():
            logger.error("Node ID cannot be empty")
            return None

        if not location or not location.strip():
            logger.error("Location name cannot be empty")
            return None

        try:
            with self.driver.session() as session:
                # 检查节点是否存在
                check_query = """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN n.name as node_name, labels(n) as node_labels
                """

                check_result = session.run(check_query, node_id=node_id).single()

                if not check_result:
                    logger.error(f"Node with ID '{node_id}' not found")
                    return None

                # 删除已有的地点关联（覆盖逻辑）
                session.run(
                    """
                    MATCH (n) WHERE elementId(n) = $node_id
                    OPTIONAL MATCH (n)-[r:HAPPENED_IN]->(l:Location)
                    DELETE r
                """,
                    node_id=node_id,
                )

                # 创建地点节点
                location_node_id = self.create_location_node(session, location, context)

                if not location_node_id:
                    logger.error(f"Failed to create location node for '{location}'")
                    return None

                # 将地点节点与目标节点关联
                relation_id = self.create_relation(
                    startNode_id=node_id,
                    endNode_id=location_node_id,
                    predicate="HAPPENED_IN",
                    source="set_location",
                    confidence=0.9,
                    directivity="single",
                )

                if not relation_id:
                    logger.error("Failed to create HAPPENED_AT relationship")
                    return None

                logger.info(f"Set location '{location}' for node {node_id}")

                # 更新节点
                self.update_node(node_id, significance=0.99, Increase_importance=True)

                return node_id

        except Exception as e:
            logger.error(f"Failed to set location for node '{node_id}': {e}")
            return None

    def update_node(
        self,
        node_id: str,
        significance: float = None,
        Increase_importance: bool = False,
    ) -> Optional[str]:
        """
        由AI上传五元组or日常记忆维护（主要是遗忘机制）所触发的节点更新

        Args:
            node_id: Neo4j节点ID
            significance: 记忆清晰度（0-1），如果不提供则自动计算衰减
            importance: 如果True则略微提高importance

        Returns:
            Optional[str]: 更新成功返回节点ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot update node significance: No Neo4j connection")
            return None

        if not node_id or not node_id.strip():
            logger.error("Node ID cannot be empty")
            return None

        try:
            with self.driver.session() as session:
                # 检查节点是否存在并获取当前属性
                check_query = """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN n.significance as current_significance, 
                       n.importance as importance,
                       properties(n) as current_properties
                """

                check_result = session.run(check_query, node_id=node_id).single()

                if not check_result:
                    logger.error(f"Node with ID '{node_id}' not found")
                    return None

                current_importance = check_result["importance"] or 0.5
                current_significance = check_result["current_significance"] or 1.0

                # 如果没有提供significance，则计算衰减值
                if significance is None:
                    # 新的significance = significance - (1-final_importance^2)/10
                    decay_factor = (1 - current_importance**2) / 10
                    new_significance = max(0.0, significance - decay_factor)
                else:
                    # 使用提供的significance值
                    new_significance = max(0.0, min(1.0, significance))

                if Increase_importance:
                    current_importance = (
                        current_importance + (1 - current_importance) * 0.1
                    )  # 提高余量的10%

                # 添加当前时间戳
                current_time = datetime.now().isoformat()

                # 更新节点的significance、importance和last_updated
                update_query = """
                MATCH (n) WHERE elementId(n) = $node_id
                SET n.significance = $new_significance,
                    n.importance = $current_importance,
                    n.last_updated = $last_updated
                RETURN elementId(n) as node_id, n.significance as updated_significance, n.importance as updated_importance
                """

                result = session.run(
                    update_query,
                    node_id=node_id,
                    new_significance=new_significance,
                    current_importance=current_importance,
                    last_updated=current_time,
                )

                updated_record = result.single()

                if updated_record:
                    updated_significance = updated_record["updated_significance"]
                    updated_importance = updated_record["updated_importance"]
                    logger.info(
                        f"Successfully updated node {node_id}: significance {current_significance} -> {updated_significance}, importance {current_importance} -> {updated_importance}"
                    )
                    return node_id
                else:
                    logger.error("Failed to update node significance and importance")
                    return None

        except Exception as e:
            logger.error(f"Failed to update node significance '{node_id}': {e}")
            return None

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

    def write_quintuple(
        self,
        subject: str,
        predicate: str,
        object: str,
        time: str = None,
        location: str = None,
        with_person: str = None,
        directivity: bool = True,
        importance: float = 0.5,
        confidence: float = 0.5,
        context: str = "reality",
        source: str = "user_input",
    ) -> Optional[str]:
        """
        创建五元组关系（已弃用
        输入(list of)：
            subject:主体（必填）,
            predicate:谓词/动作（必填），,
            object:客体/事件（选填）,
            time:事件发生的时间（选填）,
            location:事件发生的地点（选填）,
            with_person:[事件涉及到的其他人A, 事件涉及到的其他人B],
            directivity:主体与其他人的关系是否可逆（仅在有with_person时填写）,
            importance:这条信息的重要性（必填）,
            confidence:这条信息的真实性（必填）,
            context:这条信息的语境，如现实，某款游戏，某个故事（必填）,
            source:谁提供的信息（必填）
        返回：
            创建成功：返回主关系ID
            创建失败：返回None
        备注：
            概念流程中，如果A吃了苹果，B吃了苹果，这两个苹果虽然同名，但不应该是同一个节点。
            因而AI在准备创建五元组时，应该先查询到这些计划中的节点的信息，比如查询苹果，则查询到一个苹果和A相连，一个苹果和B相连。
            当试图存储，A吃的是一个红色的苹果，AI应该传输和A相连的苹果的节点ID，从而确保不会混淆。
            然具体实施过于复杂（尤其是要过两边AI），当前并不进行实际查询和匹配，主要依靠时间地点匹配和[最近提到]的机制来匹配。
        """

        if not self._ensure_connection():
            logger.error("Cannot write quintuple: No Neo4j connection")
            return None

        # 整理输入元素
        # 情况0：信息不全，无主体和谓词，直接返回None
        if not all([subject, predicate]):
            logger.error("Subject, predicate, and object are required")
            return None
        # 情况1：主谓宾，无时间地点 -> 三元组，关系建立(主语)--谓词->(宾语)
        # 无需调整
        # 情况2：主谓+时间or地点，无宾语 -> 不完全五元组，关系建立(主语)--has_action->(谓词)--HAPPENED_AT/IN->(时间/地点)
        # 将谓词作为宾语，谓词改为HAS_ACTION
        if not object:
            object = predicate
            predicate = "HAS_ACTION"
            logger.debug(
                f"No object provided, using action as object with predicate 'HAS_ACTION'"
            )

        # 情况3：主谓宾+时间or地点 -> 完整五元组
        # 无需调整，于是到这里为止至少确保后续都一定有主谓宾
        # 检查各项内容是否被标注：[时间](地点)<角色>
        def decode_annotation(text: str) -> tuple[str, str]:
            """解析标注并返回节点类型和纯净内容"""
            if text.startswith("[") and text.endswith("]"):
                return "Time", text[1:-1]
            elif text.startswith("(") and text.endswith(")"):
                return "Location", text[1:-1]
            elif text.startswith("<") and text.endswith(">"):
                return "Character", text[1:-1]
            else:
                return "Entity", text

        subject_type, subject = decode_annotation(subject)
        object_type, object = decode_annotation(object)

        # 开始事务处理
        try:
            with self.driver.session() as session:
                # 查找主体节点，提供客体作为signature_node帮助匹配
                subject_id = self._find_node(
                    session, subject, subject_type, None, None, object, context
                )
                if subject_id:
                    logger.debug(
                        f"Found existing subject node: {subject} with ID: {subject_id}"
                    )
                else:
                    # 没有找到匹配的节点，创建新的主体节点
                    subject_id = self.create_node(
                        session, subject, subject_type, context
                    )
                    if subject_id:
                        logger.debug(
                            f"Created new subject node: {subject} with ID: {subject_id}"
                        )
                    else:
                        logger.error(f"Failed to create subject node: {subject}")

                # 查找客体节点，提供时间，地点，主体作为signature_node帮助匹配
                object_id = self._find_node(
                    session, object, object_type, time, location, subject, context
                )
                if object_id:
                    logger.debug(
                        f"Found existing object node: {object} with ID: {object_id}"
                    )
                else:
                    # 没有找到匹配的节点，创建新的客体节点
                    object_id = self.create_node(session, object, object_type, context)
                    if object_id:
                        logger.debug(
                            f"Created new object node: {object} with ID: {object_id}"
                        )
                    else:
                        logger.error(f"Failed to create object node: {object}")

                if not subject_id or not object_id:
                    logger.error("Failed to create or find subject/object nodes")
                    return None

                # 创建主体到客体的关系（默认to_endNode方向）
                main_relation_id = self.create_relation(
                    startNode_id=subject_id,
                    endNode_id=object_id,
                    predicate=predicate,
                    source=source,
                    confidence=confidence,
                    directivity="single",
                )

                if not main_relation_id:
                    logger.error("Failed to create main relation")
                    return None

                # 处理时间节点（时间节点有特殊规则，可以直接使用）
                if time:
                    # 使用set_entity_time方法设置时间
                    result = self.set_entity_time(
                        object_id, time, source=source, confidence=confidence
                    )
                    if result:
                        logger.debug(f"Set time for object node {object}: {time}")
                    else:
                        logger.warning(
                            f"Failed to set time for object node {object}: {time}"
                        )

                # 处理地点节点
                if location:
                    # 直接使用set_location方法设置地点
                    result = self.set_location(
                        object_id, location, source=source, context=context
                    )
                    if result:
                        logger.debug(
                            f"Set location for object node {object}: {location}"
                        )
                    else:
                        logger.warning(
                            f"Failed to set location for object node {object}: {location}"
                        )

                # 处理同行者节点
                if with_person:
                    # 查找同行者节点
                    with_person_id = self._find_node(
                        session, with_person, "Character", None, None, object, context
                    )
                    if with_person_id:
                        logger.debug(
                            f"Found existing with_person node: {with_person} with ID: {with_person_id}"
                        )
                    else:
                        # 没有找到匹配的节点，创建新的同行者节点
                        with_person_id = self.create_entity_node(
                            session, with_person, importance, context
                        )
                        if with_person_id:
                            logger.debug(
                                f"Created new with_person node: {with_person} with ID: {with_person_id}"
                            )
                        else:
                            logger.error(
                                f"Failed to create with_person node: {with_person}"
                            )

                    if with_person_id:
                        # 根据directivity决定关系方向
                        if directivity:
                            # True: 被动参与 (to_endNode: 客体 -> 同行者)
                            # 使用"被+原动作"的形式
                            passive_predicate = f"被{predicate}"
                            with_relation_directivity = "to_endNode"
                            with_relation_id = self.create_relation(
                                startNode_id=object_id,
                                endNode_id=with_person_id,
                                predicate=passive_predicate,
                                source=source,
                                confidence=confidence,
                                directivity=with_relation_directivity,
                            )
                        else:
                            # False: 共事关系 (to_startNode: 同行者 -> 客体)
                            with_relation_directivity = "to_startNode"
                            with_relation_id = self.create_relation(
                                startNode_id=object_id,
                                endNode_id=with_person_id,
                                predicate=predicate,
                                source=source,
                                confidence=confidence,
                                directivity=with_relation_directivity,
                            )

                        logger.debug(
                            f"Created with_person relationship: {object} <-> {with_person}"
                        )

                logger.info(
                    f"Successfully created quintuple: {subject} -{predicate}-> {object}"
                )
                return main_relation_id

        except Exception as e:
            logger.error(f"Failed to write quintuple: {e}")
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

    def downloaod_memory(self, elements: Dict[str, Any]) -> bool:
        """
        保存指定的节点和关系到local_memory.json文件
        采用合并策略：更新已存在的项目，添加新项目，保留其他已有内容
        
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
            # 确保目录存在
            memory_dir = os.path.join(os.path.dirname(__file__), "memory_graph")
            os.makedirs(memory_dir, exist_ok=True)
            
            local_memory_file = os.path.join(memory_dir, "local_memory.json")
            
            # 读取现有的 local_memory.json 文件（如果存在）
            existing_data = {"nodes": [], "relationships": []}
            if os.path.exists(local_memory_file):
                try:
                    with open(local_memory_file, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            existing_data = json.loads(content)
                            logger.info(f"Loaded existing local memory: {len(existing_data.get('nodes', []))} nodes, {len(existing_data.get('relationships', []))} relationships")
                except Exception as e:
                    logger.warning(f"Failed to load existing local memory, starting fresh: {e}")
                    existing_data = {"nodes": [], "relationships": []}
            
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
                
                # 合并节点：使用字典去重，新节点覆盖旧节点
                nodes_dict = {node["id"]: node for node in existing_data.get("nodes", [])}
                added_count = 0
                updated_count = 0
                
                for node in new_nodes:
                    if node["id"] in nodes_dict:
                        updated_count += 1
                    else:
                        added_count += 1
                    nodes_dict[node["id"]] = node
                
                merged_nodes = list(nodes_dict.values())
                
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
                
                # 合并关系：使用字典去重，新关系覆盖旧关系
                relationships_dict = {rel["id"]: rel for rel in existing_data.get("relationships", [])}
                rel_added_count = 0
                rel_updated_count = 0
                
                for rel in new_relationships:
                    if rel["id"] in relationships_dict:
                        rel_updated_count += 1
                    else:
                        rel_added_count += 1
                    relationships_dict[rel["id"]] = rel
                
                merged_relationships = list(relationships_dict.values())
                
                # 构建最终数据结构
                local_memory_data = {
                    "nodes": merged_nodes,
                    "relationships": merged_relationships,
                    "metadata": {
                        "source": "local",
                        "saved_from": "neo4j",
                    },
                    "updated_at": datetime.now().isoformat(),
                }
                
                # 保存到文件（合并模式）
                with open(local_memory_file, "w", encoding="utf-8") as f:
                    json.dump(local_memory_data, f, ensure_ascii=False, indent=2)
                
                logger.info(
                    f"Memory merged to {local_memory_file}: "
                    f"{len(merged_nodes)} total nodes ({added_count} added, {updated_count} updated), "
                    f"{len(merged_relationships)} total relationships ({rel_added_count} added, {rel_updated_count} updated)"
                )
                
                print(f"💾 记忆已保存到: {local_memory_file}")
                print(f"📊 合并结果:")
                print(f"   节点: 新增 {added_count} 个, 更新 {updated_count} 个, 总计 {len(merged_nodes)} 个")
                print(f"   关系: 新增 {rel_added_count} 个, 更新 {rel_updated_count} 个, 总计 {len(merged_relationships)} 个")
                
                # 统计被过滤的关系数量
                filtered_count = len(relation_ids) - len(new_relationships)
                if filtered_count > 0:
                    print(f"⚠️  已过滤 {filtered_count} 个无效关系（节点不在保存列表中）")
                
                return True
        
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            print(f"❌ 保存记忆失败: {e}")
            return False

    def upload_memory(self, elements: Dict[str, Any]) -> bool:
        """
        从local_memory.json文件加载节点和关系到Neo4j数据库
        采用合并策略：通过属性匹配更新已存在的项目，创建新项目
        
        输入：
        {
            "nodes_ids": [],      # 节点ID列表（来自local_memory.json）
            "relation_ids": [],   # 关系ID列表（来自local_memory.json）
        }
        
        Returns:
            bool: 操作是否成功
        """
        if not self._ensure_connection():
            logger.error("Cannot upload memory: No Neo4j connection")
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
            # 确保目录存在
            memory_dir = os.path.join(os.path.dirname(__file__), "memory_graph")
            local_memory_file = os.path.join(memory_dir, "local_memory.json")
            
            # 检查local_memory.json是否存在
            if not os.path.exists(local_memory_file):
                logger.error(f"Local memory file not found: {local_memory_file}")
                print(f"❌ 本地记忆文件不存在: {local_memory_file}")
                return False
            
            # 读取local_memory.json文件
            with open(local_memory_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    logger.error("Local memory file is empty")
                    print("❌ 本地记忆文件为空")
                    return False
                local_memory_data = json.loads(content)
            
            # 提取节点和关系数据
            all_nodes = {node["id"]: node for node in local_memory_data.get("nodes", [])}
            all_relationships = {rel["id"]: rel for rel in local_memory_data.get("relationships", [])}
            
            with self.driver.session() as session:
                # 上传指定的节点
                nodes_to_upload = []
                if nodes_ids:
                    logger.info(f"Preparing {len(nodes_ids)} nodes for upload...")
                    for node_id in nodes_ids:
                        if node_id in all_nodes:
                            nodes_to_upload.append(all_nodes[node_id])
                        else:
                            logger.warning(f"Node with ID '{node_id}' not found in local memory, skipping")
                
                added_count = 0
                updated_count = 0
                id_updated = False  # 标记是否有ID被更新
                
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
                        
                        # 更新属性
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
                            # 更新本地内存中的节点ID
                            all_nodes[old_node_id]["id"] = new_node_id
                            # 同时需要更新字典的键
                            all_nodes[new_node_id] = all_nodes.pop(old_node_id)
                            
                            # 更新所有关系中引用该节点的ID
                            for rel in all_relationships.values():
                                if rel.get("start_node") == old_node_id:
                                    rel["start_node"] = new_node_id
                                if rel.get("end_node") == old_node_id:
                                    rel["end_node"] = new_node_id
                            
                            added_count += 1
                            id_updated = True
                            logger.info(f"Created node: {properties.get('name', 'Unknown')} (old_id: {old_node_id}, new_id: {new_node_id})")
                
                # 上传指定的关系
                relationships_to_upload = []
                if relation_ids:
                    logger.info(f"Preparing {len(relation_ids)} relationships for upload...")
                    for relation_id in relation_ids:
                        if relation_id in all_relationships:
                            rel = all_relationships[relation_id]
                            # 检查关系的起始和结束节点是否都存在
                            start_node_id = rel.get("start_node")
                            end_node_id = rel.get("end_node")
                            
                            # 验证节点是否存在于Neo4j中
                            check_nodes_query = """
                            MATCH (a), (b)
                            WHERE elementId(a) = $start_id AND elementId(b) = $end_id
                            RETURN elementId(a) as start_id, elementId(b) as end_id
                            """
                            check_result = session.run(check_nodes_query, start_id=start_node_id, end_id=end_node_id)
                            
                            if check_result.single():
                                relationships_to_upload.append(rel)
                            else:
                                logger.warning(
                                    f"Relationship '{relation_id}' has non-existent nodes in Neo4j "
                                    f"(start: {start_node_id}, end: {end_node_id}), skipping"
                                )
                        else:
                            logger.warning(f"Relationship with ID '{relation_id}' not found in local memory, skipping")
                
                rel_added_count = 0
                rel_updated_count = 0
                
                for rel in relationships_to_upload:
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
                        # 关系不存在，创建新关系并获取Neo4j生成的ID
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
                            new_rel_id = created_rel_record["id"]
                            # 更新本地内存中的关系ID
                            all_relationships[old_rel_id]["id"] = new_rel_id
                            # 同时需要更新字典的键
                            all_relationships[new_rel_id] = all_relationships.pop(old_rel_id)
                            
                            rel_added_count += 1
                            id_updated = True
                            logger.info(f"Created relationship: {rel_type} (old_id: {old_rel_id}, new_id: {new_rel_id})")
                
                # 如果有ID被更新，保存回local_memory.json
                if id_updated:
                    local_memory_data["nodes"] = list(all_nodes.values())
                    local_memory_data["relationships"] = list(all_relationships.values())
                    local_memory_data["updated_at"] = datetime.now().isoformat()
                    
                    with open(local_memory_file, "w", encoding="utf-8") as f:
                        json.dump(local_memory_data, f, ensure_ascii=False, indent=2)
                    
                    logger.info(f"Updated local memory file with new IDs from Neo4j")
                    print(f"💾 已同步Neo4j生成的ID到本地记忆文件")
                
                logger.info(
                    f"Memory uploaded to Neo4j: "
                    f"{added_count} nodes created, {updated_count} nodes updated, "
                    f"{rel_added_count} relationships created, {rel_updated_count} relationships updated"
                )
                
                print(f"📤 记忆已上传到Neo4j")
                print(f"📊 上传结果:")
                print(f"   节点: 新增 {added_count} 个, 更新 {updated_count} 个")
                print(f"   关系: 新增 {rel_added_count} 个, 更新 {rel_updated_count} 个")
                
                # 统计跳过的项目
                skipped_nodes = len(nodes_ids) - added_count - updated_count
                skipped_rels = len(relation_ids) - rel_added_count - rel_updated_count
                
                if skipped_nodes > 0:
                    print(f"⚠️  跳过 {skipped_nodes} 个节点（未找到或其他错误）")
                if skipped_rels > 0:
                    print(f"⚠️  跳过 {skipped_rels} 个关系（节点不存在或其他错误）")
                
                return True
        
        except Exception as e:
            logger.error(f"Failed to upload memory: {e}")
            print(f"❌ 上传记忆失败: {e}")
            return False

    def delete_from_local_memory(self, element_ids: List[str]) -> Dict[str, Any]:
        """
        从local_memory.json文件中删除指定的节点和关系
        节点标准格式：
            {
                "id": "node_id",
                "labels": [...],
                "properties": {...}
            }
        关系标准格式：
            {
                "id": "relation_id",
                "type": "RELATION_TYPE",
                "start_node": "start_node_id",
                "end_node": "end_node_id",
                "properties": {...}
            }
        
        Args:
            element_ids: 要删除的元素ID列表（节点ID或关系ID）
        
        Returns:
            {
              "success": bool,
              "error": Optional[str],
              "deleted_nodes": int,
              "deleted_relationships": int,
            }
        """
        if not element_ids:
            logger.warning("No element IDs provided for deletion")
            return {
                "success": False,
                "error": "No element IDs provided for deletion",
                "deleted_nodes": 0,
                "deleted_relationships": 0,
            }
        
        try:
            # 确保目录存在
            memory_dir = os.path.join(os.path.dirname(__file__), "memory_graph")
            local_memory_file = os.path.join(memory_dir, "local_memory.json")
            
            # 检查文件是否存在
            if not os.path.exists(local_memory_file):
                logger.warning(f"Local memory file not found: {local_memory_file}")
                print(f"⚠️  本地记忆文件不存在: {local_memory_file}")
                return {
                    "success": False,
                    "error": "Local memory file not found",
                    "deleted_nodes": 0,
                    "deleted_relationships": 0,
                }
            
            # 读取现有数据
            with open(local_memory_file, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
            
            nodes = memory_data.get("nodes", [])
            relationships = memory_data.get("relationships", [])
            
            # 记录初始数量
            initial_node_count = len(nodes)
            initial_rel_count = len(relationships)
            
            # 转换为字典以便快速查找和删除
            nodes_dict = {node["id"]: node for node in nodes}
            relationships_dict = {rel["id"]: rel for rel in relationships}
            
            # 统计删除信息
            deleted_nodes = []
            deleted_relationships = []
            auto_deleted_relationships = []  # 因节点删除而自动删除的关系
            
            # 分离节点ID和关系ID
            node_ids_to_delete = set()
            relation_ids_to_delete = set()
            
            for element_id in element_ids:
                if element_id in nodes_dict:
                    node_ids_to_delete.add(element_id)
                elif element_id in relationships_dict:
                    relation_ids_to_delete.add(element_id)
                else:
                    logger.warning(f"Element ID not found in local memory: {element_id}")
            
            # 删除节点
            for node_id in node_ids_to_delete:
                if node_id in nodes_dict:
                    deleted_nodes.append(nodes_dict[node_id])
                    del nodes_dict[node_id]
                    logger.info(f"Deleted node from local memory: {node_id}")
            
            # 查找并自动删除关联到被删除节点的关系
            for rel_id, rel in list(relationships_dict.items()):
                start_node = rel.get("start_node")
                end_node = rel.get("end_node")
                
                # 如果关系的起始节点或结束节点在删除列表中，自动删除该关系
                if start_node in node_ids_to_delete or end_node in node_ids_to_delete:
                    if rel_id not in relation_ids_to_delete:
                        auto_deleted_relationships.append(rel)
                    del relationships_dict[rel_id]
                    logger.info(f"Auto-deleted relationship from local memory (connected to deleted node): {rel_id}")
            
            # 删除显式指定的关系
            for relation_id in relation_ids_to_delete:
                if relation_id in relationships_dict:
                    deleted_relationships.append(relationships_dict[relation_id])
                    del relationships_dict[relation_id]
                    logger.info(f"Deleted relationship from local memory: {relation_id}")
            
            # 转换回列表
            remaining_nodes = list(nodes_dict.values())
            remaining_relationships = list(relationships_dict.values())
            
            # 更新数据结构
            memory_data["nodes"] = remaining_nodes
            memory_data["relationships"] = remaining_relationships
            memory_data["updated_at"] = datetime.now().isoformat()
            
            # 保存回文件
            with open(local_memory_file, "w", encoding="utf-8") as f:
                json.dump(memory_data, f, ensure_ascii=False, indent=2)
            
            # 输出统计信息
            total_deleted_nodes = len(deleted_nodes)
            total_deleted_rels = len(deleted_relationships) + len(auto_deleted_relationships)
            
            print(f"✅ 本地记忆删除完成")
            print(f"📊 删除统计:")
            print(f"   节点: 删除 {total_deleted_nodes} 个 (剩余 {len(remaining_nodes)} 个)")
            print(f"   关系: 删除 {total_deleted_rels} 个 (直接删除 {len(deleted_relationships)} 个, 自动删除 {len(auto_deleted_relationships)} 个, 剩余 {len(remaining_relationships)} 个)")
            
            if auto_deleted_relationships:
                print(f"ℹ️  因节点删除自动删除了 {len(auto_deleted_relationships)} 个关联关系")
            
            logger.info(
                f"Local memory deletion completed: "
                f"{total_deleted_nodes} nodes deleted, "
                f"{total_deleted_rels} relationships deleted "
                f"({len(deleted_relationships)} explicit + {len(auto_deleted_relationships)} auto)"
            )
            
            return {
                "success": True,
                "error": None,
                "deleted_nodes": total_deleted_nodes,
                "deleted_relationships": total_deleted_rels,
            }
        
        except Exception as e:
            logger.error(f"Failed to delete from local memory: {e}")
            print(f"❌ 从本地记忆删除失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "deleted_nodes": 0,
                "deleted_relationships": 0,
            }

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
        print("\n正在清空Neo4j数据库...")

        if not self._ensure_connection():
            logger.error("Cannot clear all memory: No Neo4j connection")
            print("❌ 错误：无法连接到Neo4j数据库")
            return False

        try:
            with self.driver.session() as session:
                # 获取清空前的统计信息
                stats_before = self.get_statistics()
                if "error" not in stats_before:
                    total_nodes = stats_before.get("nodes", {}).get("total", 0)
                    total_rels = stats_before.get("relationships", {}).get("total", 0)
                    print(f"清空前统计：{total_nodes} 个节点，{total_rels} 个关系")

                # 删除所有关系和节点
                result = session.run("MATCH (n) DETACH DELETE n")

                print("✅ Neo4j数据库已完全清空")
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
                    print(
                        f"清空后确认：{remaining_nodes} 个节点，{remaining_rels} 个关系"
                    )

                return True

        except Exception as e:
            logger.error(f"Failed to clear all memory: {e}")
            print(f"❌ 清空操作失败：{e}")
            return False

    def download_neo4j_data(self) -> bool:
        """检查Neo4j连接并将数据下载到neo4j_memory.json文件

        Returns:
            bool: 操作是否成功
        """
        # 检查Neo4j连接
        if not self._ensure_connection():
            logger.error("Cannot load Neo4j data: No Neo4j connection available")
            print("❌ 错误：无法连接到Neo4j数据库")
            return False

        print("✅ Neo4j连接正常，正在同步数据...")
        logger.info("Neo4j connection established, starting data download")

        try:
            # 确保目录存在
            neo4j_memory_dir = os.path.join(os.path.dirname(__file__), "memory_graph")
            os.makedirs(neo4j_memory_dir, exist_ok=True)

            neo4j_memory_file = os.path.join(neo4j_memory_dir, "neo4j_memory.json")

            with self.driver.session() as session:
                # 加载所有节点
                print("📥 正在下载节点数据...")
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
                print("🔗 正在下载关系数据...")
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

                print(f"💾 Neo4j数据已保存到: {neo4j_memory_file}")
                print(f"📊 下载统计: {len(nodes)} 个节点, {len(relationships)} 个关系")

                logger.info(
                    f"Neo4j data successfully downloaded to {neo4j_memory_file}: {len(nodes)} nodes, {len(relationships)} relationships"
                )
                return True

        except Exception as e:
            logger.error(f"Failed to load Neo4j data: {e}")
            print(f"❌ Neo4j数据下载失败: {e}")
            return False


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