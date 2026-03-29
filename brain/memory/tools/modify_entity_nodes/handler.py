from __future__ import annotations

from typing import Any

from brain.memory.tools._common import (
    build_string_update,
    format_json,
    get_connected_kg_manager,
    get_node_properties,
    get_node_type,
)

_EXPECTED_TYPE = "Entity"


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    del context

    node_id = str(args.get("node_id", "")).strip()

    if not node_id:
        return format_json({"success": False, "error": "缺少必要参数: node_id"})

    kg_manager, error = get_connected_kg_manager()
    if error:
        return format_json({"success": False, "error": error})

    actual_type = get_node_type(kg_manager, node_id)
    if actual_type != _EXPECTED_TYPE:
        return format_json({
            "success": False,
            "error": f"节点类型不匹配: 期望 {_EXPECTED_TYPE}，实际 {actual_type or '未知'}",
        })

    current_properties = get_node_properties(kg_manager, node_id)
    if current_properties is None:
        return format_json({"success": False, "error": "未找到目标节点属性"})

    updates = {
        "name": build_string_update(args, current_properties, "name"),
        "context": build_string_update(args, current_properties, "context"),
        "note": build_string_update(args, current_properties, "note"),
    }

    try:
        result = kg_manager.modify_node(node_id=node_id, updates=updates)
        if not result:
            return format_json({"success": False, "error": "修改实体节点失败"})
        if result == "InvalidModification":
            return format_json({"success": False, "error": "非法修改请求"})
        return format_json({"success": True, "node_id": result, "node_type": _EXPECTED_TYPE})
    except Exception as exc:
        return format_json({"success": False, "error": f"修改实体节点异常: {exc}"})
