# -*- coding: utf-8 -*-
"""
对话记忆加载器：
- 在一轮对话结束后，基于 brain/memory/prompt/memory_classfier.txt 的提示词判定并提取可记忆内容
- 若为可记忆内容：
  - 含时间/地点 => 标记为"五元组"
  - 不含时间/地点 => 标记为"三元组"
- 通过 task_manager 异步执行记忆提取任务
- 实际持久化到 json 的逻辑留 TODO

使用：
from brain.memory.memory_loader import record_memories
task_id = record_memories(messages)  # 返回任务ID，异步执行
"""
from __future__ import annotations

import logging
import os
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import asdict
from pathlib import Path

# 添加项目根目录到模块搜索路径
_project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from system.config import config
from openai import AsyncOpenAI, OpenAI

# 导入任务管理器
from brain.task_manager import task_manager, TaskPriority


# 模型配置
MODEL = config.api.model

# 初始化OpenAI客户端
client = OpenAI(
    api_key=config.api.api_key,
    base_url=config.api.base_url
)

async_client = AsyncOpenAI(
    api_key=config.api.api_key,
    base_url=config.api.base_url
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# 抑制 httpx 和相关库的详细日志输出
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# ---- 数据结构 ----
class MemoryType(str, Enum):
    NONE = "none"
    TRIPLE = "triple"
    QUINTUPLE = "quintuple"


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    source: str = "用户"
    confidence: float = 0.5
    time_record: str = ""


@dataclass
class Quintuple:
    subject: str
    action: str
    object: str
    time: Optional[str] = None
    location: Optional[str] = None
    importance: float = 0.5  # 新增重要性字段
    with_: Optional[List[str]] = None  # 新增同行者字段，使用with_避免关键字冲突
    source: str = "用户"
    confidence: float = 0.5
    time_record: str = ""


@dataclass
class MemoryResult:
    has_memory: bool
    memory_type: MemoryType  # 若混合则为 TRIPLE 与 QUINTUPLE 中较"强"的类型（优先 QUINTUPLE）
    triples: List[Triple]
    quintuples: List[Quintuple]
    raw_json: Any  # 原始解析结果（便于调试/后续处理）
    reason: str = ""


# ---- 工具函数 ----
def _project_root() -> str:
    """返回项目根目录路径"""
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_classifier_prompt() -> Optional[str]:
    """
    读取 memory_classifier.txt 作为系统提示词。
    """
    root = _project_root()
    path = os.path.join(root, "brain", "memory", "prompt", "memory_classifier.txt")

    if not os.path.exists(path):
        logger.error("[记忆] 未找到提示词文件：brain/memory/prompt/memory_classifier.txt")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"[记忆] 读取提示词失败: {e}")
        return None


def _get_real_timestamps_from_logs(messages: List[Dict[str, Any]]) -> List[Optional[str]]:
    """
    从日志文件中获取真实的对话时间戳
    返回与messages对应的时间戳列表，如果找不到则为None
    """
    if not messages:
        return []
    
    try:
        # 获取今天的日志文件
        logs_dir = os.path.join(_project_root(), "logs", "chat_logs")
        today_filename = datetime.now().strftime("chat_logs_%Y_%m_%d.txt")
        log_file_path = os.path.join(logs_dir, today_filename)
        
        if not os.path.exists(log_file_path):
            logger.warning(f"[记忆] 今日日志文件不存在: {log_file_path}")
            return [None] * len(messages)
        
        # 读取日志文件
        with open(log_file_path, "r", encoding="utf-8") as f:
            log_lines = f.readlines()
        
        # 解析日志行，提取时间戳和内容
        log_entries = []
        for line in log_lines:
            line = line.strip()
            if not line:
                continue
            # 格式: HH:MM:SS <speaker> content
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                time_str = parts[0]
                speaker = parts[1].strip("<>")
                content = parts[2]
                log_entries.append({
                    "time": time_str,
                    "speaker": speaker,
                    "content": content
                })
        
        # 匹配消息内容到日志条目
        timestamps = []
        for msg in messages:
            content = (msg.get("content") or "").strip()
            role = msg.get("role", "user")
            
            # 转换角色名称
            if role == "user":
                expected_speaker = config.ui.username if hasattr(config, 'ui') and hasattr(config.ui, 'username') else "用户"
            elif role == "assistant":
                expected_speaker = config.system.ai_name if hasattr(config, 'system') and hasattr(config.system, 'ai_name') else "AI"
            else:
                expected_speaker = role
            
            # 在日志中查找匹配的条目
            found_timestamp = None
            for entry in log_entries:
                # 内容匹配（处理换行符替换）
                log_content = entry["content"].replace(" ", " ")  # 日志中换行被替换为空格
                if (entry["speaker"] == expected_speaker and 
                    (content in log_content or log_content in content or 
                     content.replace("\n", " ").replace("\r", " ") == log_content)):
                    found_timestamp = entry["time"]
                    break
            
            timestamps.append(found_timestamp)
        
        return timestamps
        
    except Exception as e:
        logger.error(f"[记忆] 从日志获取时间戳失败: {e}")
        return [None] * len(messages)


