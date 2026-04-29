"""Lightweight DFG builder for simple same-function define-use matching."""

from __future__ import annotations

from typing import Any

try:
    from code_graph_demo.graph_schema import Edge, Node
except ImportError:
    from graph_schema import Edge, Node


def _event_id(owner_id: str, event: dict[str, Any], index: int) -> str:
    return f"var:{owner_id}:{event.get('kind')}:{event.get('name')}:{event.get('lineno')}:{index}"


def _add_dfg_for_callable(
    owner_id: str,
    owner_path: str,
    events: list[dict[str, Any]],
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    latest_defs: dict[str, str] = {}
    for index, event in enumerate(events):
        name = event.get("name", "")
        kind = event.get("kind")
        node_id = _event_id(owner_id, event, index)
        node_type = "VariableDef" if kind == "def" else "VariableUse"
        nodes.append(
            Node(
                id=node_id,
                type=node_type,
                name=name,
                path=owner_path,
                lineno=event.get("lineno"),
                metadata={"owner": owner_id, "statement_index": event.get("statement_index")},
            )
        )
        if kind == "def":
            edges.append(Edge(source=owner_id, target=node_id, type="DEFINES"))
            latest_defs[name] = node_id
        elif kind == "use":
            edges.append(Edge(source=owner_id, target=node_id, type="USES"))
            if name in latest_defs:
                edges.append(Edge(source=latest_defs[name], target=node_id, type="DATA_FLOW"))


def build_dfg(ast_infos: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Build DFG nodes and edges from AST summaries."""
    nodes: list[Node] = []
    edges: list[Edge] = []

    for info in ast_infos:
        if info.get("error"):
            continue
        path = info.get("file", {}).get("path", "")
        for function in info.get("functions", []):
            owner_id = f"function:{path}:{function.get('name', '')}"
            _add_dfg_for_callable(owner_id, path, function.get("data_flow", []), nodes, edges)
        for cls in info.get("classes", []):
            class_name = cls.get("name", "")
            for method in cls.get("methods", []):
                owner_id = f"method:{path}:{class_name}.{method.get('name', '')}"
                _add_dfg_for_callable(owner_id, path, method.get("data_flow", []), nodes, edges)

    return {"nodes": nodes, "edges": edges}
