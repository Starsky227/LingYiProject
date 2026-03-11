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
from system.system_checker import is_neo4j_available
from brain.memory.memory_download_from_neo4j import Neo4jConnector
from brain.memory.knowledge_graph_manager import load_neo4j_data_to_file, get_knowledge_graph_manager

logger = logging.getLogger(__name__)

class MemoryGraphViewer:
    """记忆图谱HTML可视化器"""
    
    def __init__(self):
        self.neo4j_memory_file = os.path.join(os.path.dirname(__file__), "memory_graph", "neo4j_memory.json")
        self.graph_data = None
        self.neo4j_data = None
        self.html_template = None
        self.neo4j_connected = False
        
    def load_memory_graph(self) -> bool:
        """加载记忆图谱数据"""
        try:
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
            
            self.graph_data = self.neo4j_data
            return True
            
        except Exception as e:
            logger.error(f"Failed to load memory graph: {e}")
            return False
    

    
    def check_neo4j_connection(self) -> bool:
        """检查Neo4j连接状态"""
        try:
            self.neo4j_connected = is_neo4j_available()
            logger.info(f"Neo4j连接状态检查结果: {self.neo4j_connected}")
            return self.neo4j_connected
        except Exception as e:
            logger.error(f"Neo4j连接检查失败: {e}")
            self.neo4j_connected = False
            return False
    

    
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
            
            viz_node["strokeColor"] = "#00FF00"  # 绿色描边
            viz_node["strokeWidth"] = 3
            viz_node["source"] = "neo4j"
            
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
                    "neo4j_id": rel["id"]
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
                logger.error("无可视化数据可用")
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
                output_file = os.path.join(os.path.dirname(__file__), "memory_graph", "memory_graph_visualization.html")
            
            # 确保目录存在
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            # 写入文件
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            logger.info(f"HTML可视化文件已生成: {output_file}")
            
            return True
            
        except Exception as e:
            logger.error(f"生成HTML可视化失败: {e}")
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
            logger.error(f"准备图谱数据失败: {e}")
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
            logger.info("已在浏览器中打开: http://localhost:5000")
                
        except Exception as e:
            logger.error(f"无法在浏览器中打开: {e}")

def check_neo4j_connection() -> bool:
    """检查Neo4j连接状态"""
    try:
        connector = Neo4jConnector()
        connected = connector.connect()
        if connected:
            connector.disconnect()
        return connected
    except Exception as e:
        logger.error(f"检查Neo4j连接失败: {e}")
        return False

def create_time_node_api(time_str: list) -> Dict[str, Any]:
    """
    API函数：创建时间节点
    
    Args:
        time_str: 时间组件列表，如 ["2001年", "2月", "3日"]
        
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
                logger.info(f"成功创建时间节点: {result_node}")
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
        logger.error(f"创建时间节点 '{time_str}' 失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "created_node": None
        }

def create_character_node_api(character_name: str, trust: float = 0.5, context: str = "reality现实") -> Dict[str, Any]:
    """
    API函数：创建角色节点
    
    Args:
        character_name: 角色名称
        trust: 信任度 (0-1)
        context: 上下文环境 (默认"reality现实")
        
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
            result_node = kg_manager.create_character_node(session, character_name, trust, context)
            
            if result_node:
                logger.info(f"成功创建角色节点: {result_node}")
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
        logger.error(f"创建角色节点 '{character_name}' 失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "created_node": None
        }

