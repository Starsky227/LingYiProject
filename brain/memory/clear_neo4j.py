#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清空Neo4j数据库脚本
该文件仅涉及一个功能：调用knowledge_graph_manager.py下的clear_all_memory
"""
import sys
import os

# 添加项目根目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.normpath(os.path.join(current_dir, "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from brain.memory.knowledge_graph_manager import clear_all_memory_interactive

if __name__ == "__main__":
    clear_all_memory_interactive()