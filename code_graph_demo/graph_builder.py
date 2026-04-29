"""Build a unified JSON-ready code graph from AST summaries."""

from __future__ import annotations

from typing import Any

try:
    from code_graph_demo.cfg_builder import build_cfg
    from code_graph_demo.dfg_builder import build_dfg
    from code_graph_demo.graph_schema import Edge, Node
except ImportError:
    from cfg_builder import build_cfg
    from dfg_builder import build_dfg
    from graph_schema import Edge, Node


def _add_node(nodes: dict[str, Node], node: Node) -> None:
    nodes.setdefault(node.id, node)


def _add_edge(edges: dict[tuple[str, str, str], Edge], edge: Edge) -> None:
    edges.setdefault((edge.source, edge.target, edge.type), edge)


def _callee_key(call_name: str) -> str:
    """Normalize foo(), self.foo(), module.foo() to a simple lookup key."""
    return call_name.split(".")[-1]


def build_code_graph(
    ast_infos: list[dict[str, Any]],
    source_root: str,
    features: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build a JSON-ready code graph from extracted Python AST info."""
    merged_features = {
        "ast": True,
        "call_graph": True,
        "cfg": False,
        "dfg": False,
    }
    if features:
        merged_features.update(features)

    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], Edge] = {}
    errors: list[dict[str, str]] = []
    callable_index: dict[str, list[str]] = {}
    callable_calls: list[tuple[str, list[str]]] = []

    for info in ast_infos:
        file_info = info.get("file", {})
        path = file_info.get("path", "")
        name = file_info.get("name", path)

        if info.get("error"):
            errors.append({"path": path, "error": str(info["error"])})
            continue

        file_id = f"file:{path}"
        _add_node(nodes, Node(id=file_id, type="File", name=name, path=path))

        for item in info.get("imports", []):
            import_name = item.get("name", "")
            lineno = item.get("lineno")
            import_id = f"import:{path}:{import_name}:{lineno}"
            _add_node(
                nodes,
                Node(
                    id=import_id,
                    type="Import",
                    name=import_name,
                    path=path,
                    lineno=lineno,
                    metadata={
                        "module": item.get("module"),
                        "import_type": item.get("type"),
                    },
                ),
            )
            _add_edge(edges, Edge(source=file_id, target=import_id, type="IMPORTS"))

        for function in info.get("functions", []):
            function_name = function.get("name", "")
            function_id = f"function:{path}:{function_name}"
            _add_node(
                nodes,
                Node(
                    id=function_id,
                    type="Function",
                    name=function_name,
                    path=path,
                    lineno=function.get("lineno"),
                    end_lineno=function.get("end_lineno"),
                    metadata={
                        "args": function.get("args", []),
                        "is_async": function.get("is_async", False),
                        "calls": function.get("calls", []),
                    },
                ),
            )
            _add_edge(edges, Edge(source=file_id, target=function_id, type="CONTAINS"))
            callable_index.setdefault(function_name, []).append(function_id)
            callable_calls.append((function_id, function.get("calls", [])))

        for cls in info.get("classes", []):
            class_name = cls.get("name", "")
            class_id = f"class:{path}:{class_name}"
            _add_node(
                nodes,
                Node(
                    id=class_id,
                    type="Class",
                    name=class_name,
                    path=path,
                    lineno=cls.get("lineno"),
                    end_lineno=cls.get("end_lineno"),
                ),
            )
            _add_edge(edges, Edge(source=file_id, target=class_id, type="CONTAINS"))

            for method in cls.get("methods", []):
                method_name = method.get("name", "")
                method_id = f"method:{path}:{class_name}.{method_name}"
                _add_node(
                    nodes,
                    Node(
                        id=method_id,
                        type="Method",
                        name=method_name,
                        path=path,
                        lineno=method.get("lineno"),
                        end_lineno=method.get("end_lineno"),
                        metadata={
                            "class_name": class_name,
                            "args": method.get("args", []),
                            "is_async": method.get("is_async", False),
                            "calls": method.get("calls", []),
                        },
                    ),
                )
                _add_edge(edges, Edge(source=class_id, target=method_id, type="CONTAINS"))
                callable_index.setdefault(method_name, []).append(method_id)
                callable_calls.append((method_id, method.get("calls", [])))

    if merged_features.get("cfg"):
        cfg = build_cfg(ast_infos)
        for node in cfg["nodes"]:
            _add_node(nodes, node)
        for edge in cfg["edges"]:
            _add_edge(edges, edge)

    if merged_features.get("dfg"):
        dfg = build_dfg(ast_infos)
        for node in dfg["nodes"]:
            _add_node(nodes, node)
        for edge in dfg["edges"]:
            _add_edge(edges, edge)

    unresolved_calls = 0
    for caller_id, calls in callable_calls:
        for call_name in calls:
            targets = callable_index.get(_callee_key(call_name), [])
            if not targets:
                unresolved_calls += 1
                continue
            for target_id in targets:
                _add_edge(
                    edges,
                    Edge(
                        source=caller_id,
                        target=target_id,
                        type="CALLS",
                        metadata={"callee": call_name},
                    ),
                )

    metadata: dict[str, Any] = {
        "source_root": source_root,
        "language": "python",
        "generated_by": "code_graph_demo",
        "features": merged_features,
        "unresolved_calls": unresolved_calls,
    }
    if errors:
        metadata["errors"] = errors

    return {
        "metadata": metadata,
        "nodes": [node.to_dict() for node in nodes.values()],
        "edges": [edge.to_dict() for edge in edges.values()],
    }
