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
import sys
import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import asdict

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
    
    def _parse_and_create_hierarchical_time(self, session, time_str: str, source: str, time_record: str) -> Optional[str]:
        """
        解析时间字符串并创建层次化的时间节点
        支持多种格式:
        - "2025年11月3日12点" -> Year:2025 -> Month:11月 -> Day:3日 -> Hour:12点
        - "2025年11月3日" -> Year:2025 -> Month:11月 -> Day:3日  
        - "2025年11月" -> Year:2025 -> Month:11月
        - "2025年" -> Year:2025
        返回最具体的时间节点名称（用于连接事件）
        """
        import re
        
        if not time_str:
            return None
            
        try:
            # 解析时间组件
            year_match = re.search(r'(\d{4})年', time_str)
            month_match = re.search(r'(\d{1,2})月', time_str)
            day_match = re.search(r'(\d{1,2})日', time_str)
            hour_match = re.search(r'(\d{1,2})点', time_str)
            
            if not year_match:
                # 如果连年份都没有，创建通用时间节点
                session.run("""
                    MERGE (t:Time {name: $time})
                    SET t.source = $source,
                        t.last_updated = $time_record
                """, 
                    time=time_str,
                    source=source,
                    time_record=time_record
                )
                return time_str
                
            # 提取时间组件
            year = year_match.group(1)
            year_name = f"{year}年"
            most_specific_node = year_name
            
            # 创建年份节点
            session.run("""
                MERGE (y:Time:Year {name: $year_name})
                SET y.year = $year,
                    y.time_type = 'year',
                    y.source = $source,
                    y.last_updated = $time_record
            """, 
                year_name=year_name,
                year=int(year),
                source=source,
                time_record=time_record
            )
            
            # 如果有月份
            if month_match:
                month = month_match.group(1)
                # 去除前导零：02 -> 2, 03 -> 3, 但保留 10, 11, 12
                month = str(int(month))
                month_name = f"{month}月"
                most_specific_node = month_name
                
                # 创建月份节点
                session.run("""
                    MERGE (m:Time:Month {name: $month_name})
                    SET m.month = $month,
                        m.year = $year,
                        m.time_type = 'month',
                        m.source = $source,
                        m.last_updated = $time_record
                """, 
                    month_name=month_name,
                    month=int(month),
                    year=int(year),
                    source=source,
                    time_record=time_record
                )
                
                # 创建层次关系: Month -> Year
                session.run("""
                    MATCH (m:Time:Month {name: $month_name})
                    MATCH (y:Time:Year {name: $year_name})
                    MERGE (m)-[r:BELONGS_TO]->(y)
                    SET r.created_at = $time_record,
                        r.hierarchy_type = 'month_to_year'
                """, 
                    month_name=month_name,
                    year_name=year_name,
                    time_record=time_record
                )
                
                # 如果有日期
                if day_match:
                    day = day_match.group(1)
                    # 去除前导零：02 -> 2, 03 -> 3, 但保留 10, 11等
                    day = str(int(day))
                    day_name = f"{day}日"
                    most_specific_node = day_name
                    
                    # 创建日期节点
                    session.run("""
                        MERGE (d:Time:Day {name: $day_name})
                        SET d.day = $day,
                            d.month = $month,
                            d.year = $year,
                            d.time_type = 'day',
                            d.source = $source,
                            d.last_updated = $time_record
                    """, 
                        day_name=day_name,
                        day=int(day),
                        month=int(month),
                        year=int(year),
                        source=source,
                        time_record=time_record
                    )
                    
                    # 创建层次关系: Day -> Month
                    session.run("""
                        MATCH (d:Time:Day {name: $day_name})
                        MATCH (m:Time:Month {name: $month_name})
                        MERGE (d)-[r:BELONGS_TO]->(m)
                        SET r.created_at = $time_record,
                            r.hierarchy_type = 'day_to_month'
                    """, 
                        day_name=day_name,
                        month_name=month_name,
                        time_record=time_record
                    )
                    
                    # 如果有小时
                    if hour_match:
                        hour = hour_match.group(1)
                        # 去除前导零：02 -> 2, 03 -> 3, 但保留 10, 11等
                        hour = str(int(hour))
                        hour_name = f"{hour}点"
                        most_specific_node = hour_name
                        
                        # 创建小时节点
                        session.run("""
                            MERGE (h:Time:Hour {name: $hour_name})
                            SET h.hour = $hour,
                                h.day = $day,
                                h.month = $month,
                                h.year = $year,
                                h.time_type = 'hour',
                                h.source = $source,
                                h.last_updated = $time_record
                        """, 
                            hour_name=hour_name,
                            hour=int(hour),
                            day=int(day),
                            month=int(month),
                            year=int(year),
                            source=source,
                            time_record=time_record
                        )
                        
                        # 创建层次关系: Hour -> Day
                        session.run("""
                            MATCH (h:Time:Hour {name: $hour_name})
                            MATCH (d:Time:Day {name: $day_name})
                            MERGE (h)-[r:BELONGS_TO]->(d)
                            SET r.created_at = $time_record,
                                r.hierarchy_type = 'hour_to_day'
                        """, 
                            hour_name=hour_name,
                            day_name=day_name,
                            time_record=time_record
                        )
            
            logger.debug(f"Created hierarchical time, most specific node: {most_specific_node}")
            return most_specific_node  # 返回最具体的时间节点用于连接事件
            
        except Exception as e:
            logger.error(f"Failed to parse hierarchical time '{time_str}': {e}")
            # 回退到创建通用时间节点
            try:
                session.run("""
                    MERGE (t:Time {name: $time})
                    SET t.source = $source,
                        t.last_updated = $time_record
                """, 
                    time=time_str,
                    source=source,
                    time_record=time_record
                )
                return time_str
            except Exception as fallback_e:
                logger.error(f"Failed to create fallback time node: {fallback_e}")
                return None
    
    def _create_quintuple_cross_references(self, session, quintuples: List) -> bool:
        """
        检测五元组之间的交叉引用并创建关系
        例如：A告诉B关于某个事件 -> 直接链接到该事件的五元组
        """
        import re
        
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
                SET r.confidence_score = $confidence_score,
                    r.created_at = $time_record,
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
                SET r.predicate = $predicate,
                    r.source = $source,
                    r.confidence = $confidence,
                    r.created_at = $time_record,
                    r.type = 'triple'
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
                specific_time_node = self._parse_and_create_hierarchical_time(
                    session, quintuple.time, quintuple.source, quintuple.time_record
                )
            
            # 如果有地点信息，创建地点节点
            if quintuple.location:
                session.run("""
                    MERGE (l:Location {name: $location})
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
                SET r.action = $action,
                    r.source = $source,
                    r.confidence = $confidence,
                    r.importance = $importance,
                    r.created_at = $time_record,
                    r.type = 'quintuple',
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
                    SET r.source = $source,
                        r.created_at = $time_record,
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
                    SET r.source = $source,
                        r.created_at = $time_record,
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
                triple_rels = session.run("MATCH ()-[r {type: 'triple'}]->() RETURN count(r) as count").single()["count"]
                quintuple_rels = session.run("MATCH ()-[r {type: 'quintuple'}]->() RETURN count(r) as count").single()["count"]
                
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
    
    def clear_recent_memory(self) -> bool:
        """清空 recent_memory.json 文件，重置为初始状态"""
        try:
            from datetime import datetime
            
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
                    r.type = 'triple',
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
                    session, time, source, time_record
                )
            
            # 如果有地点信息，创建或更新地点节点
            if location:
                session.run("""
                    MERGE (l:Location {name: $location})
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
                    r.type = 'quintuple',
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
