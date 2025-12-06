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

from system.config import config
from openai import AsyncOpenAI, OpenAI

# 导入任务管理器
from brain.task_manager import task_manager, TaskPriority

# 导入prompt
DIALOGUE_SUMMARIZER_PROMPT = os.path.join(_project_root, "brain", "memory", "prompt", "dialogue_summarizer.txt")
MEMORY_CLASSIFIER_PROMPT = os.path.join(_project_root, "brain", "memory", "prompt", "memory_classifier.txt")

# 导入配置信息
AI_NAME = config.system.ai_name
USERNAME = config.ui.username
DEBUG_MODE = config.system.debug

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
    importance: float = 0  # 新增importance字段
    source: str = "unknown"
    confidence: float = 0.5
    time_record: str = ""


@dataclass
class Quintuple:
    subject: str
    action: str
    object: str
    time: Optional[str] = None
    location: Optional[str] = None
    importance: float = 0  # 重要性字段
    with_: Optional[List[str]] = None  # 同行者字段，使用with_避免关键字冲突
    initiative: Optional[bool] = None  # 新增主动性字段
    source: str = "unknown"
    confidence: float = 0.5
    time_record: str = ""


@dataclass
class MemoryResult:
    has_memory: bool
    memory_type: MemoryType  # 若混合则为 TRIPLE 与 QUINTUPLE 中较“强”的类型（优先 QUINTUPLE）
    triples_count: int = 0  # 三元组数量
    quintuples_count: int = 0  # 五元组数量


# ---- 工具函数 ----
def _load_prompt_file(filename: str, description: str = "") -> str:
    """
    加载提示词文件的通用函数
    Args:
        filename: 文件名（如 "1_intent_analyze.txt"）
        description: 文件描述（用于错误提示）
    Returns:
        文件内容字符串，失败时返回空字符串
    """
    prompt_path = os.path.join(_project_root, "system", "prompts", filename)
    if not os.path.exists(prompt_path):
        error_msg = f"[错误] {description}提示词文件不存在: {prompt_path}"
        print(error_msg)
        return ""
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            print(f"[警告] {description}提示词文件为空: {prompt_path}")
        return content


def _parse_json(text: str) -> Dict[str, Any]:
    """
    将文本转换为JSON对象，智能处理字符串值中的控制字符
    """
    try:
        # 首先清理markdown格式
        cleaned_text = text.replace("```json", "").replace("```", "").strip()
        
        # 尝试直接解析
        try:
            return json.loads(cleaned_text)
        except json.JSONDecodeError:
            # 如果直接解析失败，尝试智能修复字符串值中的控制字符
            fixed_text = _fix_json_string_values(cleaned_text)
            return json.loads(fixed_text)
            
    except json.JSONDecodeError as json_error:
        print(f"[错误] JSON解析失败: {json_error}")
        return {}


def _fix_json_string_values(json_text: str) -> str:
    """
    智能修复JSON中字符串值的控制字符问题
    只转义字符串值内的控制字符，不影响JSON结构
    """
    result = ""
    in_string = False
    escape_next = False
    i = 0
    
    while i < len(json_text):
        char = json_text[i]
        
        if escape_next:
            # 如果前一个字符是转义符，直接添加当前字符
            result += char
            escape_next = False
        elif char == '\\':
            # 遇到转义符
            result += char
            escape_next = True
        elif char == '"':
            # 遇到引号，切换字符串状态
            result += char
            in_string = not in_string
        elif in_string:
            # 在字符串内部，转义控制字符
            if char == '\n':
                result += '\\n'
            elif char == '\t':
                result += '\\t'
            elif char == '\r':
                result += '\\r'
            else:
                result += char
        else:
            # 在JSON结构中，保持原样
            result += char
        
        i += 1
    
    return result


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


