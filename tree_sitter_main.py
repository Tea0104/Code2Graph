from __future__ import annotations

import argparse
import json
from pathlib import Path

from tree_sitter_graph.extractor import extract_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a language-agnostic graph using tree-sitter")
    parser.add_argument("--source-root", required=True, help="Path to the repository to analyze")
    parser.add_argument(
        "--languages",
        default="python,cpp",
        help="Comma-separated language list. Supported values: python, cpp",
    )
    parser.add_argument("--output-dir", default="build/json", help="Directory for nodes.json and edges.json")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    languages = [language.strip() for language in args.languages.split(",") if language.strip()]

    graph = extract_repository(source_root, languages)
    output_dir.mkdir(parents=True, exist_ok=True)

    with (output_dir / "nodes.json").open("w", encoding="utf-8") as nodes_file:
        json.dump(graph.nodes_json(), nodes_file, ensure_ascii=False, indent=2)
    with (output_dir / "edges.json").open("w", encoding="utf-8") as edges_file:
        json.dump(graph.edges_json(), edges_file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
