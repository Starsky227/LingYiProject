#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Graph Viewer
将 memory_graph.json 中的记忆数据通过 HTML 进行可视化展示
支持交互式图谱展示、节点详情查看、关系筛选等功能
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
import webbrowser

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
        
        # 添加本地节点
        if self.local_data and "nodes" in self.local_data:
            merged_nodes.extend(self.local_data["nodes"])
        
        # 添加Neo4j节点
        if self.neo4j_data and "nodes" in self.neo4j_data:
            merged_nodes.extend(self.neo4j_data["nodes"])
        
        # 添加本地关系
        if self.local_data and "relationships" in self.local_data:
            merged_relationships.extend(self.local_data["relationships"])
        
        # 添加Neo4j关系
        if self.neo4j_data and "relationships" in self.neo4j_data:
            merged_relationships.extend(self.neo4j_data["relationships"])
        
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
                viz_node["color"] = "#FF9800"
                viz_node["size"] = 15
            elif "Time" in node["labels"]:
                viz_node["color"] = "#4CAF50"
                viz_node["size"] = 12
            elif "User" in node["labels"]:
                viz_node["color"] = "#2196F3"
                viz_node["size"] = 12
            else:
                viz_node["color"] = "#9E9E9E"
            
            # 根据数据来源设置描边颜色
            source = self.get_node_source(node["id"])
            if source == "local":
                viz_node["strokeColor"] = "#00FF00"  # 绿色描边 - 只在本地
                viz_node["strokeWidth"] = 3
            elif source == "neo4j":
                viz_node["strokeColor"] = "#808080"  # 灰色描边 - 只在Neo4j
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
    
    def generate_html_template(self) -> str:
        """生成HTML模板"""
        return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>记忆图谱可视化</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 0;
            background-color: #f5f5f5;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 10px;
            text-align: center;
        }
        
        .container {
            display: flex;
            height: calc(100vh - 50px);
        }
        
        .sidebar {
            width: 280px;
            background: white;
            padding: 15px;
            box-shadow: 2px 0 5px rgba(0,0,0,0.1);
            overflow-y: auto;
        }
        
        .main-content {
            flex: 1;
            position: relative;
        }
        
        #graph-container {
            width: 100%;
            height: 100%;
            background: white;
        }
        
        .stat-card {
            background: #f8f9fa;
            border-radius: 6px;
            padding: 12px;
            margin-bottom: 12px;
            border-left: 3px solid #667eea;
        }
        
        .stat-title {
            font-weight: bold;
            color: #333;
            margin-bottom: 10px;
        }
        
        .stat-item {
            display: flex;
            justify-content: space-between;
            margin-bottom: 5px;
            font-size: 14px;
        }
        
        .node-details {
            background: #fff;
            border-radius: 8px;
            padding: 15px;
            margin-top: 15px;
            border: 1px solid #e0e0e0;
            display: none;
        }
        
        .detail-title {
            font-weight: bold;
            color: #667eea;
            margin-bottom: 10px;
            border-bottom: 1px solid #e0e0e0;
            padding-bottom: 5px;
        }
        
        .property-item {
            margin-bottom: 8px;
            font-size: 13px;
        }
        
        .property-key {
            font-weight: bold;
            color: #555;
        }
        
        .property-value {
            color: #777;
            margin-left: 10px;
        }
        
        .node {
            cursor: pointer;
        }
        
        .node:hover {
            stroke: #333 !important;
            stroke-width: 4px !important;
        }
        
        .link {
            stroke: #999;
            stroke-opacity: 0.6;
            stroke-width: 1.5px;
        }
        
        .link:hover {
            stroke: #333;
            stroke-opacity: 0.8;
            stroke-width: 2px;
        }
        
        .node-label {
            font-size: 11px;
            text-anchor: middle;
            pointer-events: none;
            fill: #333;
            font-weight: bold;
            text-shadow: 1px 1px 2px rgba(255,255,255,0.8);
        }
        
        .link-label {
            font-size: 10px;
            text-anchor: middle;
            pointer-events: none;
            fill: #666;
            font-style: italic;
            background: rgba(255,255,255,0.8);
        }
        
        .controls {
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(255, 255, 255, 0.9);
            padding: 10px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
        }
        
        .control-button {
            margin: 2px;
            padding: 5px 10px;
            border: none;
            border-radius: 3px;
            cursor: pointer;
            font-size: 12px;
        }
        
        .zoom-controls {
            background: #667eea;
            color: white;
        }
        
        .filter-controls {
            background: #28a745;
            color: white;
        }
        
        .edit-controls {
            background: #6f42c1;
            color: white;
        }
        
        .refresh-controls {
            background: #fd7e14;
            color: white;
        }
        
        .refresh-controls:hover {
            background: #e8590c;
        }
        
        .edit-controls:disabled {
            background: #cccccc;
            color: #666666;
            cursor: not-allowed;
            opacity: 0.6;
        }
        
        .legend {
            position: absolute;
            top: 60px;
            left: 10px;
            background: rgba(255, 255, 255, 0.9);
            padding: 8px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
            font-size: 11px;
            max-width: 120px;
        }
        
        .legend-item {
            display: flex;
            align-items: center;
            margin-bottom: 3px;
        }
        
        .legend-color {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }
        
        /* 节点编辑面板样式 */
        .edit-panel {
            position: fixed;
            top: 0;
            right: -400px;
            width: 380px;
            height: 100vh;
            background: white;
            box-shadow: -2px 0 10px rgba(0,0,0,0.3);
            z-index: 1000;
            transition: right 0.3s ease;
            overflow-y: auto;
        }
        
        .edit-panel.show {
            right: 0;
        }
        
        /* 节点创建模式下的样式 */
        .creating-node {
            cursor: crosshair !important;
        }
        
        .creating-node .graph-container {
            cursor: crosshair !important;
        }
        
        .edit-panel-header {
            background: #6f42c1;
            color: white;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #5a2d91;
        }
        
        .edit-panel-header h3 {
            margin: 0;
            font-size: 18px;
        }
        
        .close-button {
            background: none;
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
            padding: 0;
            width: 30px;
            height: 30px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .close-button:hover {
            background: rgba(255,255,255,0.2);
        }
        
        .edit-panel-content {
            padding: 20px;
        }
        
        .edit-buttons-row {
            display: flex;
            gap: 8px;
            margin-bottom: 15px;
        }
        
        .edit-function-button {
            flex: 1;
            padding: 12px 8px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            background: white;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 6px;
            transition: all 0.2s ease;
            font-size: 12px;
            min-width: 0;
        }
        
        .edit-function-button:hover {
            border-color: #6f42c1;
            background: #f8f6ff;
            transform: translateY(-1px);
        }
        
        .edit-function-button.active {
            border-color: #6f42c1;
            background: #6f42c1;
            color: white;
        }
        
        .edit-function-button:disabled {
            background: #f5f5f5;
            color: #cccccc;
            border-color: #e0e0e0;
            cursor: not-allowed;
            opacity: 0.6;
        }
        
        .edit-function-button:disabled:hover {
            background: #f5f5f5;
            border-color: #e0e0e0;
            transform: none;
        }
        
        .button-icon {
            font-size: 16px;
        }
        
        .button-text {
            font-weight: 500;
            font-size: 11px;
            text-align: center;
            white-space: nowrap;
        }
        
        .edit-forms {
            border-top: 1px solid #e0e0e0;
            padding-top: 20px;
            margin-top: 20px;
        }
        
        .edit-form {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 15px;
        }
        
        .edit-form h4 {
            margin: 0 0 15px 0;
            color: #333;
            font-size: 16px;
        }
        
        .form-group {
            margin-bottom: 15px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
            color: #555;
            font-size: 13px;
        }
        
        .form-group input, .form-group select, .form-group textarea {
            width: 100%;
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            box-sizing: border-box;
        }
        
        .form-group textarea {
            resize: vertical;
            min-height: 60px;
        }
        
        .form-group input:focus, .form-group select:focus, .form-group textarea:focus {
            outline: none;
            border-color: #6f42c1;
            box-shadow: 0 0 0 2px rgba(111, 66, 193, 0.1);
        }
        
        .warning-text {
            color: #dc3545;
            font-size: 13px;
            margin: 0;
            padding: 10px;
            background: #fff5f5;
            border: 1px solid #fed7d7;
            border-radius: 4px;
        }
        
        .form-actions {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        
        .action-btn {
            flex: 1;
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s ease;
        }
        
        .confirm-btn {
            background: #28a745;
            color: white;
        }
        
        .confirm-btn:hover {
            background: #218838;
        }
        
        .danger-btn {
            background: #dc3545;
            color: white;
        }
        
        .danger-btn:hover {
            background: #c82333;
        }
        
        .cancel-btn {
            background: #6c757d;
            color: white;
        }
        
        .cancel-btn:hover {
            background: #5a6268;
        }
        
        /* 节点类型选择按钮样式 */
        .node-type-buttons {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
            border: 1px solid #e0e0e0;
        }
        
        .node-type-button {
            flex: 1;
            padding: 15px 10px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            background: white;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            transition: all 0.2s ease;
            min-width: 0;
        }
        
        .node-type-button:hover {
            border-color: #4CAF50;
            background: #f8fff8;
            transform: translateY(-1px);
        }
        
        .node-type-button.selected {
            border-color: #4CAF50;
            background: #4CAF50;
            color: white;
        }
        
        .node-type-icon {
            font-size: 20px;
        }
        
        .node-type-text {
            font-weight: 500;
            font-size: 12px;
            text-align: center;
            white-space: nowrap;
        }
        
        /* 时间节点表单样式 */
        .time-node-form {
            padding: 20px;
            background: #f9f9f9;
            border-radius: 8px;
            margin-top: 15px;
        }
        
        .time-node-form h4 {
            margin: 0 0 15px 0;
            color: #333;
            font-size: 16px;
        }
        
        .form-group {
            margin-bottom: 15px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
            color: #555;
        }
        
        .time-input {
            width: 100%;
            padding: 10px;
            border: 2px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            box-sizing: border-box;
        }
        
        .time-input:focus {
            border-color: #4CAF50;
            outline: none;
        }
        
        .time-format-help {
            margin-top: 10px;
            padding: 10px;
            background: #fff;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
            font-size: 12px;
            color: #666;
        }
        
        .time-format-help ul {
            margin: 5px 0 0 0;
            padding-left: 20px;
        }
        
        .time-format-help li {
            margin: 2px 0;
        }
        
        .form-buttons {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
            margin-top: 15px;
        }
        
        .create-btn, .cancel-btn {
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s ease;
        }
        
        .create-btn {
            background: #4CAF50;
            color: white;
        }
        
        .create-btn:hover {
            background: #45a049;
        }
        
        .create-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        
        .cancel-btn {
            background: #f44336;
            color: white;
        }
        
        .cancel-btn:hover {
            background: #da190b;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🧠 心智云图</h1>
    </div>
    
    <div class="container">
        <div class="sidebar">
            <div class="stat-card">
                <div class="stat-title">📊 图谱统计</div>
                <div class="stat-item">
                    <span>节点总数:</span>
                    <span id="total-nodes">-</span>
                </div>
                <div class="stat-item">
                    <span>关系总数:</span>
                    <span id="total-links">-</span>
                </div>
                <div class="stat-item">
                    <span>更新时间:</span>
                    <span id="updated-time">-</span>
                </div>
            </div>
            
            <div class="stat-card">
                <div class="stat-title">🏷️ 节点类型</div>
                <div id="node-types"></div>
            </div>
            
            <div class="stat-card">
                <div class="stat-title">🔗 关系类型</div>
                <div id="relation-types"></div>
            </div>
            
            <div class="node-details" id="node-details">
                <div class="detail-title" id="detail-title">节点详情</div>
                <div id="node-properties"></div>
            </div>
        </div>
        
        <div class="main-content">
            <svg id="graph-container"></svg>
            
            <div class="controls">
                <button class="control-button refresh-controls" onclick="refreshData()">🔄 刷新</button>
                <button class="control-button zoom-controls" onclick="resetZoom()">重置</button>
                <button class="control-button filter-controls" onclick="toggleNodeLabels()">节点标签</button>
                <button class="control-button filter-controls" onclick="toggleLinkLabels()">关系标签</button>
                <button class="control-button edit-controls" id="edit-button">节点编辑</button>
            </div>
            
            <div class="legend">
                <div style="font-weight: bold; margin-bottom: 6px; border-bottom: 1px solid #ccc; padding-bottom: 2px; font-size: 10px;">节点类型</div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #FF9800; width: 10px; height: 10px; margin-right: 6px;"></div>
                    <span style="font-size: 10px;">实体</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #4CAF50; width: 10px; height: 10px; margin-right: 6px;"></div>
                    <span style="font-size: 10px;">时间</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #2196F3; width: 10px; height: 10px; margin-right: 6px;"></div>
                    <span style="font-size: 10px;">用户</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #9E9E9E; width: 10px; height: 10px; margin-right: 6px;"></div>
                    <span style="font-size: 10px;">其他</span>
                </div>
                
                <div style="font-weight: bold; margin: 8px 0 6px 0; border-bottom: 1px solid #ccc; padding-bottom: 2px; font-size: 10px;">数据来源</div>
                <div class="legend-item">
                    <div style="width: 8px; height: 8px; border: 2px solid #808080; border-radius: 50%; margin-right: 6px; background-color: #f0f0f0;"></div>
                    <span style="font-size: 10px;">本地</span>
                </div>
                <div class="legend-item">
                    <div style="width: 8px; height: 8px; border: 2px solid #00FF00; border-radius: 50%; margin-right: 6px; background-color: #f0f0f0;"></div>
                    <span style="font-size: 10px;">Neo4j</span>
                </div>
                <div class="legend-item">
                    <div style="width: 8px; height: 8px; border: 2px solid #0066FF; border-radius: 50%; margin-right: 6px; background-color: #f0f0f0;"></div>
                    <span style="font-size: 10px;">双源</span>
                </div>
            </div>
        </div>
        
        <!-- 节点编辑面板 -->
        <div class="edit-panel" id="edit-panel">
            <div class="edit-panel-header">
                <h3>节点编辑</h3>
                <button class="close-button" onclick="toggleEditPanel()">×</button>
            </div>
            <div class="edit-panel-content">
                <div class="edit-buttons-row">
                    <button class="edit-function-button add-node-btn" onclick="showAddNodeForm()">
                        <span class="button-icon">➕</span>
                        <span class="button-text">添加</span>
                    </button>
                    <button class="edit-function-button modify-node-btn" onclick="showModifyNodeForm()">
                        <span class="button-icon">✏️</span>
                        <span class="button-text">修改</span>
                    </button>
                    <button class="edit-function-button link-node-btn" onclick="showLinkNodeForm()">
                        <span class="button-icon">🔗</span>
                        <span class="button-text">链接</span>
                    </button>
                    <button class="edit-function-button delete-node-btn" onclick="showDeleteNodeForm()">
                        <span class="button-icon">🗑️</span>
                        <span class="button-text">删除</span>
                    </button>
                </div>
            </div>
            
            <!-- 功能表单区域 -->
            <div class="edit-forms">
            </div>
        </div>
    </div>

    <script>
        // 图谱数据占位符
        const graphData = {{GRAPH_DATA}};
        
        // 全局变量跟踪当前选中的项目
        let selectedItem = null;
        
        // 设置画布
        const svg = d3.select("#graph-container");
        const width = svg.node().getBoundingClientRect().width;
        const height = svg.node().getBoundingClientRect().height;
        
        svg.attr("width", width).attr("height", height);
        
        // 创建缩放行为
        const zoom = d3.zoom()
            .scaleExtent([0.1, 3])
            .on("zoom", function(event) {
                container.attr("transform", event.transform);
            });
        
        svg.call(zoom);
        
        // 创建容器组
        const container = svg.append("g");
        
        // 创建力导向图
        const simulation = d3.forceSimulation(graphData.nodes)
            .force("link", d3.forceLink(graphData.links).id(d => d.id).distance(100))
            .force("charge", d3.forceManyBody().strength(-300))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide().radius(d => d.size + 5));
        
        // 创建箭头标记
        container.append("defs").selectAll("marker")
            .data(["end"])
            .enter().append("marker")
            .attr("id", "arrow")
            .attr("viewBox", "0 -5 10 10")
            .attr("refX", 20)
            .attr("refY", 0)
            .attr("markerWidth", 6)
            .attr("markerHeight", 6)
            .attr("orient", "auto")
            .append("path")
            .attr("d", "M0,-5L10,0L0,5")
            .attr("fill", "#999");
        
        // 创建链接
        let link = container.append("g")
            .selectAll("line")
            .data(graphData.links)
            .enter().append("line")
            .attr("class", "link")
            .attr("marker-end", "url(#arrow)")
            .on("click", showLinkDetails);
        
        // 创建节点
        let node = container.append("g")
            .selectAll("circle")
            .data(graphData.nodes)
            .enter().append("circle")
            .attr("class", "node")
            .attr("r", d => d.size)
            .attr("fill", d => d.color)
            .attr("stroke", d => d.strokeColor)
            .attr("stroke-width", d => d.strokeWidth)
            .on("click", showNodeDetails)
            .call(d3.drag()
                .on("start", dragstarted)
                .on("drag", dragged)
                .on("end", dragended));
        
        // 创建节点标签（显示在节点上）
        let nodeLabels = container.append("g")
            .selectAll("text")
            .data(graphData.nodes)
            .enter().append("text")
            .attr("class", "node-label")
            .text(d => d.label.length > 8 ? d.label.substring(0, 8) + "..." : d.label)
            .style("display", "block");
        
        // 创建关系标签背景
        let linkLabelBgs = container.append("g")
            .selectAll("rect")
            .data(graphData.links)
            .enter().append("rect")
            .attr("class", "link-label-bg")
            .attr("fill", "rgba(255,255,255,0.8)")
            .attr("stroke", "rgba(200,200,200,0.5)")
            .attr("stroke-width", 0.5)
            .attr("rx", 3)
            .attr("ry", 3);
        
        // 创建关系标签（显示在连接线上）
        let linkLabels = container.append("g")
            .selectAll("text")
            .data(graphData.links)
            .enter().append("text")
            .attr("class", "link-label")
            .text(d => {
                // 优先显示predicate或action属性，否则显示关系类型
                if (d.properties && d.properties.predicate) {
                    return d.properties.predicate.length > 10 ? d.properties.predicate.substring(0, 10) + "..." : d.properties.predicate;
                } else if (d.properties && d.properties.action) {
                    return d.properties.action.length > 10 ? d.properties.action.substring(0, 10) + "..." : d.properties.action;
                } else {
                    return d.type.length > 10 ? d.type.substring(0, 10) + "..." : d.type;
                }
            })
            .style("display", "block");
        
        // 更新位置
        simulation.on("tick", () => {
            link
                .attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);
            
            node
                .attr("cx", d => d.x)
                .attr("cy", d => d.y);
            
            // 节点标签跟随节点
            nodeLabels
                .attr("x", d => d.x)
                .attr("y", d => d.y + 4); // 稍微向下偏移，让文字在节点中心偏下
            
            // 关系标签显示在连接线的中点
            linkLabels
                .attr("x", d => (d.source.x + d.target.x) / 2)
                .attr("y", d => (d.source.y + d.target.y) / 2 - 5); // 稍微向上偏移，避免与线重叠
            
            // 关系标签背景跟随标签位置
            linkLabelBgs.each(function(d) {
                const text = linkLabels.filter(data => data === d).node();
                if (text) {
                    const bbox = text.getBBox();
                    d3.select(this)
                        .attr("x", bbox.x - 2)
                        .attr("y", bbox.y - 1)
                        .attr("width", bbox.width + 4)
                        .attr("height", bbox.height + 2);
                }
            });
        });
        
        // 拖拽函数
        function dragstarted(event, d) {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }
        
        function dragged(event, d) {
            d.fx = event.x;
            d.fy = event.y;
        }
        
        function dragended(event, d) {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }
        
        // 显示节点详情
        function showNodeDetails(event, d) {
            const detailsDiv = document.getElementById("node-details");
            const propertiesDiv = document.getElementById("node-properties");
            const titleDiv = document.getElementById("detail-title");
            
            detailsDiv.style.display = "block";
            titleDiv.textContent = "节点详情";
            
            // 跟踪选中的节点
            selectedItem = {
                type: 'node',
                data: d,
                elementId: d.neo4j_id
            };
            
            let sourceText = '';
            if (d.source === 'local') {
                sourceText = '仅本地';
            } else if (d.source === 'neo4j') {
                sourceText = '仅Neo4j';
            } else if (d.source === 'both') {
                sourceText = '本地+Neo4j';
            } else {
                sourceText = '未知';
            }
            
            let html = `
                <div class="property-item">
                    <span class="property-key">节点ID:</span>
                    <span class="property-value">${d.neo4j_id}</span>
                </div>
                <div class="property-item">
                    <span class="property-key">数据来源:</span>
                    <span class="property-value">${sourceText}</span>
                </div>
                <div class="property-item">
                    <span class="property-key">标签:</span>
                    <span class="property-value">${d.labels.join(", ")}</span>
                </div>
                <div class="property-item">
                    <span class="property-key">名称:</span>
                    <span class="property-value">${d.label}</span>
                </div>
            `;
            
            // 显示所有属性
            for (const [key, value] of Object.entries(d.properties)) {
                html += `
                    <div class="property-item">
                        <span class="property-key">${key}:</span>
                        <span class="property-value">${JSON.stringify(value)}</span>
                    </div>
                `;
            }
            
            propertiesDiv.innerHTML = html;
        }
        
        // 显示关系详情
        function showLinkDetails(event, d) {
            const detailsDiv = document.getElementById("node-details");
            const propertiesDiv = document.getElementById("node-properties");
            const titleDiv = document.getElementById("detail-title");
            
            detailsDiv.style.display = "block";
            titleDiv.textContent = "关系详情";
            
            // 跟踪选中的关系
            selectedItem = {
                type: 'relationship',
                data: d,
                elementId: d.neo4j_id
            };
            
            // 获取起始和目标节点的名称
            const sourceNode = graphData.nodes.find(node => node.id === d.source.id);
            const targetNode = graphData.nodes.find(node => node.id === d.target.id);
            
            let html = `
                <div class="property-item">
                    <span class="property-key">关系ID:</span>
                    <span class="property-value">${d.neo4j_id}</span>
                </div>
                <div class="property-item">
                    <span class="property-key">关系类型:</span>
                    <span class="property-value">${d.type}</span>
                </div>
                <div class="property-item">
                    <span class="property-key">起始节点:</span>
                    <span class="property-value">${sourceNode ? sourceNode.label : 'Unknown'}</span>
                </div>
                <div class="property-item">
                    <span class="property-key">目标节点:</span>
                    <span class="property-value">${targetNode ? targetNode.label : 'Unknown'}</span>
                </div>
            `;
            
            // 显示所有关系属性
            for (const [key, value] of Object.entries(d.properties)) {
                html += `
                    <div class="property-item">
                        <span class="property-key">${key}:</span>
                        <span class="property-value">${JSON.stringify(value)}</span>
                    </div>
                `;
            }
            
            propertiesDiv.innerHTML = html;
        }
        
        // 控制函数
        function resetZoom() {
            svg.transition().call(zoom.transform, d3.zoomIdentity);
        }
        
        let nodeLabelsVisible = true;
        let linkLabelsVisible = true;
        
        function toggleNodeLabels() {
            nodeLabelsVisible = !nodeLabelsVisible;
            nodeLabels.style("display", nodeLabelsVisible ? "block" : "none");
        }
        
        function toggleLinkLabels() {
            linkLabelsVisible = !linkLabelsVisible;
            linkLabels.style("display", linkLabelsVisible ? "block" : "none");
            linkLabelBgs.style("display", linkLabelsVisible ? "block" : "none");
        }
        
        // 刷新数据功能
        function refreshData() {
            console.log('正在刷新数据...');
            
            // 显示加载状态
            const refreshBtn = document.querySelector('.refresh-controls');
            const originalText = refreshBtn.innerHTML;
            refreshBtn.innerHTML = '⏳ 刷新中...';
            refreshBtn.disabled = true;
            
            fetch('/api/refresh_data', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            })
            .then(response => response.json())
            .then(data => {
                console.log('刷新结果:', data);
                if (data.success) {
                    // 刷新页面以加载新数据
                    window.location.reload();
                } else {
                    alert('刷新失败: ' + (data.error || '未知错误'));
                    refreshBtn.innerHTML = originalText;
                    refreshBtn.disabled = false;
                }
            })
            .catch(error => {
                console.error('刷新错误:', error);
                alert('刷新失败: ' + error.message);
                refreshBtn.innerHTML = originalText;
                refreshBtn.disabled = false;
            });
        }
        
        // 初始化统计信息
        function initStats() {
            document.getElementById("total-nodes").textContent = graphData.stats.total_nodes;
            document.getElementById("total-links").textContent = graphData.stats.total_links;
            document.getElementById("updated-time").textContent = new Date(graphData.stats.updated_at).toLocaleString();
            
            // 节点类型统计
            const nodeTypesDiv = document.getElementById("node-types");
            let nodeTypesHtml = "";
            for (const [type, count] of Object.entries(graphData.stats.node_types)) {
                nodeTypesHtml += `
                    <div class="stat-item">
                        <span>${type}:</span>
                        <span>${count}</span>
                    </div>
                `;
            }
            nodeTypesDiv.innerHTML = nodeTypesHtml;
            
            // 关系类型统计
            const relationTypesDiv = document.getElementById("relation-types");
            let relationTypesHtml = "";
            for (const [type, count] of Object.entries(graphData.stats.relation_types)) {
                relationTypesHtml += `
                    <div class="stat-item">
                        <span>${type}:</span>
                        <span>${count}</span>
                    </div>
                `;
            }
            relationTypesDiv.innerHTML = relationTypesHtml;
        }
        
        function updateNodeSelectionLists() {
            const modifySelect = document.getElementById('modify-node-select');
            const deleteSelect = document.getElementById('delete-node-select');
            const linkSourceSelect = document.getElementById('link-source-select');
            const linkTargetSelect = document.getElementById('link-target-select');
            
            // 只更新存在的选择框
            if (modifySelect) {
                modifySelect.innerHTML = '<option value="">请选择要修改的节点</option>';
                graphData.nodes.forEach(node => {
                    const option = document.createElement('option');
                    option.value = node.id;
                    option.textContent = `${node.label} (${node.group})`;
                    modifySelect.appendChild(option);
                });
            }
            
            if (deleteSelect) {
                deleteSelect.innerHTML = '<option value="">请选择要删除的节点</option>';
                graphData.nodes.forEach(node => {
                    const option = document.createElement('option');
                    option.value = node.id;
                    option.textContent = `${node.label} (${node.group})`;
                    deleteSelect.appendChild(option);
                });
            }
            
            if (linkSourceSelect) {
                linkSourceSelect.innerHTML = '<option value="">请选择起始节点</option>';
                graphData.nodes.forEach(node => {
                    const option = document.createElement('option');
                    option.value = node.id;
                    option.textContent = `${node.label} (${node.group})`;
                    linkSourceSelect.appendChild(option);
                });
            }
            
            if (linkTargetSelect) {
                linkTargetSelect.innerHTML = '<option value="">请选择目标节点</option>';
                graphData.nodes.forEach(node => {
                    const option = document.createElement('option');
                    option.value = node.id;
                    option.textContent = `${node.label} (${node.group})`;
                    linkTargetSelect.appendChild(option);
                });
            }
        }
        
        function showAddNodeForm() {
            hideEditForms();
            
            // 创建节点类型选择界面
            const editForms = document.querySelector('.edit-forms');
            editForms.innerHTML = `
                <div class="node-type-buttons">
                    <button class="node-type-button" onclick="selectNodeType('Entity')">
                        <span class="node-type-icon">🏷️</span>
                        <span class="node-type-text">实体节点</span>
                    </button>
                    <button class="node-type-button" onclick="selectNodeType('Time')">
                        <span class="node-type-icon">⏰</span>
                        <span class="node-type-text">时间节点</span>
                    </button>
                    <button class="node-type-button" onclick="selectNodeType('User')">
                        <span class="node-type-icon">👤</span>
                        <span class="node-type-text">用户节点</span>
                    </button>
                </div>
            `;
            
            // 重置按钮状态
            resetButtonStates();
            document.querySelector('.add-node-btn').classList.add('active');
        }
        
        function showModifyNodeForm() {
            // 功能已禁用
        }
        
        function showLinkNodeForm() {
            // 功能已禁用
        }
        
        // 获取选中项目的信息显示文本
        function getSelectedItemInfo() {
            if (!selectedItem) {
                return '无选中项目';
            }
            
            if (selectedItem.type === 'node') {
                const node = selectedItem.data;
                return `
                    <strong>节点：</strong>${node.label}<br>
                    <strong>类型：</strong>${node.labels.join(", ")}<br>
                    <strong>ID：</strong>${node.neo4j_id}
                `;
            } else if (selectedItem.type === 'relationship') {
                const rel = selectedItem.data;
                const sourceNode = graphData.nodes.find(node => node.id === rel.source.id);
                const targetNode = graphData.nodes.find(node => node.id === rel.target.id);
                return `
                    <strong>关系：</strong>${rel.type}<br>
                    <strong>从：</strong>${sourceNode ? sourceNode.label : 'Unknown'}<br>
                    <strong>到：</strong>${targetNode ? targetNode.label : 'Unknown'}<br>
                    <strong>ID：</strong>${rel.neo4j_id}
                `;
            }
            
            return '未知项目类型';
        }
        
        // 获取相关项目数量（仅对节点有意义）
        function getRelatedItemsCount() {
            if (!selectedItem || selectedItem.type !== 'node') {
                return 0;
            }
            
            const nodeId = selectedItem.data.id;
            return graphData.links.filter(link => 
                link.source.id === nodeId || link.target.id === nodeId
            ).length;
        }
        
        function showDeleteNodeForm() {
            hideEditForms();
            
            const editForms = document.querySelector('.edit-forms');
            
            if (!selectedItem) {
                // 未选中任何项目
                editForms.innerHTML = `
                    <div class="edit-form">
                        <h4>删除项目</h4>
                        <div class="warning-text">
                            请先点击选中一个节点或关系后再进行删除操作。
                        </div>
                    </div>
                `;
            } else {
                // 显示选中项目的删除确认
                const itemInfo = getSelectedItemInfo();
                const relatedCount = getRelatedItemsCount();
                
                let warningText = '';
                if (selectedItem.type === 'node' && relatedCount > 0) {
                    warningText = `
                        <div class="warning-text">
                            警告：删除此节点将同时删除 ${relatedCount} 个相关关系！
                        </div>
                    `;
                }
                
                editForms.innerHTML = `
                    <div class="edit-form">
                        <h4>确认删除</h4>
                        <div class="form-group">
                            <label>选中项目：</label>
                            <div style="padding: 8px; background: #f8f9fa; border-radius: 4px; margin-top: 5px;">
                                ${itemInfo}
                            </div>
                        </div>
                        ${warningText}
                        <div class="form-actions">
                            <button class="action-btn danger-btn" onclick="confirmDelete()">确认删除</button>
                            <button class="action-btn cancel-btn" onclick="hideEditForms()">取消</button>
                        </div>
                    </div>
                `;
            }
            
            // 设置按钮状态
            resetButtonStates();
            document.querySelector('.delete-node-btn').classList.add('active');
        }
        
        function hideEditForms() {
            // 清空功能表单区域
            const editForms = document.querySelector('.edit-forms');
            editForms.innerHTML = '';
            resetButtonStates();
            
            // 如果正在创建节点，取消创建
            if (isCreatingNode) {
                cleanupNodeCreation();
            }
        }
        
        // 确认删除函数
        async function confirmDelete() {
            if (!selectedItem) {
                alert('请先选择要删除的节点或关系');
                return;
            }
            
            // 计算将要删除的内容
            let nodeCount = 0;
            let relationshipCount = 0;
            let itemDescription = '';
            
            if (selectedItem.type === 'node') {
                nodeCount = 1;
                relationshipCount = getRelatedItemsCount();
                itemDescription = `节点"${selectedItem.data.label}"`;
            } else if (selectedItem.type === 'relationship') {
                relationshipCount = 1;
                const sourceNode = graphData.nodes.find(node => node.id === selectedItem.data.source.id);
                const targetNode = graphData.nodes.find(node => node.id === selectedItem.data.target.id);
                itemDescription = `关系"${selectedItem.data.type}" (从"${sourceNode ? sourceNode.label : 'Unknown'}"到"${targetNode ? targetNode.label : 'Unknown'}")`;
            }
            
            // 构建确认消息
            let confirmMessage = `确认删除 ${itemDescription}？\n\n`;
            confirmMessage += `此操作将删除：\n`;
            if (nodeCount > 0) {
                confirmMessage += `• ${nodeCount} 个节点\n`;
            }
            if (relationshipCount > 0) {
                confirmMessage += `• ${relationshipCount} 个关系\n`;
            }
            confirmMessage += `\n此操作不可撤销，确定要继续吗？`;
            
            // 进行删除确认
            if (!confirm(confirmMessage)) {
                return;
            }
            
            try {
                // 显示加载状态
                const confirmBtn = document.querySelector('.danger-btn');
                const originalText = confirmBtn.textContent;
                confirmBtn.textContent = '删除中...';
                confirmBtn.disabled = true;
                
                const response = await fetch('/api/delete_item', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        element_id: selectedItem.elementId
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    alert(`删除成功！${result.message || ''}`);
                    // 清空选中项目
                    selectedItem = null;
                    // 隐藏编辑表单
                    hideEditForms();
                    // 隐藏详情面板
                    document.getElementById("node-details").style.display = "none";
                    // 重新加载数据
                    await refreshData();
                } else {
                    alert(`删除失败：${result.error || '未知错误'}`);
                }
                
            } catch (error) {
                console.error('删除操作失败:', error);
                alert(`删除操作失败：${error.message}`);
            } finally {
                // 恢复按钮状态
                const confirmBtn = document.querySelector('.danger-btn');
                if (confirmBtn) {
                    confirmBtn.textContent = originalText;
                    confirmBtn.disabled = false;
                }
            }
        }
        
        // 清空选中项目（当详情面板关闭时）
        function clearSelection() {
            selectedItem = null;
        }
        
        function resetButtonStates() {
            document.querySelectorAll('.edit-function-button').forEach(btn => {
                btn.classList.remove('active');
            });
        }
        
        // 添加全局变量用于跟踪节点创建状态
        let isCreatingNode = false;
        let newNodeData = null;
        let ghostNode = null;
        
        // 选择节点类型
        function selectNodeType(nodeType) {
            if (nodeType === 'Time') {
                // 显示时间节点创建表单
                showTimeNodeForm();
            } else {
                alert(`${nodeType} 节点类型暂未实现`);
            }
        }
        
        // 显示时间节点创建表单
        function showTimeNodeForm() {
            const editForms = document.querySelector('.edit-forms');
            editForms.innerHTML = `
                <div class="time-node-form">
                    <h4>创建时间节点</h4>
                    <div class="form-group">
                        <label for="time-input">请输入时间：</label>
                        <input type="text" id="time-input" placeholder="例如：2024年12月5日14点30分" class="time-input" />
                        <div class="time-format-help">
                            支持格式：
                            <ul>
                                <li>完整格式：2024年12月5日14点30分45秒</li>
                                <li>日期格式：2024年12月5日</li>
                                <li>时间格式：14点30分</li>
                                <li>周次格式：第3个星期一</li>
                                <li>相对格式：三点半</li>
                            </ul>
                        </div>
                    </div>
                    <div class="form-buttons">
                        <button class="create-btn" onclick="createTimeNode()">创建时间节点</button>
                        <button class="cancel-btn" onclick="hideEditForms()">取消</button>
                    </div>
                </div>
            `;
            
            // 聚焦到输入框
            setTimeout(() => {
                document.getElementById('time-input').focus();
            }, 100);
            
            // 添加回车键监听
            document.getElementById('time-input').addEventListener('keypress', function(event) {
                if (event.key === 'Enter') {
                    createTimeNode();
                }
            });
        }
        

        
        // 节点操作函数
        function addNode(nodeType) {
            // 功能已禁用
        }
        
        function modifyNode() {
            // 功能已禁用
        }
        
        function linkNodes() {
            // 功能已禁用
        }
        
        function createGhostNode() {
            // 功能已禁用
        }
        
        function placeNewNode(x, y) {
            // 功能已禁用
        }
        
        // 时间格式验证函数
        function validateTimeFormat(timeStr) {
            // 功能已禁用
        }
        
        // 创建时间节点
        function createTimeNode() {
            const timeInput = document.getElementById('time-input');
            const timeStr = timeInput.value.trim();
            
            if (!timeStr) {
                alert('请输入时间信息');
                timeInput.focus();
                return;
            }
            
            // 显示加载状态
            const createBtn = document.querySelector('.create-btn');
            const originalText = createBtn.textContent;
            createBtn.textContent = '创建中...';
            createBtn.disabled = true;
            
            // 调用API创建时间节点
            fetch('/api/create_time_node', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    time_str: timeStr
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert(`时间节点创建成功: ${data.message}`);
                    hideEditForms();
                    // 自动刷新数据
                    console.log('自动刷新数据...');
                    refreshData();
                } else {
                    alert(`时间节点创建失败: ${data.error}`);
                }
            })
            .catch(error => {
                console.error('创建时间节点时出错:', error);
                alert('创建时间节点时出错: ' + error.message);
            })
            .finally(() => {
                createBtn.textContent = originalText;
                createBtn.disabled = false;
            });
        }
        
        function saveNodeToLocalMemory(nodeData) {
            // 功能已禁用
        }
        
        function cleanupNodeCreation() {
            // 功能已禁用
        }
        
        function deleteNode() {
            // 功能已禁用
        }
        
        function updateVisualization() {
            // 更新节点选择列表
            updateNodeSelectionLists();
            
            // 更新力导向图数据
            simulation.nodes(graphData.nodes);
            simulation.force("link").links(graphData.links);
            
            // 重新绑定并更新节点
            node = node.data(graphData.nodes, d => d.id);
            node.exit().remove();
            node = node.enter().append("circle")
                .attr("class", "node")
                .attr("r", d => d.size)
                .attr("fill", d => d.color)
                .attr("stroke", d => d.strokeColor)
                .attr("stroke-width", d => d.strokeWidth)
                .on("click", showNodeDetails)
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended))
                .merge(node);
            
            // 重新绑定并更新链接
            link = link.data(graphData.links);
            link.exit().remove();
            link = link.enter().append("line")
                .attr("class", "link")
                .attr("marker-end", "url(#arrow)")
                .merge(link);
            
            // 重新绑定并更新节点标签
            nodeLabels = nodeLabels.data(graphData.nodes, d => d.id);
            nodeLabels.exit().remove();
            nodeLabels = nodeLabels.enter().append("text")
                .attr("class", "node-label")
                .text(d => d.label.length > 8 ? d.label.substring(0, 8) + "..." : d.label)
                .style("display", "block")
                .merge(nodeLabels);
            
            // 重新绑定并更新链接标签
            linkLabels = linkLabels.data(graphData.links);
            linkLabels.exit().remove();
            linkLabels = linkLabels.enter().append("text")
                .attr("class", "link-label")
                .text(d => {
                    if (d.properties && d.properties.predicate) {
                        return d.properties.predicate.length > 10 ? d.properties.predicate.substring(0, 10) + "..." : d.properties.predicate;
                    } else if (d.properties && d.properties.action) {
                        return d.properties.action.length > 10 ? d.properties.action.substring(0, 10) + "..." : d.properties.action;
                    } else {
                        return d.type.length > 10 ? d.type.substring(0, 10) + "..." : d.type;
                    }
                })
                .style("display", "block")
                .merge(linkLabels);
            
            // 重新绑定并更新链接标签背景
            linkLabelBgs = linkLabelBgs.data(graphData.links);
            linkLabelBgs.exit().remove();
            linkLabelBgs = linkLabelBgs.enter().append("rect")
                .attr("class", "link-label-bg")
                .attr("fill", "rgba(255,255,255,0.8)")
                .attr("stroke", "rgba(200,200,200,0.5)")
                .attr("stroke-width", 0.5)
                .attr("rx", 3)
                .attr("ry", 3)
                .merge(linkLabelBgs);
            
            // 重新启动仿真
            simulation.alpha(1).restart();
        }
        
        // 初始化
        initStats();
        initEditButtonState();
        
        // 初始化编辑按钮状态
        function initEditButtonState() {
            const editButton = document.getElementById('edit-button');
            const neo4jConnected = graphData.neo4j_connected;
            
            console.log('🔍 Neo4j连接状态检查:', neo4jConnected);
            console.log('🔍 完整graphData:', graphData);
            console.log('🔍 编辑按钮元素:', editButton);
            
            if (!editButton) {
                console.error('❌ 无法找到编辑按钮元素');
                return;
            }
            
            if (!neo4jConnected) {
                console.log('❌ Neo4j未连接，禁用编辑按钮');
                editButton.disabled = true;
                editButton.className = 'control-button edit-controls';
                editButton.title = 'Neo4j数据库未连接，无法编辑节点';
                editButton.onclick = function(event) {
                    event.preventDefault();
                    alert('Neo4j数据库未连接，无法进行节点编辑操作。\\n\\n请检查：\\n1. Neo4j服务是否启动\\n2. 网络连接是否正常\\n3. 配置文件中的连接信息是否正确');
                };
            } else {
                console.log('✅ Neo4j已连接，启用编辑按钮');
                editButton.disabled = false;
                editButton.className = 'control-button edit-controls';
                editButton.title = '编辑记忆图谱节点';
                editButton.onclick = function(event) {
                    event.preventDefault();
                    toggleEditPanel();
                };
            }
        }
        
        // 修改toggleEditPanel函数，增加连接状态检查
        function toggleEditPanel() {
            console.log('🔄 toggleEditPanel被调用');
            console.log('🔍 当前Neo4j连接状态:', graphData.neo4j_connected);
            
            if (!graphData.neo4j_connected) {
                console.log('❌ Neo4j未连接，阻止面板打开');
                alert('Neo4j数据库未连接，无法进行节点编辑操作。');
                return;
            }
            
            console.log('✅ Neo4j已连接，切换编辑面板');
            const panel = document.getElementById('edit-panel');
            panel.classList.toggle('show');
            
            // 如果打开面板，更新节点选择列表
            if (panel.classList.contains('show')) {
                console.log('📋 更新节点选择列表');
                updateNodeSelectionLists();
            }
        }
        
        // 测试API连接的函数
        window.testApiConnection = function() {
            console.log('🧪 测试API连接');
            fetch('/api/create_time_node', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    time_str: '测试时间'
                })
            })
            .then(response => {
                console.log('API响应状态:', response.status);
                return response.json();
            })
            .then(data => {
                console.log('API响应数据:', data);
                alert('API连接测试完成，请查看控制台');
            })
            .catch(error => {
                console.error('API连接测试失败:', error);
                alert('API连接失败: ' + error.message);
            });
        };
        
        // 添加ESC键监听来取消节点创建
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape' && isCreatingNode) {
                cleanupNodeCreation();
            }
        });
    </script>
</body>
</html>
        """
    
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
            
            # 生成HTML
            html_content = self.generate_html_template()
            html_content = html_content.replace("{{GRAPH_DATA}}", json.dumps(viz_data, ensure_ascii=False))
            
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
        import threading
        
        app = Flask(__name__)
        CORS(app)  # 允许跨域请求
        
        # 提供HTML页面
        @app.route('/')
        def serve_html():
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
        
        @app.route('/api/delete_item', methods=['POST'])
        def handle_delete_item():
            """删除节点或关系"""
            try:
                from brain.memory.knowledge_graph_manager import delete_node_or_relation_by_id
                
                data = request.get_json()
                element_id = data.get('element_id', '')
                
                if not element_id.strip():
                    return jsonify({
                        "success": False,
                        "error": "元素ID不能为空"
                    }), 400
                
                result = delete_node_or_relation_by_id(element_id.strip())
                
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
        
        # 在后台线程中启动服务器
        def run_server():
            print("\n🚀 启动API服务器 (http://localhost:5000)")
            app.run(host='localhost', port=5000, debug=False, use_reloader=False)
        
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        
        # 等待服务器启动
        import time
        time.sleep(2)
        
        # 等待用户按Enter键退出
        input("\n按Enter键退出...")
        
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