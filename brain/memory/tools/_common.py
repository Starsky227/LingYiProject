from __future__ import annotations

import json
from typing import Any, Dict, Optional

from brain.memory.knowledge_graph_manager import get_knowledge_graph_manager
from system.system_checker import is_neo4j_available


def format_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def get_connected_kg_manager() -> tuple[Optional[Any], Optional[str]]:
    if not is_neo4j_available():
        return None, "Neo4j 未连接，无法执行记忆图谱操作"

    kg_manager = get_knowledge_graph_manager()
    if not kg_manager.connected:
        return None, "Neo4j 连接不可用，无法执行记忆图谱操作"

    return kg_manager, None


def get_node_type(kg_manager: Any, node_id: str) -> Optional[str]:
    if not node_id:
        return None

    try:
        with kg_manager.driver.session() as session:
            record = session.run(
                """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN n.node_type as node_type
                """,
                node_id=node_id,
            ).single()
            if not record:
                return None
            return record.get("node_type")
    except Exception:
        return None


def get_node_properties(kg_manager: Any, node_id: str) -> Optional[Dict[str, Any]]:
    if not node_id:
        return None

    try:
        with kg_manager.driver.session() as session:
            record = session.run(
                """
                MATCH (n) WHERE elementId(n) = $node_id
                RETURN properties(n) as properties
                """,
                node_id=node_id,
            ).single()
            if not record:
                return None
            return record.get("properties") or {}
    except Exception:
        return None


def build_string_update(
    args: Dict[str, Any],
    current_properties: Dict[str, Any],
    field_name: str,
) -> str:
    raw_value = args.get(field_name, None)
    if raw_value is None:
        return str(current_properties.get(field_name, ""))

    value = str(raw_value).strip()
    if value == "":
        return str(current_properties.get(field_name, ""))
    return value


def build_numeric_update(
    args: Dict[str, Any],
    current_properties: Dict[str, Any],
    field_name: str,
) -> float:
    raw_value = args.get(field_name, None)
    if raw_value in (None, ""):
        current_value = current_properties.get(field_name, 0.5)
        return float(current_value)
    return float(raw_value)
