"""Import CodeQL JSON graph exports into Neo4j.

This script reads `build/json/nodes.json` and `build/json/edges.json`,
creates Neo4j nodes using the precomputed `id` values, and then creates
relationships between the stored source and target node ids.
"""

from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

DEFAULT_NODES_PATH = Path("build/json/nodes.json")
DEFAULT_EDGES_PATH = Path("build/json/edges.json")
DEFAULT_NODE_LABEL = "CodeQLNode"
TARGET_NODE_KINDS = {"Class", "Function", "Method"}
NODE_LABEL_OVERRIDES = {"ClassAttribute": "classAttribute"}


def extract_segment(file_path: Path, start_line: int | None, end_line: int | None) -> str | None:
    if start_line is None:
        return None

    try:
        lines = _read_source_lines(file_path)
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return None

    start_index = max(start_line - 1, 0)
    end_index = end_line if end_line is not None else start_line
    end_index = max(end_index, start_line)
    snippet = "\n".join(lines[start_index:end_index])
    return snippet or None


@lru_cache(maxsize=2048)
def _read_source_lines(file_path: Path) -> list[str]:
    return file_path.read_text(encoding="utf-8").splitlines()


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_source_path(source_root: Path, file_value: Any) -> Path | None:
    if not file_value:
        return None
    file_path = Path(str(file_value))
    if file_path.is_absolute():
        return file_path
    return source_root / file_path


def _safe_label(value: str | None) -> str:
    normalized_value = _normalize_value(value)
    if normalized_value in NODE_LABEL_OVERRIDES:
        return NODE_LABEL_OVERRIDES[normalized_value]

    label = str(normalized_value or "Entity")
    label = re.sub(r"[^A-Za-z0-9_]", "_", label)
    if label and label[0].isdigit():
        label = f"L_{label}"
    return label or "Entity"


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def _safe_rel_type(value: str | None) -> str:
    rel_type = str(_normalize_value(value) or "RELATED_TO").upper()
    rel_type = re.sub(r"[^A-Z0-9_]", "_", rel_type)
    if rel_type and rel_type[0].isdigit():
        rel_type = f"R_{rel_type}"
    return rel_type or "RELATED_TO"


