#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 logs_to_load 文件夹中读取聊天日志文件，逐行录入模拟对话情景，录入三元组/五元组
"""

import sys
import os
import json
import re
import glob
import shutil
from datetime import datetime

# 添加项目根目录到模块搜索路径
project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

def parse_filename_date(filename):
    """从文件名中解析日期信息"""
    # 期望格式: chat_logs_YYYY_MM_DD.txt
    pattern = r'chat_logs_(\d{4})_(\d{2})_(\d{2})\.txt$'
    match = re.search(pattern, filename)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    return None

def parse_log_line(line, date_str):
    """解析日志行，提取时间、发言者和内容"""
    # 期望格式: HH:MM:SS <发言者> 内容
    pattern = r'^(\d{2}:\d{2}:\d{2})\s+<([^>]+)>\s+(.+)$'
    match = re.match(pattern, line)
    if match:
        time_str, speaker, content = match.groups()
        # 组合完整时间戳
        full_timestamp = f"{date_str}T{time_str}"
        # 保持原始发言者身份，不进行角色映射
        return {
            'timestamp': full_timestamp,
            'speaker': speaker,
            'role': speaker,  # 使用原始发言者作为角色
            'content': content.strip()
        }
    return None

def move_processed_file(source_file_path, filename):
    """将处理完的日志文件移动到 logs/chat_logs 目录"""
    try:
        from system.config import config
        
        # 目标目录
        target_dir = os.path.join(config.system.log_dir, "chat_logs")
        
        # 确保目标目录存在
        os.makedirs(target_dir, exist_ok=True)
        
        # 目标文件路径
        target_file_path = os.path.join(target_dir, filename)
        
        # 如果目标文件已存在，创建带时间戳的副本
        if os.path.exists(target_file_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name, ext = os.path.splitext(filename)
            new_filename = f"{name}_{timestamp}{ext}"
            target_file_path = os.path.join(target_dir, new_filename)
            print(f"    ⚠️  目标文件已存在，重命名为: {new_filename}")
        
        # 移动文件
        shutil.move(source_file_path, target_file_path)
        print(f"    📂 文件已移动到: {target_file_path}")
        
        return True
        
    except Exception as e:
        print(f"    ❌ 移动文件失败: {e}")
        return False

def load_chat_logs_from_folder():
    """从 logs_to_load 文件夹加载所有聊天日志文件并进行记忆提取"""
    print("🧠 从日志文件加载对话记忆（逐行录入模拟）")
    print("=" * 80)
    
    try:
        from brain.memory.quintuples_extractor import _extract_memories_task, _read_classifier_prompt, _flatten_messages
        from system.config import config
        import time
        
        # 查找 logs_to_load 文件夹下的所有 txt 文件
        logs_folder = os.path.join(project_root, "brain", "memory", "logs_to_load")
        if not os.path.exists(logs_folder):
            print(f"❌ 日志文件夹不存在: {logs_folder}")
            return False
        
        txt_files = glob.glob(os.path.join(logs_folder, "*.txt"))
        if not txt_files:
            print(f"❌ 在 {logs_folder} 中未找到任何 txt 文件")
            return False
        
        print(f"📁 找到 {len(txt_files)} 个日志文件:")
        
        # 验证文件名格式并提取日期
        valid_files = []
        for file_path in txt_files:
            filename = os.path.basename(file_path)
            date_str = parse_filename_date(filename)
            if date_str:
                valid_files.append((file_path, filename, date_str))
                print(f"  ✅ {filename} -> 日期: {date_str}")
            else:
                print(f"  ❌ {filename} -> 文件名格式不正确")
                print(f"     期望格式: chat_logs_YYYY_MM_DD.txt")
                print(f"     请修改文件名为正确格式后重新运行")
                return False
        
        if not valid_files:
            print("❌ 没有有效的日志文件，请检查文件名格式")
            return False
        
        # 按日期排序文件
        valid_files.sort(key=lambda x: x[2])
        
        print(f"\n🔧 配置信息:")
        print(f"  - 日志目录: {config.system.log_dir}")
        print(f"  - 输出文件: {os.path.join(config.system.log_dir, 'recent_memory.json')}")
        
        total_conversations = 0
        total_memories_extracted = 0
        
        # 处理每个文件
        for file_path, filename, date_str in valid_files:
            print(f"\n📖 处理文件: {filename} (日期: {date_str})")
            print("-" * 60)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                
                if not lines:
                    print(f"  ⚠️  文件为空，跳过")
                    continue
                
                print(f"  📝 读取到 {len(lines)} 行对话")
                conversation_history = []
                
                # 逐行处理对话
                for line_num, line in enumerate(lines, 1):
                    parsed = parse_log_line(line, date_str)
                    if not parsed:
                        print(f"    ⚠️  第 {line_num} 行格式不正确，跳过: {line}")
                        continue
                    
                    # 构建消息对象
                    message = {
                        "role": parsed['role'],
                        "content": parsed['content'],
                        "timestamp": parsed['timestamp']
                    }
                    conversation_history.append(message)
                    
                    print(f"    📤 第 {line_num} 轮 - ({parsed['timestamp']}) <{parsed['speaker']}> {parsed['content'][:50]}{'...' if len(parsed['content']) > 50 else ''}")
                    print(f"         当前历史: {len(conversation_history)} 条消息")
                    
                    # 直接调用记忆提取函数（同步版本）
                    system_prompt = _read_classifier_prompt()
                    if not system_prompt:
                        print(f"         ❌ 无法读取分类器提示词，跳过本轮")
                        continue
                    
                    # 根据字数限制选择历史消息
                    def select_messages_by_content_length(messages, max_chars=100, min_messages=2):
                        """
                        根据内容字数选择消息，确保至少包含min_messages条消息，
                        在此基础上尽可能多地包含历史消息，但总字数不超过max_chars
                        """
                        if len(messages) <= min_messages:
                            return messages
                        
                        # 从最新的消息开始，向前累积
                        selected_messages = []
                        total_chars = 0
                        
                        # 先确保至少有min_messages条消息
                        for i in range(min(min_messages, len(messages))):
                            msg = messages[-(i+1)]  # 从最后一条开始
                            selected_messages.insert(0, msg)  # 插入到开头保持顺序
                            total_chars += len(msg.get('content', ''))
                        
                        # 在字数限制内尽可能添加更多历史消息
                        for i in range(min_messages, len(messages)):
                            msg = messages[-(i+1)]
                            msg_chars = len(msg.get('content', ''))
                            
                            if total_chars + msg_chars <= max_chars:
                                selected_messages.insert(0, msg)
                                total_chars += msg_chars
                            else:
                                break
                        
                        return selected_messages
                    
                    recent_messages = select_messages_by_content_length(conversation_history, max_chars=100, min_messages=2)
                    total_content_chars = sum(len(msg.get('content', '')) for msg in recent_messages)
                    
                    print(f"         📝 选择了 {len(recent_messages)} 条消息，内容总字数: {total_content_chars} 字")
                    
                    conversation_text = _flatten_messages(recent_messages)
                    print(f"         📄 完整对话文本长度: {len(conversation_text)} 字符")
                    print(conversation_text)
                    
                    # 同步提取记忆
                    memory_result = _extract_memories_task(system_prompt, conversation_text)
                    
                    if memory_result and memory_result.has_memory:
                        extracted_count = len(memory_result.triples) + len(memory_result.quintuples)
                        total_memories_extracted += extracted_count
                        print(f"         ✅ 提取记忆: {len(memory_result.triples)} 三元组, {len(memory_result.quintuples)} 五元组")
                        
                        # 显示提取的记忆内容（简化显示）
                        for triple in memory_result.triples[-2:]:  # 只显示最新的2个
                            print(f"            🔗 三元组: {triple.subject} -> {triple.predicate} -> {triple.object}")
                        
                        for quintuple in memory_result.quintuples[-2:]:  # 只显示最新的2个
                            time_str = f", 时间: {quintuple.time}" if quintuple.time else ""
                            loc_str = f", 地点: {quintuple.location}" if quintuple.location else ""
                            print(f"            🎯 五元组: {quintuple.subject} -> {quintuple.action} -> {quintuple.object}{time_str}{loc_str}")
                    else:
                        print(f"         ⚪ 本轮无记忆内容")
                    
                    # 短暂延迟，模拟真实对话间隔
                    time.sleep(0.1)
                
                total_conversations += len(lines)
                print(f"  ✅ 文件处理完成: {len(lines)} 轮对话")
                
                # 处理完成后移动文件到 logs/chat_logs 目录
                print(f"  📦 移动已处理的文件...")
                if move_processed_file(file_path, filename):
                    print(f"  ✅ 文件移动成功，避免重复处理")
                else:
                    print(f"  ⚠️  文件移动失败，可能会在下次运行时重复处理")
                
            except Exception as e:
                print(f"  ❌ 处理文件异常: {e}")
                continue
        
        # 检查最终的 JSON 文件
        json_file = os.path.join(config.system.log_dir, "recent_memory.json")
        if os.path.exists(json_file):
            print(f"\n📄 最终记忆文件检查: {json_file}")
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 处理可能的数据格式问题
                if isinstance(data, list):
                    # 如果是列表格式，转换为期望的字典格式
                    data = {"triples": [], "quintuples": [], "metadata": {}}
                    print("  ⚠️  检测到旧格式数据，已转换为新格式")
                
                total_triples = len(data.get('triples', []))
                total_quintuples = len(data.get('quintuples', []))
                
                print(f"  - 累计三元组数: {total_triples}")
                print(f"  - 累计五元组数: {total_quintuples}")  
                print(f"  - 总记忆数量: {total_triples + total_quintuples}")
                print(f"  - 最后更新: {data.get('metadata', {}).get('last_updated', 'Unknown')}")
                
                # 显示最新的记忆
                if data.get('triples'):
                    print(f"\n🔗 最新的三元组 (显示最新5个):")
                    for i, triple in enumerate(data['triples'][-5:], 1):
                        print(f"    {i}. {triple['subject']} -> {triple['predicate']} -> {triple['object']} ({triple['time_record']})")
                
                if data.get('quintuples'):
                    print(f"\n🎯 最新的五元组 (显示最新5个):")
                    for i, quint in enumerate(data['quintuples'][-5:], 1):
                        time_str = f", 时间: {quint['time']}" if quint['time'] else ""
                        loc_str = f", 地点: {quint['location']}" if quint['location'] else ""
                        print(f"    {i}. {quint['subject']} -> {quint['action']} -> {quint['object']}{time_str}{loc_str} ({quint['time_record']})")
                
            except Exception as e:
                print(f"  ❌ 读取文件失败: {e}")
                return False
        else:
            print(f"\n❌ 记忆文件不存在: {json_file}")
            return False
        
        print(f"\n{'='*80}")
        print(f"✅ 所有日志文件处理完成！")
        print(f"📊 统计信息:")
        print(f"  - 处理文件数: {len(valid_files)}")
        print(f"  - 总对话轮数: {total_conversations}")
        print(f"  - 总提取记忆数: {total_memories_extracted}")
        print(f"💾 所有记忆已保存到 recent_memory.json")
        print(f"📂 已处理的文件已移动到 logs/chat_logs 目录，避免重复处理")
        
        return True
        
    except Exception as e:
        print(f"❌ 加载日志异常: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("🧪 日志文件记忆提取测试程序")
    print("=" * 80)
    
    # 主要功能：从日志文件加载对话记忆
    main_success = load_chat_logs_from_folder()
    
    print("\n" + "=" * 80)
    print("📈 测试总结:")
    print(f"  - 日志文件记忆提取: {'✅ 成功' if main_success else '❌ 失败'}")
    
    if main_success:
        print("🎉 日志文件记忆提取功能正常工作！")
        print("💡 重点：从 logs_to_load 文件夹逐行读取聊天记录，每轮对话都生成记忆并保存到 recent_memory.json")
        print("📂 处理完的文件自动移动到 logs/chat_logs 目录，避免重复导入")
    else:
        print("⚠️  日志文件记忆提取失败，请检查:")
        print("  - 文件名格式是否为 chat_logs_YYYY_MM_DD.txt")
        print("  - 文件内容格式是否为 HH:MM:SS <发言者> 内容")
        print("  - logs_to_load 文件夹是否存在且包含 txt 文件")

if __name__ == "__main__":
    main()