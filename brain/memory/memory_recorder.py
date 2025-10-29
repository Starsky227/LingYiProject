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
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional

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


@dataclass
class Quintuple:
    subject: str
    action: str
    object: str
    time: Optional[str] = None
    location: Optional[str] = None


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


def _flatten_messages(messages: List[Dict[str, Any]]) -> str:
    """将对话历史转为可读文本，便于送入分类器"""
    lines = []
    for m in messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


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
    user_prompt = f"输入：\n{conversation}\n输出："
    
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
                if subj and pred and obj:
                    triples.append(Triple(subject=subj, predicate=pred, object=obj))
            elif t == "quintuple":
                subj = str(data.get("subject", "")).strip()
                action = str(data.get("action", "")).strip()
                obj = str(data.get("object", "")).strip()
                tm = data.get("time")
                loc = data.get("location")
                # time/location 可以为 null
                if subj and action and obj:
                    quintuples.append(
                        Quintuple(
                            subject=subj,
                            action=action,
                            object=obj,
                            time=(None if tm in (None, "", "null") else str(tm)),
                            location=(None if loc in (None, "", "null") else str(loc)),
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

    # TODO: 将 triples/quintuples 持久化到本地 JSON 文件（后续实现）
    # 例如：
    # save_path = os.path.join(_project_root(), "data", "memories.json")
    # ensure_dir(...)
    # append_or_merge_memories(save_path, triples, quintuples)

    return MemoryResult(
        has_memory=has_memory,
        memory_type=memory_type,
        triples=triples,
        quintuples=quintuples,
        raw_json=parsed,
        reason="",
    )


# ---- 核心入口 ----
def record_memories(messages: List[Dict[str, Any]], priority: TaskPriority = TaskPriority.LOW) -> str:
    """
    在一轮对话结束后调用：
    - 读取分类提示词（同步）
    - 将记忆提取任务提交到 task_manager（异步执行）
    - 返回任务 ID，可通过 task_manager.get_task_status(task_id) 查询进度
    """
    # 同步读取提示词和处理对话文本
    system_prompt = _read_classifier_prompt()
    if not system_prompt:
        logger.warning("[记忆] 提示词缺失，已终止本轮记忆提取。")
        return ""

    conversation = _flatten_messages(messages)
    
    # 提交异步任务
    task_id = task_manager.submit_task(
        "对话记忆提取",
        _extract_memories_task,
        system_prompt,
        conversation,
        priority=priority
    )
    
    logger.info(f"[记忆] 已提交记忆提取任务: {task_id}")
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