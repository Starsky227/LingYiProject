from __future__ import annotations

from typing import Any

from brain.memory.tools._common import format_json, get_connected_kg_manager


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    del context

    relation_id = str(args.get("relation_id", "")).strip()
    predicate = str(args.get("predicate", "")).strip()
    source = str(args.get("source", "")).strip()
    directivity = str(args.get("directivity", "to_endNode")).strip() or "to_endNode"
    evidence = str(args.get("evidence", "")).strip()

    try:
        confidence = float(args.get("confidence", 0.5))
    except (TypeError, ValueError):
        return format_json({"success": False, "error": "参数 confidence 必须是数�?})

    confidence = max(0.0, min(1.0, confidence))

    importance = args.get("importance", None)
    if importance is not None:
        try:
            importance = float(importance)
            importance = max(0.0, min(1.0, importance))
        except (TypeError, ValueError):
            return format_json({"success": False, "error": "参数 importance 必须是数�?})

    if not all([relation_id, predicate, source]):
        return format_json({"success": False, "error": "缺少必要参数"})

    kg_manager, error = get_connected_kg_manager()
    if error:
        return format_json({"success": False, "error": error})

    try:
        result = kg_manager.modify_relation(
            relation_id=relation_id,
            predicate=predicate,
            source=source,
            confidence=confidence,
            directivity=directivity,
            evidence=evidence,
            importance=importance,
        )

        if not result:
            return format_json({"success": False, "error": "修改关系失败"})

        return format_json({"success": True, "relation_id": result})
    except Exception as exc:
        return format_json({"success": False, "error": f"修改关系异常: {exc}"})
