from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from repository_analysis.languages import normalize_language
from file_topo_sort import analyze_project
from test_mapping.embedding import make_embedder
from test_mapping.index import VectorIndex
from test_mapping.models import FunctionChunk, TestChunk
from test_mapping.repository import load_source_repository
from test_mapping.source_function_mapping import (
    SourceFunctionMappingMethod,
    resolve_source_function_mapping,
    summarize_source_function_mapping,
    write_source_function_mapping,
)
from tree_sitter_graph import extract_repository


@dataclass(frozen=True)
class InitializationResult:
    schema_version: int
    repository_id: str
    source_root: str
    source_language: str
    artifact_dir: str
    source_file_count: int
    source_test_file_count: int
    source_function_count: int
    source_test_count: int
    artifacts: dict[str, str | None]
    reports: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, chunks: list[FunctionChunk] | list[TestChunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n"
            for chunk in chunks
        ),
        encoding="utf-8",
    )


def initialize_repository(
    source_root: str | Path,
    *,
    source_language: str | None = None,
    repository_id: str | None = None,
    artifact_dir: str | Path | None = None,
    embedder_kind: str = "unixcoder",
    model_path: str | Path | None = None,
    device: str = "auto",
    batch_size: int = 16,
    index_backend: str = "numpy",
    mapping_method: SourceFunctionMappingMethod = "verified_static_with_medium",
) -> InitializationResult:
    """Initialize all reusable artifacts from a Source repository only."""
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source repository does not exist: {root}")
    output = (
        root / ".code2graph"
        if artifact_dir is None
        else Path(artifact_dir).expanduser()
    )
    if not output.is_absolute():
        output = root / output
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    data = load_source_repository(
        root, source_language=source_language, project=repository_id
    )
    canonical_language = normalize_language(data.language)
    warnings = list(data.errors)
    detected_supported = {
        language: count
        for language, count in data.language_counts.items()
        if language in {"python", "cpp"}
    }
    if source_language is None and len(detected_supported) > 1:
        warnings.append(
            "multiple_supported_languages_detected:"
            + ",".join(
                f"{language}={count}"
                for language, count in sorted(detected_supported.items())
            )
            + f";selected={canonical_language}"
        )

    graph = extract_repository(root, [canonical_language])
    graph_dir = output / "graph"
    nodes_path = graph_dir / "nodes.json"
    edges_path = graph_dir / "edges.json"
    _write_json(nodes_path, graph.nodes_json())
    _write_json(edges_path, graph.edges_json())

    translation = analyze_project(root, canonical_language, include_tests=False)
    translation_path = output / "translation" / "translation_order.json"
    _write_json(translation_path, translation)

    functions_path = output / "chunks" / "source_functions.jsonl"
    tests_path = output / "chunks" / "source_tests.jsonl"
    _write_jsonl(functions_path, data.source_functions)
    _write_jsonl(tests_path, data.source_tests)

    mapping_records = [
        resolve_source_function_mapping(
            pair=f"{data.language}_source_only",
            test=test,
            functions=data.source_functions,
            method=mapping_method,
        )
        for test in data.source_tests
    ]
    mapping_path = output / "mappings" / "source_test_to_source_function.jsonl"
    write_source_function_mapping(mapping_records, mapping_path)
    mapping_report = summarize_source_function_mapping(mapping_records)
    mapping_report.update(
        {
            "resolver_method": mapping_method,
            "source_repository": str(root),
        }
    )
    mapping_report_path = output / "reports" / "source_function_mapping.json"
    _write_json(mapping_report_path, mapping_report)

    index_path: Path | None = None
    index_report: dict[str, Any]
    if data.source_tests:
        embedder = make_embedder(
            embedder_kind,
            model_path=str(model_path) if model_path is not None else None,
            device=device,
            batch_size=batch_size,
        )
        index = VectorIndex.build(
            data.source_tests,
            embedder,
            backend=index_backend,
            corpus_role="source_test",
        )
        index_path = output / "indexes" / "source_tests"
        index.save(index_path)
        index_report = {
            "status": "built",
            "chunk_count": len(data.source_tests),
            "embedder": embedder.name,
            "backend": index.backend,
        }
    else:
        warnings.append("no_source_tests_detected;source_test_index_not_built")
        index_report = {
            "status": "skipped",
            "reason": "no_source_tests_detected",
            "chunk_count": 0,
        }

    result = InitializationResult(
        schema_version=1,
        repository_id=data.project,
        source_root=str(root),
        source_language=data.language,
        artifact_dir=str(output),
        source_file_count=len(data.source_files),
        source_test_file_count=len(data.test_files),
        source_function_count=len(data.source_functions),
        source_test_count=len(data.source_tests),
        artifacts={
            "graph_nodes": str(nodes_path),
            "graph_edges": str(edges_path),
            "translation_order": str(translation_path),
            "source_functions": str(functions_path),
            "source_tests": str(tests_path),
            "source_test_index": str(index_path) if index_path else None,
            "source_test_to_source_function": str(mapping_path),
            "source_function_mapping_report": str(mapping_report_path),
        },
        reports={
            "detected_language_file_counts": data.language_counts,
            "code_graph_node_count": len(graph.nodes),
            "code_graph_edge_count": len(graph.edges),
            "translation_file_count": len(translation["translation_order"]),
            "source_test_index": index_report,
            "source_function_mapping": mapping_report,
        },
        warnings=warnings,
    )
    _write_json(output / "manifest.json", result.to_dict())
    return result
