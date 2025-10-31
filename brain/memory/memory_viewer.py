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

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

from system.config import config

logger = logging.getLogger(__name__)

class MemoryGraphViewer:
    """记忆图谱HTML可视化器"""
    
    def __init__(self, memory_graph_file: Optional[str] = None):
        self.memory_graph_file = memory_graph_file or os.path.join(config.system.log_dir, "memory_graph.json")
        self.graph_data = None
        self.html_template = None
        
    def load_memory_graph(self) -> bool:
        """加载记忆图谱数据"""
        try:
            if not os.path.exists(self.memory_graph_file):
                logger.error(f"Memory graph file not found: {self.memory_graph_file}")
                return False
            
            with open(self.memory_graph_file, 'r', encoding='utf-8') as f:
                self.graph_data = json.load(f)
            
            logger.info(f"Loaded memory graph from {self.memory_graph_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load memory graph: {e}")
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
                viz_node["color"] = "#4CAF50"
                viz_node["size"] = 15
            elif "Time" in node["labels"]:
                viz_node["color"] = "#FF9800"
                viz_node["size"] = 12
            elif "Location" in node["labels"]:
                viz_node["color"] = "#2196F3"
                viz_node["size"] = 12
            else:
                viz_node["color"] = "#9E9E9E"
            
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
            "metadata": self.graph_data.get("metadata", {})
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
            padding: 20px;
            text-align: center;
        }
        
        .container {
            display: flex;
            height: calc(100vh - 80px);
        }
        
        .sidebar {
            width: 300px;
            background: white;
            padding: 20px;
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
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
            border-left: 4px solid #667eea;
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
            stroke: #fff;
            stroke-width: 2px;
        }
        
        .node:hover {
            stroke: #333;
            stroke-width: 3px;
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
        
        .legend {
            position: absolute;
            bottom: 10px;
            left: 10px;
            background: rgba(255, 255, 255, 0.9);
            padding: 10px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
            font-size: 12px;
        }
        
        .legend-item {
            display: flex;
            align-items: center;
            margin-bottom: 5px;
        }
        
        .legend-color {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🧠 记忆图谱可视化</h1>
        <p>交互式知识图谱展示 - 点击节点查看详情</p>
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
                <div class="detail-title">节点详情</div>
                <div id="node-properties"></div>
            </div>
        </div>
        
        <div class="main-content">
            <svg id="graph-container"></svg>
            
            <div class="controls">
                <button class="control-button zoom-controls" onclick="zoomIn()">放大</button>
                <button class="control-button zoom-controls" onclick="zoomOut()">缩小</button>
                <button class="control-button zoom-controls" onclick="resetZoom()">重置</button>
                <button class="control-button filter-controls" onclick="toggleNodeLabels()">节点标签</button>
                <button class="control-button filter-controls" onclick="toggleLinkLabels()">关系标签</button>
            </div>
            
            <div class="legend">
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #4CAF50;"></div>
                    <span>实体节点</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #FF9800;"></div>
                    <span>时间节点</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #2196F3;"></div>
                    <span>地点节点</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #9E9E9E;"></div>
                    <span>其他节点</span>
                </div>
            </div>
        </div>
    </div>

    <script>
        // 图谱数据占位符
        const graphData = {{GRAPH_DATA}};
        
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
        const link = container.append("g")
            .selectAll("line")
            .data(graphData.links)
            .enter().append("line")
            .attr("class", "link")
            .attr("marker-end", "url(#arrow)");
        
        // 创建节点
        const node = container.append("g")
            .selectAll("circle")
            .data(graphData.nodes)
            .enter().append("circle")
            .attr("class", "node")
            .attr("r", d => d.size)
            .attr("fill", d => d.color)
            .on("click", showNodeDetails)
            .call(d3.drag()
                .on("start", dragstarted)
                .on("drag", dragged)
                .on("end", dragended));
        
        // 创建节点标签（显示在节点上）
        const nodeLabels = container.append("g")
            .selectAll("text")
            .data(graphData.nodes)
            .enter().append("text")
            .attr("class", "node-label")
            .text(d => d.label.length > 8 ? d.label.substring(0, 8) + "..." : d.label)
            .style("display", "block");
        
        // 创建关系标签背景
        const linkLabelBgs = container.append("g")
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
        const linkLabels = container.append("g")
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
            
            detailsDiv.style.display = "block";
            
            let html = `
                <div class="property-item">
                    <span class="property-key">节点ID:</span>
                    <span class="property-value">${d.neo4j_id}</span>
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
        
        // 控制函数
        function zoomIn() {
            svg.transition().call(zoom.scaleBy, 1.2);
        }
        
        function zoomOut() {
            svg.transition().call(zoom.scaleBy, 0.8);
        }
        
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
        
        // 初始化
        initStats();
    </script>
</body>
</html>
        """
    
    def generate_html_visualization(self, output_file: Optional[str] = None) -> bool:
        """生成HTML可视化文件"""
        try:
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
            import webbrowser
            
            if html_file is None:
                html_file = os.path.join(config.system.log_dir, "memory_graph_visualization.html")
            
            if os.path.exists(html_file):
                webbrowser.open(f"file://{os.path.abspath(html_file)}")
                print(f"🌐 已在浏览器中打开: {html_file}")
            else:
                print(f"❌ 文件不存在: {html_file}")
                
        except Exception as e:
            logger.error(f"Failed to open in browser: {e}")
            print(f"❌ 无法在浏览器中打开: {e}")

def main():
    """主函数"""
    print("🧠 记忆图谱可视化工具")
    print("=" * 50)
    
    viewer = MemoryGraphViewer()
    
    # 生成HTML可视化
    success = viewer.generate_html_visualization()
    
    if success:
        print("\n📊 可视化功能:")
        print("- 交互式节点拖拽")
        print("- 点击节点查看详情")
        print("- 缩放和平移")
        print("- 节点标签切换")
        print("- 实时统计信息")
        
        # 询问是否在浏览器中打开
        try:
            user_input = input("\n是否在浏览器中打开可视化页面? (y/n): ").strip().lower()
            if user_input in ['y', 'yes', '是']:
                viewer.open_in_browser()
        except KeyboardInterrupt:
            print("\n操作已取消")
    else:
        print("❌ 可视化生成失败")

if __name__ == "__main__":
    main()