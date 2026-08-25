from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .alignment import align_tests
from .dataset import PairLayout
from .models import LanguagePair
from .source_function_gold import load_source_function_gold
from .source_function_mapping import (
    build_source_function_mapping_records,
    evaluate_source_function_mapping,
    load_source_function_mapping,
    query_source_function_mapping,
    write_source_function_mapping,
)
from .embedding import make_embedder
from .evaluation import evaluate, evaluate_functions
from .ground_truth import load_ground_truth
from .index import VectorIndex
from .models import LanguagePair
from .pipeline import TestLocator
from .repository import (
    load_project,
    load_source_function_corpus,
    load_source_test_corpus,
    load_target_corpus,
)
from .reverse import SourceFunctionLocator, evaluate_target_tests
from .test_to_test import SourceTestLocator, evaluate_target_test_to_source_test


def _json(value, path: Path | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _layout(args) -> PairLayout:
    return PairLayout.detect(Path(args.dataset_root), LanguagePair.parse(args.pair))


def _embedder(args):
    return make_embedder(
        args.embedder,
        model_path=args.model_path,
        device=args.device,
        batch_size=args.batch_size,
    )


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
    _json(
        {
            "layout": layout.layout,
            "pair": layout.pair.name,
            "project_count": len(rows),
            "projects": rows,
        },
        Path(args.output) if args.output else None,
    )
    return 0


def command_build_source_function_map(args) -> int:
    layout = _layout(args)
    records, report = build_source_function_mapping_records(
        layout,
        projects=[args.project] if args.project else None,
        method=args.method,
        test_scope=args.test_scope,
        project_limit=args.project_limit,
        limit_per_project=args.limit_per_project,
    )
    output = Path(args.output)
    write_source_function_mapping(records, output)
    report["output"] = str(output)
    _json(report, Path(args.report) if args.report else None)
    return 0


def command_query_source_function_map(args) -> int:
    records = load_source_function_mapping(args.mapping)
    matches = query_source_function_mapping(
        records,
        args.source_test,
        project=args.project,
    )
    if not matches:
        raise ValueError(f"Source test not found in mapping table: {args.source_test}")
    if len(matches) > 1 and not args.allow_many:
        choices = ", ".join(
            f"{record.project}:{record.source_test_nodeid}" for record in matches
        )
        raise ValueError(f"Ambiguous source test; pass --project or --allow-many: {choices}")
    payload = {
        "mapping": args.mapping,
        "source_test": args.source_test,
        "match_count": len(matches),
        "results": [record.to_dict() for record in matches],
    }
    _json(payload, Path(args.output) if args.output else None)
    return 0


def command_build_index(args) -> int:
    layout = _layout(args)
    chunks, reports = load_target_corpus(layout, [args.project] if args.project else None)
    if not chunks:
        raise RuntimeError("No target public test chunks were extracted")
    embedder = _embedder(args)
    index = VectorIndex.build(
        chunks, embedder, backend=args.index_backend, corpus_role="target_test"
    )
    output = Path(args.output_dir)
    index.save(output)
    _json({"index": str(output.resolve()), "embedder": embedder.name, "chunks": len(chunks), "projects": reports}, output / "build_report.json")
    return 0


def command_build_source_index(args) -> int:
    layout = _layout(args)
    chunks, reports = load_source_function_corpus(
        layout, [args.project] if args.project else None
    )
    if not chunks:
        raise RuntimeError("No source function chunks were extracted")
    embedder = _embedder(args)
    index = VectorIndex.build(
        chunks, embedder, backend=args.index_backend, corpus_role="source_function"
    )
    output = Path(args.output_dir)
    index.save(output)
    _json({
        "index": str(output.resolve()),
        "index_type": "source_function",
        "embedder": embedder.name,
        "chunks": len(chunks),
        "projects": reports,
    }, output / "build_report.json")
    return 0


def command_build_source_test_index(args) -> int:
    layout = _layout(args)
    chunks, reports = load_source_test_corpus(
        layout, [args.project] if args.project else None
    )
    if not chunks:
        raise RuntimeError("No Source public test chunks were extracted")
    embedder = _embedder(args)
    index = VectorIndex.build(
        chunks, embedder, backend=args.index_backend, corpus_role="source_test"
    )
    output = Path(args.output_dir)
    index.save(output)
    _json({
        "index": str(output.resolve()),
        "index_type": "source_test",
        "embedder": embedder.name,
        "chunks": len(chunks),
        "projects": reports,
    }, output / "build_report.json")
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


def command_evaluate_source_function_map(args) -> int:
    mapping_records = load_source_function_mapping(args.mapping)
    gold_records = load_source_function_gold(args.gold)
    summary, rows = evaluate_source_function_mapping(
        mapping_records=mapping_records,
        gold_records=gold_records,
        mapping_path=args.mapping,
        gold_path=args.gold,
        project=args.project,
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _json(summary.to_dict(), output / "metrics.json")
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return 0


def command_locate_source(args) -> int:
    layout = _layout(args)
    data = load_project(layout, args.project)
    matches = [
        chunk
        for chunk in data.target_tests
        if args.target_test in {chunk.chunk_id, chunk.name, chunk.qualified_name}
    ]
    if not matches:
        available = ", ".join(chunk.qualified_name for chunk in data.target_tests)
        raise ValueError(
            f"Target test not found: {args.target_test}. Available: {available}"
        )
    if len(matches) > 1:
        choices = ", ".join(chunk.chunk_id for chunk in matches)
        raise ValueError(f"Ambiguous target test; use a chunk id: {choices}")
    embedder = _embedder(args)
    locator = SourceFunctionLocator(
        VectorIndex.load(Path(args.index_dir)), embedder
    )
    payload = locator.locate(
        matches[0],
        strategy=args.strategy,
        k=args.top_k,
        mask_names=args.mask_names,
    ).to_dict()
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
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return 0


def command_evaluate_source(args) -> int:
    layout = _layout(args)
    embedder = _embedder(args)
    locator = SourceFunctionLocator(
        VectorIndex.load(Path(args.index_dir)), embedder
    )
    summary, rows = evaluate_target_tests(
        layout,
        locator,
        projects=[args.project] if args.project else None,
        strategy=args.strategy,
        mask_names=args.mask_names,
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _json(summary.to_dict(), output / "metrics.json")
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return 0


def _target_test(data, value: str):
    matches = [
        chunk
        for chunk in data.target_tests
        if value in {chunk.chunk_id, chunk.name, chunk.qualified_name}
    ]
    if not matches:
        available = ", ".join(chunk.qualified_name for chunk in data.target_tests)
        raise ValueError(f"Target test not found: {value}. Available: {available}")
    if len(matches) > 1:
        choices = ", ".join(chunk.chunk_id for chunk in matches)
        raise ValueError(f"Ambiguous target test; use a chunk id: {choices}")
    return matches[0]


def command_locate_source_test(args) -> int:
    layout = _layout(args)
    data = load_project(layout, args.project)
    target = _target_test(data, args.target_test)
    embedder = _embedder(args)
    locator = SourceTestLocator(VectorIndex.load(Path(args.index_dir)), embedder)
    result = locator.locate(
        target,
        data.source_functions,
        strategy=args.strategy,
        k=args.top_k,
        mask_names=args.mask_names,
    )
    _json(result.to_dict(), Path(args.output) if args.output else None)
    return 0


def command_evaluate_source_test(args) -> int:
    layout = _layout(args)
    records = load_ground_truth(Path(args.ground_truth))
    wrong_pairs = sorted({
        record.pair for record in records if record.pair != layout.pair.name
    })
    if wrong_pairs:
        raise ValueError(
            f"Ground truth does not match {layout.pair.name}: {wrong_pairs}"
        )
    embedder = _embedder(args)
    locator = SourceTestLocator(VectorIndex.load(Path(args.index_dir)), embedder)
    summary, rows = evaluate_target_test_to_source_test(
        layout,
        locator,
        records,
        projects=[args.project] if args.project else None,
        strategy=args.strategy,
        mask_names=args.mask_names,
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _json(summary.to_dict(), output / "metrics.json")
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return 0


def _common_dataset(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--pair", default="C++_to_Python")


def _common_embedder(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--embedder", choices=("hashing", "unixcoder"), default="hashing")
    parser.add_argument("--model-path")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--batch-size", type=int, default=16)


def _common_locator(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--strategy",
        choices=("test", "function", "function_test", "adaptive", "fusion"),
        default="adaptive",
    )
    parser.add_argument("--confidence-threshold", type=float, default=0.55)
    parser.add_argument("--margin-threshold", type=float, default=0.03)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m test_mapping",
        description="RAG and static mapping tools for translated tests",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser(
        "inspect", help="Inspect parser and alignment coverage"
    )
    _common_dataset(inspect_parser)
    inspect_parser.add_argument("--project")
    inspect_parser.add_argument("--output")
    inspect_parser.set_defaults(handler=command_inspect)

    build_sf_map = sub.add_parser(
        "build-source-function-map",
        help="Build a static Source-test to Source-function mapping table",
    )
    _common_dataset(build_sf_map)
    build_sf_map.add_argument("--project")
    build_sf_map.add_argument("--project-limit", type=int)
    build_sf_map.add_argument("--limit-per-project", type=int)
    build_sf_map.add_argument(
        "--method",
        choices=(
            "static",
            "verified_static",
            "verified_static_with_medium",
            "verified_static_with_low",
            "recall_static",
        ),
        default="verified_static",
        help=(
            "static keeps all resolved direct calls; verified_static filters/ranks "
            "directly verified business APIs; verified_static_with_medium also "
            "adds tagged medium-confidence helper-expanded candidates; "
            "verified_static_with_low additionally emits weak low-confidence candidates; "
            "recall_static builds a recall-first table with low candidates and unresolved rows"
        ),
    )
    build_sf_map.add_argument(
        "--test-scope",
        choices=("public", "all", "gtest"),
        default="all",
        help=(
            "public scans public tests only; all scans public, original, and internal "
            "test-like files; gtest scans all test-like files but keeps only TEST, "
            "TEST_F, TEST_P, TYPED_TEST, and TYPED_TEST_P"
        ),
    )
    build_sf_map.add_argument("--output", required=True)
    build_sf_map.add_argument("--report")
    build_sf_map.set_defaults(handler=command_build_source_function_map)

    query_sf_map = sub.add_parser(
        "query-source-function-map",
        help="Query a generated Source-test to Source-function mapping table",
    )
    query_sf_map.add_argument("--mapping", required=True)
    query_sf_map.add_argument("--source-test", required=True)
    query_sf_map.add_argument("--project")
    query_sf_map.add_argument("--allow-many", action="store_true")
    query_sf_map.add_argument("--output")
    query_sf_map.set_defaults(handler=command_query_source_function_map)

    eval_sf_map = sub.add_parser(
        "evaluate-source-function-map",
        help="Evaluate a generated mapping table against reviewed source-function gold",
    )
    eval_sf_map.add_argument("--mapping", required=True)
    eval_sf_map.add_argument("--gold", required=True)
    eval_sf_map.add_argument("--project")
    eval_sf_map.add_argument("--output-dir", required=True)
    eval_sf_map.set_defaults(handler=command_evaluate_source_function_map)

    build_source = sub.add_parser(
        "build-source-index",
        help="Build a persistent Source-function index for reverse RAG",
    )
    _common_dataset(build_source)
    _common_embedder(build_source)
    build_source.add_argument("--project")
    build_source.add_argument("--output-dir", required=True)
    build_source.add_argument(
        "--index-backend", choices=("numpy", "faiss", "auto"), default="numpy"
    )
    build_source.set_defaults(handler=command_build_source_index)

    build_source_test = sub.add_parser(
        "build-source-test-index",
        help="Build a persistent Source-public-test index for Target-test RAG",
    )
    _common_dataset(build_source_test)
    _common_embedder(build_source_test)
    build_source_test.add_argument("--project")
    build_source_test.add_argument("--output-dir", required=True)
    build_source_test.add_argument(
        "--index-backend", choices=("numpy", "faiss", "auto"), default="numpy"
    )
    build_source_test.set_defaults(handler=command_build_source_test_index)

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

    locate_source = sub.add_parser(
        "locate-source",
        help="Locate Source functions for one failed Target test",
    )
    _common_dataset(locate_source)
    _common_embedder(locate_source)
    locate_source.add_argument("--index-dir", required=True)
    locate_source.add_argument("--project", required=True)
    locate_source.add_argument("--target-test", required=True)
    locate_source.add_argument(
        "--strategy", choices=("dense", "call_name", "fusion"), default="dense"
    )
    locate_source.add_argument("--top-k", type=int, default=5)
    locate_source.add_argument("--mask-names", action="store_true")
    locate_source.add_argument("--output")
    locate_source.set_defaults(handler=command_locate_source)

    evaluation = sub.add_parser("evaluate", help="Evaluate strict name-aligned queries")
    _common_dataset(evaluation)
    _common_embedder(evaluation)
    _common_locator(evaluation)
    evaluation.add_argument("--index-dir", required=True)
    evaluation.add_argument("--project")
    evaluation.add_argument("--query-unit", choices=("test", "function"), default="test")
    evaluation.add_argument("--output-dir", required=True)
    evaluation.set_defaults(handler=command_evaluate)

    evaluate_source = sub.add_parser(
        "evaluate-source",
        help="Evaluate Target-test to Source-function retrieval",
    )
    _common_dataset(evaluate_source)
    _common_embedder(evaluate_source)
    evaluate_source.add_argument("--index-dir", required=True)
    evaluate_source.add_argument("--project")
    evaluate_source.add_argument(
        "--strategy", choices=("dense", "call_name", "fusion"), default="dense"
    )
    evaluate_source.add_argument("--mask-names", action="store_true")
    evaluate_source.add_argument("--output-dir", required=True)
    evaluate_source.set_defaults(handler=command_evaluate_source)

    locate_source_test = sub.add_parser(
        "locate-source-test",
        help="Locate Source tests and linked Source functions for a Target test",
    )
    _common_dataset(locate_source_test)
    _common_embedder(locate_source_test)
    locate_source_test.add_argument("--index-dir", required=True)
    locate_source_test.add_argument("--project", required=True)
    locate_source_test.add_argument("--target-test", required=True)
    locate_source_test.add_argument(
        "--strategy", choices=("dense", "structure", "fusion"), default="fusion"
    )
    locate_source_test.add_argument("--top-k", type=int, default=5)
    locate_source_test.add_argument("--mask-names", action="store_true")
    locate_source_test.add_argument("--output")
    locate_source_test.set_defaults(handler=command_locate_source_test)

    evaluate_source_test = sub.add_parser(
        "evaluate-source-test",
        help="Evaluate Target-test to Source-test RAG against reviewed ground truth",
    )
    _common_dataset(evaluate_source_test)
    _common_embedder(evaluate_source_test)
    evaluate_source_test.add_argument("--index-dir", required=True)
    evaluate_source_test.add_argument("--ground-truth", required=True)
    evaluate_source_test.add_argument("--project")
    evaluate_source_test.add_argument(
        "--strategy", choices=("dense", "structure", "fusion"), default="fusion"
    )
    evaluate_source_test.add_argument("--mask-names", action="store_true")
    evaluate_source_test.add_argument("--output-dir", required=True)
    evaluate_source_test.set_defaults(handler=command_evaluate_source_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