def create_entity_node_api(entity_name: str, note: str = "无", context: str = "reality现实") -> Dict[str, Any]:
    """
    API函数：创建实体节点
    
    Args:
        entity_name: 实体名称
        note: 备注 (默认"无")
        context: 上下文环境 (默认"reality现实")
        
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
            result_node = kg_manager.create_entity_node(session, entity_name, context, note)
            
            if result_node:
                logger.info(f"成功创建实体节点: {result_node}")
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
        logger.error(f"创建实体节点 '{entity_name}' 失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "created_node": None
        }

def create_location_node_api(location_name: str, context: str = "reality现实") -> Dict[str, Any]:
    """
    API函数：创建地点节点
    
    Args:
        location_name: 地点名称
        context: 上下文环境 (默认"reality现实")
        
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
            result_node = kg_manager.create_location_node(session, location_name, context)
            
            if result_node:
                logger.info(f"成功创建地点节点: {result_node}")
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
        logger.error(f"创建地点节点 '{location_name}' 失败: {e}")
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
            html_file = os.path.join(os.path.dirname(__file__), "memory_graph", "memory_graph_visualization.html")
            if os.path.exists(html_file):
                return send_file(html_file)
            else:
                return "HTML文件未找到", 404
        
        @app.route('/api/create_time_node', methods=['POST'])
        def handle_create_time_node():
            try:
                data = request.get_json()
                time_str = data.get('time_str', [])
                
                # 兼容字符串输入：自动按逗号拆分为列表
                if isinstance(time_str, str):
                    import re as _re
                    time_str = [s.strip() for s in _re.split(r'[,，]', time_str) if s.strip()]
                
                if not time_str:
                    return jsonify({
                        "success": False,
                        "error": "时间列表不能为空"
                    }), 400
                
                result = create_time_node_api(time_str)
                
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
                context = data.get('context', 'reality现实')
                
                if not character_name.strip():
                    return jsonify({
                        "success": False,
                        "error": "角色名称不能为空"
                    }), 400
                
                result = create_character_node_api(character_name.strip(), trust, context)
                
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
                note = data.get('note', '无')
                context = data.get('context', 'reality现实')
                
                if not entity_name.strip():
                    return jsonify({
                        "success": False,
                        "error": "实体名称不能为空"
                    }), 400
                
                result = create_entity_node_api(entity_name.strip(), note, context)
                
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
                context = data.get('context', 'reality现实')
                
                if not location_name.strip():
                    return jsonify({
                        "success": False,
                        "error": "地点名称不能为空"
                    }), 400
                
                result = create_location_node_api(location_name.strip(), context)
                
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
            """从Neo4j删除节点或关系（批量删除）"""
            try:
                # 获取知识图谱管理器实例
                kg_manager = get_knowledge_graph_manager()
                
                data = request.get_json()
                element_ids = data.get('element_ids', [])
                
                if not element_ids or not isinstance(element_ids, list):
                    return jsonify({
                        "success": False,
                        "error": "元素ID列表不能为空"
                    }), 400
                
                # 批量删除
                result = kg_manager.delete_node_or_relation(element_ids)
                
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
        
        @app.route('/api/modify_node', methods=['POST'])
        def handle_modify_node():
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
                result = kg_manager.modify_node(element_id.strip(), updates, call="active")
                
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
                importance = data.get('importance', None)
                directivity = data.get('directivity', 'single')
                evidence = data.get('evidence', '')
                
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
                    directivity,
                    evidence,
                    call="active",
                    importance=importance,
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
                importance = data.get('importance', 0.5)
                directivity = data.get('directivity', 'to_endNode')
                evidence = data.get('evidence', 'user manual input.')
                if directivity == 'to_startNode':
                    temp = startNode_id
                    startNode_id = endNode_id
                    endNode_id = temp
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
                    startNode_id=startNode_id.strip(), 
                    endNode_id=endNode_id.strip(), 
                    predicate=predicate.strip(), 
                    source=source.strip(), 
                    confidence=confidence, 
                    importance=importance,
                    directivity=directivity,
                    evidence=evidence
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
        
        @app.route('/api/collide_nodes', methods=['POST'])
        def handle_collide_nodes():
            """合并两个节点"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                data = request.get_json()
                node_id_1 = data.get('node_id_1', '')
                node_id_2 = data.get('node_id_2', '')
                
                if not node_id_1 or not node_id_2:
                    return jsonify({
                        "success": False,
                        "error": "两个节点ID不能为空"
                    }), 400
                
                kg_manager = get_knowledge_graph_manager()
                result_id = kg_manager.collide_nodes(node_id_1.strip(), node_id_2.strip())
                
                if result_id:
                    return jsonify({
                        "success": True,
                        "node_id": result_id,
                        "message": "节点合并成功"
                    }), 200
                else:
                    return jsonify({
                        "success": False,
                        "error": "节点合并失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Collide nodes API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/memory_decay', methods=['POST'])
        def handle_memory_decay():
            """执行记忆衰退"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                data = request.get_json() or {}
                
                kg_manager = get_knowledge_graph_manager()
                kg_manager.memory_decay()
                
                return jsonify({
                    "success": True,
                    "message": f"记忆衰退已执行 (decay_factor=默认)"
                }), 200
                
            except Exception as e:
                logger.error(f"Memory decay API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/list_memory_snapshots', methods=['GET'])
        def handle_list_memory_snapshots():
            """列出logs/memory_graph目录下可用的记忆快照文件"""
            try:
                log_dir = os.path.join(project_root, "logs", "memory_graph")
                if not os.path.exists(log_dir):
                    return jsonify({"success": True, "files": []}), 200
                
                files = []
                for f in sorted(os.listdir(log_dir), reverse=True):
                    if f.endswith('.jsonl') or f.endswith('.json'):
                        full_path = os.path.join(log_dir, f)
                        size_kb = round(os.path.getsize(full_path) / 1024, 1)
                        files.append({
                            "name": f,
                            "path": full_path,
                            "size_kb": size_kb
                        })
                
                return jsonify({"success": True, "files": files}), 200
            except Exception as e:
                logger.error(f"List memory snapshots error: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
        
        @app.route('/api/save_memory', methods=['POST'])
        def handle_save_memory():
            """保存选中的记忆到本地文件"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                data = request.get_json()
                nodes_ids = data.get('nodes_ids', [])
                relation_ids = data.get('relation_ids', [])
                
                if not nodes_ids and not relation_ids:
                    return jsonify({
                        "success": False,
                        "error": "至少需要提供一个节点或关系ID"
                    }), 400
                
                # 获取知识图谱管理器实例
                kg_manager = get_knowledge_graph_manager()
                
                # 调用 downloaod_memory 函数
                success = kg_manager.downloaod_memory({
                    "nodes_ids": nodes_ids,
                    "relation_ids": relation_ids
                })
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": f"成功保存 {len(nodes_ids)} 个节点和 {len(relation_ids)} 个关系"
                    }), 200
                else:
                    return jsonify({
                        "success": False,
                        "error": "保存记忆失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Save memory API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route('/api/upload_memory', methods=['POST'])
        def handle_upload_memory():
            """从JSON文件上传记忆到Neo4j数据库"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                data = request.get_json()
                file_path = data.get('file_path', '')
                
                if not file_path:
                    return jsonify({
                        "success": False,
                        "error": "需要提供JSON文件路径"
                    }), 400
                
                if not os.path.exists(file_path):
                    return jsonify({
                        "success": False,
                        "error": f"文件不存在: {file_path}"
                    }), 400
                
                # 读取并解析JSON文件
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        return jsonify({
                            "success": False,
                            "error": "文件内容为空"
                        }), 400
                    try:
                        memory_data = json.loads(content)
                    except json.JSONDecodeError as e:
                        return jsonify({
                            "success": False,
                            "error": f"JSON解析失败: {e}"
                        }), 400
                
                nodes = memory_data.get('nodes', [])
                relationships = memory_data.get('relationships', [])
                if not nodes and not relationships:
                    return jsonify({
                        "success": False,
                        "error": "文件中没有节点和关系数据"
                    }), 400
                
                # 获取知识图谱管理器实例
                kg_manager = get_knowledge_graph_manager()
                
                # 调用 upload_memory 函数
                success = kg_manager.upload_memory(memory_data)
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": f"成功从 {file_path} 上传 {len(nodes)} 个节点和 {len(relationships)} 个关系到Neo4j",
                    }), 200
                else:
                    return jsonify({
                        "success": False,
                        "error": "上传记忆失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Upload memory API error: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500

        @app.route('/api/upload_memory_content', methods=['POST'])
        def handle_upload_memory_content():
            """直接接收JSON内容并上传记忆到Neo4j数据库（用于浏览器文件选择）"""
            try:
                from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
                
                memory_data = request.get_json()
                if not memory_data:
                    return jsonify({
                        "success": False,
                        "error": "未收到有效的JSON数据"
                    }), 400
                
                nodes = memory_data.get('nodes', [])
                relationships = memory_data.get('relationships', [])
                
                if not nodes and not relationships:
                    return jsonify({
                        "success": False,
                        "error": "文件中没有节点和关系数据"
                    }), 400
                
                kg_manager = get_knowledge_graph_manager()
                success = kg_manager.upload_memory(memory_data)
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": f"成功导入 {len(nodes)} 个节点和 {len(relationships)} 个关系"
                    }), 200
                else:
                    return jsonify({
                        "success": False,
                        "error": "上传记忆失败"
                    }), 500
                    
            except Exception as e:
                logger.error(f"Upload memory content API error: {e}")
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
        logger.error("Flask未安装，无法启动API服务器")
        logger.error("API功能需要安装Flask: pip install flask flask-cors")
        input("\n按Enter键退出...")
    except Exception as e:
        logger.error(f"API服务器启动失败: {e}")
        input("\n按Enter键退出...")

if __name__ == "__main__":
    main()