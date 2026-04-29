"""Export code graph data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def export_graph_to_json(graph: dict[str, Any], out_path: str | Path) -> None:
    """Export graph data to a formatted JSON file."""
    output_path = Path(out_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def export_json(graph: dict[str, Any], output_path: str | Path) -> None:
    """Backward-compatible alias for JSON export."""
    export_graph_to_json(graph, output_path)
