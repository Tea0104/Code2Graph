from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

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


def _json(value, path: Path | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _layout(args) -> PairLayout:
    return PairLayout.detect(Path(args.dataset_root), LanguagePair.parse(args.pair))


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


def _common_dataset(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--pair", default="C++_to_Python")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m test_mapping",
        description="Build and query Source-test -> Source-function mapping tables",
    )
    sub = parser.add_subparsers(dest="command", required=True)

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
        choices=("public", "all"),
        default="all",
        help="public scans public tests only; all scans public, original, and internal test-like files",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
