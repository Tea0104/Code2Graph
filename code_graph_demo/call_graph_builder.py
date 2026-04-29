"""Call graph construction skeleton."""

from __future__ import annotations

from typing import Any


def build_call_graph(ast_items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build a placeholder call graph from AST summaries."""
    # TODO: resolve simple repo-local and external calls.
    _ = ast_items
    return {"nodes": [], "edges": []}
