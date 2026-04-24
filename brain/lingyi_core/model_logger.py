"""模型调用调试日志（输入 + 输出 + 工具结果）。

从 ``lingyi_core.py`` 抽出，让核心调度文件聚焦业务逻辑。
日志按日期切片写入 ``data/cache/model_input_logs/model_log_YYYY_MM_DD.txt``，
每天最多保留 ``_MODEL_LOG_MAX_FILES`` 个文件。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


_MODEL_LOG_DIR = Path("data/cache/model_input_logs")
_MODEL_LOG_MAX_FILES = 5


def _write_model_log(content: str) -> None:
    """将内容追加写入按日期命名的调试日志文件，最多保留 _MODEL_LOG_MAX_FILES 个文件。"""
    try:
        _MODEL_LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y_%m_%d")
        log_file = _MODEL_LOG_DIR / f"model_log_{today}.txt"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(content)

        # 清理旧文件
        log_files = sorted(_MODEL_LOG_DIR.glob("model_log_*.txt"))
        if len(log_files) > _MODEL_LOG_MAX_FILES:
            for old_file in log_files[:-_MODEL_LOG_MAX_FILES]:
                old_file.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"[模型日志] 写入失败: {e}")


def _serialize_input_items(input_items: list, round_num: int) -> str:
    """将 input_items 序列化为模型实际阅读到的文本格式。"""
    lines: list[str] = []
    lines.append(f"第 {round_num} 轮模型调用  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 第一条 system 消息是 prompt，省略其内容；后续 system 消息（屏幕环境/便签/记忆等）正常记录
    prompt_skipped = False

    for item in input_items:
        if isinstance(item, dict):
            role = item.get("role", "")
            content = item.get("content", "")
            item_type = item.get("type", "")

            if item_type == "function_call_output":
                lines.append(f"[function_call_output] call_id={item.get('call_id', '')}")
                lines.append(str(item.get("output", "")))
                lines.append("")
            elif role == "system" and not prompt_skipped:
                # 首条 system 消息是主 prompt，省略内容
                prompt_skipped = True
                lines.append("[system] (prompt省略)")
                lines.append("")
            elif isinstance(content, list):
                # 多模态内容（图片+文本）
                lines.append(f"[{role}]")
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "input_text":
                            lines.append(part.get("text", ""))
                        elif part.get("type") == "input_image":
                            lines.append("[图片内容 - 省略base64数据]")
                lines.append("")
            elif content:
                lines.append(f"[{role}]")
                lines.append(str(content))
                lines.append("")
        else:
            # SDK 对象（reasoning / function_call 等）
            obj_type = getattr(item, "type", "unknown")
            if obj_type == "function_call":
                name = getattr(item, "name", "")
                arguments = getattr(item, "arguments", "")
                call_id = getattr(item, "call_id", "")
                lines.append(f"[function_call] {name}  call_id={call_id}")
                lines.append(str(arguments))
                lines.append("")
            elif obj_type == "reasoning":
                summaries = getattr(item, "summary", None)
                if summaries:
                    lines.append("[reasoning]")
                    for s in summaries:
                        lines.append(getattr(s, "text", ""))
                    lines.append("")
            else:
                lines.append(f"[{obj_type}] {str(item)[:500]}")
                lines.append("")

    return "\n".join(lines)


def _serialize_model_output(response, round_num: int) -> str:
    """将模型响应序列化为可读文本。"""
    lines: list[str] = []
    lines.append(f"第 {round_num} 轮模型输出  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    for item in response.output:
        item_type = getattr(item, "type", "unknown")
        if item_type == "message":
            for content in getattr(item, "content", []):
                if getattr(content, "type", "") == "output_text":
                    lines.append("[assistant]")
                    lines.append(getattr(content, "text", ""))
                    lines.append("")
        elif item_type == "function_call":
            name = getattr(item, "name", "")
            arguments = getattr(item, "arguments", "")
            call_id = getattr(item, "call_id", "")
            lines.append(f"[function_call] {name}  call_id={call_id}")
            lines.append(str(arguments))
            lines.append("")
        elif item_type == "reasoning":
            summaries = getattr(item, "summary", None)
            if summaries:
                lines.append("[reasoning]")
                for s in summaries:
                    lines.append(getattr(s, "text", ""))
                lines.append("")
        else:
            lines.append(f"[{item_type}] {str(item)[:500]}")
            lines.append("")

    # 附加 usage 信息
    usage = getattr(response, "usage", None)
    if usage:
        lines.append(f"[usage] input_tokens={getattr(usage, 'input_tokens', '?')}  "
                      f"output_tokens={getattr(usage, 'output_tokens', '?')}")
        lines.append("")

    return "\n".join(lines)


def log_model_input(input_items: list, round_num: int) -> None:
    """记录本轮模型输入。"""
    content = _serialize_input_items(input_items, round_num)
    separator = "=" * 60
    _write_model_log(f"{separator}\n[INPUT]\n{content}\n")


def log_model_output(response, round_num: int) -> None:
    """记录本轮模型输出（文本消息 + 工具调用 + reasoning + usage）。"""
    content = _serialize_model_output(response, round_num)
    _write_model_log(f"[OUTPUT]\n{content}\n")


def log_tool_result(tool_name: str, call_id: str, result: str, round_num: int) -> None:
    """记录工具执行结果。"""
    lines = [
        f"[TOOL_RESULT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  round={round_num}",
        f"工具: {tool_name}  call_id={call_id}",
        f"结果: {result[:2000]}",
        "",
    ]
    _write_model_log("\n".join(lines) + "\n")