def _flatten_messages(messages: List[Dict[str, Any]]) -> str:
    """将对话历史转为带时间戳的文本格式，直接使用消息中的真实时间戳"""
    lines = []
    current_time = datetime.now()
    
    for i, m in enumerate(messages):
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        
        timestamp = None
        
        # 直接使用消息中的真实时间戳
        msg_timestamp = m.get("timestamp")
        if msg_timestamp:
            try:
                # 解析ISO格式时间戳
                if isinstance(msg_timestamp, str):
                    msg_time = datetime.fromisoformat(msg_timestamp.replace('Z', '+00:00'))
                else:
                    msg_time = msg_timestamp
                timestamp = msg_time.strftime("%Y_%m_%d_%H:%M:%S")
            except (ValueError, TypeError) as e:
                logger.warning(f"[记忆] 消息时间戳解析失败: {msg_timestamp}, 错误: {e}")
        
        # 如果消息没有时间戳，报错并终止
        if not timestamp:
            error_msg = f"[记忆] 消息缺少时间戳信息，无法处理记忆提取。消息索引: {i}, role: {role}, content: {content[:50]}..."
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 直接使用原始角色名
        lines.append(f"{timestamp} <{role}> {content}")
    
    return "\n".join(lines)


def _summarize_dialogue(conversation: str) -> str:
    """使用对话总结器处理对话内容"""
    try:
        # 读取对话总结器提示词
        prompt_path = os.path.join(_project_root(), "brain", "memory", "prompt", "dialogue_summarizer.txt")
        if not os.path.exists(prompt_path):
            logging.warning(f"对话总结器提示词文件不存在: {prompt_path}")
            return conversation
            
        with open(prompt_path, "r", encoding="utf-8") as f:
            summarizer_prompt = f.read().strip()
        
        # 构建完整提示
        full_prompt = f"{summarizer_prompt}\n\n输入：\n{conversation}\n\n输出："
        
        # 调用模型进行对话总结
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个专业的对话总结器，严格按照格式要求处理对话内容。"},
                {"role": "user", "content": full_prompt}
            ],
            temperature=0.3
        )
        
        result = response.choices[0].message.content.strip()
        logging.info(f"对话总结完成，原文长度: {len(conversation)}, 总结长度: {len(result)}")
        return result
        
    except Exception as e:
        logging.error(f"对话总结失败: {e}")
        return conversation


def _safe_json_parse(text: str) -> Optional[Any]:
    """宽松解析：兼容 ```json ... ``` 包裹与前后噪声"""
    s = text.strip()
    # 去除代码块围栏
    if s.startswith("```"):
        parts = s.split("```")
        # 取中间主体
        if len(parts) >= 2:
            s = parts[1]
            if s.lstrip().startswith("json"):
                s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.strip()
    # 尝试直接解析
    try:
        return json.loads(s)
    except Exception:
        # 兜底：截取首个 JSON 数组片段
        if "[" in s and "]" in s:
            try:
                start = s.index("[")
                end = s.rindex("]") + 1
                return json.loads(s[start:end])
            except Exception:
                return None
        return None


def _normalize_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """规范化单条输出，返回标准字典：{"type": MemoryType, "data": dict}"""
    if not isinstance(item, dict):
        return None
    t = str(item.get("type", "none")).strip().lower()
    data = item.get("data", {})
    if t not in ("triple", "quintuple", "none"):
        return None
    if not isinstance(data, dict):
        data = {}
    return {"type": t, "data": data}


# ---- 使用 OpenAI 客户端调用模型 ----
def _call_model_with_openai(system_prompt: str, user_prompt: str) -> str:
    """使用 OpenAI 客户端（指向本地 Ollama）进行同步调用"""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            stream=False
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"[记忆] 模型调用失败: {e}")
        return ""


