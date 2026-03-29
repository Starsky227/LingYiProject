from __future__ import annotations

from typing import Any

from brain.memory.tools._common import format_json, get_connected_kg_manager


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    del context

    name = str(args.get("name", "")).strip()
    context_name = str(args.get("context", "reality现实")).strip() or "reality现实"
    note = str(args.get("note", "无")).strip() or "无"

    if not name:
        return format_json({"success": False, "error": "缺少必要参数: name"})

    kg_manager, error = get_connected_kg_manager()
    if error:
        return format_json({"success": False, "error": error})

    try:
        with kg_manager.driver.session() as session:
            node_id = kg_manager.create_entity_node(
                session=session,
                name=name,
                context=context_name,
                note=note,
            )

        if not node_id:
            return format_json({"success": False, "error": "创建实体节点失败"})

        return format_json({"success": True, "node_id": node_id, "node_type": "Entity"})
    except Exception as exc:
        return format_json({"success": False, "error": f"创建实体节点异常: {exc}"})