# ---- 异步记忆提取任务 ----
def _extract_memories_task(conversation: List[Dict], source: str = "unknown") -> MemoryResult:
    """
    实际的记忆提取逻辑（同步函数，会在 task_manager 的工作线程中执行）
    
    Args:
        system_prompt: 系统提示词
        conversation: 对话内容
        source: 数据来源，默认为"unknown"
    """
    # 首先使用对话总结器处理对话内容
    logging.info("开始对话总结处理...")
    
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": DIALOGUE_SUMMARIZER_PROMPT},
            {"role": "user", "content": conversation}
        ],
        temperature=0.3
    )
    summarized_conversation = response.choices[0].message.content
    logging.info(f"对话总结完成: {summarized_conversation}")
    if DEBUG_MODE:
        logging.info(f"[记忆] 记录总结: {summarized_conversation}")
    # 将总结后的内容发送给记忆分类器
    logging.info("开始记忆分类处理...")
    
    # 读取记忆分类器提示词
    with open(MEMORY_CLASSIFIER_PROMPT, 'r', encoding='utf-8') as f:
        classifier_prompt = f.read().replace('{AI_NAME}', AI_NAME).replace('{USERNAME}', USERNAME)
    
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": classifier_prompt},
            {"role": "user", "content": summarized_conversation}
        ],
        temperature=0.3
    )
    classifier_result = response.choices[0].message.content
    logging.info(f"记忆分类完成: {classifier_result}")
    
    extracted_quintuples = _parse_json(classifier_result)

    triples: List[Triple] = []
    quintuples: List[Quintuple] = []

    #记录三&五元组
    if isinstance(extracted_quintuples, list):
        for it in extracted_quintuples:
            # norm = _normalize_item(it)
            t = it.get("type", None)
            data = it.get("data", {})
            if t is None:
                return
            if t == "triple":
                subj = str(data.get("subject", "")).strip()
                pred = str(data.get("predicate", "")).strip()
                obj = str(data.get("object", "")).strip()
                importance = data.get("importance", 0)
                
                # 处理重要性字段
                try:
                    importance = float(importance) if importance is not None else 0
                    importance = max(0.0, min(1.0, importance))
                except (ValueError, TypeError):
                    importance = 0
                
                current_time = datetime.now().strftime("%Y-%m-%dT%H:%M")
                if subj and pred and obj:
                    triples.append(
                        Triple(
                            subject=subj, 
                            predicate=pred, 
                            object=obj,
                            importance=importance,
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
                importance = data.get("importance", 0)
                with_list = data.get("with")
                initiative = data.get("initiative")  # 新增主动性字段
                
                # 处理重要性字段
                try:
                    importance = float(importance) if importance is not None else 0
                    importance = max(0.0, min(1.0, importance))
                except (ValueError, TypeError):
                    importance = 0
                
                # 处理同行者字段
                with_processed = None
                if with_list and isinstance(with_list, list):
                    with_processed = [str(item).strip() for item in with_list if str(item).strip()]
                    if not with_processed:
                        with_processed = None
                
                # 处理主动性字段
                initiative_processed = None
                if initiative is not None:
                    try:
                        initiative_processed = bool(initiative)
                    except (ValueError, TypeError):
                        initiative_processed = None
                
                current_time = datetime.now().strftime("%Y-%m-%dT%H:%M")
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
                            initiative=initiative_processed,
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
            logger.info(f"[记忆] 识别到 {len(quintuples)} 条五元组。")
        if triples:
            logger.info(f"[记忆] 识别到 {len(triples)} 条三元组。")

    # 将 triples/quintuples 持久化到 recent_memory_quintuples.json
    if has_memory:
        _save_memories_to_json(triples, quintuples)
        logger.info(f"[记忆] 成功保存到 recent_memory_quintuples.json: {len(triples)} 三元组, {len(quintuples)} 五元组")
        # 调用knowledge_graph_manager将数据推送至neo4j，并保留一份[新记忆]node组在本地



    return MemoryResult(
        has_memory=has_memory,
        memory_type=memory_type,
        triples_count=len(triples),
        quintuples_count=len(quintuples)
    )


# ---- 核心入口 ----
def record_messages_to_memories(messages: List[Dict], source: str = "unknown", priority: TaskPriority = TaskPriority.LOW):
    """
    应在一轮对话结束后被调用：
    - 提取最新消息中所含的信息，判断是否需要记忆
    - 读取分类提示词（同步）
    - 将记忆提取任务提交到 task_manager（异步执行）
    - 返回任务 ID，可通过 task_manager.get_task_status(task_id) 查询进度
    
    Args:
        messages: 完整的消息列表
        source: 数据来源，应该是USERNAME，否则unknown
        priority: 任务优先级
    """
    # 提交异步任务
    if DEBUG_MODE:
        logger.info(f"[记忆] 记忆消息: {messages[-1].get('content', '') if messages else ''}, 信息源: {source}")
        return ""
    task_id = task_manager.submit_task(
        "对话记忆提取",
        _extract_memories_task,
        messages,
        source,
        priority=priority
    )
    
    last_message = messages[-1].get("content", "") if messages else ""

    logger.info(f"[记忆] 已提交记忆提取任务: {task_id} (处理内容：{last_message})")
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
        file_path = os.path.join(config.system.log_dir, "recent_memory_quintuples.json")
    
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