# ---- 异步记忆提取任务 ----
def _extract_memories_task(system_prompt: str, conversation: str) -> MemoryResult:
    """
    实际的记忆提取逻辑（同步函数，会在 task_manager 的工作线程中执行）
    """
    # 首先使用对话总结器处理对话内容
    logging.info("开始对话总结处理...")
    summarized_conversation = _summarize_dialogue(conversation)
    logging.info(f"对话总结完成: {summarized_conversation[:100]}...")
    
    # 将总结后的内容发送给记忆分类器
    user_prompt = f"输入：\n{summarized_conversation}\n输出："
    
    # 使用 OpenAI 客户端调用模型
    raw = _call_model_with_openai(system_prompt, user_prompt)
    parsed = _safe_json_parse(raw)

    triples: List[Triple] = []
    quintuples: List[Quintuple] = []

    if isinstance(parsed, list):
        for it in parsed:
            norm = _normalize_item(it)
            if not norm:
                continue
            t = norm["type"]
            data = norm["data"]

            if t == "triple":
                subj = str(data.get("subject", "")).strip()
                pred = str(data.get("predicate", "")).strip()
                obj = str(data.get("object", "")).strip()
                # Note: 'source' field no longer in prompt output, using default
                source = "用户"
                current_time = datetime.now().strftime("%Y-%m-%dT%H:%M")
                if subj and pred and obj:
                    triples.append(
                        Triple(
                            subject=subj, 
                            predicate=pred, 
                            object=obj, 
                            source=source,
                            confidence=0.5,
                            time_record=current_time
                        )
                    )
            elif t == "quintuple":
                subj = str(data.get("subject", "")).strip()
                action = str(data.get("action", "")).strip()
                obj = str(data.get("object", "")).strip()
                tm = data.get("time")
                loc = data.get("location")
                importance = data.get("importance", 0.5)  # 新增重要性字段
                with_list = data.get("with")  # 新增同行者字段
                
                # 处理重要性字段
                try:
                    importance = float(importance) if importance is not None else 0.5
                    # 确保重要性在合理范围内
                    importance = max(0.0, min(1.0, importance))
                except (ValueError, TypeError):
                    importance = 0.5
                
                # 处理同行者字段
                with_processed = None
                if with_list and isinstance(with_list, list):
                    with_processed = [str(item).strip() for item in with_list if str(item).strip()]
                    if not with_processed:  # 如果列表为空，设为None
                        with_processed = None
                
                # Note: 'source' field no longer in prompt output, using default
                source = "用户"
                current_time = datetime.now().strftime("%Y-%m-%dT%H:%M")
                # time/location 可以为 null
                if subj and action and obj:
                    quintuples.append(
                        Quintuple(
                            subject=subj,
                            action=action,
                            object=obj,
                            time=(None if tm in (None, "", "null") else str(tm)),
                            location=(None if loc in (None, "", "null") else str(loc)),
                            importance=importance,
                            with_=with_processed,
                            source=source,
                            confidence=0.5,
                            time_record=current_time
                        )
                    )
            else:
                # type == none => 忽略
                pass

    has_memory = bool(triples or quintuples)
    memory_type = (
        MemoryType.QUINTUPLE if quintuples else (MemoryType.TRIPLE if triples else MemoryType.NONE)
    )

    # 控制台反馈（便于调试）
    if not has_memory:
        logger.info("[记忆] 本轮对话无可记忆内容，已忽略。")
    else:
        if quintuples:
            logger.info(f"[记忆] 识别到 {len(quintuples)} 条五元组（含时间/地点）。")
        if triples:
            logger.info(f"[记忆] 识别到 {len(triples)} 条三元组。")

    # 将 triples/quintuples 持久化到 recent_memory.json
    if has_memory:
        try:
            _save_memories_to_json(triples, quintuples)
            logger.info(f"[记忆] 成功保存到 recent_memory.json: {len(triples)} 三元组, {len(quintuples)} 五元组")
        except Exception as e:
            logger.error(f"[记忆] recent_memory.json 保存异常: {e}")
            # 不阻断流程，继续返回结果

    return MemoryResult(
        has_memory=has_memory,
        memory_type=memory_type,
        triples=triples,
        quintuples=quintuples,
        raw_json=parsed,
        reason="",
    )


