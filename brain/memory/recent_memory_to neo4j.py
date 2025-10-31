#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
该文件为测试文件，无具体测试目的。包含多条测试代码，非当前测试目标的代码会被注释掉。
需要测试时候直接运行即可。
"""

import os
import sys
import json

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from brain.memory.knowledge_graph_manager import upload_recent_memory_to_graph
from brain.memory.memory_loader import update_memory_graph_file, load_memory_graph_from_file

def test_upload_and_download_memories():
    """测试将recent_memory.json上传到Neo4j，然后下载到memory_graph.json"""
    print("=" * 60)
    print("开始测试：上传recent_memory.json到Neo4j，然后下载到memory_graph.json")
    print("=" * 60)
    
    # 第一步：上传recent_memory.json到Neo4j
    print("\n1. 正在上传recent_memory.json到Neo4j...")
    try:
        upload_result = upload_recent_memory_to_graph()
        
        if upload_result["success"]:
            print(f"✅ 上传成功！")
            print(f"   - 三元组上传数量: {upload_result['triples_uploaded']}")
            print(f"   - 五元组上传数量: {upload_result['quintuples_uploaded']}")
            print(f"   - 总计上传数量: {upload_result['total_uploaded']}")
        else:
            print(f"❌ 上传失败: {upload_result.get('error', '未知错误')}")
            if upload_result.get('errors'):
                print("错误详情:")
                for error in upload_result['errors'][:5]:  # 只显示前5个错误
                    print(f"   - {error}")
                if len(upload_result['errors']) > 5:
                    print(f"   ... 还有 {len(upload_result['errors']) - 5} 个错误")
            return False
            
    except Exception as e:
        print(f"❌ 上传过程发生异常: {e}")
        return False
    
    print("\n" + "-" * 40)
    
    # 第二步：从Neo4j下载记忆到memory_graph.json
    print("\n2. 正在从Neo4j下载记忆到memory_graph.json...")
    try:
        download_success = update_memory_graph_file()
        
        if download_success:
            print(f"✅ 下载成功！")
            
            # 加载并显示统计信息
            graph = load_memory_graph_from_file()
            if graph:
                print(f"   - 节点数量: {len(graph.nodes)}")
                print(f"   - 关系数量: {len(graph.relationships)}")
                print(f"   - 更新时间: {graph.updated_at}")
                
                from system.config import config
                memory_graph_file = os.path.join(config.system.log_dir, "memory_graph.json")
                print(f"   - 保存位置: {memory_graph_file}")
                
                # 显示节点类型统计
                if graph.metadata and "node_labels" in graph.metadata:
                    print("   - 节点类型统计:")
                    for label, count in graph.metadata["node_labels"].items():
                        print(f"     {label}: {count}")
                        
                # 显示关系类型统计
                if graph.metadata and "relationship_types" in graph.metadata:
                    print("   - 关系类型统计:")
                    for rel_type, count in graph.metadata["relationship_types"].items():
                        print(f"     {rel_type}: {count}")
            else:
                print("   ⚠️ 无法读取下载的图谱文件")
        else:
            print(f"❌ 下载失败，请检查Neo4j连接和配置")
            return False
            
    except Exception as e:
        print(f"❌ 下载过程发生异常: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✅ 测试完成：记忆数据已成功从recent_memory.json上传到Neo4j，并下载到memory_graph.json")
    print("=" * 60)
    return True

def test_check_memory_files():
    """检查相关记忆文件的存在性和内容"""
    print("\n" + "=" * 60)
    print("检查记忆文件状态")
    print("=" * 60)
    
    from system.config import config
    
    # 检查recent_memory.json
    recent_memory_file = os.path.join(config.system.log_dir, "recent_memory.json")
    print(f"\n1. recent_memory.json: {recent_memory_file}")
    
    if os.path.exists(recent_memory_file):
        try:
            with open(recent_memory_file, 'r', encoding='utf-8') as f:
                recent_data = json.load(f)
            
            triples_count = len(recent_data.get("triples", []))
            quintuples_count = len(recent_data.get("quintuples", []))
            
            print(f"   ✅ 文件存在")
            print(f"   📊 包含 {triples_count} 个三元组，{quintuples_count} 个五元组")
            
            if triples_count > 0:
                print(f"   🔍 第一个三元组示例: {recent_data['triples'][0]}")
            if quintuples_count > 0:
                print(f"   🔍 第一个五元组示例: {recent_data['quintuples'][0]}")
                
        except Exception as e:
            print(f"   ❌ 读取文件失败: {e}")
    else:
        print(f"   ❌ 文件不存在")
    
    # 检查memory_graph.json
    memory_graph_file = os.path.join(config.system.log_dir, "memory_graph.json")
    print(f"\n2. memory_graph.json: {memory_graph_file}")
    
    if os.path.exists(memory_graph_file):
        try:
            with open(memory_graph_file, 'r', encoding='utf-8') as f:
                graph_data = json.load(f)
            
            triples_count = len(graph_data.get("triples", []))
            quintuples_count = len(graph_data.get("quintuples", []))
            
            print(f"   ✅ 文件存在")
            print(f"   📊 包含 {triples_count} 个三元组，{quintuples_count} 个五元组")
            
            if triples_count > 0:
                print(f"   🔍 第一个三元组示例: {graph_data['triples'][0]}")
            if quintuples_count > 0:
                print(f"   🔍 第一个五元组示例: {graph_data['quintuples'][0]}")
                
        except Exception as e:
            print(f"   ❌ 读取文件失败: {e}")
    else:
        print(f"   ❌ 文件不存在")

if __name__ == "__main__":
    print("🧠 知识图谱测试开始...")
    
    # 检查文件状态
    test_check_memory_files()
    
    # 执行上传和下载测试
    success = test_upload_and_download_memories()
    
    if success:
        print("\n🎉 所有测试通过！")
    else:
        print("\n❌ 测试失败，请检查错误信息")
    
    print("\n测试结束。")