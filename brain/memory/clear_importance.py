#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
清理重要性低的节点
删除Neo4j中importance<=0.2的节点
"""

import logging
import sys
from pathlib import Path
from neo4j import GraphDatabase

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from system.config import config

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ImportanceCleaner:
    """重要性清理器"""
    
    def __init__(self):
        self.driver = None
        self.connected = False
        self._connect()
    
    def _connect(self) -> bool:
        """连接到 Neo4j 数据库"""
        if not config.grag.enabled:
            logger.warning("GRAG is disabled, skipping Neo4j connection")
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
                session.run("RETURN 1")
            
            self.connected = True
            logger.info("Successfully connected to Neo4j")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            self.connected = False
            return False
    
    def close(self):
        """关闭数据库连接"""
        if self.driver:
            self.driver.close()
            logger.info("Neo4j connection closed")
    
    def count_low_importance_nodes(self, threshold: float = 0.2) -> dict:
        """统计低重要性节点数量"""
        if not self.connected:
            logger.error("Not connected to Neo4j")
            return {"error": "Not connected to database"}
        
        try:
            with self.driver.session() as session:
                # 统计各类型的低重要性节点
                queries = {
                    "Entity": "MATCH (n:Entity) WHERE n.importance IS NOT NULL AND n.importance <= $threshold RETURN count(n) as count",
                    "Location": "MATCH (n:Location) WHERE n.importance IS NOT NULL AND n.importance <= $threshold RETURN count(n) as count",
                    "Time": "MATCH (n:Time) WHERE n.importance IS NOT NULL AND n.importance <= $threshold RETURN count(n) as count",
                    "All": "MATCH (n) WHERE n.importance IS NOT NULL AND n.importance <= $threshold RETURN count(n) as count"
                }
                
                counts = {}
                for node_type, query in queries.items():
                    result = session.run(query, threshold=threshold)
                    counts[node_type] = result.single()["count"]
                
                logger.info(f"Low importance nodes (importance <= {threshold}): {counts}")
                return counts
                
        except Exception as e:
            logger.error(f"Failed to count low importance nodes: {e}")
            return {"error": str(e)}
    
    def delete_low_importance_nodes(self, threshold: float = 0.2, dry_run: bool = True) -> dict:
        """删除低重要性节点"""
        if not self.connected:
            logger.error("Not connected to Neo4j")
            return {"error": "Not connected to database"}
        
        try:
            with self.driver.session() as session:
                # 首先统计将要删除的节点
                count_result = self.count_low_importance_nodes(threshold)
                if "error" in count_result:
                    return count_result
                
                if dry_run:
                    logger.info(f"DRY RUN: Would delete {count_result['All']} nodes with importance <= {threshold}")
                    return {
                        "dry_run": True,
                        "threshold": threshold,
                        "nodes_to_delete": count_result,
                        "message": f"DRY RUN: Would delete {count_result['All']} nodes"
                    }
                
                # 实际删除操作
                delete_queries = [
                    # 删除低重要性的实体节点及其关系
                    """
                    MATCH (n:Entity)
                    WHERE n.importance IS NOT NULL AND n.importance <= $threshold
                    DETACH DELETE n
                    """,
                    # 删除低重要性的位置节点及其关系
                    """
                    MATCH (n:Location)
                    WHERE n.importance IS NOT NULL AND n.importance <= $threshold
                    DETACH DELETE n
                    """,
                    # 删除低重要性的时间节点及其关系
                    """
                    MATCH (n:Time)
                    WHERE n.importance IS NOT NULL AND n.importance <= $threshold
                    DETACH DELETE n
                    """
                ]
                
                deleted_counts = []
                for query in delete_queries:
                    result = session.run(query, threshold=threshold)
                    summary = result.consume()
                    deleted_counts.append(summary.counters.nodes_deleted)
                
                total_deleted = sum(deleted_counts)
                logger.info(f"Successfully deleted {total_deleted} nodes with importance <= {threshold}")
                
                return {
                    "success": True,
                    "threshold": threshold,
                    "nodes_deleted": total_deleted,
                    "entity_deleted": deleted_counts[0],
                    "location_deleted": deleted_counts[1],
                    "time_deleted": deleted_counts[2],
                    "message": f"Successfully deleted {total_deleted} nodes"
                }
                
        except Exception as e:
            logger.error(f"Failed to delete low importance nodes: {e}")
            return {"error": str(e)}
    
    def clean_orphaned_relationships(self) -> dict:
        """清理孤立的关系（指向已删除节点的关系）"""
        if not self.connected:
            logger.error("Not connected to Neo4j")
            return {"error": "Not connected to database"}
        
        try:
            with self.driver.session() as session:
                # 查找孤立的关系
                orphan_query = """
                MATCH ()-[r]->()
                WHERE startNode(r) IS NULL OR endNode(r) IS NULL
                RETURN count(r) as count
                """
                
                result = session.run(orphan_query)
                orphan_count = result.single()["count"]
                
                if orphan_count > 0:
                    # 删除孤立的关系
                    delete_query = """
                    MATCH ()-[r]->()
                    WHERE startNode(r) IS NULL OR endNode(r) IS NULL
                    DELETE r
                    """
                    
                    session.run(delete_query)
                    logger.info(f"Cleaned up {orphan_count} orphaned relationships")
                
                return {
                    "success": True,
                    "orphaned_relationships_cleaned": orphan_count
                }
                
        except Exception as e:
            logger.error(f"Failed to clean orphaned relationships: {e}")
            return {"error": str(e)}


def main():
    """主函数"""
    cleaner = ImportanceCleaner()
    
    if not cleaner.connected:
        logger.error("Failed to connect to Neo4j. Please check your configuration.")
        return
    
    try:
        # 设置阈值
        threshold = 0.2
        
        # 显示将要删除的节点统计
        logger.info("=== Counting low importance nodes ===")
        count_result = cleaner.count_low_importance_nodes(threshold)
        if "error" in count_result:
            logger.error(f"Error counting nodes: {count_result['error']}")
            return
        
        if count_result['All'] == 0:
            logger.info(f"No nodes found with importance <= {threshold}")
            return
        
        # 先进行干运行
        logger.info("=== Performing dry run ===")
        dry_run_result = cleaner.delete_low_importance_nodes(threshold, dry_run=True)
        print(f"Dry run result: {dry_run_result}")
        
        # 询问用户是否继续
        response = input(f"\nFound {count_result['All']} nodes with importance <= {threshold}. Do you want to delete them? (y/N): ")
        
        if response.lower() in ['y', 'yes']:
            logger.info("=== Deleting low importance nodes ===")
            delete_result = cleaner.delete_low_importance_nodes(threshold, dry_run=False)
            print(f"Delete result: {delete_result}")
            
            if delete_result.get("success"):
                # 清理孤立的关系
                logger.info("=== Cleaning orphaned relationships ===")
                cleanup_result = cleaner.clean_orphaned_relationships()
                print(f"Cleanup result: {cleanup_result}")
        else:
            logger.info("Operation cancelled by user")
            
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
    except Exception as e:
        logger.error(f"Error during cleanup operation: {e}")
    finally:
        cleaner.close()


if __name__ == "__main__":
    main()