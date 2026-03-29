from __future__ import annotations

from typing import Any

from brain.memory.tools._common import format_json, get_connected_kg_manager


async def execute(args: dict[str, Any], context: dict[str, Any]) -> str:
    del context

    name = str(args.get("name", "")).strip()
    context_name = str(args.get("context", "reality现实")).strip() or "reality现实"
    note = str(args.get("note", "")).strip()

    trust_raw = args.get("trust", 0.5)
    try:
        trust = float(trust_raw)
    except (TypeError, ValueError):
        return format_json({"success": False, "error": "参数 trust 必须是数字"})

    trust = max(0.0, min(1.0, trust))

    if not name:
        return format_json({"success": False, "error": "缺少必要参数: name"})

    kg_manager, error = get_connected_kg_manager()
    if error:
        return format_json({"success": False, "error": error})

    try:
        with kg_manager.driver.session() as session:
            node_id = kg_manager.create_character_node(
                session=session,
                name=name,
                trust=trust,
                context=context_name,
                note=note,
            )

        if not node_id:
            return format_json({"success": False, "error": "创建角色节点失败"})

        return format_json({"success": True, "node_id": node_id, "node_type": "Character"})
    except Exception as exc:
        return format_json({"success": False, "error": f"创建角色节点异常: {exc}"})
