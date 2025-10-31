#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专门测试 _extract_memories_task 函数输出的测试程序
目标：输入测试对话记录，查看 _extract_memories_task 的具体输出内容
"""

import sys
import os
import json
from typing import Dict, List, Any

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

def print_separator(title="", char="=", length=80):
    """打印分隔线"""
    if title:
        padding = (length - len(title) - 2) // 2
        print(f"{char * padding} {title} {char * padding}")
    else:
        print(char * length)

def print_memory_result_details(result, test_name):
    """详细打印 MemoryResult 的每个字段"""
    print_separator(f"测试结果: {test_name}")
    
    print("📋 MemoryResult 完整输出:")
    print(f"├── 类型: {type(result).__name__}")
    print(f"├── has_memory: {result.has_memory} (类型: {type(result.has_memory).__name__})")
    print(f"├── memory_type: {result.memory_type} (类型: {type(result.memory_type).__name__})")
    print(f"├── triples: 包含 {len(result.triples)} 个元素 (类型: {type(result.triples).__name__})")
    print(f"├── quintuples: 包含 {len(result.quintuples)} 个元素 (类型: {type(result.quintuples).__name__})")
    print(f"├── raw_json: {type(result.raw_json).__name__}")
    print(f"└── reason: '{result.reason}' (类型: {type(result.reason).__name__})")
    
    # 详细展示三元组
    if result.triples:
        print("\n🔗 三元组 (Triples) 详情:")
        for i, triple in enumerate(result.triples, 1):
            print(f"  第 {i} 个三元组:")
            print(f"    ├── subject (主体): '{triple.subject}' ({type(triple.subject).__name__})")
            print(f"    ├── predicate (谓词): '{triple.predicate}' ({type(triple.predicate).__name__})")
            print(f"    ├── object (客体): '{triple.object}' ({type(triple.object).__name__})")
            print(f"    ├── source (来源): '{triple.source}' ({type(triple.source).__name__})")
            print(f"    ├── confidence (置信度): {triple.confidence} ({type(triple.confidence).__name__})")
            print(f"    └── time_record (记录时间): '{triple.time_record}' ({type(triple.time_record).__name__})")
    else:
        print("\n🔗 三元组: 无")
    
    # 详细展示五元组
    if result.quintuples:
        print("\n🎯 五元组 (Quintuples) 详情:")
        for i, quintuple in enumerate(result.quintuples, 1):
            print(f"  第 {i} 个五元组:")
            print(f"    ├── subject (主体): '{quintuple.subject}' ({type(quintuple.subject).__name__})")
            print(f"    ├── action (动作): '{quintuple.action}' ({type(quintuple.action).__name__})")
            print(f"    ├── object (客体): '{quintuple.object}' ({type(quintuple.object).__name__})")
            print(f"    ├── time (时间): {quintuple.time} ({type(quintuple.time).__name__})")
            print(f"    ├── location (地点): {quintuple.location} ({type(quintuple.location).__name__})")
            print(f"    ├── source (来源): '{quintuple.source}' ({type(quintuple.source).__name__})")
            print(f"    ├── confidence (置信度): {quintuple.confidence} ({type(quintuple.confidence).__name__})")
            print(f"    └── time_record (记录时间): '{quintuple.time_record}' ({type(quintuple.time_record).__name__})")
    else:
        print("\n🎯 五元组: 无")
    
    # 展示原始JSON
    if result.raw_json:
        print("\n📄 原始 JSON 输出:")
        try:
            formatted_json = json.dumps(result.raw_json, ensure_ascii=False, indent=2)
            print(formatted_json)
        except Exception as e:
            print(f"JSON格式化失败: {e}")
            print(f"原始数据: {result.raw_json}")
    else:
        print("\n📄 原始 JSON: 无")
    
    print_separator()

def test_extract_memories_task_direct():
    """直接测试 _extract_memories_task 函数"""
    print_separator("直接测试 _extract_memories_task 函数")
    
    try:
        # 导入必要的模块
        from brain.memory.memory_recorder import _extract_memories_task, _read_classifier_prompt, _flatten_messages
        
        # 准备测试数据
        test_conversations = [
            {
                "name": "三元组测试 - 游戏内容",
                "messages": [
                    {"role": "user", "content": "男枪是英雄联盟里的一个角色"},
                    {"role": "assistant", "content": "是的，男枪是ADC位置的英雄。"}
                ]
            },
            {
                "name": "五元组测试 - 含时间地点",
                "messages": [
                    {"role": "user", "content": "我昨天在北京和朋友吃了火锅"},
                    {"role": "assistant", "content": "火锅是很好的聚餐选择！"}
                ]
            },
            {
                "name": "复合测试 - 复杂提取（匹配新 prompt 示例）",
                "messages": [
                    {"role": "user", "content": "我昨天下午在玩我的世界，里面有一种怪物叫小白"},
                    {"role": "assistant", "content": "我的世界确实有很多有趣的怪物！"}
                ]
            },
            {
                "name": "个人信息测试",
                "messages": [
                    {"role": "id12345", "content": "我叫张三，我喜欢喝咖啡"},
                    {"role": "assistant", "content": "你好张三！"},
                    {"role": "id12345", "content": "我今天早上在星巴克买了拿铁"},
                    {"role": "assistant", "content": "拿铁很受欢迎！"}
                ]
            },
            {
                "name": "无记忆测试 - 纯情绪",
                "messages": [
                    {"role": "user", "content": "你真好看"},
                    {"role": "assistant", "content": "谢谢夸奖！"}
                ]
            }
        ]
        
        # 读取系统提示词
        system_prompt = _read_classifier_prompt()
        if not system_prompt:
            print("❌ 无法读取分类器提示词")
            return False
        
        print(f"✅ 成功读取提示词 (长度: {len(system_prompt)} 字符)")
        
        # 测试每个对话
        success_count = 0
        for i, test_case in enumerate(test_conversations, 1):
            print(f"\n{'='*20} 测试 {i}/{len(test_conversations)} {'='*20}")
            print(f"测试名称: {test_case['name']}")
            print("输入对话:")
            for msg in test_case['messages']:
                print(f"  {msg['role']}: {msg['content']}")
            
            # 扁平化消息
            conversation = _flatten_messages(test_case['messages'])
            print(f"\n扁平化对话:\n{conversation}")
            
            try:
                # 直接调用 _extract_memories_task
                print(f"\n🔄 调用 _extract_memories_task...")
                result = _extract_memories_task(system_prompt, conversation)
                
                # 详细打印结果
                print_memory_result_details(result, test_case['name'])
                success_count += 1
                
            except Exception as e:
                print(f"❌ 测试失败: {e}")
                import traceback
                traceback.print_exc()
        
        # 汇总结果
        print_separator("测试汇总")
        print(f"总测试数: {len(test_conversations)}")
        print(f"成功数: {success_count}")
        print(f"失败数: {len(test_conversations) - success_count}")
        print(f"成功率: {success_count/len(test_conversations)*100:.1f}%")
        
        return success_count == len(test_conversations)
        
    except Exception as e:
        print(f"❌ 导入或初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_custom_conversation():
    """测试自定义对话"""
    print_separator("自定义对话测试")
    
    try:
        from brain.memory.memory_recorder import _extract_memories_task, _read_classifier_prompt, _flatten_messages
        
        # 读取系统提示词
        system_prompt = _read_classifier_prompt()
        if not system_prompt:
            print("❌ 无法读取分类器提示词")
            return False
        
        print("请输入测试对话 (输入空行结束):")
        messages = []
        role = "user"
        
        while True:
            content = input(f"{role}: ").strip()
            if not content:
                break
            
            messages.append({"role": role, "content": content})
            role = "assistant" if role == "user" else "user"
        
        if not messages:
            print("未输入任何对话内容")
            return False
        
        print(f"\n收到 {len(messages)} 条消息:")
        for msg in messages:
            print(f"  {msg['role']}: {msg['content']}")
        
        # 处理消息
        conversation = _flatten_messages(messages)
        print(f"\n扁平化对话:\n{conversation}")
        
        # 调用函数
        print(f"\n🔄 调用 _extract_memories_task...")
        result = _extract_memories_task(system_prompt, conversation)
        
        # 打印结果
        print_memory_result_details(result, "自定义对话")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主函数"""
    print("🧠 Memory Loader _extract_memories_task 函数输出测试程序")
    print_separator()
    print("目标: 测试 _extract_memories_task 函数的具体输出内容")
    print("功能: 直接调用函数，无需异步任务管理器")
    print_separator()
    
    print("请选择测试模式:")
    print("1. 预设对话批量测试 (推荐)")
    print("2. 自定义对话测试")
    print("3. 全部测试")
    
    try:
        choice = input("请输入选择 (1/2/3，默认1): ").strip()
        if not choice:
            choice = "1"
        
        if choice == "1":
            success = test_extract_memories_task_direct()
        elif choice == "2":
            success = test_custom_conversation()
        elif choice == "3":
            print("\n执行预设测试...")
            success1 = test_extract_memories_task_direct()
            print("\n执行自定义测试...")
            success2 = test_custom_conversation()
            success = success1 and success2
        else:
            print("无效选择，执行默认测试...")
            success = test_extract_memories_task_direct()
        
        print_separator("最终结果")
        if success:
            print("🎉 测试完成！_extract_memories_task 函数输出已验证")
        else:
            print("❌ 测试过程中出现错误，请检查配置")
        print_separator()
        
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n❌ 程序异常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()