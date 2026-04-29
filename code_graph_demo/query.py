"""Minimal keyword query demo for generated code graph JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _matches(node: dict[str, Any], query: str) -> bool:
    """Return whether a node matches the keyword query."""
    needle = query.lower()
    fields = [
        node.get("id", ""),
        node.get("name", ""),
        node.get("path", ""),
        node.get("type", ""),
        json.dumps(node.get("metadata", {}), ensure_ascii=False),
    ]
    return any(needle in str(value).lower() for value in fields)


def _format_node(node: dict[str, Any]) -> str:
    return (
        f"id={node.get('id')} | type={node.get('type')} | "
        f"name={node.get('name')} | path={node.get('path')} | "
        f"lineno={node.get('lineno')}"
    )


def query_graph(graph_path: str | Path, query: str) -> tuple[list[dict[str, Any]], int]:
    """Print matching nodes and their one-hop incoming/outgoing edges."""
    graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    matches = [node for node in nodes if _matches(node, query)]
    neighbor_edges = 0

    for node in matches:
        node_id = node.get("id")
        incoming = [edge for edge in edges if edge.get("target") == node_id]
        outgoing = [edge for edge in edges if edge.get("source") == node_id]
        neighbor_edges += len(incoming) + len(outgoing)

        print("MATCH")
        print(_format_node(node))
        print("incoming edges:")
        for edge in incoming:
            print(f"  {edge.get('source')} -[{edge.get('type')}]-> {edge.get('target')}")
        print("outgoing edges:")
        for edge in outgoing:
            print(f"  {edge.get('source')} -[{edge.get('type')}]-> {edge.get('target')}")

    print(f"summary: matches={len(matches)}, neighbor_edges={neighbor_edges}")
    return matches, neighbor_edges


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query a generated code graph JSON file")
    parser.add_argument("--graph", required=True, help="Path to code graph JSON")
    parser.add_argument("--q", required=True, help="Keyword query")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    query_graph(args.graph, args.q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
