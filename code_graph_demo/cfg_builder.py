"""Lightweight CFG builder for function and method top-level statements."""

from __future__ import annotations

from typing import Any

try:
    from code_graph_demo.graph_schema import Edge, Node
except ImportError:
    from graph_schema import Edge, Node


def _statement_id(owner_id: str, statement: dict[str, Any]) -> str:
    return f"stmt:{owner_id}:{statement.get('index')}"


def _branch_statement_id(owner_id: str, parent: dict[str, Any], branch: str) -> str:
    return f"stmt:{owner_id}:{parent.get('index')}:{branch}"


def _add_cfg_for_callable(
    owner_id: str,
    owner_path: str,
    statements: list[dict[str, Any]],
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    if not statements:
        return

    line_to_id: dict[int, str] = {}
    for statement in statements:
        node_id = _statement_id(owner_id, statement)
        lineno = statement.get("lineno")
        if lineno is not None:
            line_to_id[lineno] = node_id
        nodes.append(
            Node(
                id=node_id,
                type=statement.get("type", "Statement"),
                name=statement.get("ast_type", "Statement"),
                path=owner_path,
                lineno=lineno,
                end_lineno=statement.get("end_lineno"),
                metadata={"owner": owner_id, "index": statement.get("index")},
            )
        )
        for branch_name in ("body_first", "orelse_first"):
            branch = statement.get(branch_name)
            if not branch:
                continue
            branch_id = _branch_statement_id(owner_id, statement, branch_name)
            branch_lineno = branch.get("lineno")
            if branch_lineno is not None:
                line_to_id[branch_lineno] = branch_id
            nodes.append(
                Node(
                    id=branch_id,
                    type=branch.get("type", "Statement"),
                    name=branch.get("ast_type", "Statement"),
                    path=owner_path,
                    lineno=branch_lineno,
                    end_lineno=branch.get("end_lineno"),
                    metadata={
                        "owner": owner_id,
                        "parent_index": statement.get("index"),
                        "branch": branch_name,
                    },
                )
            )

    first_id = _statement_id(owner_id, statements[0])
    edges.append(Edge(source=owner_id, target=first_id, type="CFG_ENTRY"))

    for current, nxt in zip(statements, statements[1:]):
        current_id = _statement_id(owner_id, current)
        next_id = _statement_id(owner_id, nxt)
        edges.append(Edge(source=current_id, target=next_id, type="NEXT"))

        ast_type = current.get("ast_type")
        body_target = line_to_id.get(current.get("body_first_lineno"))
        if ast_type == "If" and body_target:
            edges.append(Edge(source=current_id, target=body_target, type="TRUE_BRANCH"))
        if ast_type == "If":
            false_target = line_to_id.get(current.get("orelse_first_lineno")) or next_id
            edges.append(Edge(source=current_id, target=false_target, type="FALSE_BRANCH"))
        if ast_type in {"For", "While"} and body_target:
            edges.append(Edge(source=current_id, target=body_target, type="LOOP_BODY"))


def build_cfg(ast_infos: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Build CFG nodes and edges from AST summaries."""
    nodes: list[Node] = []
    edges: list[Edge] = []

    for info in ast_infos:
        if info.get("error"):
            continue
        path = info.get("file", {}).get("path", "")
        for function in info.get("functions", []):
            owner_id = f"function:{path}:{function.get('name', '')}"
            _add_cfg_for_callable(owner_id, path, function.get("statements", []), nodes, edges)
        for cls in info.get("classes", []):
            class_name = cls.get("name", "")
            for method in cls.get("methods", []):
                owner_id = f"method:{path}:{class_name}.{method.get('name', '')}"
                _add_cfg_for_callable(owner_id, path, method.get("statements", []), nodes, edges)

    return {"nodes": nodes, "edges": edges}
