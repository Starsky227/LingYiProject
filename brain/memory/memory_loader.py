#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Graph Loader
从 Neo4j 数据库加载知识图谱并保存到 JSON 文件
不与对话消息或其他组件进行联动
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, AuthError
except ImportError:
    print("Neo4j driver not installed. Please install with: pip install neo4j")
    sys.exit(1)

from system.config import config

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class GraphNode:
    """图谱节点数据结构"""
    id: str
    labels: List[str]
    properties: Dict[str, Any]

@dataclass
class GraphRelationship:
    """图谱关系数据结构"""
    id: str
    type: str
    start_node: str
    end_node: str
    properties: Dict[str, Any]

@dataclass
class MemoryGraph:
    """内存图谱数据结构"""
    nodes: List[GraphNode]
    relationships: List[GraphRelationship]
    metadata: Dict[str, Any]
    updated_at: str

class Neo4jConnector:
    """Neo4j 数据库连接器"""
    
    def __init__(self):
        self.driver = None
        self.connected = False
        
    def connect(self) -> bool:
        """连接到 Neo4j 数据库"""
        try:
            # 从配置中获取连接信息
            uri = config.grag.neo4j_uri
            user = config.grag.neo4j_user
            password = config.grag.neo4j_password
            database = config.grag.neo4j_database
            
            logger.info(f"Connecting to Neo4j at {uri}")
            
            self.driver = GraphDatabase.driver(
                uri, 
                auth=(user, password),
                database=database
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
    
    def load_all_nodes(self) -> List[GraphNode]:
        """加载所有节点"""
        nodes = []
        
        if not self.connected:
            logger.error("Not connected to Neo4j")
            return nodes
            
        try:
            with self.driver.session() as session:
                # 查询所有节点
                result = session.run("""
                    MATCH (n)
                    RETURN ID(n) as id, labels(n) as labels, properties(n) as properties
                """)
                
                for record in result:
                    node = GraphNode(
                        id=str(record["id"]),
                        labels=record["labels"],
                        properties=dict(record["properties"])
                    )
                    nodes.append(node)
                    
                logger.info(f"Loaded {len(nodes)} nodes from Neo4j")
                
        except Exception as e:
            logger.error(f"Failed to load nodes: {e}")
            
        return nodes
    
    def load_all_relationships(self) -> List[GraphRelationship]:
        """加载所有关系"""
        relationships = []
        
        if not self.connected:
            logger.error("Not connected to Neo4j")
            return relationships
            
        try:
            with self.driver.session() as session:
                # 查询所有关系
                result = session.run("""
                    MATCH (a)-[r]->(b)
                    RETURN ID(r) as id, type(r) as type, 
                           ID(a) as start_node, ID(b) as end_node,
                           properties(r) as properties
                """)
                
                for record in result:
                    relationship = GraphRelationship(
                        id=str(record["id"]),
                        type=record["type"],
                        start_node=str(record["start_node"]),
                        end_node=str(record["end_node"]),
                        properties=dict(record["properties"])
                    )
                    relationships.append(relationship)
                    
                logger.info(f"Loaded {len(relationships)} relationships from Neo4j")
                
        except Exception as e:
            logger.error(f"Failed to load relationships: {e}")
            
        return relationships
    
    def get_database_info(self) -> Dict[str, Any]:
        """获取数据库信息"""
        info = {}
        
        if not self.connected:
            return info
            
        try:
            with self.driver.session() as session:
                # 获取节点统计
                result = session.run("MATCH (n) RETURN count(n) as node_count")
                info["node_count"] = result.single()["node_count"]
                
                # 获取关系统计
                result = session.run("MATCH ()-[r]->() RETURN count(r) as rel_count")
                info["relationship_count"] = result.single()["rel_count"]
                
                # 获取节点标签统计
                result = session.run("""
                    MATCH (n)
                    UNWIND labels(n) as label
                    RETURN label, count(*) as count
                    ORDER BY count DESC
                """)
                info["node_labels"] = {record["label"]: record["count"] for record in result}
                
                # 获取关系类型统计
                result = session.run("""
                    MATCH ()-[r]->()
                    RETURN type(r) as rel_type, count(*) as count
                    ORDER BY count DESC
                """)
                info["relationship_types"] = {record["rel_type"]: record["count"] for record in result}
                
        except Exception as e:
            logger.error(f"Failed to get database info: {e}")
            
        return info

def load_memory_graph() -> Optional[MemoryGraph]:
    """从 Neo4j 加载完整的内存图谱"""
    
    # 检查 GRAG 是否启用
    if not config.grag.enabled:
        logger.warning("GRAG is disabled in configuration")
        return None
    
    connector = Neo4jConnector()
    
    try:
        # 连接数据库
        if not connector.connect():
            logger.error("Failed to connect to Neo4j database")
            return None
        
        # 加载节点和关系
        nodes = connector.load_all_nodes()
        relationships = connector.load_all_relationships()
        
        # 获取元数据
        metadata = connector.get_database_info()
        metadata["source"] = "neo4j"
        metadata["neo4j_uri"] = config.grag.neo4j_uri
        metadata["neo4j_database"] = config.grag.neo4j_database
        
        # 创建图谱对象
        graph = MemoryGraph(
            nodes=nodes,
            relationships=relationships,
            metadata=metadata,
            updated_at=datetime.now().isoformat()
        )
        
        logger.info(f"Successfully loaded memory graph: {len(nodes)} nodes, {len(relationships)} relationships")
        return graph
        
    except Exception as e:
        logger.error(f"Failed to load memory graph: {e}")
        return None
        
    finally:
        connector.disconnect()

def save_memory_graph_to_file(graph: MemoryGraph, file_path: Optional[str] = None) -> bool:
    """将内存图谱保存到 JSON 文件"""
    
    if file_path is None:
        file_path = os.path.join(config.system.log_dir, "memory_graph.json")
    
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 转换为字典格式
        graph_dict = asdict(graph)
        
        # 保存到文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(graph_dict, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Memory graph saved to {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save memory graph to file: {e}")
        return False

def load_memory_graph_from_file(file_path: Optional[str] = None) -> Optional[MemoryGraph]:
    """从 JSON 文件加载内存图谱"""
    
    if file_path is None:
        file_path = os.path.join(config.system.log_dir, "memory_graph.json")
    
    try:
        if not os.path.exists(file_path):
            logger.warning(f"Memory graph file not found: {file_path}")
            return None
        
        with open(file_path, 'r', encoding='utf-8') as f:
            graph_dict = json.load(f)
        
        # 转换为对象
        nodes = [GraphNode(**node) for node in graph_dict["nodes"]]
        relationships = [GraphRelationship(**rel) for rel in graph_dict["relationships"]]
        
        graph = MemoryGraph(
            nodes=nodes,
            relationships=relationships,
            metadata=graph_dict["metadata"],
            updated_at=graph_dict["updated_at"]
        )
        
        logger.info(f"Memory graph loaded from {file_path}: {len(nodes)} nodes, {len(relationships)} relationships")
        return graph
        
    except Exception as e:
        logger.error(f"Failed to load memory graph from file: {e}")
        return None

def update_memory_graph_file() -> bool:
    """更新内存图谱文件 - 从 Neo4j 重新加载并保存"""
    
    logger.info("Updating memory graph file...")
    
    # 从 Neo4j 加载图谱
    graph = load_memory_graph()
    
    if graph is None:
        logger.error("Failed to load memory graph from Neo4j")
        return False
    
    # 保存到文件
    success = save_memory_graph_to_file(graph)
    
    if success:
        logger.info("Memory graph file updated successfully")
    else:
        logger.error("Failed to update memory graph file")
    
    return success

def main():
    """主函数 - 用于命令行调用"""
    
    print("Memory Graph Loader")
    print("=" * 50)
    
    # 显示配置信息
    print(f"GRAG enabled: {config.grag.enabled}")
    print(f"Neo4j URI: {config.grag.neo4j_uri}")
    print(f"Neo4j Database: {config.grag.neo4j_database}")
    print(f"Output file: {os.path.join(config.system.log_dir, 'memory_graph.json')}")
    print()
    
    if not config.grag.enabled:
        print("GRAG is disabled. Please enable it in config.json")
        return
    
    # 更新图谱文件
    success = update_memory_graph_file()
    
    if success:
        print("✓ Memory graph loaded and saved successfully")
        
        # 显示统计信息
        graph = load_memory_graph_from_file()
        if graph:
            print(f"  - Nodes: {len(graph.nodes)}")
            print(f"  - Relationships: {len(graph.relationships)}")
            print(f"  - Updated at: {graph.updated_at}")
            
            if graph.metadata:
                print("  - Metadata:")
                for key, value in graph.metadata.items():
                    if key not in ['source', 'neo4j_uri', 'neo4j_database']:
                        print(f"    {key}: {value}")
    else:
        print("✗ Failed to load memory graph")

if __name__ == "__main__":
    main()
