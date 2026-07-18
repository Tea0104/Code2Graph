from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .alignment import align_tests
from .dataset import PairLayout
from .embedding import make_embedder
from .evaluation import evaluate, evaluate_functions
from .index import VectorIndex
from .models import LanguagePair
from .pipeline import TestLocator
from .repository import load_project, load_target_corpus


def _json(value, path: Path | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _layout(args) -> PairLayout:
    return PairLayout.detect(Path(args.dataset_root), LanguagePair.parse(args.pair))


def _embedder(args):
    return make_embedder(args.embedder, model_path=args.model_path, device=args.device, batch_size=args.batch_size)


def command_inspect(args) -> int:
    layout = _layout(args)
    selected = [args.project] if args.project else [item.project for item in layout.projects()]
    rows = []
    for project in selected:
        data = load_project(layout, project)
        alignments = align_tests(data.source_tests, data.target_tests, expanded=True)
        confidence_counts = {
            confidence: sum(item.confidence == confidence for item in alignments)
            for confidence in ("high", "medium", "low", "ambiguous")
        }
        rows.append({
            "project": project,
            "source_functions": len(data.source_functions),
            "source_test_chunks": len(data.source_tests),
            "target_test_chunks": len(data.target_tests),
            "alignment_counts": confidence_counts,
            "errors": data.errors,
        })
    _json({"layout": layout.layout, "pair": layout.pair.name, "project_count": len(rows), "projects": rows}, Path(args.output) if args.output else None)
    return 0


def command_build_index(args) -> int:
    layout = _layout(args)
    chunks, reports = load_target_corpus(layout, [args.project] if args.project else None)
    if not chunks:
        raise RuntimeError("No target public test chunks were extracted")
    embedder = _embedder(args)
    index = VectorIndex.build(chunks, embedder, backend=args.index_backend)
    output = Path(args.output_dir)
    index.save(output)
    _json({"index": str(output.resolve()), "embedder": embedder.name, "chunks": len(chunks), "projects": reports}, output / "build_report.json")
    return 0


def command_locate(args) -> int:
    layout = _layout(args)
    data = load_project(layout, args.project)
    embedder = _embedder(args)
    locator = TestLocator(VectorIndex.load(Path(args.index_dir)), embedder, confidence_threshold=args.confidence_threshold, margin_threshold=args.margin_threshold)
    if args.source_test:
        matches = [chunk for chunk in data.source_tests if args.source_test in {chunk.chunk_id, chunk.name, chunk.qualified_name}]
        if not matches:
            available = ", ".join(chunk.qualified_name for chunk in data.source_tests)
            raise ValueError(f"Source test not found: {args.source_test}. Available: {available}")
        if len(matches) > 1:
            choices = ", ".join(chunk.chunk_id for chunk in matches)
            raise ValueError(f"Ambiguous source test; use a chunk id: {choices}")
        payload = locator.locate(matches[0], data.source_functions, strategy=args.strategy, k=args.top_k).to_dict()
    else:
        matches = [chunk for chunk in data.source_functions if args.source_function in {chunk.chunk_id, chunk.name, chunk.qualified_name}]
        if not matches:
            available = ", ".join(chunk.qualified_name for chunk in data.source_functions[:50])
            raise ValueError(f"Source function not found: {args.source_function}. First available: {available}")
        if len(matches) > 1:
            choices = ", ".join(chunk.chunk_id for chunk in matches)
            raise ValueError(f"Ambiguous source function; use a qualified name or chunk id: {choices}")
        payload = locator.locate_function_with_tests(matches[0], data.source_tests, strategy=args.strategy, k=args.top_k).to_dict()
    _json(payload, Path(args.output) if args.output else None)
    return 0


def command_evaluate(args) -> int:
    layout = _layout(args)
    embedder = _embedder(args)
    locator = TestLocator(VectorIndex.load(Path(args.index_dir)), embedder, confidence_threshold=args.confidence_threshold, margin_threshold=args.margin_threshold)
    projects = [args.project] if args.project else None
    if args.query_unit == "function":
        summary, rows = evaluate_functions(layout, locator, projects=projects, strategy=args.strategy)
    else:
        summary, rows = evaluate(layout, locator, strategy=args.strategy, projects=projects)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _json(summary.to_dict(), output / "metrics.json")
    (output / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return 0


def _common_dataset(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--pair", required=True, help="For example: Python_to_C++")


def _common_embedder(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--embedder", choices=("hashing", "unixcoder"), default="hashing")
    parser.add_argument("--model-path")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--batch-size", type=int, default=16)


def _common_locator(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", choices=("test", "function", "function_test", "adaptive", "fusion"), default="adaptive")
    parser.add_argument("--confidence-threshold", type=float, default=0.55)
    parser.add_argument("--margin-threshold", type=float, default=0.03)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m test_mapping", description="Locate translated public tests with structure-aware RAG")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect", help="Inspect parser and alignment coverage")
    _common_dataset(inspect_parser)
    inspect_parser.add_argument("--project")
    inspect_parser.add_argument("--output")
    inspect_parser.set_defaults(handler=command_inspect)

    build = sub.add_parser("build-index", help="Build a persistent target-test vector index")
    _common_dataset(build)
    _common_embedder(build)
    build.add_argument("--project")
    build.add_argument("--output-dir", required=True)
    build.add_argument("--index-backend", choices=("numpy", "faiss", "auto"), default="numpy")
    build.set_defaults(handler=command_build_index)

    locate = sub.add_parser("locate", help="Locate target tests for one source test")
    _common_dataset(locate)
    _common_embedder(locate)
    _common_locator(locate)
    locate.add_argument("--index-dir", required=True)
    locate.add_argument("--project", required=True)
    source_query = locate.add_mutually_exclusive_group(required=True)
    source_query.add_argument("--source-test")
    source_query.add_argument("--source-function")
    locate.add_argument("--top-k", type=int, default=5)
    locate.add_argument("--output")
    locate.set_defaults(handler=command_locate)

    evaluation = sub.add_parser("evaluate", help="Evaluate strict name-aligned queries")
    _common_dataset(evaluation)
    _common_embedder(evaluation)
    _common_locator(evaluation)
    evaluation.add_argument("--index-dir", required=True)
    evaluation.add_argument("--project")
    evaluation.add_argument("--query-unit", choices=("test", "function"), default="test")
    evaluation.add_argument("--output-dir", required=True)
    evaluation.set_defaults(handler=command_evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