def _load_json_data(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_node_rows(path: Path) -> list[dict[str, Any]]:
    data = _load_json_data(path)
    if isinstance(data, dict):
        rows = list(data.values())
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _load_edge_rows(path: Path) -> list[dict[str, Any]]:
    data = _load_json_data(path)
    if isinstance(data, dict):
        rows = list(data.values())
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _pick_id(value: Any) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("id")
        if candidate is not None:
            return str(candidate)
    if value is None:
        return None
    return str(value)


def _pick_edge_kind(row: dict[str, Any]) -> str:
    return str(
        _normalize_value(row.get("kind"))
        or _normalize_value(row.get("rel"))
        or _normalize_value(row.get("type"))
        or "RELATED_TO"
    )


def _pick_node_field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def import_nodes(tx, rows: list[dict[str, Any]], clear: bool) -> None:
    if clear:
        tx.run(f"MATCH (n:{DEFAULT_NODE_LABEL}) DETACH DELETE n")

    rows_by_kind: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_kind.setdefault(_safe_label(row.get("kind")), []).append(row)

    for kind_label, kind_rows in rows_by_kind.items():
        tx.run(
            f"""
            UNWIND $rows AS row
            MERGE (n:{DEFAULT_NODE_LABEL}:{kind_label} {{id: row.id}})
            SET n.kind = row.kind,
                n.name = row.name,
                n.file = row.file,
                n.startline = row.startline,
                n.endline = row.endline,
                n.startLine = row.startline,
                n.endLine = row.endline,
                n.definitionSnippet = row.definitionSnippet
            """,
            rows=kind_rows,
        )


def import_edges(tx, rows: list[dict[str, Any]]) -> None:
    rows_by_rel: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_rel.setdefault(_safe_rel_type(row.get("kind") or row.get("rel")), []).append(row)

    for rel_type, rel_rows in rows_by_rel.items():
        tx.run(
            f"""
            UNWIND $rows AS row
            MERGE (source:{DEFAULT_NODE_LABEL} {{id: row.source_id}})
            MERGE (target:{DEFAULT_NODE_LABEL} {{id: row.target_id}})
            MERGE (source)-[r:{rel_type}]->(target)
            SET r.edge_id = row.edge_id,
                r.rel = row.rel,
                r.source_id = row.source_id,
                r.target_id = row.target_id
            """,
            rows=rel_rows,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import CodeQL JSON graph exports into Neo4j")
    parser.add_argument("--nodes", default=str(DEFAULT_NODES_PATH), help="Path to nodes.json")
    parser.add_argument("--edges", default=str(DEFAULT_EDGES_PATH), help="Path to edges.json")
    parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j Bolt URI")
    parser.add_argument("--user", default="neo4j", help="Neo4j username")
    parser.add_argument("--password", required=True, help="Neo4j password")
    parser.add_argument("--database", default=None, help="Neo4j database name, if your instance uses multi-database")
    parser.add_argument("--source-root", default=None, help="Root directory of the source code used to extract snippets")
    parser.add_argument("--clear", action="store_true", help="Delete existing CodeQL nodes before import")
    parser.add_argument("--clear-only", action="store_true", help="Delete all Neo4j nodes and relationships, then exit")
    return parser


def _prepare_node_rows(path: Path, source_root: Path | None) -> list[dict[str, Any]]:
    rows = []
    for row in _load_node_rows(path):
        node_id = _pick_id(row.get("id"))
        if not node_id:
            continue
        startline = _to_int(_pick_node_field(row, "startline", "startLine", "lineno"))
        endline = _to_int(_pick_node_field(row, "endline", "endLine", "end_lineno"))
        definition_snippet = None
        implementation_snippet = None
        if source_root is not None:
            source_path = _resolve_source_path(source_root, _normalize_value(row.get("file")))
            if source_path is not None:
                kind = _normalize_value(_pick_node_field(row, "kind", "type")) or DEFAULT_NODE_LABEL
                if kind in TARGET_NODE_KINDS:
                    definition_snippet = extract_segment(source_path, startline, endline)
                    implementation_snippet = extract_segment(source_path, startline, endline)
        rows.append(
            {
                "id": node_id,
                "kind": _normalize_value(_pick_node_field(row, "kind", "type")) or DEFAULT_NODE_LABEL,
                "name": _normalize_value(row.get("name")),
                "file": _normalize_value(row.get("file")),
                "startline": startline,
                "endline": endline,
                "definitionSnippet": definition_snippet,
                "implementationSnippet": implementation_snippet,
            }
        )
    return rows


def _prepare_edge_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for row in _load_edge_rows(path):
        source = row.get("source")
        target = row.get("target")
        source_id = _pick_id(source) or _pick_id(row.get("source_id"))
        target_id = _pick_id(target) or _pick_id(row.get("target_id"))
        if not source_id or not target_id:
            continue
        rel_kind = _pick_edge_kind(row)
        rows.append(
            {
                "edge_id": _normalize_value(row.get("edge_id")) or f"{source_id}:{target_id}:{rel_kind}",
                "kind": rel_kind,
                "rel": _normalize_value(row.get("rel")) or rel_kind,
                "source_id": source_id,
                "target_id": target_id,
            }
        )
    return rows


def main() -> int:
    args = build_parser().parse_args()

    if args.clear_only:
        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        try:
            session_kwargs = {}
            if args.database:
                session_kwargs["database"] = args.database
            with driver.session(**session_kwargs) as session:
                session.run("MATCH (n) DETACH DELETE n")
            print("Deleted all nodes and relationships from Neo4j")
            return 0
        finally:
            driver.close()

    nodes_path = Path(args.nodes).resolve()
    edges_path = Path(args.edges).resolve()
    if not nodes_path.exists():
        raise FileNotFoundError(f"Nodes JSON not found: {nodes_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"Edges JSON not found: {edges_path}")

    source_root = Path(args.source_root).resolve() if args.source_root else None
    node_rows = _prepare_node_rows(nodes_path, source_root)
    edge_rows = _prepare_edge_rows(edges_path)

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        session_kwargs = {}
        if args.database:
            session_kwargs["database"] = args.database
        with driver.session(**session_kwargs) as session:
            session.execute_write(import_nodes, node_rows, args.clear)
            session.execute_write(import_edges, edge_rows)
    finally:
        driver.close()

    print(f"Imported {len(node_rows)} nodes and {len(edge_rows)} edges into Neo4j")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
