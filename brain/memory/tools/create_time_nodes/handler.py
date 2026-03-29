from __future__ import annotations

from typing import Any

from brain.memory.tools._common import format_json, get_connected_kg_manager


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    del context

    time_str = args.get("time_str", [])
    time_type = str(args.get("time_type", "static")).strip() or "static"
    context_name = str(args.get("context", "reality现实")).strip() or "reality现实"

    if not isinstance(time_str, list) or not time_str:
        return format_json({"success": False, "error": "缺少必要参数: time_str(非空数组)"})

    kg_manager, error = get_connected_kg_manager()
    if error:
        return format_json({"success": False, "error": error})

    try:
        with kg_manager.driver.session() as session:
            node_id = kg_manager.create_time_node(
                session=session,
                time_str=[str(item).strip() for item in time_str],
                time_type=time_type,
                context=context_name,
            )

        if not node_id:
            return format_json({"success": False, "error": "创建时间节点失败"})

        return format_json({"success": True, "node_id": node_id, "node_type": "Time"})
    except Exception as exc:
        return format_json({"success": False, "error": f"创建时间节点异常: {exc}"})
