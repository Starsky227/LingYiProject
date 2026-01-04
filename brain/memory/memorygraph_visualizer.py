#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Graph Viewer
将 memory_graph.json 中的记忆数据通过 HTML 进行可视化展示
支持交互记忆管理：添加，修改，删除节点与关系
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
import webbrowser
import signal
import threading
import time
import atexit

# 设置日志级别为WARNING，避免显示INFO级别的日志
logging.getLogger().setLevel(logging.WARNING)
logging.getLogger('brain.memory.memory_download_from_neo4j').setLevel(logging.WARNING)

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

from system.config import config
from system.config import is_neo4j_available
from brain.memory.memory_download_from_neo4j import update_memory_graph_file, Neo4jConnector
from brain.memory.knowledge_graph_manager import load_neo4j_data_to_file, get_knowledge_graph_manager

logger = logging.getLogger(__name__)

class MemoryGraphViewer:
    """记忆图谱HTML可视化器"""
    
    def __init__(self, memory_graph_file: Optional[str] = None):
        self.memory_graph_file = memory_graph_file or os.path.join(config.system.log_dir, "memory_graph.json")
        self.local_memory_file = os.path.join(os.path.dirname(__file__), "memory_graph", "local_memory.json")
        self.neo4j_memory_file = os.path.join(os.path.dirname(__file__), "memory_graph", "neo4j_memory.json")
        self.graph_data = None
        self.local_data = None
        self.neo4j_data = None
        self.html_template = None
        self.neo4j_connected = False
        
    def load_memory_graph(self) -> bool:
        """加载记忆图谱数据"""
        try:
            # 加载本地内存数据
            if os.path.exists(self.local_memory_file):
                with open(self.local_memory_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        self.local_data = json.loads(content)
                        logger.info(f"Loaded local memory data: {len(self.local_data.get('nodes', []))} nodes")
                    else:
                        self.local_data = {"nodes": [], "relationships": []}
            else:
                self.local_data = {"nodes": [], "relationships": []}
            
            # 加载Neo4j内存数据
            if os.path.exists(self.neo4j_memory_file):
                with open(self.neo4j_memory_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        self.neo4j_data = json.loads(content)
                        logger.info(f"Loaded neo4j memory data: {len(self.neo4j_data.get('nodes', []))} nodes")
                    else:
                        self.neo4j_data = {"nodes": [], "relationships": []}
            else:
                self.neo4j_data = {"nodes": [], "relationships": []}
            
            # 合并数据用于向后兼容
            self.graph_data = self.merge_graph_data()
            
            logger.info(f"Loaded merged memory graph data")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load memory graph: {e}")
            return False
    
    def merge_graph_data(self) -> Dict[str, Any]:
        """合并本地和Neo4j数据"""
        merged_nodes = []
        merged_relationships = []
        relationship_map = {}  # 用于检测重复关系
        
        # 添加本地节点
        if self.local_data and "nodes" in self.local_data:
            merged_nodes.extend(self.local_data["nodes"])
        
        # 添加Neo4j节点
        if self.neo4j_data and "nodes" in self.neo4j_data:
            merged_nodes.extend(self.neo4j_data["nodes"])
        
        # 添加本地关系（标记为本地来源）
        if self.local_data and "relationships" in self.local_data:
            for rel in self.local_data["relationships"]:
                rel_key = f"{rel['start_node']}-{rel['type']}-{rel['end_node']}"
                rel_copy = rel.copy()
                rel_copy["source_type"] = "local"
                merged_relationships.append(rel_copy)
                relationship_map[rel_key] = len(merged_relationships) - 1  # 记录关系位置
        
        # 添加Neo4j关系（标记为Neo4j来源，检测重复）
        if self.neo4j_data and "relationships" in self.neo4j_data:
            for rel in self.neo4j_data["relationships"]:
                rel_key = f"{rel['start_node']}-{rel['type']}-{rel['end_node']}"
                if rel_key in relationship_map:
                    # 关系已存在，更新为双端存储
                    existing_index = relationship_map[rel_key]
                    merged_relationships[existing_index]["source_type"] = "both"
                else:
                    # 新关系，添加为Neo4j来源
                    rel_copy = rel.copy()
                    rel_copy["source_type"] = "neo4j"
                    merged_relationships.append(rel_copy)
        
        return {
            "nodes": merged_nodes,
            "relationships": merged_relationships,
            "updated_at": datetime.now().isoformat()
        }
    
    def check_neo4j_connection(self) -> bool:
        """检查Neo4j连接状态"""
        try:
            self.neo4j_connected = is_neo4j_available()
            logger.info(f"Neo4j connection status: {self.neo4j_connected}")
            print(f"🔍 Neo4j连接状态检查结果: {self.neo4j_connected}")
            return self.neo4j_connected
        except Exception as e:
            logger.error(f"Failed to check Neo4j connection: {e}")
            print(f"❌ Neo4j连接检查失败: {e}")
            self.neo4j_connected = False
            return False
    
    def get_node_source(self, node_id: str) -> str:
        """获取节点来源（local, neo4j, both）"""
        in_local = False
        in_neo4j = False
        
        # 检查本地数据
        if self.local_data and "nodes" in self.local_data:
            for node in self.local_data["nodes"]:
                if str(node["id"]) == str(node_id):
                    in_local = True
                    break
        
        # 检查Neo4j数据
        if self.neo4j_data and "nodes" in self.neo4j_data:
            for node in self.neo4j_data["nodes"]:
                if str(node["id"]) == str(node_id):
                    in_neo4j = True
                    break
        
        if in_local and in_neo4j:
            return "both"
        elif in_local:
            return "local"
        elif in_neo4j:
            return "neo4j"
        else:
            return "unknown"
    
    def prepare_visualization_data(self) -> Dict[str, Any]:
        """准备可视化数据"""
        if not self.graph_data:
            return {}
        
        # 提取节点数据
        nodes = []
        node_id_map = {}  # Neo4j ID 到可视化 ID 的映射
        
        for i, node in enumerate(self.graph_data.get("nodes", [])):
            viz_node = {
                "id": i,
                "neo4j_id": node["id"],
                "label": node["properties"].get("name", f"Node_{node['id']}"),
                "group": node["labels"][0] if node["labels"] else "Unknown",
                "labels": node["labels"],
                "properties": node["properties"],
                "size": 10
            }
            
            # 根据节点类型设置不同颜色和大小
            if "Entity" in node["labels"]:
                viz_node["color"] = "#FFD700"  # 黄色 - 实体
                viz_node["size"] = 15
            elif "Time" in node["labels"]:
                viz_node["color"] = "#4CAF50"  # 绿色 - 时间
                viz_node["size"] = 12
            elif "Location" in node["labels"]:
                viz_node["color"] = "#FF9800"  # 橙色 - 角色
                viz_node["size"] = 12
            elif "Character" in node["labels"]:
                viz_node["color"] = "#2196F3"  # 蓝色 - 地点
                viz_node["size"] = 12
            else:
                viz_node["color"] = "#9E9E9E"  # 灰色 - 其他
            
            # 根据数据来源设置描边颜色
            source = self.get_node_source(node["id"])
            if source == "local":
                viz_node["strokeColor"] = "#808080"  # 灰色描边 - 只在本地
                viz_node["strokeWidth"] = 3
            elif source == "neo4j":
                viz_node["strokeColor"] = "#00FF00"  # 绿色描边 - 只在Neo4j
                viz_node["strokeWidth"] = 3
            elif source == "both":
                viz_node["strokeColor"] = "#0066FF"  # 蓝色描边 - 两者都有
                viz_node["strokeWidth"] = 3
            else:
                viz_node["strokeColor"] = "#FFFFFF"  # 白色描边 - 默认
                viz_node["strokeWidth"] = 2
            
            viz_node["source"] = source
            
            nodes.append(viz_node)
            node_id_map[node["id"]] = i
        
        # 提取关系数据
        links = []
        for rel in self.graph_data.get("relationships", []):
            if rel["start_node"] in node_id_map and rel["end_node"] in node_id_map:
                viz_link = {
                    "source": node_id_map[rel["start_node"]],
                    "target": node_id_map[rel["end_node"]],
                    "type": rel["type"],
                    "properties": rel["properties"],
                    "neo4j_id": rel["id"],
                    "source_type": rel.get("source_type", "neo4j")  # 默认为neo4j
                }
                links.append(viz_link)
        
        # 统计信息
        stats = {
            "total_nodes": len(nodes),
            "total_links": len(links),
            "node_types": {},
            "relation_types": {},
            "updated_at": self.graph_data.get("updated_at", "Unknown")
        }
        
        # 统计节点类型
        for node in nodes:
            node_type = node["group"]
            stats["node_types"][node_type] = stats["node_types"].get(node_type, 0) + 1
        
        # 统计关系类型
        for link in links:
            rel_type = link["type"]
            stats["relation_types"][rel_type] = stats["relation_types"].get(rel_type, 0) + 1
        
        return {
            "nodes": nodes,
            "links": links,
            "stats": stats,
            "metadata": self.graph_data.get("metadata", {}),
            "neo4j_connected": self.neo4j_connected
        }
    
    def load_html_template(self) -> str:
        """从外部文件加载HTML模板"""
        template_path = os.path.join(os.path.dirname(__file__), 'memory_graph_template.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            # 如果找不到外部模板文件，返回错误页面
            return """<!DOCTYPE html>"""
    
    def generate_html_visualization(self, output_file: Optional[str] = None) -> bool:
        """生成HTML可视化文件"""
        try:
            # 检查Neo4j连接状态
            self.check_neo4j_connection()
            
            # 加载数据
            if not self.load_memory_graph():
                return False
            
            # 准备可视化数据
            viz_data = self.prepare_visualization_data()
            
            if not viz_data:
                logger.error("No visualization data available")
                return False
            
            # 加载HTML模板
            html_content = self.load_html_template()
            
            # 替换模板变量
            html_content = html_content.replace("{{GRAPH_DATA}}", json.dumps(viz_data, ensure_ascii=False))
            html_content = html_content.replace("{{DATA_SOURCE}}", viz_data.get('metadata', {}).get('source', '未知'))
            html_content = html_content.replace("{{NEO4J_STATUS}}", "已连接" if viz_data.get('neo4j_connected', False) else "未连接")
            html_content = html_content.replace("{{NODE_COUNT}}", str(len(viz_data.get('nodes', []))))
            html_content = html_content.replace("{{LINK_COUNT}}", str(len(viz_data.get('links', []))))
            html_content = html_content.replace("{{LAST_UPDATE}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            
            # 确定输出文件路径
            if output_file is None:
                output_file = os.path.join(config.system.log_dir, "memory_graph_visualization.html")
            
            # 确保目录存在
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            # 写入文件
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            logger.info(f"HTML visualization generated: {output_file}")
            print(f"✅ HTML可视化文件已生成: {output_file}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to generate HTML visualization: {e}")
            print(f"❌ 生成HTML可视化失败: {e}")
            return False
    
    def prepare_graph_data(self) -> Dict[str, Any]:
        """准备图谱数据用于API返回，不包含HTML模板"""
        try:
            viz_data = self.prepare_visualization_data()
            
            if not viz_data:
                return {
                    "nodes": [],
                    "links": [],
                    "stats": {
                        "total_nodes": 0,
                        "total_links": 0,
                        "node_types": {},
                        "relation_types": {},
                        "updated_at": datetime.now().isoformat()
                    },
                    "neo4j_connected": self.neo4j_connected
                }
            
            return viz_data
            
        except Exception as e:
            logger.error(f"Failed to prepare graph data: {e}")
            return {
                "nodes": [],
                "links": [],
                "stats": {
                    "total_nodes": 0,
                    "total_links": 0,
                    "node_types": {},
                    "relation_types": {},
                    "updated_at": datetime.now().isoformat()
                },
                "neo4j_connected": False,
                "error": str(e)
            }
    
    def open_in_browser(self, html_file: Optional[str] = None):
        """在浏览器中打开HTML文件"""
        try:
            # 直接打开服务器地址而不是文件
            webbrowser.open("http://localhost:5000")
            print(f"🌐 已在浏览器中打开: http://localhost:5000")
                
        except Exception as e:
            logger.error(f"Failed to open in browser: {e}")
            print(f"❌ 无法在浏览器中打开: {e}")

def check_neo4j_connection() -> bool:
    """检查Neo4j连接状态"""
    try:
        connector = Neo4jConnector()
        connected = connector.connect()
        if connected:
            connector.disconnect()
        return connected
    except Exception as e:
        logger.error(f"Failed to check Neo4j connection: {e}")
        return False

def create_time_node_api(time_str: str) -> Dict[str, Any]:
    """
    API函数：创建时间节点
    
    Args:
        time_str: 时间字符串
        
    Returns:
        Dict: 包含成功状态和结果信息的字典
    """
    try:
        # 获取知识图谱管理器
        kg_manager = get_knowledge_graph_manager()
        
        # 检查连接状态
        if not kg_manager._ensure_connection():
            return {
                "success": False,
                "error": "Neo4j数据库连接失败",
                "created_node": None
            }
        
        # 创建时间节点
        with kg_manager.driver.session() as session:
            result_node = kg_manager.create_time_node(session, time_str)
            
            if result_node:
                logger.info(f"Successfully created time node: {result_node}")
                return {
                    "success": True,
                    "error": None,
                    "created_node": result_node,
                    "message": f"时间节点 '{result_node}' 创建成功"
                }
            else:
                return {
                    "success": False,
                    "error": "时间节点创建失败",
                    "created_node": None
                }
                
    except Exception as e:
        logger.error(f"Failed to create time node '{time_str}': {e}")
        return {
            "success": False,
            "error": str(e),
            "created_node": None
        }

def create_character_node_api(character_name: str, trust: float = 0.5, importance: float = 0.5) -> Dict[str, Any]:
    """
    API函数：创建角色节点
    
    Args:
        character_name: 角色名称
        trust: 信任度 (0-1)
        importance: 重要性 (0-1)
        
    Returns:
        Dict: 包含成功状态和结果信息的字典
    """
    try:
        # 获取知识图谱管理器
        kg_manager = get_knowledge_graph_manager()
        
        # 检查连接状态
        if not kg_manager._ensure_connection():
            return {
                "success": False,
                "error": "Neo4j数据库连接失败",
                "created_node": None
            }
        
        # 创建角色节点
        with kg_manager.driver.session() as session:
            result_node = kg_manager.create_character_node(session, character_name, importance, trust)
            
            if result_node:
                logger.info(f"Successfully created character node: {result_node}")
                return {
                    "success": True,
                    "error": None,
                    "created_node": result_node,
                    "message": f"角色节点 '{result_node}' 创建成功"
                }
            else:
                return {
                    "success": False,
                    "error": "角色节点创建失败",
                    "created_node": None
                }
                
    except Exception as e:
        logger.error(f"Failed to create character node '{character_name}': {e}")
        return {
            "success": False,
            "error": str(e),
            "created_node": None
        }

def create_entity_node_api(entity_name: str, importance: float = 0.5, note: str = "无") -> Dict[str, Any]:
    """
    API函数：创建实体节点
    
    Args:
        entity_name: 实体名称
        importance: 重要程度 (0-1)
        note: 备注 (默认"无")
        
    Returns:
        Dict: 包含成功状态和结果信息的字典
    """
    try:
        # 获取知识图谱管理器
        kg_manager = get_knowledge_graph_manager()
        
        # 检查连接状态
        if not kg_manager._ensure_connection():
            return {
                "success": False,
                "error": "Neo4j数据库连接失败",
                "created_node": None
            }
        
        # 创建实体节点
        with kg_manager.driver.session() as session:
            result_node = kg_manager.create_entity_node(session, entity_name, importance, note)
            
            if result_node:
                logger.info(f"Successfully created entity node: {result_node}")
                return {
                    "success": True,
                    "error": None,
                    "created_node": result_node,
                    "message": f"实体节点 '{result_node}' 创建成功"
                }
            else:
                return {
                    "success": False,
                    "error": "实体节点创建失败",
                    "created_node": None
                }
                
    except Exception as e:
        logger.error(f"Failed to create entity node '{entity_name}': {e}")
        return {
            "success": False,
            "error": str(e),
            "created_node": None
        }

def create_location_node_api(location_name: str) -> Dict[str, Any]:
    """
    API函数：创建地点节点
    
    Args:
        location_name: 地点名称
        
    Returns:
        Dict: 包含成功状态和结果信息的字典
    """
    try:
        # 获取知识图谱管理器
        kg_manager = get_knowledge_graph_manager()
        
        # 检查连接状态
        if not kg_manager._ensure_connection():
            return {
                "success": False,
                "error": "Neo4j数据库连接失败",
                "created_node": None
            }
        
        # 创建地点节点
        with kg_manager.driver.session() as session:
            result_node = kg_manager.create_location_node(session, location_name)
            
            if result_node:
                logger.info(f"Successfully created location node: {result_node}")
                return {
                    "success": True,
                    "error": None,
                    "created_node": result_node,
                    "message": f"地点节点 '{result_node}' 创建成功"
                }
            else:
                return {
                    "success": False,
                    "error": "地点节点创建失败",
                    "created_node": None
                }
                
    except Exception as e:
        logger.error(f"Failed to create location node '{location_name}': {e}")
        return {
            "success": False,
            "error": str(e),
            "created_node": None
        }

def main():
    """主函数"""
    print("🧠 记忆图谱可视化工具")
    print("=" * 50)
    
    # 检查Neo4j连接
    print("\n🔍 检查Neo4j连接状态...")
    neo4j_connected = check_neo4j_connection()
    
    if neo4j_connected:
        print("✅ Neo4j连接成功")
        print("\n🔄 正在从Neo4j下载最新数据...")
        download_success = load_neo4j_data_to_file()
        if not download_success:
            print("⚠️  Neo4j数据下载失败，将使用现有数据")
    else:
        print("❌ Neo4j连接失败")
        print("⚠️ 记忆图谱将为只读模式，无法进行编辑操作")
    
    viewer = MemoryGraphViewer()
    
    # 生成HTML可视化
    success = viewer.generate_html_visualization()
    
    if success:
        # 直接在浏览器中打开可视化页面
        print("\n🌐 正在浏览器中打开可视化页面...")
        viewer.open_in_browser()
        
        # 启动简单的HTTP服务器来处理API请求
        start_api_server()
    else:
        print("❌ 可视化生成失败")

def start_api_server():
    """启动简单的HTTP API服务器"""
    try:
        from flask import Flask, request, jsonify, send_file
        from flask_cors import CORS
        
        app = Flask(__name__)
        CORS(app)  # 允许跨域请求
        
        # 全局变量用于控制服务器状态
        server_running = threading.Event()
        server_running.set()
        last_heartbeat = time.time()
        client_connected = False
        heartbeat_timeout = 120  # 2分钟心跳超时时间（两个心跳周期）
        
        # 心跳检测端点
        @app.route('/api/heartbeat', methods=['GET', 'POST'])
        def heartbeat():
            nonlocal last_heartbeat, client_connected
            last_heartbeat = time.time()
            client_connected = True
            return jsonify({"status": "ok", "timestamp": last_heartbeat}), 200
        
        # 客户端断开连接通知端点
        @app.route('/api/disconnect', methods=['POST'])
        def client_disconnect():
            nonlocal client_connected
            try:
                # 尝试解析JSON数据，如果失败则使用默认值
                data = {}
                content_type = request.content_type or ''
                
                if 'application/json' in content_type:
                    data = request.get_json() or {}
                elif request.data:
                    # 尝试解析原始数据
                    try:
                        import json
                        data = json.loads(request.data.decode('utf-8'))
                    except:
                        data = {"raw_data": request.data.decode('utf-8', errors='ignore')}
                
                print(f"🔌 接收到客户端断开连接通知 (Content-Type: {content_type}): {data}")
                client_connected = False
                
                # 延迟停止服务器，给响应时间
                threading.Timer(1.0, lambda: server_running.clear()).start()
                return jsonify({"status": "disconnecting", "timestamp": time.time()}), 200
            except Exception as e:
                print(f"处理断开连接请求时出错: {e}")
                client_connected = False
                threading.Timer(1.0, lambda: server_running.clear()).start()
                return jsonify({"status": "error", "message": str(e)}), 500
        
        # 提供HTML页面
        @app.route('/')
        def serve_html():
            nonlocal client_connected, last_heartbeat
            client_connected = True
            last_heartbeat = time.time()
            html_file = os.path.join(config.system.log_dir, "memory_graph_visualization.html")
            if os.path.exists(html_file):
                return send_file(html_file)
            else:
                return "HTML文件未找到", 404
        
        @app.route('/api/create_time_node', methods=['POST'])
        def handle_create_time_node():
            try:
                data = request.get_json()
                time_str = data.get('time_str', '')
                
                if not time_str.strip():
                    return jsonify({
                        "success": False,
                        "error": "时间字符串不能为空"
                    }), 400
                
                result = create_time_node_api(time_str.strip())
                
                if result["success"]:
                    return jsonify(result), 200
                else:
                    return jsonify(result), 500
                    
            except Exception as e:
                logger.error(f"API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/create_character_node', methods=['POST'])
        def handle_create_character_node():
            try:
                data = request.get_json()
                character_name = data.get('character_name', '')
                trust = data.get('trust', 0.5)
                importance = data.get('importance', 0.5)
                
                if not character_name.strip():
                    return jsonify({
                        "success": False,
                        "error": "角色名称不能为空"
                    }), 400
                
                result = create_character_node_api(character_name.strip(), trust, importance)
                
                if result["success"]:
                    return jsonify(result), 200
                else:
                    return jsonify(result), 500
                    
            except Exception as e:
                logger.error(f"API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/create_entity_node', methods=['POST'])
        def handle_create_entity_node():
            try:
                data = request.get_json()
                entity_name = data.get('entity_name', '')
                importance = data.get('importance', 0.5)
                note = data.get('note', '无')
                
                if not entity_name.strip():
                    return jsonify({
                        "success": False,
                        "error": "实体名称不能为空"
                    }), 400
                
                result = create_entity_node_api(entity_name.strip(), importance, note)
                
                if result["success"]:
                    return jsonify(result), 200
                else:
                    return jsonify(result), 500
                    
            except Exception as e:
                logger.error(f"API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/create_location_node', methods=['POST'])
        def handle_create_location_node():
            try:
                data = request.get_json()
                location_name = data.get('location_name', '')
                
                if not location_name.strip():
                    return jsonify({
                        "success": False,
                        "error": "地点名称不能为空"
                    }), 400
                
                result = create_location_node_api(location_name.strip())
                
                if result["success"]:
                    return jsonify(result), 200
                else:
                    return jsonify(result), 500
                    
            except Exception as e:
                logger.error(f"API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/refresh_data', methods=['POST'])
        def handle_refresh_data():
            """刷新Neo4j数据"""
            try:
                # 调用knowledge_graph_manager的download_neo4j_data函数
                success = load_neo4j_data_to_file()
                
                if success:
                    # 重新生成HTML可视化
                    viewer = MemoryGraphViewer()
                    if viewer.load_memory_graph() and viewer.generate_html_visualization():
                        return jsonify({
                            "success": True,
                            "message": "数据刷新成功"
                        }), 200
                    else:
                        return jsonify({
                            "success": False,
                            "error": "HTML生成失败"
                        }), 500
                else:
                    return jsonify({
                        "success": False,
                        "error": "Neo4j数据下载失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Refresh API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/get_graph_data', methods=['GET'])
        def get_current_graph_data():
            """获取当前最新的图谱数据，用于动态更新"""
            try:
                # 重新加载Neo4j数据
                success = load_neo4j_data_to_file()
                
                if not success:
                    return jsonify({
                        "success": False,
                        "error": "Failed to load Neo4j data"
                    }), 500
                
                # 重新创建viewer并加载数据
                viewer = MemoryGraphViewer()
                if not viewer.load_memory_graph():
                    return jsonify({
                        "success": False,
                        "error": "Failed to load memory graph"
                    }), 500
                
                # 返回图谱数据（不包含HTML模板）
                graph_data = viewer.prepare_graph_data()
                
                return jsonify({
                    "success": True,
                    "data": graph_data
                }), 200
                
            except Exception as e:
                logger.error(f"Get graph data API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/delete_item', methods=['POST'])
        def handle_delete_item():
            """删除节点或关系"""
            try:
                # 获取知识图谱管理器实例
                kg_manager = get_knowledge_graph_manager()
                
                data = request.get_json()
                element_id = data.get('element_id', '')
                
                if not element_id.strip():
                    return jsonify({
                        "success": False,
                        "error": "元素ID不能为空"
                    }), 400
                
                result = kg_manager.delete_node_or_relation(element_id.strip())
                
                if result["success"]:
                    return jsonify(result), 200
                else:
                    return jsonify(result), 500
                    
            except Exception as e:
                logger.error(f"Delete API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/modify_item', methods=['POST'])
        def handle_modify_item():
            """修改节点或关系的属性"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                data = request.get_json()
                element_id = data.get('element_id', '')
                updates = data.get('updates', {})
                
                if not element_id.strip():
                    return jsonify({
                        "success": False,
                        "error": "元素ID不能为空"
                    }), 400
                
                if not updates or not isinstance(updates, dict):
                    return jsonify({
                        "success": False,
                        "error": "更新数据必须是非空字典"
                    }), 400
                
                # 获取知识图谱管理器实例
                kg_manager = get_knowledge_graph_manager()
                result = kg_manager.modify_node_or_relation(element_id.strip(), updates)
                
                if result:
                    return jsonify({
                        "success": True,
                        "element_id": result,
                        "message": "元素属性已成功更新"
                    }), 200
                else:
                    return jsonify({
                        "success": False,
                        "error": "修改失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Modify API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/modify_relation', methods=['POST'])
        def handle_modify_relation():
            """修改关系的属性"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                data = request.get_json()
                relation_id = data.get('relation_id', '')
                predicate = data.get('predicate', '')
                source = data.get('source', '')
                confidence = data.get('confidence', 0.5)
                directivity = data.get('directivity', 'single')
                if directivity != 'bidirectional':
                    directivity = 'single'
                
                if not relation_id.strip():
                    return jsonify({
                        "success": False,
                        "error": "关系ID不能为空"
                    }), 400
                
                if not predicate.strip():
                    return jsonify({
                        "success": False,
                        "error": "关系类型不能为空"
                    }), 400
                
                if not source.strip():
                    return jsonify({
                        "success": False,
                        "error": "关系来源不能为空"
                    }), 400
                
                try:
                    confidence = float(confidence)
                    if confidence < 0 or confidence > 1:
                        return jsonify({
                            "success": False,
                            "error": "置信度必须在0-1之间"
                        }), 400
                except (ValueError, TypeError):
                    return jsonify({
                        "success": False,
                        "error": "置信度必须是有效的数字"
                    }), 400
                
                # 获取知识图谱管理器实例
                kg_manager = get_knowledge_graph_manager()
                result = kg_manager.modify_relation(
                    relation_id.strip(), 
                    predicate.strip(), 
                    source.strip(), 
                    confidence, 
                    directivity
                )
                
                if result:
                    return jsonify({
                        "success": True,
                        "relation_id": result,
                        "message": "关系属性已成功更新"
                    }), 200
                else:
                    return jsonify({
                        "success": False,
                        "error": "关系修改失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Modify relation API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/create_relation', methods=['POST'])
        def handle_create_relation():
            """连接两个节点"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                data = request.get_json()
                startNode_id = data.get('startNode_id', '')
                endNode_id = data.get('endNode_id', '')
                predicate = data.get('predicate', '')
                source = data.get('source', '')
                confidence = data.get('confidence', 0.5)
                directivity = data.get('directivity', 'single')
                if directivity != 'bidirectional':
                    directivity = 'single'
                
                if not all([startNode_id.strip(), endNode_id.strip(), predicate.strip(), source.strip()]):
                    return jsonify({
                        "success": False,
                        "error": "节点ID、关系类型和来源不能为空"
                    }), 400
                
                if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
                    return jsonify({
                        "success": False,
                        "error": "置信度必须在0-1之间"
                    }), 400
                
                # 获取知识图谱管理器实例
                kg_manager = get_knowledge_graph_manager()
                relationship_id = kg_manager.create_relation(
                    startNode_id.strip(), 
                    endNode_id.strip(), 
                    predicate.strip(), 
                    source.strip(), 
                    confidence, 
                    directivity
                )
                
                if relationship_id:
                    return jsonify({
                        "success": True,
                        "relationship_id": relationship_id,
                        "message": "节点链接已成功创建"
                    }), 200
                else:
                    return jsonify({
                        "success": False,
                        "error": "节点链接创建失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Connect nodes API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        

        
        # 在后台线程中启动服务器
        def run_server():
            print("\n🚀 启动API服务器 (http://localhost:5000)")
            try:
                from werkzeug.serving import make_server
                
                # 创建可控制的服务器
                server = make_server('localhost', 5000, app, threaded=True)
                
                # 在另一个线程中监控服务器状态
                def server_monitor():
                    while server_running.is_set():
                        time.sleep(0.5)
                    print("📡 接收到停止信号，正在关闭服务器...")
                    server.shutdown()
                
                # 心跳超时监控线程
                def heartbeat_monitor():
                    while server_running.is_set():
                        current_time = time.time()
                        if client_connected and (current_time - last_heartbeat) > heartbeat_timeout:
                            print(f"💔 客户端心跳超时 ({heartbeat_timeout}秒)，自动停止服务器...")
                            server_running.clear()
                            break
                        time.sleep(10)  # 每10秒检查一次心跳状态
                
                monitor = threading.Thread(target=server_monitor, daemon=True)
                heartbeat_thread = threading.Thread(target=heartbeat_monitor, daemon=True)
                monitor.start()
                heartbeat_thread.start()
                
                # 启动服务器
                server.serve_forever()
                
            except Exception as e:
                print(f"❌ 服务器运行错误: {e}")
            finally:
                server_running.clear()
                print("🔌 Flask服务器已关闭")
        
        # 优雅关闭处理
        def cleanup():
            print("\n🧹 清理资源...")
            server_running.clear()
        
        # 注册退出处理
        atexit.register(cleanup)
        
        # 信号处理
        def signal_handler(sig, frame):
            print(f"\n📡 接收到信号 {sig}，正在关闭服务器...")
            server_running.clear()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 启动服务器线程
        server_thread = threading.Thread(target=run_server, daemon=False)
        server_thread.start()
        

        
        # 等待服务器启动
        time.sleep(2)
        
        print("📝 提示：关闭浏览器页面将自动停止服务器")
        print("⌨️  或者按 Ctrl+C 手动停止服务器")
        
        try:
            # 等待服务器停止信号
            while server_running.is_set():
                time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n⌨️  接收到键盘中断，正在关闭服务器...")
            server_running.clear()
        
        # 等待服务器线程结束
        if server_thread.is_alive():
            server_thread.join(timeout=5)
        
    except ImportError:
        print("⚠️ Flask未安装，无法启动API服务器")
        print("API功能需要安装Flask: pip install flask flask-cors")
        input("\n按Enter键退出...")
    except Exception as e:
        logger.error(f"Failed to start API server: {e}")
        print(f"❌ API服务器启动失败: {e}")
        input("\n按Enter键退出...")

if __name__ == "__main__":
    main()