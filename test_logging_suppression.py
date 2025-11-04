#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试httpx日志抑制功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from brain.memory.quintuples_extractor import record_memories

def test_logging_suppression():
    """测试httpx日志是否被正确抑制"""
    
    print("🧪 测试httpx日志抑制功能")
    print("=" * 50)
    
    # 创建测试消息
    test_messages = [
        {
            "role": "user",
            "content": "我今天去了公园散步",
            "timestamp": "2025-11-04T10:00:00"
        },
        {
            "role": "assistant", 
            "content": "听起来很不错！散步对健康很有好处。",
            "timestamp": "2025-11-04T10:00:05"
        }
    ]
    
    print("📝 测试消息:")
    for i, msg in enumerate(test_messages, 1):
        print(f"   {i}. [{msg['role']}] {msg['content']}")
    print()
    
    print("🔄 开始记忆提取...")
    print("注意观察是否还有 'HTTP Request: POST' 类型的日志输出")
    print()
    
    try:
        # 调用记忆记录功能 - 这会触发API调用
        task_id = record_memories(test_messages)
        
        if task_id:
            print(f"✅ 记忆提取任务已启动，任务ID: {task_id}")
            print("💡 如果没有看到HTTP请求日志，说明抑制成功！")
        else:
            print("❌ 记忆提取任务启动失败")
            
    except Exception as e:
        print(f"❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("✅ 测试完成")

if __name__ == "__main__":
    test_logging_suppression()