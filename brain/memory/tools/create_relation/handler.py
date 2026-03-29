from __future__ import annotations

from typing import Any

from brain.memory.tools._common import format_json, get_connected_kg_manager


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    del context

    start_node_id = str(args.get("startNode_id", "")).strip()
    end_node_id = str(args.get("endNode_id", "")).strip()
    predicate = str(args.get("predicate", "")).strip()
    source = str(args.get("source", "")).strip()
    directivity = str(args.get("directivity", "single")).strip() or "single"
    evidence = str(args.get("evidence", "")).strip()

    try:
        confidence = float(args.get("confidence", 0.5))
        importance = float(args.get("importance", 0.5))
    except (TypeError, ValueError):
        return format_json({"success": False, "error": "confidence/importance 必须是数�?})

    confidence = max(0.0, min(1.0, confidence))
    importance = max(0.0, min(1.0, importance))

    if not all([start_node_id, end_node_id, predicate, source]):
        return format_json({"success": False, "error": "缺少必要参数"})

    kg_manager, error = get_connected_kg_manager()
    if error:
        return format_json({"success": False, "error": error})

    try:
        relation_id = kg_manager.create_relation(
            startNode_id=start_node_id,
            endNode_id=end_node_id,
            predicate=predicate,
            source=source,
            confidence=confidence,
            importance=importance,
            directivity=directivity,
            evidence=evidence,
        )

        if not relation_id:
            return format_json({"success": False, "error": "创建关系失败"})

        return format_json({"success": True, "relation_id": relation_id})
    except Exception as exc:
        return format_json({"success": False, "error": f"创建关系异常: {exc}"})