# ---- 核心入口 ----
def record_memories(messages: List[Dict[str, Any]], priority: TaskPriority = TaskPriority.LOW, max_messages: int = 6) -> str:
    """
    在一轮对话结束后调用：
    - 自动提取最近的消息记录（默认6条，适应多角色互动场景）
    - 读取分类提示词（同步）
    - 将记忆提取任务提交到 task_manager（异步执行）
    - 返回任务 ID，可通过 task_manager.get_task_status(task_id) 查询进度
    
    Args:
        messages: 完整的消息列表
        priority: 任务优先级
        max_messages: 最大处理消息数量（默认6条，支持3用户+3AI或多角色场景）
    """
    # 提取最近的消息记录
    recent_messages = _extract_recent_messages(messages, max_messages)
    
    if not recent_messages:
        logger.info("[记忆] 没有有效的消息需要处理")
        return ""
    
    # 同步读取提示词和处理对话文本
    system_prompt = _read_classifier_prompt()
    if not system_prompt:
        logger.warning("[记忆] 提示词缺失，已终止本轮记忆提取。")
        return ""

    conversation = _flatten_messages(recent_messages)
    
    # 提交异步任务
    task_id = task_manager.submit_task(
        "对话记忆提取",
        _extract_memories_task,
        system_prompt,
        conversation,
        priority=priority
    )
    
    logger.info(f"[记忆] 已提交记忆提取任务: {task_id} (处理 {len(recent_messages)} 条最近消息)")
    return task_id


def get_memory_result(task_id: str) -> Optional[MemoryResult]:
    """
    查询记忆提取任务结果
    
    Args:
        task_id: record_memories() 返回的任务 ID
    
    Returns:
        MemoryResult 或 None（任务未完成/失败时）
    """
    status = task_manager.get_task_status(task_id)
    if not status:
        return None
    
    if status["status"] == "completed":
        return status.get("result")
    
    return None


# ---- JSON 持久化功能 ----
def _save_memories_to_json(triples: List[Triple], quintuples: List[Quintuple], file_path: Optional[str] = None) -> bool:
    """将三元组和五元组保存到 JSON 文件"""
    if file_path is None:
        file_path = os.path.join(config.system.log_dir, "recent_memory.json")
    
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 读取现有数据
        existing_data = {"triples": [], "quintuples": [], "metadata": {}}
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except Exception as e:
                logger.warning(f"[记忆] 读取现有JSON文件失败，将创建新文件: {e}")
        
        # 转换新数据为字典格式
        new_triples = [asdict(triple) for triple in triples]
        new_quintuples = []
        for quintuple in quintuples:
            quintuple_dict = asdict(quintuple)
            # 将 with_ 字段重命名为 with
            if 'with_' in quintuple_dict:
                quintuple_dict['with'] = quintuple_dict.pop('with_')
            new_quintuples.append(quintuple_dict)
        
        # 合并数据
        existing_data["triples"].extend(new_triples)
        existing_data["quintuples"].extend(new_quintuples)
        
        # 更新元数据
        existing_data["metadata"] = {
            "last_updated": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "total_triples": len(existing_data["triples"]),
            "total_quintuples": len(existing_data["quintuples"]),
            "total_memories": len(existing_data["triples"]) + len(existing_data["quintuples"])
        }
        
        # 保存到文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"[记忆] 成功保存到 JSON: {len(new_triples)} 三元组, {len(new_quintuples)} 五元组 -> {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"[记忆] JSON 保存失败: {e}")
        return False


def _extract_recent_messages(messages: List[Dict[str, Any]], max_messages: int = 6) -> List[Dict[str, Any]]:
    """
    提取最近的消息记录，适应多角色互动场景
    
    Args:
        messages: 完整的消息列表
        max_messages: 最大消息数量（默认6条，支持3用户+3AI或多角色场景）
    
    Returns:
        最近的消息列表，最多包含 max_messages 条消息
    """
    if not messages:
        return []
    
    # 过滤有效消息（有role和content的消息）
    valid_messages = []
    for msg in messages:
        role = msg.get("role", "").strip()
        content = (msg.get("content") or "").strip()
        if role and content:
            valid_messages.append(msg)
    
    # 如果消息数量不足，返回全部
    if len(valid_messages) <= max_messages:
        logger.info(f"[记忆] 历史消息不足 {max_messages} 条，使用全部 {len(valid_messages)} 条消息")
        return valid_messages
    
    # 提取最近的消息
    recent_messages = valid_messages[-max_messages:]
    logger.info(f"[记忆] 提取最近 {len(recent_messages)} 条消息进行记忆处理")
    return recent_messages


def record_recent_conversations_async(messages: List[Dict[str, Any]], max_messages: int = 6, priority: TaskPriority = TaskPriority.LOW) -> str:
    """
    异步处理最近的消息并保存到 recent_memory.json
    注意：此函数现在直接调用 record_memories，推荐直接使用 record_memories
    
    Args:
        messages: 完整的消息列表
        max_messages: 要处理的最大消息数量（默认6条）
        priority: 任务优先级
    
    Returns:
        任务 ID
    """
    # 直接调用 record_memories，功能已经集成
    return record_memories(messages, priority=priority, max_messages=max_messages)