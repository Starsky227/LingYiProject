#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识图谱管理器：
- 负责将三元组和五元组数据写入 Neo4j 数据库
- 提供统一的图谱操作接口
- 支持批量写入和单条写入
- 自动处理节点创建和关系建立
"""

import os
import re
import sys
import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import List, Dict, Any, Optional

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
                connection_acquisition_timeout=30  # 30 seconds
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
    
    def create_time_node(self, session, time_str: str) -> Optional[str]:
        """
        创建时间节点并建立层次关系
        自动根据时间完整性确定类型：没有年份为recurring，有年份为static
        标准格式：XXXX年XX月XX日XX点XX分XX秒XX
        
        Args:
            session: Neo4j session
            time_str: 时间字符串
            
        Returns:
            Optional[str]: 创建的最具体时间节点名称，失败返回None
        """
        if not time_str:
            return None
        logger.debug(f"Creating time node for: {time_str}")

        try:
            # 解析时间组件
            year_match = re.search(r'(\d{4})年', time_str)
            month_match = re.search(r'(\d{1,2})月', time_str)
            day_match = re.search(r'(\d{1,2})日', time_str)
            hour_match = re.search(r'(\d{1,2})点', time_str)
            sub_hour_match = re.search(r'点(.+)', time_str)
            
            # 自动确定时间类型：没有年份为recurring，有年份为static
            if not year_match:
                time_type = 'recurring'
            else:
                time_type = 'static'

            # 如果有年份，创建年份节点
            if year_match:
                year = year_match.group(1)
                year_name = f"{year}年"
                if time_type == 'recurring':
                    year_name = f"[re]{year}年"
                most_specific_node = year_name
                
                # 创建年份节点 - 只记录到年份
                year_time_str = f"{year}年"  # 只记录年份
                session.run("""
                    MERGE (y:Time:Year {name: $year_name, time: $year_time_str})
                    SET y.node_type = 'Time',
                        y.type = $time_type
                """, 
                    year_name=year_name,
                    year_time_str=year_time_str,
                    time_type=time_type
                )
            
            if month_match:
            # 如果有月份
                if month_match:
                    month = month_match.group(1)
                    month = str(int(month))
                    month_name = f"{month}月"
                    if time_type == 'recurring':
                        month_name = f"[re]{month}月"
                    most_specific_node = month_name
                    
                    # 创建月份节点 - 记录到年月或只记录月（recurring类型）
                    if time_type == 'recurring':
                        month_time_str = f"{month}月"  # recurring类型只记录月份
                    else:
                        month_time_str = f"{year}年{month}月"  # static类型记录年月
                    
                    session.run("""
                        MERGE (m:Time:Month {name: $month_name, time: $month_time_str})
                        SET m.node_type = 'Time',
                            m.type = $time_type
                    """, 
                        month_name=month_name,
                        month_time_str=month_time_str,
                        time_type=time_type
                    )
                    
                    # 创建月份到年份的层次关系（年份节点在同一函数中已创建）
                    if year_match:
                        # 直接创建层次关系，无需检查存在性（年份节点刚刚创建）
                        session.run("""
                            MATCH (m:Time:Month {name: $month_name, time: $month_time_str})
                            MATCH (y:Time:Year {name: $year_name, time: $year_time_str})
                            MERGE (m)-[r:BELONGS_TO]->(y)
                            SET r.hierarchy_type = 'month_to_year'
                        """, 
                            month_name=month_name,
                            month_time_str=month_time_str,
                            year_name=year_name,
                            year_time_str=year_time_str
                        )
                
                # 如果有日期
                if day_match:
                    day = day_match.group(1)
                    day = str(int(day))
                    day_name = f"{day}日"
                    if time_type == 'recurring':
                        day_name = f"[re]{day}日"
                    most_specific_node = day_name
                    
                    # 创建日期节点
                    day_time_str = f"{day}日"
                    if month_match:
                        day_time_str = f"{month}月{day}日"
                        if year_match:
                            day_time_str = f"{year}年{month}月{day}日"
                    
                    session.run("""
                        MERGE (d:Time:Day {name: $day_name, time: $day_time_str})
                        SET d.node_type = 'Time',
                            d.type = $time_type
                    """, 
                        day_name=day_name,
                        day_time_str=day_time_str,
                        time_type=time_type
                    )
                    
                    # 创建日期到月份的层次关系（月份节点在同一函数中已创建）
                    if month_match:
                        # 直接创建层次关系，无需检查存在性（月份节点刚刚创建）
                        session.run("""
                            MATCH (d:Time:Day {name: $day_name, time: $day_time_str})
                            MATCH (m:Time:Month {name: $month_name, time: $month_time_str})
                            MERGE (d)-[r:BELONGS_TO]->(m)
                            SET r.hierarchy_type = 'day_to_month'
                        """, 
                            day_name=day_name,
                            day_time_str=day_time_str,
                            month_name=month_name,
                            month_time_str=month_time_str
                        )
                
                # 如果没有具体日期，尝试解析"第X个星期X"格式
                elif not day_match:
                    # 匹配"第X个星期X"、"第X周星期X"等格式
                    week_pattern = re.search(r'第(\d{1,2})[个周]?星期([一二三四五六七日天1234567])', time_str)
                    if week_pattern:
                        week_number = week_pattern.group(1)
                        weekday = week_pattern.group(2)
                        
                        # 将数字转换为中文
                        weekday_map = {'1': '一', '2': '二', '3': '三', '4': '四', 
                                     '5': '五', '6': '六', '7': '七', '天': '日'}
                        weekday = weekday_map.get(weekday, weekday)
                        
                        day_name = f"第{week_number}周星期{weekday}"
                        if time_type == 'recurring':
                            day_name = f"[re]第{week_number}周星期{weekday}"
                        most_specific_node = day_name
                        
                        # 创建周日期节点 - 根据类型记录对应精度
                        day_time_str = f"第{week_number}周星期{weekday}"
                        if month_match:
                            day_time_str = f"{month}月第{week_number}周星期{weekday}"
                            if year_match:
                                day_time_str = f"{year}年{month}月第{week_number}周星期{weekday}"
                        
                        session.run("""
                            MERGE (d:Time:WeekDay {name: $day_name, time: $day_time_str})
                            SET d.node_type = 'Time',
                                d.type = $time_type,
                                d.week_number = $week_number,
                                d.weekday = $weekday
                        """, 
                            day_name=day_name,
                            day_time_str=day_time_str,
                            time_type=time_type,
                            week_number=week_number,
                            weekday=weekday
                        )
                        
                        # 创建WeekDay到月份的层次关系（月份节点在同一函数中已创建）
                        if month_match:
                            # 直接创建层次关系，无需检查存在性（月份节点刚刚创建）
                            session.run("""
                                MATCH (d:Time:WeekDay {name: $day_name, time: $day_time_str})
                                MATCH (m:Time:Month {name: $month_name, time: $month_time_str})
                                MERGE (d)-[r:BELONGS_TO]->(m)
                                SET r.hierarchy_type = 'weekday_to_month'
                            """, 
                                day_name=day_name,
                                day_time_str=day_time_str,
                                month_name=month_name,
                                month_time_str=month_time_str
                            )
            
            # 如果有小时（在月份处理之后）
            if hour_match:
                hour = hour_match.group(1)
                hour = str(int(hour))
                hour_name = f"{hour}点"
                if time_type == 'recurring':
                    hour_name = f"[re]{hour}点"
                most_specific_node = hour_name
                
                # 创建小时节点 - 根据类型和已有信息记录对应精度
                hour_time_str = f"{hour}点"
                if day_match:
                    hour_time_str = f"{day}日{hour}点"
                    if month_match:
                        hour_time_str = f"{month}月{day}日{hour}点"
                        if year_match:
                            hour_time_str = f"{year}年{month}月{day}日{hour}点"
                
                session.run("""
                    MERGE (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                    SET h.node_type = 'Time',
                        h.type = $time_type
                """, 
                    hour_name=hour_name,
                    hour_time_str=hour_time_str,
                    time_type=time_type
                )
                
                # 创建小时到日期的层次关系（日期节点在同一函数中已创建）
                if day_match:
                    day_name_for_check = f"{day_match.group(1)}日"
                    # 直接创建层次关系，无需检查存在性（日期节点刚刚创建）
                    # 需要构建对应的day_time_str
                    day_time_str_for_relation = f"{day_match.group(1)}日"
                    if month_match:
                        day_time_str_for_relation = f"{month}月{day_match.group(1)}日"
                        if year_match:
                            day_time_str_for_relation = f"{year}年{month}月{day_match.group(1)}日"
                    
                    session.run("""
                        MATCH (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                        MATCH (d:Time:Day {name: $day_name, time: $day_time_str})
                        MERGE (h)-[r:BELONGS_TO]->(d)
                        SET r.hierarchy_type = 'hour_to_day'
                    """, 
                        hour_name=hour_name,
                        hour_time_str=hour_time_str,
                        day_name=day_name_for_check,
                        day_time_str=day_time_str_for_relation
                    )
                elif not day_match:
                    # 如果没有具体日期但有周日期，检查WeekDay节点
                    week_pattern = re.search(r'第(\d{1,2})[个周]?星期([一二三四五六七日天1234567])', time_str)
                    if week_pattern:
                        week_number = week_pattern.group(1)
                        weekday = week_pattern.group(2)
                        weekday_map = {'1': '一', '2': '二', '3': '三', '4': '四', 
                                     '5': '五', '6': '六', '7': '七', '天': '日'}
                        weekday = weekday_map.get(weekday, weekday)
                        weekday_name = f"第{week_number}周星期{weekday}"
                        
                        # 直接创建层次关系，WeekDay节点已在同一函数中创建
                        # 需要构建对应的weekday_time_str
                        weekday_time_str = f"第{week_number}周星期{weekday}"
                        if month_match:
                            weekday_time_str = f"{month}月第{week_number}周星期{weekday}"
                            if year_match:
                                weekday_time_str = f"{year}年{month}月第{week_number}周星期{weekday}"
                        
                        session.run("""
                            MATCH (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                            MATCH (d:Time:WeekDay {name: $day_name, time: $weekday_time_str})
                            MERGE (h)-[r:BELONGS_TO]->(d)
                            SET r.hierarchy_type = 'hour_to_weekday'
                        """, 
                            hour_name=hour_name,
                            hour_time_str=hour_time_str,
                            day_name=weekday_name,
                            weekday_time_str=weekday_time_str
                        )
                    
            # 如果有子小时单位，创建独立的SubHour节点
            if sub_hour_match:
                # 提取小时后的所有时间单位
                sub_hour_content = sub_hour_match.group(1).strip()
                sub_hour = sub_hour_content
                sub_hour_name = sub_hour
                if time_type == 'recurring':
                    sub_hour_name = f"[re]{sub_hour}"
                most_specific_node = sub_hour_name
                    
                # 创建SubHour节点
                sub_hour_time_str = time_str

                session.run("""
                    MERGE (sh:Time:SubHour {name: $sub_hour_name, time: $sub_hour_time_str})
                    SET sh.node_type = 'Time',
                        sh.type = $time_type
                """, 
                    sub_hour_name=sub_hour_name,
                    sub_hour_time_str=sub_hour_time_str,
                    time_type=time_type
                )
                
                # 创建SubHour到Hour的层次关系（Hour节点在同一函数中已创建）
                if hour_match:
                    hour_name_for_check = f"{hour_match.group(1)}点"
                    # 直接创建层次关系，无需检查存在性（Hour节点刚刚创建）
                    session.run("""
                        MATCH (sh:Time:SubHour {name: $sub_hour_name, time: $sub_hour_time_str})
                        MATCH (h:Time:Hour {name: $hour_name, time: $hour_time_str})
                        MERGE (sh)-[r:BELONGS_TO]->(h)
                        SET r.hierarchy_type = 'subhour_to_hour'
                    """, 
                        sub_hour_name=sub_hour_name,
                        sub_hour_time_str=sub_hour_time_str,
                        hour_name=hour_name_for_check,
                        hour_time_str=hour_time_str
                    )
            
            logger.debug(f"Created hierarchical time node, most specific node: {most_specific_node}")
            return most_specific_node
            
        except Exception as e:
            logger.error(f"Failed to create time node '{time_str}': {e}")
            # 回退到创建通用时间节点
            try:
                session.run("""
                    MERGE (t:Time {name: $time, time: $time})
                    SET t.node_type = 'Time',
                        t.type = $time_type
                """, 
                    time=time_str,
                    time_type=time_type
                )
                return time_str
            except Exception as fallback_e:
                logger.error(f"Failed to create fallback time node: {fallback_e}")
                return None
    
    def create_character_node(self, session, name: str, importance: float = 0.5, trust: float = 0.5) -> Optional[str]:
        """
        创建角色节点
        
        Args:
            session: Neo4j session
            name: 角色名称
            importance: 重要程度 (默认0.5)
            trust: 信任度 (默认0.5)
            
        Returns:
            Optional[str]: 创建的角色节点名称，失败返回None
        """
        if not name or not name.strip():
            logger.warning("Character name cannot be empty")
            return None
            
        name = name.strip()
        logger.debug(f"Creating character node for: {name}")
        
        try:
            current_time = datetime.now().isoformat()
            
            session.run("""
                CREATE (c:Character {name: $name})
                SET c.created_at = $created_at,
                    c.node_type = 'Character',
                    c.importance = $importance,
                    c.trust = $trust,
                    c.last_updated = $last_updated,
                    c.significance = 1
            """, 
                name=name,
                importance=importance,
                trust=trust,
                created_at=current_time,
                last_updated=current_time
            )
            
            logger.debug(f"Created character node: {name}")
            return name
            
        except Exception as e:
            logger.error(f"Failed to create character node '{name}': {e}")
            return None
    

    def create_location_node(self, session, name: str) -> Optional[str]:
        """
        创建地点节点

        Args:
            name: 地点名称
        """
        if not name or not name.strip():
            logger.warning("Location name cannot be empty")
            return None
            
        name = name.strip()
        logger.debug(f"Creating location node for: {name}")
        
        try:
            current_time = datetime.now().isoformat()
            
            session.run("""
                CREATE (l:Location {name: $name})
                SET l.node_type = 'Location'
            """, 
                name=name,
                created_at=current_time,
                last_updated=current_time
            )
            
            logger.debug(f"Created location node: {name}")
            return name
            
        except Exception as e:
            logger.error(f"Failed to create location node '{name}': {e}")
            return None


    def create_entity_node(self, session, name: str, importance: float = 0.5, note: str = "无") -> Optional[str]:
        """
        创建实体节点（事件，物品，概念等）
        
        Args:
            session: Neo4j session
            name: 实体名称
            importance: 重要程度 (默认0.5)
            note: 备注 (默认"无")
            
        Returns:
            Optional[str]: 创建的实体节点名称，失败返回None
        """
        if not name or not name.strip():
            logger.warning("Entity name cannot be empty")
            return None
            
        name = name.strip()
        logger.debug(f"Creating entity node for: {name}")
        
        try:
            current_time = datetime.now().isoformat()
            
            session.run("""
                CREATE (e:Entity {name: $name})
                SET e.created_at = $created_at,
                    e.node_type = 'Entity',
                    e.importance = $importance,
                    e.note = $note,
                    e.last_updated = $last_updated,
                    e.significance = 1
            """, 
                name=name,
                importance=importance,
                note=note,
                created_at=current_time,
                last_updated=current_time
            )
            
            logger.debug(f"Created entity node: {name}")
            return name
            
        except Exception as e:
            logger.error(f"Failed to create entity node '{name}': {e}")
            return None
    

    def create_relation(self, node_a_id: str, node_b_id: str, predicate: str, source: str, 
                     confidence: float = 0.5, directivity: str = "to_B") -> Optional[str]:
        """
        在两个节点之间创建关系
        
        Args:
            node_a_id: 第一个节点的Neo4j元素ID
            node_b_id: 第二个节点的Neo4j元素ID
            predicate: 关系谓词/类型
            source: 关系来源
            confidence: 置信度 (默认0.5)
            directivity: 关系方向 (默认'to_B'，即A->B；'to_A'表示B->A；'bidirectional'表示双向)
            
        Returns:
            Optional[str]: 成功返回关系ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot connect nodes: No Neo4j connection")
            return None
        
        if not all([node_a_id, node_b_id, predicate, source]):
            logger.error("Missing required parameters for connecting nodes")
            return None

        try:
            with self.driver.session() as session:
                # 首先验证两个节点是否存在
                validate_query = """
                OPTIONAL MATCH (a) WHERE elementId(a) = $node_a_id
                OPTIONAL MATCH (b) WHERE elementId(b) = $node_b_id
                RETURN a IS NOT NULL as a_exists, b IS NOT NULL as b_exists,
                       a.name as a_name, b.name as b_name,
                       labels(a) as a_labels, labels(b) as b_labels
                """
                
                validation_result = session.run(validate_query, 
                                              node_a_id=node_a_id, 
                                              node_b_id=node_b_id).single()
                
                if not validation_result["a_exists"]:
                    logger.error(f"Node A with ID '{node_a_id}' not found")
                    return None
                    
                if not validation_result["b_exists"]:
                    logger.error(f"Node B with ID '{node_b_id}' not found")
                    return None
                
                # 根据方向性确定源节点和目标节点
                if directivity == "to_A":
                    source_id, target_id = node_b_id, node_a_id
                    direction_desc = f"{validation_result['b_name']} -> {validation_result['a_name']}"
                elif directivity == "to_B":
                    source_id, target_id = node_a_id, node_b_id
                    direction_desc = f"{validation_result['a_name']} -> {validation_result['b_name']}"
                elif directivity == "bidirectional":
                    # 对于双向关系，创建两个单向关系
                    source_id, target_id = node_a_id, node_b_id
                    direction_desc = f"{validation_result['a_name']} <-> {validation_result['b_name']}"
                else:
                    logger.error(f"Invalid directivity value: {directivity}")
                    return None
                
                # 处理关系类型名称，确保符合Neo4j关系类型命名规范
                predicate_safe = predicate.replace(" ", "_").replace("-", "_").upper()
                if not predicate_safe.replace("_", "").isalnum():
                    predicate_safe = "CONNECTED_TO"  # 回退到通用关系类型
                
                current_time = datetime.now().isoformat()
                
                # 删除已存在的相同方向关系，避免重复
                if directivity == "bidirectional":
                    # 删除双向关系
                    session.run("""
                        MATCH (a) WHERE elementId(a) = $node_a_id
                        MATCH (b) WHERE elementId(b) = $node_b_id
                        OPTIONAL MATCH (a)-[r1]->(b) WHERE r1.directivity = 'to_B'
                        OPTIONAL MATCH (b)-[r2]->(a) WHERE r2.directivity = 'to_A'
                        DELETE r1, r2
                    """, node_a_id=node_a_id, node_b_id=node_b_id)
                elif directivity == "to_A":
                    # 删除B->A方向的关系
                    session.run("""
                        MATCH (a) WHERE elementId(a) = $node_b_id
                        MATCH (b) WHERE elementId(b) = $node_a_id
                        OPTIONAL MATCH (a)-[r]->(b) WHERE r.directivity = 'to_A'
                        DELETE r
                    """, node_a_id=node_a_id, node_b_id=node_b_id)
                else:  # directivity == "to_B"
                    # 删除A->B方向的关系
                    session.run("""
                        MATCH (a) WHERE elementId(a) = $node_a_id
                        MATCH (b) WHERE elementId(b) = $node_b_id
                        OPTIONAL MATCH (a)-[r]->(b) WHERE r.directivity = 'to_B'
                        DELETE r
                    """, node_a_id=node_a_id, node_b_id=node_b_id)
                
                # 如果是双向关系，直接创建两个单向关系
                if directivity == "bidirectional":
                    # 创建 A->B 关系 (to_B)
                    to_b_query = f"""
                    MATCH (source) WHERE elementId(source) = $node_a_id
                    MATCH (target) WHERE elementId(target) = $node_b_id
                    CREATE (source)-[r:{predicate_safe}]->(target)
                    SET r.created_at = $created_at,
                        r.predicate = $predicate,
                        r.source = $source,
                        r.confidence = $confidence,
                        r.directivity = 'to_B'
                    RETURN elementId(r) as to_b_relationship_id
                    """
                    
                    # 创建 B->A 关系 (to_A)
                    to_a_query = f"""
                    MATCH (source) WHERE elementId(source) = $node_b_id
                    MATCH (target) WHERE elementId(target) = $node_a_id
                    CREATE (source)-[r:{predicate_safe}]->(target)
                    SET r.created_at = $created_at,
                        r.predicate = $predicate,
                        r.source = $source,
                        r.confidence = $confidence,
                        r.directivity = 'to_A'
                    RETURN elementId(r) as to_a_relationship_id
                    """
                    
                    # 执行两个关系创建
                    to_b_result = session.run(to_b_query,
                                             node_a_id=node_a_id,
                                             node_b_id=node_b_id,
                                             predicate=predicate,
                                             source=source,
                                             confidence=confidence,
                                             created_at=current_time)
                    
                    to_a_result = session.run(to_a_query,
                                             node_a_id=node_a_id,
                                             node_b_id=node_b_id,
                                             predicate=predicate,
                                             source=source,
                                             confidence=confidence,
                                             created_at=current_time)
                    
                    to_b_record = to_b_result.single()
                    to_a_record = to_a_result.single()
                    
                    if to_b_record and to_a_record:
                        logger.debug(f"Created bidirectional relationships: to_B and to_A")
                        relationship_id = to_b_record["to_b_relationship_id"]  # 返回其中一个关系ID
                    elif to_b_record:
                        logger.debug(f"Created to_B relationship")
                        relationship_id = to_b_record["to_b_relationship_id"]
                    elif to_a_record:
                        logger.debug(f"Created to_A relationship")
                        relationship_id = to_a_record["to_a_relationship_id"]
                    else:
                        logger.warning(f"Failed to create bidirectional relationships")
                        relationship_id = None
                else:
                    # 创建单向关系
                    create_relationship_query = f"""
                    MATCH (source) WHERE elementId(source) = $source_id
                    MATCH (target) WHERE elementId(target) = $target_id
                    CREATE (source)-[r:{predicate_safe}]->(target)
                    SET r.created_at = $created_at,
                        r.predicate = $predicate,
                        r.source = $source,
                        r.confidence = $confidence,
                        r.directivity = $directivity
                    RETURN elementId(r) as relationship_id
                    """
                    
                    result = session.run(create_relationship_query,
                                       source_id=source_id,
                                       target_id=target_id,
                                       predicate=predicate,
                                       source=source,
                                       confidence=confidence,
                                       directivity=directivity,
                                       created_at=current_time)
                    
                    relationship_record = result.single()
                    if relationship_record:
                        relationship_id = relationship_record["relationship_id"]
                        logger.debug(f"Created relationship: {direction_desc} with predicate '{predicate}'")
                    else:
                        relationship_id = None
                
                if relationship_id:
                    logger.info(f"Successfully connected nodes: {direction_desc}")
                    return relationship_id
                else:
                    logger.error("Failed to create relationship")
                    return None
                    
        except Exception as e:
            logger.error(f"Failed to connect nodes '{node_a_id}' and '{node_b_id}': {e}")
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
                # 检查节点是否存在
                check_query = """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN labels(n) as node_labels, n.name as node_name, properties(n) as current_properties
                """
                
                check_result = session.run(check_query, node_id=node_id).single()
                
                if not check_result:
                    logger.error(f"Node with ID '{node_id}' not found")
                    return None
                
                # 添加当前时间戳到更新中
                updates["last_updated"] = datetime.now().isoformat()
                
                # 构建SET子句
                set_clauses = []
                params = {"node_id": node_id}
                
                for key, value in updates.items():
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
                SET {', '.join(set_clauses)}
                RETURN properties(n) as updated_properties
                """
                
                result = session.run(update_query, **params)
                updated_record = result.single()
                
                if updated_record:
                    logger.info(f"Successfully updated node {node_id}")
                    return node_id
                else:
                    logger.error("Failed to update node")
                    return None
                
        except Exception as e:
            logger.error(f"Failed to modify node '{node_id}': {e}")
            return None
    

    def set_entity_time(self, node_id: str, time_str: str) -> Optional[str]:
        """
        为节点设置时间属性，如：
        Entity--HAPPENED_AT->Time
        Lation--BUILT_AT->Time
        Character--BIRTHDAY_AT->Time
        
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
                    logger.error(f"Only Entity nodes can have time attributes. Node {node_id} has labels: {node_labels}")
                    return None
                
                # 删除已有的时间关联（覆盖逻辑）
                session.run("""
                    MATCH (n) WHERE elementId(n) = $node_id
                    OPTIONAL MATCH (n)-[r:HAPPENED_AT]->(t:Time)
                    DELETE r
                """, node_id=node_id)
                
                # 创建时间节点
                time_node_name = self.create_time_node(session, time_str)
                
                if not time_node_name:
                    logger.error(f"Failed to create time node for '{time_str}'")
                    return None
                
                # 将时间节点与目标节点关联
                session.run("""
                    MATCH (n) WHERE elementId(n) = $node_id
                    MATCH (t:Time {name: $time_node_name})
                    CREATE (n)-[r:HAPPENED_AT]->(t)
                    SET r.created_at = $created_at
                """, 
                    node_id=node_id,
                    time_node_name=time_node_name,
                    created_at=datetime.now().isoformat()
                )
                
                logger.info(f"Set time '{time_str}' for Entity node {node_id}")
                return node_id
                
        except Exception as e:
            logger.error(f"Failed to set time for node '{node_id}': {e}")
            return None

    def set_location(self, relation_id: str, location_name: str) -> Optional[str]:
        """
        为关系设置地点属性，如：
        (Entity)-[HAPPENED_AT]->(Location)
        添加地点信息
        
        Args:
            relation_id: Neo4j关系ID
            location_name: 地点名称
            
        Returns:
            Optional[str]: 设置成功返回关系ID，失败返回None
        """
        if not self._ensure_connection():
            logger.error("Cannot set relation location: No Neo4j connection")
            return None
        
        if not relation_id or not relation_id.strip():
            logger.error("Relation ID cannot be empty")
            return None
        
        if not location_name or not location_name.strip():
            logger.error("Location name cannot be empty")
            return None
        
        try:
            with self.driver.session() as session:
                # 检查关系是否存在
                check_query = """
                MATCH ()-[r]->() WHERE elementId(r) = $relation_id
                RETURN type(r) as rel_type, properties(r) as rel_properties
                """
                
                check_result = session.run(check_query, relation_id=relation_id).single()
                
                if not check_result:
                    logger.error(f"Relation with ID '{relation_id}' not found")
                    return None
                
                # 更新关系属性，添加地点信息
                session.run("""
                    MATCH ()-[r]->() WHERE elementId(r) = $relation_id
                    SET r.location = $location_name,
                        r.last_updated = $last_updated
                """, 
                    relation_id=relation_id,
                    location_name=location_name,
                    last_updated=datetime.now().isoformat()
                )
                
                logger.info(f"Set location '{location_name}' for relation {relation_id}")
                return relation_id
                
        except Exception as e:
            logger.error(f"Failed to set location for relation '{relation_id}': {e}")
            return None


    def modify_relation(self, relation_id: str, predicate: str, source: str, confidence: float = 0.5, 
                        directivity: str = "to_B") -> Optional[str]:
        """
        修改关系的属性
        
        Args:
            relation_id: Neo4j关系ID
            predicate: 关系谓词/类型
            source: 关系来源
            confidence: 置信度 (默认0.5)
            directivity: 关系方向 (默认'to_B'，即A->B；'to_A'表示B->A；'bidirectional'表示双向)
            
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
            logger.error("Missing required parameters: predicate and source cannot be empty")
            return None
        
        try:
            with self.driver.session() as session:
                # 检查关系是否存在并获取节点信息
                check_query = """
                MATCH (a)-[r]->(b) WHERE elementId(r) = $relation_id
                RETURN elementId(a) as source_node_id, elementId(b) as target_node_id,
                       a.name as source_name, b.name as target_name,
                       type(r) as rel_type_name, properties(r) as current_properties,
                       r.directivity as current_directivity
                """
                
                check_result = session.run(check_query, relation_id=relation_id).single()
                
                if not check_result:
                    logger.error(f"Relation with ID '{relation_id}' not found")
                    return None
                
                source_node_id = check_result["source_node_id"]
                target_node_id = check_result["target_node_id"]
                source_name = check_result["source_name"]
                target_name = check_result["target_name"]
                current_directivity = check_result["current_directivity"]
                
                # 检查是否需要进行关系交换保护
                opposite_relation_info = None
                if ((current_directivity == "to_B" and directivity == "to_A") or 
                    (current_directivity == "to_A" and directivity == "to_B")) and directivity != "bidirectional":
                    
                    # 检查反向是否已有关系
                    if directivity == "to_A":
                        # 当前要改为 B->A，检查是否已有 B->A 关系
                        opposite_check_query = """
                        MATCH (a) WHERE elementId(a) = $target_node_id
                        MATCH (b) WHERE elementId(b) = $source_node_id
                        OPTIONAL MATCH (a)-[r]->(b) WHERE r.directivity = 'to_A'
                        RETURN elementId(r) as opposite_rel_id, properties(r) as opposite_properties
                        """
                    else:  # directivity == "to_B"
                        # 当前要改为 A->B，检查是否已有 A->B 关系
                        opposite_check_query = """
                        MATCH (a) WHERE elementId(a) = $source_node_id
                        MATCH (b) WHERE elementId(b) = $target_node_id
                        OPTIONAL MATCH (a)-[r]->(b) WHERE r.directivity = 'to_B'
                        RETURN elementId(r) as opposite_rel_id, properties(r) as opposite_properties
                        """
                    
                    opposite_result = session.run(opposite_check_query, 
                                                source_node_id=source_node_id, 
                                                target_node_id=target_node_id).single()
                    
                    if opposite_result and opposite_result["opposite_rel_id"]:
                        # 保存即将被覆盖的关系信息
                        opposite_relation_info = opposite_result["opposite_properties"]
                        logger.info(f"Found existing opposite relation, will preserve its data")
                
                # 删除原关系
                session.run("""
                    MATCH ()-[r]-() WHERE elementId(r) = $relation_id
                    DELETE r
                """, relation_id=relation_id)
                
                logger.info(f"Deleted original relation {relation_id} between {source_name} and {target_name}")
            
            # 使用create_relation方法重新创建关系（在事务外调用以避免嵌套）
            new_relation_id = self.create_relation(
                node_a_id=source_node_id,
                node_b_id=target_node_id,
                predicate=predicate,
                source=source,
                confidence=confidence,
                directivity=directivity
            )
            
            # 如果有需要保护的反向关系，将其数据写到原位置
            if opposite_relation_info and new_relation_id:
                try:
                    with self.driver.session() as session:
                        # 确定原位置的方向
                        if current_directivity == "to_B":
                            restore_directivity = "to_B"
                            restore_source_id, restore_target_id = source_node_id, target_node_id
                        else:  # current_directivity == "to_A"
                            restore_directivity = "to_A" 
                            restore_source_id, restore_target_id = target_node_id, source_node_id
                        
                        # 创建保护关系到原位置
                        restore_relation_id = self.create_relation(
                            node_a_id=source_node_id,
                            node_b_id=target_node_id,
                            predicate=opposite_relation_info.get("predicate", "PRESERVED_RELATION"),
                            source=opposite_relation_info.get("source", "relation_swap_protection"),
                            confidence=opposite_relation_info.get("confidence", 0.5),
                            directivity=restore_directivity
                        )
                        
                        if restore_relation_id:
                            logger.info(f"Successfully preserved opposite relation data at original position")
                        else:
                            logger.warning(f"Failed to preserve opposite relation data")
                            
                except Exception as e:
                    logger.error(f"Failed to preserve opposite relation: {e}")
            
            if new_relation_id:
                logger.info(f"Successfully modified relation: created new relation {new_relation_id} with predicate '{predicate}' and directivity '{directivity}'")
                return new_relation_id
            else:
                logger.error("Failed to create new relation after deletion")
                return None
                
        except Exception as e:
            logger.error(f"Failed to modify relation '{relation_id}': {e}")
            return None

    
    def update_node(self, node_id: str, significance: float = None, Increase_importance: bool = False) -> Optional[str]:
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
                    decay_factor = (1 - current_importance ** 2) / 10
                    new_significance = max(0.0, significance - decay_factor)
                else:
                    # 使用提供的significance值
                    new_significance = max(0.0, min(1.0, significance))
                
                if Increase_importance:
                    current_importance = current_importance + (1 - current_importance) * 0.1  # 提高余量的10%

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
                
                result = session.run(update_query,
                                   node_id=node_id,
                                   new_significance=new_significance,
                                   current_importance=current_importance,
                                   last_updated=current_time)
                
                updated_record = result.single()
                
                if updated_record:
                    updated_significance = updated_record["updated_significance"]
                    updated_importance = updated_record["updated_importance"]
                    logger.info(f"Successfully updated node {node_id}: significance {current_significance} -> {updated_significance}, importance {current_importance} -> {updated_importance}")
                    return node_id
                else:
                    logger.error("Failed to update node significance and importance")
                    return None
                
        except Exception as e:
            logger.error(f"Failed to update node significance '{node_id}': {e}")
            return None

    def _find_node(self, session, node_name: str, node_type: str, time: str, location: str, signature_node: str) -> Optional[str]:
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
                   labels(n) as node_labels, n.node_type as node_type,
                   t.time as node_time, l.name as node_location
            """
            
            same_name_results = session.run(same_name_nodes_query, name=node_name)
            candidates = []

            for record in same_name_results:
                candidates.append({
                    'node_id': record['node_id'],
                    'last_updated': record['last_updated'],
                    'node_labels': record['node_labels'],
                    'node_type': record['node_type'],
                    'node_time': record['node_time'],
                    'node_location': record['node_location']
                })
            
            if not candidates:
                # 没有找到同名节点
                return None
            
            # 第2步：移除不符合类型（node_type）的节点
            if node_type:  # 只有提供了node_type才进行类型过滤
                candidates = [
                    candidate for candidate in candidates
                    if candidate['node_type'] == node_type
                ]
                if not candidates:
                    return None
            
            # 第3步：移除不符合时间条件的节点
            if time:  # 只有提供了time才进行时间过滤
                # 优先选择有明确时间匹配的节点
                time_matched_candidates = [
                    candidate for candidate in candidates
                    if candidate['node_time'] == time
                ]
                
                # 如果有明确时间匹配的节点，就只使用这些节点
                if time_matched_candidates:
                    candidates = time_matched_candidates
                else:
                    # 如果没有明确匹配的，才考虑无时间信息的节点
                    candidates = [
                        candidate for candidate in candidates
                        if not candidate['node_time']
                    ]
                # 如果也无法匹配，返回None
                if not candidates:
                    return None
            
            # 第4步：移除不符合地点条件的节点
            if location:  # 只有提供了location才进行地点过滤
                location_matched_candidates = [
                    candidate for candidate in candidates
                    if candidate['node_location'] == location
                ]

                # 如果有明确地点匹配的节点，就只使用这些节点
                if location_matched_candidates:
                    candidates = location_matched_candidates
                else:
                    # 如果没有明确匹配的，才考虑无地点信息的节点
                    candidates = [
                        candidate for candidate in candidates
                        if not candidate['node_location']
                    ]
                # 如果也无法匹配，返回None
                if not candidates:
                    return None
            
            # 如果只有一个候选节点，直接返回（通常只应该有一个候选节点）
            if len(candidates) == 1:
                return candidates[0]['node_id']
            
            # 第5步：如果仍有多个候选，使用特征节点进行匹配，查询每个候选是否与特征节点相连。
            if signature_node:
                signature_matched_candidates = []
                for candidate in candidates:
                    signature_check_query = """
                    MATCH (n) WHERE elementId(n) = $node_id
                    MATCH (s {name: $signature_name})
                    RETURN EXISTS( (n)-[]->(s) OR (s)-[]->(n) ) as is_connected
                    """
                    signature_result = session.run(signature_check_query,
                                                   node_id=candidate['node_id'],
                                                   signature_name=signature_node).single()
                    if signature_result and signature_result['is_connected']:
                        signature_matched_candidates.append(candidate)
                
                # 如果有与特征节点相连的候选，使用这些候选；否则继续使用原有候选
                if signature_matched_candidates:
                    candidates = signature_matched_candidates

            # 再检查一下节点数量
            if len(candidates) == 1:
                return candidates[0]['node_id']

            # 第6步：如果仍有多个候选节点，按last_updated排序选择最近更新的
            def parse_datetime(dt_str):
                if not dt_str:
                    return datetime.min
                try:
                    return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                except:
                    return datetime.min
            
            # 计算与当前时间的距离
            for candidate in candidates:
                last_updated_dt = parse_datetime(candidate['last_updated'])
                candidate['time_distance'] = abs((current_time - last_updated_dt).total_seconds())
            
            # 按时间距离排序，选择最近更新的节点
            candidates.sort(key=lambda x: x['time_distance'])
            
            # 由于last_updated基于时间且绝对不会重复，直接返回最接近当前时间的节点
            return candidates[0]['node_id']
            
        except Exception as e:
            logger.error(f"Failed to find object node '{node_name}': {e}")
            return None
        

    def write_quintuple(self, subject: str, action: str, object: str, time: str = None, location: str = None, with_person: str = None, directivity: bool = True, importance: float = 0.5, confidence: float = 0.5, context: str = "", source: str = "user_input") -> Optional[str]:
        """
        创建五元组关系
        
        输入(list of)：
            subject:"主体（必填）",
            predicate:"谓词/动作（必填），",
            object:"客体/事件（选填）",
            time:"事件发生的时间（选填）",
            location:"事件发生的地点（选填）",
            with_person:["事件涉及到的其他人A", "事件涉及到的其他人B"],
            directivity:"主体与其他人的关系是否可逆（仅在有with_person时填写）",
            importance:"这条信息的重要性（必填）",
            confidence:"这条信息的真实性（必填）",
            context:"这条信息的语境，如现实，某款游戏，某个故事（必填）",
            source:"谁提供的信息（必填）"
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
        # 情况1：主谓宾，无时间地点 -> 三元组，关系建立(主语)--谓词->(宾语)
        # 无需调整
        # 情况2：主谓+时间or地点，无宾语 -> 不完全五元组，关系建立(主语)--has_action->(谓词)--HAPPENED_AT/IN->(时间/地点)
        # 将谓词作为宾语，谓词改为HAS_ACTION
        # 情况3：主谓宾+时间or地点 -> 完整五元组
        # 无需调整

        # 必须有主体和谓词，没有则直接返回None
        if not all([subject, action]):
            logger.error("Subject, action, and object are required")
            return None

        # 如果没有客体，将谓词作为客体，谓词填HAS_ACTION
        if not object:
            object = action
            action = "HAS_ACTION"
            logger.debug(f"No object provided, using action as object with predicate 'HAS_ACTION'")
        
        # 检查各项内容是否被标注：[时间](地点)<角色>
        
        
         # 开始事务处理
        try:
            with self.driver.session() as session:
                # 查找主体节点，提供主体，客体作为signature_node帮助匹配
                subject_id = self._find_node(session, subject, None, None, None, object)
                if subject_id:
                    logger.debug(f"Found existing subject node: {subject} with ID: {subject_id}")
                else:
                    # 没有找到匹配的节点，创建新的主体节点
                    self.create_entity_node(session, subject, 0.5, "主体节点")
                    subject_result = session.run("""
                        MATCH (n:Entity {name: $name})
                        WHERE n.created_at IS NOT NULL
                        RETURN elementId(n) as node_id
                        ORDER BY n.created_at DESC
                        LIMIT 1
                    """, name=subject).single()
                    subject_id = subject_result["node_id"] if subject_result else None
                    logger.debug(f"Created new subject node: {subject} with ID: {subject_id}")
                
                # 查找客体节点
                object_id = self._find_node(session, object, time, location)
                if object_id:
                    logger.debug(f"Found existing object node: {object} with ID: {object_id}")
                else:
                    # 没有找到匹配的节点，创建新的客体节点
                    self.create_entity_node(session, object, importance, "客体节点")
                    object_result = session.run("""
                        MATCH (n:Entity {name: $name})
                        WHERE n.created_at IS NOT NULL
                        RETURN elementId(n) as node_id
                        ORDER BY n.created_at DESC
                        LIMIT 1
                    """, name=object).single()
                    object_id = object_result["node_id"] if object_result else None
                    logger.debug(f"Created new object node: {object} with ID: {object_id}")
                
                if not subject_id or not object_id:
                    logger.error("Failed to create or find subject/object nodes")
                    return None
                
                # 创建主体到客体的关系（默认to_B方向）
                main_relation_id = self.create_relation(
                    node_a_id=subject_id,
                    node_b_id=object_id,
                    predicate=action,
                    source=source,
                    confidence=confidence,
                    directivity="to_B"
                )
                
                if not main_relation_id:
                    logger.error("Failed to create main relation")
                    return None
                
                # 处理时间节点
                if time:
                    # 使用set_entity_time方法设置时间
                    result = self.set_entity_time(object_id, time)
                    if result:
                        logger.debug(f"Set time for object node {object}: {time}")
                    else:
                        logger.warning(f"Failed to set time for object node {object}: {time}")
                
                # 处理地点节点
                if location:
                    # 检查客体节点是否已经有地点链接
                    existing_location_check = session.run("""
                        MATCH (obj) WHERE elementId(obj) = $object_id
                        OPTIONAL MATCH (obj)-[:HAPPENED_IN]->(l:Location)
                        RETURN l.name as existing_location
                    """, object_id=object_id).single()
                    
                    existing_location = existing_location_check["existing_location"] if existing_location_check else None
                    
                    if existing_location:
                        logger.debug(f"Object node {object} already has location link to: {existing_location}, skipping location creation")
                    else:
                        # 创建地点节点
                        session.run("""
                            MERGE (l:Location {name: $location})
                            ON CREATE SET l.created_at = $created_at,
                                         l.node_type = 'Location'
                            SET l.source = $source,
                                l.last_updated = $created_at
                        """, 
                            location=location,
                            source=source,
                            created_at=datetime.now().isoformat()
                        )
                        
                        # 通过HAPPENED_IN链接客体到地点节点
                        location_relation = session.run("""
                            MATCH (obj) WHERE elementId(obj) = $object_id
                            MATCH (l:Location {name: $location})
                            CREATE (obj)-[r:HAPPENED_IN]->(l)
                            SET r.created_at = $created_at,
                                r.source = $source,
                                r.confidence = $confidence,
                                r.action_context = $action
                            RETURN elementId(r) as relation_id
                        """, 
                            object_id=object_id,
                            location=location,
                            created_at=datetime.now().isoformat(),
                            source=source,
                            confidence=confidence,
                            action=action
                        ).single()
                        logger.debug(f"Created location relationship: {object} -> {location}")
                
                # 处理同行者节点
                if with_person:
                    # 查找同行者节点
                    with_person_id = self._find_node(session, with_person)
                    if with_person_id:
                        logger.debug(f"Found existing with_person node: {with_person} with ID: {with_person_id}")
                    else:
                        # 没有找到匹配的节点，创建新的同行者节点
                        self.create_entity_node(session, with_person, importance, "同行者节点")
                        with_person_result = session.run("""
                            MATCH (n:Entity {name: $name})
                            WHERE n.created_at IS NOT NULL
                            RETURN elementId(n) as node_id
                            ORDER BY n.created_at DESC
                            LIMIT 1
                        """, name=with_person).single()
                        with_person_id = with_person_result["node_id"] if with_person_result else None
                        logger.debug(f"Created new with_person node: {with_person} with ID: {with_person_id}")
                    
                    if with_person_id:
                        # 根据directivity决定关系方向
                        if directivity:
                            # True: 被动参与 (to_B: 客体 -> 同行者)
                            with_relation_directivity = "to_B"
                            with_relation_id = self.create_relation(
                                node_a_id=object_id,
                                node_b_id=with_person_id,
                                predicate="同行",
                                source=source,
                                confidence=confidence,
                                directivity=with_relation_directivity
                            )
                        else:
                            # False: 共事关系 (to_A: 同行者 -> 客体)
                            with_relation_directivity = "to_A"
                            with_relation_id = self.create_relation(
                                node_a_id=object_id,
                                node_b_id=with_person_id,
                                predicate="共事",
                                source=source,
                                confidence=confidence,
                                directivity=with_relation_directivity
                            )
                        
                        logger.debug(f"Created with_person relationship: {object} <-> {with_person}")
                
                logger.info(f"Successfully created quintuple: {subject} -{action}-> {object}")
                return main_relation_id
                
        except Exception as e:
            logger.error(f"Failed to write quintuple: {e}")
            return None


    def _create_quintuple_cross_references(self, session, quintuples: List) -> bool:
        """
        检测五元组之间的交叉引用并创建关系
        例如：A告诉B关于某个事件 -> 直接链接到该事件的五元组
        """
        
        try:
            # 为每个五元组创建一个事件标识符
            events = []
            for quintuple in quintuples:
                # 创建事件签名：主体+动作+客体+时间
                event_signature = f"{quintuple.subject}_{quintuple.action}_{quintuple.object}_{quintuple.time or 'no_time'}"
                events.append({
                    'quintuple': quintuple,
                    'signature': event_signature,
                    'key_info': {
                        'subject': quintuple.subject,
                        'action': quintuple.action,
                        'object': quintuple.object,
                        'time': quintuple.time,
                        'location': quintuple.location
                    }
                })
            
            # 检测讲述类型的五元组
            narrative_actions = ['告诉', '说', '讲', '提到', '描述', '回忆']
            
            for event in events:
                quintuple = event['quintuple']
                
                # 检查是否是讲述类型的动作
                if any(action in quintuple.action for action in narrative_actions):
                    object_content = quintuple.object
                    
                    # 尝试从object中提取时间、地点、人物信息
                    time_matches = re.findall(r'\(([^)]+)\)', object_content)
                    location_matches = re.findall(r'\[([^\]]+)\]', object_content)
                    person_matches = re.findall(r'<([^>]+)>', object_content)
                    
                    # 查找可能匹配的事件
                    for other_event in events:
                        if other_event == event:  # 跳过自己
                            continue
                        
                        other_quintuple = other_event['quintuple']
                        match_score = 0
                        
                        # 检查时间匹配
                        if time_matches and other_quintuple.time:
                            for time_ref in time_matches:
                                if time_ref in other_quintuple.time or other_quintuple.time in time_ref:
                                    match_score += 3
                        
                        # 检查地点匹配
                        if location_matches and other_quintuple.location:
                            for loc_ref in location_matches:
                                if loc_ref in other_quintuple.location or other_quintuple.location in loc_ref:
                                    match_score += 2
                        
                        # 检查人物匹配
                        if person_matches:
                            for person_ref in person_matches:
                                if person_ref == other_quintuple.subject:
                                    match_score += 2
                        
                        # 检查动作关键词匹配
                        object_clean = re.sub(r'[()<>\[\]]', '', object_content)
                        if other_quintuple.action in object_clean or any(word in object_clean for word in other_quintuple.action.split()):
                            match_score += 1
                        
                        # 如果匹配分数够高，创建引用关系
                        if match_score >= 3:
                            self._create_event_reference_relationship(
                                session, quintuple, other_quintuple, match_score
                            )
                            logger.debug(f"Created cross-reference: {quintuple.subject} {quintuple.action} -> {other_quintuple.subject} {other_quintuple.action} (score: {match_score})")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create quintuple cross-references: {e}")
            return False
    
    def _create_event_reference_relationship(self, session, narrative_quintuple, referenced_quintuple, confidence_score: int):
        """
        在讲述类五元组和被引用的事件五元组之间创建引用关系
        """
        try:
            # 创建事件节点（如果不存在）
            narrative_event_id = f"event_{hash(f'{narrative_quintuple.subject}_{narrative_quintuple.action}_{narrative_quintuple.object}_{narrative_quintuple.time_record}')}"
            referenced_event_id = f"event_{hash(f'{referenced_quintuple.subject}_{referenced_quintuple.action}_{referenced_quintuple.object}_{referenced_quintuple.time_record}')}"
            
            # 创建或更新讲述事件节点
            session.run("""
                MERGE (ne:Event {id: $narrative_event_id})
                SET ne.subject = $narrative_subject,
                    ne.action = $narrative_action,
                    ne.object = $narrative_object,
                    ne.time = $narrative_time,
                    ne.location = $narrative_location,
                    ne.source = $narrative_source,
                    ne.confidence = $narrative_confidence,
                    ne.importance = $narrative_importance,
                    ne.time_record = $narrative_time_record,
                    ne.event_type = 'narrative'
            """, 
                narrative_event_id=narrative_event_id,
                narrative_subject=narrative_quintuple.subject,
                narrative_action=narrative_quintuple.action,
                narrative_object=narrative_quintuple.object,
                narrative_time=narrative_quintuple.time,
                narrative_location=narrative_quintuple.location,
                narrative_source=narrative_quintuple.source,
                narrative_confidence=narrative_quintuple.confidence,
                narrative_importance=narrative_quintuple.importance,
                narrative_time_record=narrative_quintuple.time_record
            )
            
            # 创建或更新被引用事件节点
            session.run("""
                MERGE (re:Event {id: $referenced_event_id})
                SET re.subject = $referenced_subject,
                    re.action = $referenced_action,
                    re.object = $referenced_object,
                    re.time = $referenced_time,
                    re.location = $referenced_location,
                    re.source = $referenced_source,
                    re.confidence = $referenced_confidence,
                    re.importance = $referenced_importance,
                    re.time_record = $referenced_time_record,
                    re.event_type = 'actual'
            """, 
                referenced_event_id=referenced_event_id,
                referenced_subject=referenced_quintuple.subject,
                referenced_action=referenced_quintuple.action,
                referenced_object=referenced_quintuple.object,
                referenced_time=referenced_quintuple.time,
                referenced_location=referenced_quintuple.location,
                referenced_source=referenced_quintuple.source,
                referenced_confidence=referenced_quintuple.confidence,
                referenced_importance=referenced_quintuple.importance,
                referenced_time_record=referenced_quintuple.time_record
            )
            
            # 创建引用关系
            session.run("""
                MATCH (ne:Event {id: $narrative_event_id})
                MATCH (re:Event {id: $referenced_event_id})
                MERGE (ne)-[r:REFERS_TO]->(re)
                ON CREATE SET r.created_at = $time_record
                SET r.confidence_score = $confidence_score,
                    r.last_updated = $time_record,
                    r.relationship_type = 'narrative_reference'
            """, 
                narrative_event_id=narrative_event_id,
                referenced_event_id=referenced_event_id,
                confidence_score=confidence_score,
                time_record=narrative_quintuple.time_record
            )
            
            logger.debug(f"Created event reference relationship with confidence {confidence_score}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create event reference relationship: {e}")
            return False
    
    def _create_triple_node_and_relationship(self, session, triple) -> bool:
        """创建三元组的节点和关系"""
        try:
            # 创建主体节点
            session.run("""
                MERGE (s:Entity {name: $subject})
                ON CREATE SET s.created_at = $time_record
                SET s.source = $source,
                    s.confidence = $confidence,
                    s.last_updated = $time_record
            """,
                subject=triple.subject,
                source=triple.source,
                confidence=triple.confidence,
                time_record=triple.time_record
            )
            
            # 创建客体节点
            session.run("""
                MERGE (o:Entity {name: $object})
                ON CREATE SET o.created_at = $time_record
                SET o.source = $source,
                    o.confidence = $confidence,
                    o.last_updated = $time_record
            """,
                object=triple.object,
                source=triple.source,
                confidence=triple.confidence,
                time_record=triple.time_record
            )
            
            # 创建关系
            # 使用 APOC 或直接构建动态关系类型
            predicate_safe = triple.predicate.replace(" ", "_").replace("-", "_").upper()
            if not predicate_safe.replace("_", "").isalnum():
                predicate_safe = "RELATED_TO"  # 回退到通用关系
            
            session.run(f"""
                MATCH (s:Entity {{name: $subject}})
                MATCH (o:Entity {{name: $object}})
                MERGE (s)-[r:{predicate_safe}]->(o)
                ON CREATE SET r.created_at = $time_record
                SET r.predicate = $predicate,
                    r.source = $source,
                    r.confidence = $confidence,
                    r.last_updated = $time_record
            """,
                subject=triple.subject,
                object=triple.object,
                predicate=triple.predicate,
                source=triple.source,
                confidence=triple.confidence,
                time_record=triple.time_record
            )
            
            logger.debug(f"Created triple: {triple.subject} -> {triple.predicate} -> {triple.object}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create triple: {e}")
            return False
    
    def _create_quintuple_nodes_and_relationships(self, session, quintuple) -> bool:
        """创建五元组的节点和关系"""
        try:
            # 创建主体节点
            session.run("""
                MERGE (s:Entity {name: $subject})
                ON CREATE SET s.created_at = $time_record
                SET s.source = $source,
                    s.confidence = $confidence,
                    s.last_updated = $time_record
            """, 
                subject=quintuple.subject,
                source=quintuple.source,
                confidence=quintuple.confidence,
                time_record=quintuple.time_record
            )
            
            # 创建客体节点
            session.run("""
                MERGE (o:Entity {name: $object})
                ON CREATE SET o.created_at = $time_record
                SET o.source = $source,
                    o.confidence = $confidence,
                    o.importance = $importance,
                    o.significance = 1,
                    o.last_updated = $time_record
            """, 
                object=quintuple.object,
                source=quintuple.source,
                confidence=quintuple.confidence,
                importance=quintuple.importance,
                time_record=quintuple.time_record
            )
            
            # 如果有时间信息，创建层次化时间节点
            specific_time_node = None
            if quintuple.time:
                specific_time_node = self.create_time_node(
                    session, quintuple.time
                )
            
            # 如果有地点信息，创建地点节点
            if quintuple.location:
                session.run("""
                    MERGE (l:Location {name: $location})
                    ON CREATE SET l.created_at = $time_record
                    SET l.source = $source,
                        l.last_updated = $time_record
                """, 
                    location=quintuple.location,
                    source=quintuple.source,
                    time_record=quintuple.time_record
                )
            
            # 创建主要动作关系
            action_safe = quintuple.action.replace(" ", "_").replace("-", "_").upper()
            if not action_safe.replace("_", "").isalnum():
                action_safe = "PERFORMED_ACTION"  # 回退到通用动作
            
            session.run(f"""
                MATCH (s:Entity {{name: $subject}})
                MATCH (o:Entity {{name: $object}})
                MERGE (s)-[r:{action_safe}]->(o)
                ON CREATE SET r.created_at = $time_record
                SET r.action = $action,
                    r.source = $source,
                    r.confidence = $confidence,
                    r.importance = $importance,
                    r.last_updated = $time_record,
                    r.time = $time,
                    r.location = $location
            """, 
                subject=quintuple.subject,
                object=quintuple.object,
                action=quintuple.action,
                source=quintuple.source,
                confidence=quintuple.confidence,
                importance=quintuple.importance,
                time_record=quintuple.time_record,
                time=quintuple.time,
                location=quintuple.location
            )
            
            # 如果有时间，创建时间关系（连接到最具体的时间节点）
            if specific_time_node:
                # 根据时间节点类型选择合适的标签查询
                time_query = """
                    MATCH (o:Entity {name: $object})
                    MATCH (t {name: $time_node})
                    MERGE (o)-[r:HAPPENED_AT]->(t)
                    ON CREATE SET r.created_at = $time_record
                    SET r.source = $source,
                        r.last_updated = $time_record,
                        r.action_context = $action,
                        r.importance = $importance
                """
                session.run(time_query, 
                    object=quintuple.object,
                    time_node=specific_time_node,
                    source=quintuple.source,
                    time_record=quintuple.time_record,
                    action=quintuple.action,
                    importance=quintuple.importance
                )
            
            # 如果有地点，创建地点关系
            # 如果有地点，应该把地点关联到事件（object）而非主体
            if quintuple.location:
                session.run("""
                    MATCH (o:Entity {name: $object})
                    MATCH (l:Location {name: $location})
                    MERGE (o)-[r:HAPPENED_IN]->(l)
                    ON CREATE SET r.created_at = $time_record
                    SET r.source = $source,
                        r.last_updated = $time_record,
                        r.action_context = $action,
                        r.importance = $importance
                """, 
                    object=quintuple.object,
                    location=quintuple.location,
                    source=quintuple.source,
                    time_record=quintuple.time_record,
                    action=quintuple.action,
                    importance=quintuple.importance
                )
            
            logger.debug(f"Created quintuple: {quintuple.subject} -> {quintuple.action} -> {quintuple.object}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create quintuple: {e}")
            return False
    
    def write_triple(self, triple) -> bool:
        """写入单个三元组到 Neo4j"""
        if not self._ensure_connection():
            logger.error("Cannot write triple: No Neo4j connection")
            return False
        
        try:
            with self.driver.session() as session:
                return self._create_triple_node_and_relationship(session, triple)
        except TransientError as e:
            logger.warning(f"Transient error writing triple, retrying: {e}")
            try:
                with self.driver.session() as session:
                    return self._create_triple_node_and_relationship(session, triple)
            except Exception as retry_e:
                logger.error(f"Failed to write triple after retry: {retry_e}")
                return False
        except Exception as e:
            logger.error(f"Failed to write triple: {e}")
            return False
    
    def write_quintuple(self, quintuple) -> bool:
        """写入单个五元组到 Neo4j"""
        if not self._ensure_connection():
            logger.error("Cannot write quintuple: No Neo4j connection")
            return False
        
        try:
            with self.driver.session() as session:
                return self._create_quintuple_nodes_and_relationships(session, quintuple)
        except TransientError as e:
            logger.warning(f"Transient error writing quintuple, retrying: {e}")
            try:
                with self.driver.session() as session:
                    return self._create_quintuple_nodes_and_relationships(session, quintuple)
            except Exception as retry_e:
                logger.error(f"Failed to write quintuple after retry: {retry_e}")
                return False
        except Exception as e:
            logger.error(f"Failed to write quintuple: {e}")
            return False
    
    def write_memories_batch(self, triples: List, quintuples: List) -> Dict[str, Any]:
        """批量写入三元组和五元组"""
        if not self._ensure_connection():
            logger.error("Cannot write memories: No Neo4j connection")
            return {
                "success": False,
                "error": "No Neo4j connection",
                "triples_written": 0,
                "quintuples_written": 0
            }
        
        triples_written = 0
        quintuples_written = 0
        errors = []
        
        try:
            with self.driver.session() as session:
                # 开启事务以确保数据一致性
                with session.begin_transaction() as tx:
                    # 写入三元组
                    for triple in triples:
                        try:
                            if self._create_triple_node_and_relationship(session, triple):
                                triples_written += 1
                            else:
                                errors.append(f"Failed to write triple: {triple}")
                        except Exception as e:
                            errors.append(f"Error writing triple {triple}: {e}")
                    
                    # 写入五元组
                    for quintuple in quintuples:
                        try:
                            if self._create_quintuple_nodes_and_relationships(session, quintuple):
                                quintuples_written += 1
                            else:
                                errors.append(f"Failed to write quintuple: {quintuple}")
                        except Exception as e:
                            errors.append(f"Error writing quintuple {quintuple}: {e}")
                    
                    # 在所有五元组写入后，检测和创建交叉引用
                    try:
                        self._create_quintuple_cross_references(session, quintuples)
                    except Exception as e:
                        errors.append(f"Error creating cross-references: {e}")
                    
                    # 提交事务
                    tx.commit()
                    
        except Exception as e:
            logger.error(f"Transaction failed: {e}")
            errors.append(f"Transaction error: {e}")
        
        success = len(errors) == 0
        
        result = {
            "success": success,
            "triples_written": triples_written,
            "quintuples_written": quintuples_written,
            "total_written": triples_written + quintuples_written,
            "errors": errors
        }
        
        if success:
            logger.info(f"Successfully wrote {triples_written} triples and {quintuples_written} quintuples to Neo4j")
        else:
            logger.warning(f"Wrote {triples_written} triples and {quintuples_written} quintuples with {len(errors)} errors")
        
        return result
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取知识图谱统计信息"""
        if not self._ensure_connection():
            return {"error": "No Neo4j connection"}
        
        try:
            with self.driver.session() as session:
                # 获取节点统计
                entity_count = session.run("MATCH (n:Entity) RETURN count(n) as count").single()["count"]
                time_count = session.run("MATCH (n:Time) RETURN count(n) as count").single()["count"]
                location_count = session.run("MATCH (n:Location) RETURN count(n) as count").single()["count"]
                
                # 获取关系统计
                triple_rels = session.run("MATCH ()-[r]->() WHERE r.predicate IS NOT NULL AND r.action IS NULL RETURN count(r) as count").single()["count"]
                quintuple_rels = session.run("MATCH ()-[r]->() WHERE r.action IS NOT NULL RETURN count(r) as count").single()["count"]
                
                return {
                    "nodes": {
                        "entities": entity_count,
                        "times": time_count,
                        "locations": location_count,
                        "total": entity_count + time_count + location_count
                    },
                    "relationships": {
                        "triples": triple_rels,
                        "quintuples": quintuple_rels,
                        "total": triple_rels + quintuple_rels
                    }
                }
                
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return {"error": str(e)}
    
    def delete_node_or_relation(self, element_id: str) -> Dict[str, Any]:
        """
        根据Neo4j元素ID删除节点或关系
        
        Args:
            element_id: Neo4j元素ID（节点ID或关系ID）
            
        Returns:
            Dict[str, Any]: 包含操作结果的字典
        """
        if not self._ensure_connection():
            logger.error("Cannot delete element: No Neo4j connection")
            return {
                "success": False,
                "error": "No Neo4j connection",
                "element_id": element_id,
                "element_type": None
            }
        
        if not element_id or not element_id.strip():
            return {
                "success": False,
                "error": "Element ID cannot be empty",
                "element_id": element_id,
                "element_type": None
            }
        
        try:
            with self.driver.session() as session:
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
                
                if not check_result or (not check_result["node_type"] and not check_result["rel_type"]):
                    return {
                        "success": False,
                        "error": f"Element with ID '{element_id}' not found",
                        "element_id": element_id,
                        "element_type": None
                    }
                
                element_type = None
                element_info = {}
                
                # 删除节点（会自动删除相关关系）
                if check_result["node_type"]:
                    element_type = "node"
                    element_info = {
                        "labels": check_result["node_labels"],
                        "name": check_result["node_name"]
                    }
                    
                    # 获取删除前的关系数量
                    rel_count_query = """
                    MATCH (n)-[r]-() WHERE elementId(n) = $element_id
                    RETURN count(r) as rel_count
                    """
                    rel_count = session.run(rel_count_query, element_id=element_id).single()["rel_count"]
                    
                    # 删除节点（DETACH DELETE 会同时删除所有相关关系）
                    delete_query = """
                    MATCH (n) WHERE elementId(n) = $element_id
                    DETACH DELETE n
                    RETURN count(n) as deleted_count
                    """
                    
                    result = session.run(delete_query, element_id=element_id)
                    deleted_count = result.single()["deleted_count"]
                    
                    if deleted_count > 0:
                        logger.info(f"Successfully deleted node {element_id} and {rel_count} related relationships")
                        return {
                            "success": True,
                            "error": None,
                            "element_id": element_id,
                            "element_type": element_type,
                            "element_info": element_info,
                            "deleted_relationships": rel_count,
                            "message": f"节点及其 {rel_count} 个关系已成功删除"
                        }
                    else:
                        return {
                            "success": False,
                            "error": "Node deletion failed",
                            "element_id": element_id,
                            "element_type": element_type
                        }
                
                # 删除关系
                elif check_result["rel_type"]:
                    element_type = "relationship"
                    element_info = {
                        "type": check_result["rel_type_name"]
                    }
                    
                    # 删除关系
                    delete_query = """
                    MATCH ()-[r]-() WHERE elementId(r) = $element_id
                    DELETE r
                    RETURN count(r) as deleted_count
                    """
                    
                    result = session.run(delete_query, element_id=element_id)
                    deleted_count = result.single()["deleted_count"]
                    
                    if deleted_count > 0:
                        logger.info(f"Successfully deleted relationship {element_id}")
                        return {
                            "success": True,
                            "error": None,
                            "element_id": element_id,
                            "element_type": element_type,
                            "element_info": element_info,
                            "deleted_relationships": 0,
                            "message": "关系已成功删除"
                        }
                    else:
                        return {
                            "success": False,
                            "error": "Relationship deletion failed",
                            "element_id": element_id,
                            "element_type": element_type
                        }
                
                return {
                    "success": False,
                    "error": "Unknown element type",
                    "element_id": element_id,
                    "element_type": None
                }
                
        except Exception as e:
            logger.error(f"Failed to delete element '{element_id}': {e}")
            return {
                "success": False,
                "error": str(e),
                "element_id": element_id,
                "element_type": None
            }
    
    def clear_recent_memory(self) -> bool:
        """清空 recent_memory.json 文件，重置为初始状态"""
        try:
            # 获取 recent_memory.json 文件路径
            recent_memory_file = os.path.join(config.system.log_dir, "recent_memory.json")
            
            # 创建初始状态的数据结构
            initial_data = {
                "triples": [],
                "quintuples": [],
                "metadata": {
                    "last_cleared": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "total_triples": 0,
                    "total_quintuples": 0,
                    "total_memories": 0
                }
            }
            
            # 确保目录存在
            os.makedirs(os.path.dirname(recent_memory_file), exist_ok=True)
            
            # 写入初始状态
            with open(recent_memory_file, 'w', encoding='utf-8') as f:
                json.dump(initial_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Successfully cleared recent_memory.json at {recent_memory_file}")
            logger.info(f"File reset to initial state at {initial_data['metadata']['last_cleared']}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to clear recent_memory.json: {e}")
            return False
    
    def clear_all_memory(self) -> bool:
        """清空Neo4j中的全部记忆节点，彻底格式化记忆，无法回退
        
        警告：此操作将永久删除Neo4j数据库中的所有节点和关系！
        需要用户输入'yes'确认才能执行。
        
        Returns:
            bool: 操作是否成功
        """
        # 显示严重警告
        print("\n" + "="*60)
        print("⚠️  严重警告：记忆完全清空操作 ⚠️")
        print("="*60)
        print("该操作将会：")
        print("1. 清空Neo4j数据库中的全部节点和关系")
        print("2. 彻底格式化所有记忆数据")
        print("3. 此操作无法回退，数据将永久丢失")
        print("4. 影响范围：所有实体、时间、地点节点及其关系")
        print("="*60)
        
        # 要求用户确认
        try:
            user_input = input("如果您确定要执行此操作，请输入 'yes'（区分大小写）: ").strip()
            
            if user_input != "yes":
                print("操作已取消。")
                logger.info("clear_all_memory operation cancelled by user")
                return False
                
        except KeyboardInterrupt:
            print("\n操作已取消。")
            logger.info("clear_all_memory operation cancelled by user (KeyboardInterrupt)")
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
                logger.warning("All Neo4j memory data has been cleared by clear_all_memory function")
                
                # 确认清空结果
                stats_after = self.get_statistics()
                if "error" not in stats_after:
                    remaining_nodes = stats_after.get("nodes", {}).get("total", 0)
                    remaining_rels = stats_after.get("relationships", {}).get("total", 0)
                    print(f"清空后确认：{remaining_nodes} 个节点，{remaining_rels} 个关系")
                
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
                        "properties": dict(record["properties"])
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
                        "properties": dict(record["properties"])
                    }
                    relationships.append(relationship)
                
                # 构建数据结构
                neo4j_data = {
                    "nodes": nodes,
                    "relationships": relationships,
                    "metadata": {
                        "source": "neo4j",
                        "neo4j_uri": config.grag.neo4j_uri,
                        "neo4j_database": config.grag.neo4j_database
                    },
                    "updated_at": __import__('datetime').datetime.now().isoformat()
                }
                
                # 保存到文件（覆盖模式）
                with open(neo4j_memory_file, 'w', encoding='utf-8') as f:
                    json.dump(neo4j_data, f, ensure_ascii=False, indent=2)
                
                print(f"💾 Neo4j数据已保存到: {neo4j_memory_file}")
                print(f"📊 下载统计: {len(nodes)} 个节点, {len(relationships)} 个关系")
                
                logger.info(f"Neo4j data successfully downloaded to {neo4j_memory_file}: {len(nodes)} nodes, {len(relationships)} relationships")
                return True
                
        except Exception as e:
            logger.error(f"Failed to load Neo4j data: {e}")
            print(f"❌ Neo4j数据下载失败: {e}")
            return False
    
    def upload_recent_memory(self) -> Dict[str, Any]:
        """将 recent_memory.json 中的记忆上传到 Neo4j
        
        读取 recent_memory.json 文件，将其中的三元组和五元组写入 Neo4j。
        对于每个记忆项，进行查重检查，如果发现冲突则用新数据覆盖旧数据。
        
        Returns:
            Dict[str, Any]: 包含上传结果的字典
        """
        if not self._ensure_connection():
            logger.error("Cannot upload recent memory: No Neo4j connection")
            return {
                "success": False,
                "error": "No Neo4j connection",
                "triples_uploaded": 0,
                "quintuples_uploaded": 0
            }
        
        try:
            # 读取 recent_memory.json 文件
            recent_memory_file = os.path.join(config.system.log_dir, "recent_memory.json")
            
            if not os.path.exists(recent_memory_file):
                logger.warning(f"Recent memory file not found: {recent_memory_file}")
                return {
                    "success": False,
                    "error": "Recent memory file not found",
                    "triples_uploaded": 0,
                    "quintuples_uploaded": 0
                }
            
            with open(recent_memory_file, 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
            
            triples = memory_data.get("triples", [])
            quintuples = memory_data.get("quintuples", [])
            
            logger.info(f"Found {len(triples)} triples and {len(quintuples)} quintuples to upload")
            print(f"准备上传：{len(triples)} 个三元组，{len(quintuples)} 个五元组")
            
            triples_uploaded = 0
            quintuples_uploaded = 0
            errors = []
            
            with self.driver.session() as session:
                # 上传三元组
                for triple_data in triples:
                    try:
                        if self._upload_triple_with_dedup(session, triple_data):
                            triples_uploaded += 1
                        else:
                            errors.append(f"Failed to upload triple: {triple_data}")
                    except Exception as e:
                        errors.append(f"Error uploading triple {triple_data}: {e}")
                
                # 上传五元组
                for quintuple_data in quintuples:
                    try:
                        if self._upload_quintuple_with_dedup(session, quintuple_data):
                            quintuples_uploaded += 1
                        else:
                            errors.append(f"Failed to upload quintuple: {quintuple_data}")
                    except Exception as e:
                        errors.append(f"Error uploading quintuple {quintuple_data}: {e}")
            
            success = len(errors) == 0
            
            result = {
                "success": success,
                "triples_uploaded": triples_uploaded,
                "quintuples_uploaded": quintuples_uploaded,
                "total_uploaded": triples_uploaded + quintuples_uploaded,
                "errors": errors
            }
            
            if success:
                logger.info(f"Successfully uploaded {triples_uploaded} triples and {quintuples_uploaded} quintuples to Neo4j")
                print(f"✅ 成功上传：{triples_uploaded} 个三元组，{quintuples_uploaded} 个五元组")
                
                # 上传成功后清空recent_memory.json文件
                print("正在清空recent_memory.json文件...")
                if self.clear_recent_memory():
                    logger.info("Successfully cleared recent_memory.json after upload")
                    print("✅ recent_memory.json已清空")
                else:
                    logger.warning("Failed to clear recent_memory.json after upload")
                    print("⚠️ 清空recent_memory.json失败")
            else:
                logger.warning(f"Uploaded {triples_uploaded} triples and {quintuples_uploaded} quintuples with {len(errors)} errors")
                print(f"⚠️ 部分上传成功：{triples_uploaded} 个三元组，{quintuples_uploaded} 个五元组，{len(errors)} 个错误")
                
                # 即使部分失败，如果有数据上传成功，也清空文件
                if triples_uploaded > 0 or quintuples_uploaded > 0:
                    print("部分数据上传成功，正在清空recent_memory.json文件...")
                    if self.clear_recent_memory():
                        logger.info("Successfully cleared recent_memory.json after partial upload")
                        print("✅ recent_memory.json已清空")
                    else:
                        logger.warning("Failed to clear recent_memory.json after partial upload")
                        print("⚠️ 清空recent_memory.json失败")
            
            return result
            
        except FileNotFoundError:
            logger.error(f"Recent memory file not found: {recent_memory_file}")
            return {
                "success": False,
                "error": "Recent memory file not found",
                "triples_uploaded": 0,
                "quintuples_uploaded": 0
            }
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse recent memory JSON: {e}")
            return {
                "success": False,
                "error": f"JSON parse error: {e}",
                "triples_uploaded": 0,
                "quintuples_uploaded": 0
            }
        except Exception as e:
            logger.error(f"Failed to upload recent memory: {e}")
            return {
                "success": False,
                "error": str(e),
                "triples_uploaded": 0,
                "quintuples_uploaded": 0
            }
    
    def _upload_triple_with_dedup(self, session, triple_data) -> bool:
        """上传三元组到Neo4j，处理查重和覆盖逻辑"""
        try:
            subject = triple_data.get("subject", "")
            predicate = triple_data.get("predicate", "")
            object_name = triple_data.get("object", "")
            source = triple_data.get("source", "")
            confidence = triple_data.get("confidence", 0.0)
            time_record = triple_data.get("time_record", "")
            
            if not all([subject, predicate, object_name]):
                logger.warning(f"Incomplete triple data: {triple_data}")
                return False
            
            # 创建或更新主体节点（如果存在则覆盖属性）
            session.run("""
                MERGE (s:Entity {name: $subject})
                ON CREATE SET s.created_at = $time_record
                SET s.source = $source,
                    s.confidence = $confidence,
                    s.last_updated = $time_record
            """, 
                subject=subject,
                source=source,
                confidence=confidence,
                time_record=time_record
            )
            
            # 创建或更新客体节点（如果存在则覆盖属性）
            session.run("""
                MERGE (o:Entity {name: $object})
                ON CREATE SET o.created_at = $time_record
                SET o.source = $source,
                    o.confidence = $confidence,
                    o.last_updated = $time_record
            """, 
                object=object_name,
                source=source,
                confidence=confidence,
                time_record=time_record
            )
            
            # 处理关系名称
            predicate_safe = predicate.replace(" ", "_").replace("-", "_").upper()
            if not predicate_safe.replace("_", "").isalnum():
                predicate_safe = "RELATED_TO"
            
            # 创建或更新关系（如果存在则覆盖属性）
            session.run(f"""
                MATCH (s:Entity {{name: $subject}})
                MATCH (o:Entity {{name: $object}})
                MERGE (s)-[r:{predicate_safe}]->(o)
                SET r.predicate = $predicate,
                    r.source = $source,
                    r.confidence = $confidence,
                    r.created_at = $time_record,
                    r.last_updated = $time_record
            """, 
                subject=subject,
                object=object_name,
                predicate=predicate,
                source=source,
                confidence=confidence,
                time_record=time_record
            )
            
            logger.debug(f"Uploaded triple with dedup: {subject} -> {predicate} -> {object_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload triple with dedup: {e}")
            return False
    
    def _upload_quintuple_with_dedup(self, session, quintuple_data) -> bool:
        """上传五元组到Neo4j，处理查重和覆盖逻辑"""
        try:
            subject = quintuple_data.get("subject", "")
            action = quintuple_data.get("action", "")
            object_name = quintuple_data.get("object", "")
            time = quintuple_data.get("time", "")
            location = quintuple_data.get("location", "")
            source = quintuple_data.get("source", "")
            confidence = quintuple_data.get("confidence", 0.0)
            importance = quintuple_data.get("importance", 0.5)
            time_record = quintuple_data.get("time_record", "")
            
            if not all([subject, action, object_name]):
                logger.warning(f"Incomplete quintuple data: {quintuple_data}")
                return False
            
            # 创建或更新主体节点
            session.run("""
                MERGE (s:Entity {name: $subject})
                ON CREATE SET s.created_at = $time_record
                SET s.source = $source,
                    s.confidence = $confidence,
                    s.last_updated = $time_record
            """, 
                subject=subject,
                source=source,
                confidence=confidence,
                time_record=time_record
            )
            
            # 创建或更新客体节点
            session.run("""
                MERGE (o:Entity {name: $object})
                ON CREATE SET o.created_at = $time_record
                SET o.source = $source,
                    o.confidence = $confidence,
                    o.importance = $importance,
                    o.significance = 1,
                    o.last_updated = $time_record
            """, 
                object=object_name,
                source=source,
                confidence=confidence,
                importance=importance,
                time_record=time_record
            )
            
            # 如果有时间信息，创建层次化时间节点
            specific_time_node = None
            if time:
                specific_time_node = self._parse_and_create_hierarchical_time(
                    session, time, time_record
                )
            
            # 如果有地点信息，创建或更新地点节点
            if location:
                session.run("""
                    MERGE (l:Location {name: $location})
                    ON CREATE SET l.created_at = $time_record
                    SET l.source = $source,
                        l.last_updated = $time_record
                """, 
                    location=location,
                    source=source,
                    time_record=time_record
                )
            
            # 处理动作关系名称
            action_safe = action.replace(" ", "_").replace("-", "_").upper()
            if not action_safe.replace("_", "").isalnum():
                action_safe = "PERFORMED_ACTION"
            
            # 创建或更新主要动作关系
            session.run(f"""
                MATCH (s:Entity {{name: $subject}})
                MATCH (o:Entity {{name: $object}})
                MERGE (s)-[r:{action_safe}]->(o)
                SET r.action = $action,
                    r.source = $source,
                    r.confidence = $confidence,
                    r.importance = $importance,
                    r.created_at = $time_record,
                    r.time = $time,
                    r.location = $location,
                    r.last_updated = $time_record
            """, 
                subject=subject,
                object=object_name,
                action=action,
                source=source,
                confidence=confidence,
                importance=importance,
                time_record=time_record,
                time=time,
                location=location
            )
            
            # 如果有时间，创建时间关系（连接到最具体的时间节点）
            if specific_time_node:
                # 根据时间节点类型选择合适的标签查询
                time_query = """
                    MATCH (o:Entity {name: $object})
                    MATCH (t {name: $time_node})
                    MERGE (o)-[r:HAPPENED_AT]->(t)
                    SET r.source = $source,
                        r.created_at = $time_record,
                        r.action_context = $action,
                        r.last_updated = $time_record,
                        r.importance = $importance
                """
                session.run(time_query, 
                    object=object_name,
                    time_node=specific_time_node,
                    source=source,
                    time_record=time_record,
                    action=action,
                    importance=importance
                )
            
            # 如果有地点，创建或更新地点关系
            # 如果有地点，关联到事件（object）而非主体
            if location:
                session.run("""
                    MATCH (o:Entity {name: $object})
                    MATCH (l:Location {name: $location})
                    MERGE (o)-[r:HAPPENED_IN]->(l)
                    SET r.source = $source,
                        r.created_at = $time_record,
                        r.action_context = $action,
                        r.last_updated = $time_record,
                        r.importance = $importance
                """, 
                    object=object_name,
                    location=location,
                    source=source,
                    time_record=time_record,
                    action=action,
                    importance=importance
                )
            
            logger.debug(f"Uploaded quintuple with dedup: {subject} -> {action} -> {object_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload quintuple with dedup: {e}")
            return False


# 全局实例
_kg_manager = None

def get_knowledge_graph_manager() -> KnowledgeGraphManager:
    """获取知识图谱管理器单例"""
    global _kg_manager
    if _kg_manager is None:
        _kg_manager = KnowledgeGraphManager()
    return _kg_manager

def write_memories_to_graph(triples: List, quintuples: List) -> Dict[str, Any]:
    """便捷函数：将记忆数据写入知识图谱"""
    manager = get_knowledge_graph_manager()
    return manager.write_memories_batch(triples, quintuples)

def clear_recent_memory_file() -> bool:
    """便捷函数：清空 recent_memory.json 文件"""
    manager = get_knowledge_graph_manager()
    return manager.clear_recent_memory()

def clear_all_memory_interactive() -> bool:
    """便捷函数：交互式清空Neo4j中的全部记忆数据"""
    manager = get_knowledge_graph_manager()
    return manager.clear_all_memory()

def upload_recent_memory_to_graph() -> Dict[str, Any]:
    """便捷函数：将 recent_memory.json 中的记忆上传到 Neo4j"""
    manager = get_knowledge_graph_manager()
    return manager.upload_recent_memory()

def load_neo4j_data_to_file() -> bool:
    """便捷函数：检查Neo4j连接并将数据下载到neo4j_memory.json文件"""
    manager = get_knowledge_graph_manager()
    return manager.download_neo4j_data()

def relevant_memories_by_keywords(keywords: List[str], max_results: int = 10) -> str:
    """
    直接使用关键词列表从Neo4j查询相关的记忆信息
    
    Args:
        keywords: 已提取的关键词列表
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
        
        if not keywords:
            logger.debug("[记忆查询] 关键词列表为空")
            return ""
        
        logger.debug(f"[记忆查询] 使用关键词: {keywords}")
        
        memories = []
        
        with kg_manager.driver.session() as session:
            # 查询实体节点
            for keyword in keywords[:5]:  # 限制关键词数量避免查询过多
                # 查询包含关键词的实体
                entity_query = """
                MATCH (e:Entity)
                WHERE toLower(e.name) CONTAINS $keyword
                RETURN e.name as entity, 
                       e.source as source,
                       e.importance as importance,
                       e.confidence as confidence,
                       e.last_updated as last_updated
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
                        'confidence': record.get('confidence', 0.5),
                        'last_updated': record.get('last_updated', '')
                    }
                    memories.append(entity_info)
            
            # 查询相关的五元组关系
            for keyword in keywords[:5]:
                relation_query = """
                MATCH (s:Entity)-[r]->(o:Entity)
                WHERE (toLower(s.name) CONTAINS $keyword OR toLower(o.name) CONTAINS $keyword)
                  AND r.action IS NOT NULL
                RETURN s.name as subject,
                       type(r) as relation_type,
                       r.action as action,
                       o.name as object,
                       r.time as time,
                       r.location as location,
                       r.source as source,
                       r.importance as importance,
                       r.confidence as confidence,
                       r.created_at as created_at
                ORDER BY r.importance DESC, r.confidence DESC
                LIMIT 3
                """
                
                result = session.run(relation_query, keyword=keyword.lower())
                for record in result:
                    relation_info = {
                        'type': 'relation',
                        'subject': record['subject'],
                        'relation_type': record['relation_type'],
                        'action': record.get('action', ''),
                        'object': record['object'],
                        'time': record.get('time', ''),
                        'location': record.get('location', ''),
                        'source': record['source'],
                        'importance': record.get('importance', 0.5),
                        'confidence': record.get('confidence', 0.5),
                        'created_at': record.get('created_at', '')
                    }
                    memories.append(relation_info)
            
            # 查询时间相关的记忆
            for keyword in keywords[:3]:
                time_query = """
                MATCH (e:Entity)-[r:HAPPENED_AT]->(t:Time)
                WHERE toLower(e.name) CONTAINS $keyword OR toLower(t.name) CONTAINS $keyword
                RETURN e.name as entity,
                       t.name as time_node,
                       r.action_context as action,
                       r.importance as importance,
                       r.source as source
                ORDER BY r.importance DESC
                LIMIT 2
                """
                
                result = session.run(time_query, keyword=keyword.lower())
                for record in result:
                    time_info = {
                        'type': 'time_relation',
                        'entity': record['entity'],
                        'time_node': record['time_node'],
                        'action': record.get('action', ''),
                        'importance': record.get('importance', 0.5),
                        'source': record['source']
                    }
                    memories.append(time_info)
            
            # 查询地点相关的记忆
            for keyword in keywords[:3]:
                location_query = """
                MATCH (e:Entity)-[r:HAPPENED_IN]->(l:Location)
                WHERE toLower(e.name) CONTAINS $keyword OR toLower(l.name) CONTAINS $keyword
                RETURN e.name as entity,
                       l.name as location,
                       r.action_context as action,
                       r.importance as importance,
                       r.source as source
                ORDER BY r.importance DESC
                LIMIT 2
                """
                
                result = session.run(location_query, keyword=keyword.lower())
                for record in result:
                    location_info = {
                        'type': 'location_relation',
                        'entity': record['entity'],
                        'location': record['location'],
                        'action': record.get('action', ''),
                        'importance': record.get('importance', 0.5),
                        'source': record['source']
                    }
                    memories.append(location_info)
        
        if not memories:
            return ""
        
        # 按重要性和置信度排序
        memories.sort(key=lambda x: (x.get('importance', 0.5), x.get('confidence', 0.5)), reverse=True)
        
        # 去重（基于内容）
        unique_memories = []
        seen_content = set()
        for memory in memories:
            if memory['type'] == 'entity':
                content_key = f"entity_{memory['name']}"
            elif memory['type'] == 'relation':
                content_key = f"relation_{memory['subject']}_{memory['action']}_{memory['object']}"
            elif memory['type'] == 'time_relation':
                content_key = f"time_{memory['entity']}_{memory['time_node']}"
            else:  # location_relation
                content_key = f"location_{memory['entity']}_{memory['location']}"
            
            if content_key not in seen_content:
                seen_content.add(content_key)
                unique_memories.append(memory)
        
        # 限制结果数量
        unique_memories = unique_memories[:max_results]
        
        # 格式化输出
        formatted_memories = []
        for memory in unique_memories:
            if memory['type'] == 'entity':
                formatted_memories.append(
                    f"实体: {memory['name']} (重要性: {memory['importance']:.2f}, 来源: {memory.get('source', 'unknown')})"
                )
            elif memory['type'] == 'relation':
                time_str = f" 时间: {memory['time']}" if memory['time'] else ""
                location_str = f" 地点: {memory['location']}" if memory['location'] else ""
                action_str = f" 动作: {memory['action']}" if memory['action'] else ""
                
                formatted_memories.append(
                    f"关系: {memory['subject']} -> {memory['object']}"
                    f"{action_str}{time_str}{location_str} "
                    f"(重要性: {memory['importance']:.2f})"
                )
            elif memory['type'] == 'time_relation':
                action_str = f" 动作: {memory['action']}" if memory['action'] else ""
                formatted_memories.append(
                    f"时间记忆: {memory['entity']} 在 {memory['time_node']}{action_str} "
                    f"(重要性: {memory['importance']:.2f})"
                )
            elif memory['type'] == 'location_relation':
                action_str = f" 动作: {memory['action']}" if memory['action'] else ""
                formatted_memories.append(
                    f"地点记忆: {memory['entity']} 在 {memory['location']}{action_str} "
                    f"(重要性: {memory['importance']:.2f})"
                )
        
        result = "\n".join(formatted_memories)
        logger.info(f"[记忆查询] 基于关键词 {keywords} 找到 {len(formatted_memories)} 条相关记忆")
        return result
        
    except Exception as e:
        logger.error(f"[错误] 基于关键词的记忆查询失败: {e}")
        return ""

def delete_node_or_relation_by_id(element_id: str) -> Dict[str, Any]:
    """便捷函数：根据元素ID删除节点或关系"""
    manager = get_knowledge_graph_manager()
    return manager.delete_node_or_relation(element_id)


