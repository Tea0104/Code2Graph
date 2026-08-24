from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import glob
import json
from pathlib import Path
import os
import re
import shutil
import sys
import time

from test_mapping.models import TestChunk

from .build import configure_and_build, discover_executables, ensure_googletest_install
from .coverage import collect_coverage
from .models import DynamicProbeRecord, DynamicProjectReport
from .recipes import load_recipe
from .test_discovery import RunnableTest, list_runnable_tests, match_source_test_to_runnable
from .runner import run_command


def _json_dump(value, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(records: list[DynamicProbeRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _read_dynamic_probe(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _dynamic_probe_key(record: dict) -> tuple[str, str]:
    return (record.get("project", ""), record.get("source_test_id") or record.get("source_test_nodeid") or "")


def _base_record_key(record) -> tuple[str, str]:
    return (record.project, record.source_test_id or record.source_test_nodeid)


def _top_static_confidence(record) -> str | None:
    if not record.source_functions:
        return None
    return record.source_functions[0].confidence


def _dynamic_hit_to_mapping_hit(hit: dict, *, rank: int):
    from test_mapping.source_function_mapping import SourceFunctionMappingHit

    return SourceFunctionMappingHit(
        rank=rank,
        chunk_id=hit["chunk_id"],
        file=hit["file"],
        name=hit["name"],
        qualified_name=hit["qualified_name"],
        start_line=hit["start_line"],
        end_line=hit["end_line"],
        parent=None,
        matched_call=hit.get("test_filter") or "dynamic_coverage",
        resolution_reason="dynamic_coverage_line_intersection",
        verification_reason=f"covered_lines={hit.get('covered_lines', 0)}; executable={hit.get('executable', '')}",
        confidence="dynamic",
    )


def _merge_dynamic_records(base_records: list, dynamic_records: list[dict]) -> tuple[list, dict]:
    by_key = {
        _dynamic_probe_key(record): record
        for record in dynamic_records
        if record.get("status") == "candidate" and record.get("source_functions")
    }
    merged = []
    applied = 0
    replaced_low = 0
    filled_empty = 0
    kept_static = 0
    for record in base_records:
        dynamic = by_key.get(_base_record_key(record))
        top_confidence = _top_static_confidence(record)
        keep_static = top_confidence in {"high", "medium"}
        if dynamic is None or keep_static:
            if keep_static:
                kept_static += 1
            merged.append(record)
            continue
        dynamic_hits = [
            _dynamic_hit_to_mapping_hit(hit, rank=index)
            for index, hit in enumerate(dynamic.get("source_functions", [])[:10], start=1)
        ]
        if not dynamic_hits:
            merged.append(record)
            continue
        if top_confidence == "low":
            replaced_low += 1
        if top_confidence is None:
            filled_empty += 1
        record.source_functions = dynamic_hits
        record.status = "candidate"
        record.resolver_method = f"{record.resolver_method}+dynamic_coverage"
        record.diagnostics = {
            **record.diagnostics,
            "dynamic_applied": True,
            "dynamic_stage": dynamic.get("diagnostics", {}).get("dynamic_stage"),
            "dynamic_framework": dynamic.get("diagnostics", {}).get("framework"),
            "dynamic_test_filter": dynamic.get("diagnostics", {}).get("test_filter"),
            "dynamic_coverage_tool": dynamic.get("diagnostics", {}).get("coverage_tool"),
            "dynamic_hit_count": len(dynamic_hits),
        }
        applied += 1
        merged.append(record)
    report = {
        "schema_version": 1,
        "base_record_count": len(base_records),
        "dynamic_record_count": len(dynamic_records),
        "dynamic_candidate_count": len(by_key),
        "dynamic_applied_count": applied,
        "dynamic_replaced_low_count": replaced_low,
        "dynamic_filled_empty_count": filled_empty,
        "static_high_medium_kept_count": kept_static,
        "status_counts": dict(sorted(Counter(record.status for record in merged).items())),
        "top_confidence_counts": dict(sorted(Counter(_top_static_confidence(record) or "none" for record in merged).items())),
    }
    return merged, report


def _selected_mapping_rows(args) -> dict[str, list]:
    from test_mapping.source_function_mapping import load_source_function_mapping

    records = load_source_function_mapping(args.mapping)
    selected = []
    for record in records:
        if args.project and record.project != args.project:
            continue
        has_functions = bool(record.source_functions)
        top_confidence = record.source_functions[0].confidence if has_functions else None
        if args.selection == "unresolved" and has_functions:
            continue
        if args.selection == "low" and top_confidence != "low":
            continue
        if args.selection == "unresolved_or_low" and has_functions and top_confidence != "low":
            continue
        selected.append(record)
    by_project: dict[str, list] = defaultdict(list)
    for record in selected:
        by_project[record.project].append(record)
    ordered = dict(sorted(
        by_project.items(),
        key=lambda item: (-len(item[1]), item[0]),
    ))
    if args.max_projects is not None:
        ordered = dict(list(ordered.items())[: args.max_projects])
    if args.max_tests_per_project is not None:
        ordered = {
            project: rows[: args.max_tests_per_project]
            for project, rows in ordered.items()
        }
    return ordered


def _index_tests(tests: list[TestChunk]) -> dict[str, TestChunk]:
    from test_mapping.source_function_mapping import source_test_nodeid

    result: dict[str, TestChunk] = {}
    for test in tests:
        selectors = {
            test.chunk_id,
            source_test_nodeid(test),
            test.qualified_name,
            f"{test.file}::{test.qualified_name}",
        }
        for selector in selectors:
            result[selector] = test
    return result


def _runnable_index(executables: list[Path], *, list_timeout: int) -> list[RunnableTest]:
    result: list[RunnableTest] = []
    for executable in executables:
        try:
            result.extend(list_runnable_tests(executable, timeout=list_timeout))
        except Exception:
            result.append(RunnableTest(executable, "binary", None, executable.name))
    return result


def _materialize_existing_executables(
    executables: list[Path],
    *,
    source_dir: Path,
    sandbox_dir: Path,
) -> list[Path]:
    """Copy dataset-provided executables to the sandbox and make them runnable."""

    materialized: list[Path] = []
    output_root = sandbox_dir / "existing_executables"
    for executable in executables:
        try:
            rel = executable.relative_to(source_dir)
        except ValueError:
            rel = Path(executable.name)
        target = output_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(executable, target)
        target.chmod(target.stat().st_mode | 0o700)
        for suffix in (".gcno", ".gcda"):
            sidecar = executable.with_suffix(executable.suffix + suffix) if executable.suffix else Path(str(executable) + suffix)
            if sidecar.is_file():
                shutil.copy2(sidecar, Path(str(target) + suffix))
            plain_sidecar = executable.parent / f"{executable.name}{suffix}"
            if plain_sidecar.is_file() and plain_sidecar != sidecar:
                shutil.copy2(plain_sidecar, target.parent / plain_sidecar.name)
        materialized.append(target)
    return materialized


def _is_gtest_like(source_test: TestChunk) -> bool:
    return source_test.framework in {"TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P"}


def _is_catch_like(source_test: TestChunk) -> bool:
    return source_test.framework in {"TEST_CASE", "SCENARIO"}


def _testish_path(path: Path) -> bool:
    return bool(re.search(r"(^|/)(test|tests|testing|unittest|unit_test|benchmark|bench|example|examples)(/|$)", path.as_posix(), re.I)) or bool(re.search(r"(^|[_-])(test|tests|spec|benchmark|bench)([_./-]|$)", path.name, re.I))


def _has_main_function(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:200000]
    except OSError:
        return False
    return bool(re.search(r"\bint\s+main\s*\(", text))


def _include_args_for_project(
    source_dir: Path,
    source_file: Path,
    *,
    extra_include_dirs: list[Path] | None = None,
) -> list[str]:
    include_dirs: list[Path] = [source_dir, source_file.parent, source_file.parent.parent]
    blocked_include_parts = {".git", "build", "cmake-build-debug", "third_party", "vendor", "external", "win"}
    if extra_include_dirs:
        include_dirs.extend(extra_include_dirs)
    for rel in ("include", "src", "lib", "test", "tests"):
        path = source_dir / rel
        if path.is_dir():
            include_dirs.append(path)
    for include_dir in sorted(source_dir.rglob("include"))[:80]:
        if include_dir.is_dir():
            include_dirs.append(include_dir)
    header_dirs: list[Path] = []
    for pattern in ("*.h", "*.hpp", "*.hh", "*.hxx"):
        for header in sorted(source_dir.rglob(pattern))[:300]:
            if any(part in blocked_include_parts for part in header.parts):
                continue
            header_dirs.append(header.parent)
            # Many C++ projects include headers from a module root, e.g.
            # `#include "db/dbformat.h"` with files under `<root>/leveldb/db`.
            # Adding the parent keeps ad-hoc dynamic builds closer to the
            # project's normal include layout without project-specific recipes.
            header_dirs.append(header.parent.parent)
    result: list[str] = []
    seen: set[Path] = set()
    for include_dir in [*include_dirs, *header_dirs[:80]]:
        try:
            resolved = include_dir.resolve()
        except OSError:
            resolved = include_dir
        if resolved in seen or not include_dir.is_dir():
            continue
        seen.add(resolved)
        result.extend(["-I", str(include_dir)])
    return result


def _implementation_sources(source_dir: Path, source_file: Path, *, limit: int = 160) -> list[Path]:
    suffixes = {".cc", ".cpp", ".cxx"}
    blocked_parts = {
        ".git", "build", "cmake-build-debug", "third_party", "third-party", "3rdparty",
        "vendor", "external", "extern", "benchmark", "benchmarks", "example", "examples",
    }
    try:
        rel_source = source_file.relative_to(source_dir)
    except ValueError:
        rel_source = Path(source_file.name)
    scan_root = source_dir
    if len(rel_source.parts) > 1 and (source_dir / rel_source.parts[0]).is_dir():
        scan_root = source_dir / rel_source.parts[0]

    result: list[Path] = []
    for path in sorted(scan_root.rglob("*")):
        if path == source_file or not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        rel = path.relative_to(source_dir)
        if any(part in blocked_parts for part in rel.parts):
            continue
        if _testish_path(rel):
            continue
        if _has_main_function(path):
            continue
        result.append(path)
        if len(result) >= limit:
            break
    return result


def _source_includes_gtest(source_file: Path) -> bool:
    try:
        text = source_file.read_text(encoding="utf-8", errors="ignore")[:200000]
    except OSError:
        return False
    return "gtest/gtest.h" in text or "gmock/gmock.h" in text or re.search(r"\bTEST(_F|_P)?\s*\(", text) is not None


def _source_uses_gtest_header(source_file: Path) -> bool:
    try:
        text = source_file.read_text(encoding="utf-8", errors="ignore")[:200000]
    except OSError:
        return False
    return "gtest/gtest.h" in text or "gmock/gmock.h" in text


def _source_uses_leveldb_testharness(source_file: Path) -> bool:
    try:
        text = source_file.read_text(encoding="utf-8", errors="ignore")[:200000]
    except OSError:
        return False
    return "util/testharness.h" in text and "leveldb::test::RunAllTests" in text


def _compile_variants(source_dir: Path, primary_source: Path, *, original_source_file: Path | None = None) -> list[tuple[str, list[Path]]]:
    implementation_sources = _implementation_sources(source_dir, original_source_file or primary_source)
    variants = [("single_source", [primary_source])]
    if implementation_sources:
        variants.append(("single_source_plus_project_sources", [primary_source, *implementation_sources]))
    return variants


def _plain_function_harness(source_test: TestChunk, source_file: Path, output_dir: Path) -> Path | None:
    if source_test.framework != "plain_function" or source_test.name == "main":
        return None
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_:]*$", source_test.qualified_name):
        return None
    if not re.search(rf"\b{re.escape(source_test.name)}\s*\(\s*\)", source_test.code):
        return None
    harness = output_dir / "code2graph_plain_function_harness.cpp"
    escaped_source = str(source_file).replace('\\', '\\\\').replace('"', '\\"')
    harness.write_text(
        "#define main code2graph_original_main\n"
        f"#include \"{escaped_source}\"\n"
        "#undef main\n"
        "int main() {\n"
        f"  {source_test.qualified_name}();\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    return harness



def _compile_single_source_test(
    source_dir: Path,
    source_test: TestChunk,
    output_dir: Path,
    *,
    timeout: int,
    extra_include_dirs: list[Path] | None = None,
) -> tuple[RunnableTest | None, dict]:
    compiler = shutil.which("g++") or shutil.which("c++")
    if compiler is None:
        return None, {"single_file_stage": "missing_compiler"}
    source_file = source_dir / source_test.file
    if not source_file.is_file():
        return None, {"single_file_stage": "source_file_missing", "source_file": str(source_file)}
    output_dir.mkdir(parents=True, exist_ok=True)
    include_args = _include_args_for_project(
        source_dir,
        source_file,
        extra_include_dirs=extra_include_dirs,
    )
    link_args: list[str] = []
    framework = "single_file"
    test_filter = None
    primary_source = source_file
    harness_file = _plain_function_harness(source_test, source_file, output_dir)
    if harness_file is not None:
        primary_source = harness_file
        framework = "plain_function_harness"
        test_filter = source_test.qualified_name
    if _source_uses_leveldb_testharness(source_file):
        framework = "leveldb_testharness"
        test_filter = source_test.qualified_name
        link_args.append("-pthread")
    compile_defines: list[str] = []
    if framework == "leveldb_testharness":
        compile_defines.append("-DLEVELDB_PLATFORM_POSIX")
    gtest_install = ensure_googletest_install() if (_source_uses_gtest_header(source_file) or (_is_gtest_like(source_test) and not test_filter)) else None
    if gtest_install is not None:
        include_args.extend(["-I", str(gtest_install / "include")])
        link_args.extend([
            "-L",
            str(gtest_install / "lib"),
            "-lgtest_main",
            "-lgtest",
            "-pthread",
        ])
        framework = "gtest"
        test_filter = source_test.qualified_name
    elif _is_catch_like(source_test):
        framework = "catch"
        test_filter = source_test.qualified_name

    attempts = []
    for variant_name, sources in _compile_variants(source_dir, primary_source, original_source_file=source_file):
        for standard in ("c++17", "c++14", "c++11"):
            executable = output_dir / f"{variant_name}_{standard.replace('+', 'p')}"
            command = [
                compiler,
                f"-std={standard}",
                "-O0",
                "-g",
                "--coverage",
                "-DCATCH_CONFIG_NO_POSIX_SIGNALS",
                *compile_defines,
                *include_args,
                *map(str, sources),
                *link_args,
                "-o",
                str(executable),
            ]
            result = run_command(command, cwd=source_dir, timeout=timeout)
            attempts.append({
                "variant": variant_name,
                "standard": standard,
                "source_count": len(sources),
                "command": command,
                "ok": result.ok,
                "log_excerpt": result.excerpt(1200),
            })
            if result.ok:
                return RunnableTest(executable, framework, test_filter, source_test.qualified_name if test_filter else executable.name), {
                    "single_file_stage": "compiled",
                    "single_file_variant": variant_name,
                    "single_file_standard": standard,
                    "single_file_source_count": len(sources),
                    "single_file_harness": str(harness_file) if harness_file else None,
                    "single_file_command": command,
                    "single_file_log_excerpt": result.excerpt(),
                    "single_file_attempts": attempts[-3:],
                }
    return None, {
        "single_file_stage": "compile_failed",
        "single_file_attempts": attempts[-6:],
    }

def _probe_project(args, layout: PairLayout, project: str, mapping_rows: list) -> tuple[list[DynamicProbeRecord], DynamicProjectReport]:
    from test_mapping.repository import load_project
    from test_mapping.source_function_mapping import _load_source_tests_for_scope

    paths = layout.project(project)
    if paths.source_dir is None:
        report = DynamicProjectReport(project=project, build_status="failed", error_stage="dataset", error="missing source project")
        return [
            DynamicProbeRecord(
                schema_version=1,
                project=project,
                source_test_id=mapping.source_test_id,
                source_test_nodeid=mapping.source_test_nodeid,
                source_test_file=mapping.source_test_file,
                source_test_name=mapping.source_test_name,
                source_test_framework=mapping.source_test_framework,
                status="unresolved",
                diagnostics={"dynamic_stage": "dataset_missing_source_project"},
            )
            for mapping in mapping_rows
        ], report

    sandbox_dir = Path(args.sandbox_root) / project
    if args.clean and sandbox_dir.exists():
        shutil.rmtree(sandbox_dir)
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    recipe = load_recipe(project, Path(args.recipe_dir) if args.recipe_dir else None)

    build = configure_and_build(
        paths.source_dir,
        sandbox_dir / "build",
        configure_timeout=args.configure_timeout,
        build_timeout=args.build_timeout,
        recipe=recipe,
    )
    existing_executables: list[Path] = []
    initial_build_failure: dict | None = None
    if not build.ok or build.build_dir is None:
        existing_executables = discover_executables(paths.source_dir, paths.source_dir)
    if (not build.ok or build.build_dir is None) and not existing_executables:
        initial_build_failure = {
            "initial_dynamic_stage": "build_failed",
            "initial_build_system": build.build_system,
            "initial_build_dir": str(build.build_dir) if build.build_dir else None,
            "initial_error_stage": build.error_stage,
            "initial_error": build.error,
            "initial_log_excerpt": build.log_excerpt(),
        }
        build.build_dir = sandbox_dir / "ad_hoc_build"
        build.build_dir.mkdir(parents=True, exist_ok=True)
        build.build_system = f"{build.build_system or 'none'}+ad_hoc"
    if existing_executables:
        build.build_dir = sandbox_dir / "existing_executables"
        build.build_system = f"{build.build_system or 'none'}+existing_executables"
        existing_executables = _materialize_existing_executables(
            existing_executables,
            source_dir=paths.source_dir,
            sandbox_dir=sandbox_dir,
        )

    data = load_project(layout, project)
    tests, test_errors = _load_source_tests_for_scope(
        source_dir=paths.source_dir,
        project=project,
        language=layout.pair.source,
        scope="all",
    )
    tests_by_selector = _index_tests(tests)
    tests_per_file = Counter(test.file for test in tests)
    executables = existing_executables or discover_executables(build.build_dir, paths.source_dir)
    if recipe is not None and recipe.executable_globs and build.build_dir is not None:
        recipe_executables: list[Path] = []
        for pattern in recipe.executable_globs:
            full_pattern = str(Path(pattern) if Path(pattern).is_absolute() else build.build_dir / pattern)
            for executable_text in sorted(glob.glob(full_pattern, recursive=True)):
                executable = Path(executable_text)
                if executable.is_file() and os.access(executable, os.X_OK):
                    recipe_executables.append(executable)
        if recipe_executables:
            seen = set(executables)
            executables = [*executables, *(path for path in recipe_executables if path not in seen)]
    runnable_tests = _runnable_index(executables, list_timeout=args.list_timeout)
    fallback_include_dirs = [build.build_dir] if build.build_dir is not None else []
    if recipe is not None:
        fallback_include_dirs.extend(recipe.extra_include_dirs(paths.source_dir, build.build_dir))

    records: list[DynamicProbeRecord] = []
    mapped = 0
    for mapping in mapping_rows:
        source_test = (
            tests_by_selector.get(mapping.source_test_id)
            or tests_by_selector.get(mapping.source_test_nodeid)
            or tests_by_selector.get(mapping.source_test_name)
        )
        if source_test is None:
            records.append(DynamicProbeRecord(
                schema_version=1,
                project=project,
                source_test_id=mapping.source_test_id,
                source_test_nodeid=mapping.source_test_nodeid,
                source_test_file=mapping.source_test_file,
                source_test_name=mapping.source_test_name,
                source_test_framework=mapping.source_test_framework,
                status="unresolved",
                diagnostics={"dynamic_stage": "source_test_not_reparsed", "test_parse_errors": test_errors},
            ))
            continue
        runnable = match_source_test_to_runnable(source_test.qualified_name, runnable_tests)
        runnable_diagnostics = {}
        if runnable is None and len(runnable_tests) == 1 and runnable_tests[0].test_filter is None and not _is_gtest_like(source_test):
            if tests_per_file[source_test.file] <= 1:
                runnable = runnable_tests[0]
                runnable_diagnostics["dynamic_binary_match"] = "single_runnable_single_source_test_fallback"
            else:
                runnable_diagnostics["dynamic_binary_match"] = "single_runnable_suppressed_multi_test_file"
                runnable_diagnostics["source_test_file_test_count"] = tests_per_file[source_test.file]
        if runnable is None and source_test.framework == "plain_function" and runnable_tests:
            binary_runnables = [item for item in runnable_tests if item.test_filter is None]
            if tests_per_file[source_test.file] <= 1:
                if len(binary_runnables) == 1:
                    runnable = binary_runnables[0]
                    runnable_diagnostics["dynamic_binary_match"] = "plain_function_single_source_test_binary_fallback"
                elif runnable_tests and not binary_runnables:
                    runnable_diagnostics["dynamic_binary_match"] = "plain_function_filtered_runnables_suppressed"
                    runnable_diagnostics["runnable_test_count"] = len(runnable_tests)
            else:
                runnable_diagnostics["dynamic_binary_match"] = "plain_function_binary_suppressed_multi_test_file"
                runnable_diagnostics["source_test_file_test_count"] = tests_per_file[source_test.file]
        if runnable is None:
            if tests_per_file[source_test.file] <= 1 or source_test.framework == "plain_function" or _is_gtest_like(source_test) or _is_catch_like(source_test):
                runnable, runnable_diagnostics = _compile_single_source_test(
                    paths.source_dir,
                    source_test,
                    sandbox_dir / "single_file" / source_test.chunk_id.replace("/", "_").replace(":", "_"),
                    timeout=args.build_timeout,
                    extra_include_dirs=fallback_include_dirs,
                )
            else:
                runnable_diagnostics = {
                    "single_file_stage": "suppressed",
                    "single_file_reason": "single_file_ambiguous_multi_test_file",
                    "source_test_file_test_count": tests_per_file[source_test.file],
                }
        if runnable is None:
            records.append(DynamicProbeRecord(
                schema_version=1,
                project=project,
                source_test_id=mapping.source_test_id,
                source_test_nodeid=mapping.source_test_nodeid,
                source_test_file=mapping.source_test_file,
                source_test_name=mapping.source_test_name,
                source_test_framework=mapping.source_test_framework,
                status="unresolved",
                diagnostics={
                    "dynamic_stage": "test_filter_not_found",
                    "runnable_test_count": len(runnable_tests),
                    "executable_count": len(executables),
                    "dynamic_recipe": recipe.project if recipe is not None else None,
                    **(initial_build_failure or {}),
                    **runnable_diagnostics,
                },
            ))
            continue
        hits, diagnostics = collect_coverage(
            runnable=runnable,
            source_test=source_test,
            functions=data.source_functions,
            source_dir=paths.source_dir,
            build_dir=build.build_dir,
            coverage_dir=sandbox_dir / "coverage" / source_test.chunk_id.replace("/", "_").replace(":", "_"),
            timeout=args.test_timeout,
        )
        if (
            not hits
            and diagnostics.get("coverage_stage") in {"no_gcda", "missing_gcov"}
            and (tests_per_file[source_test.file] <= 1 or source_test.framework == "plain_function" or _is_gtest_like(source_test) or _is_catch_like(source_test))
        ):
            fallback_runnable, fallback_diagnostics = _compile_single_source_test(
                paths.source_dir,
                source_test,
                sandbox_dir / "single_file_after_no_gcda" / source_test.chunk_id.replace("/", "_").replace(":", "_"),
                timeout=args.build_timeout,
                extra_include_dirs=fallback_include_dirs,
            )
            if fallback_runnable is not None:
                fallback_hits, fallback_coverage_diagnostics = collect_coverage(
                    runnable=fallback_runnable,
                    source_test=source_test,
                    functions=data.source_functions,
                    source_dir=paths.source_dir,
                    build_dir=build.build_dir,
                    coverage_dir=sandbox_dir / "coverage_ad_hoc" / source_test.chunk_id.replace("/", "_").replace(":", "_"),
                    timeout=args.test_timeout,
                )
                if fallback_hits or fallback_coverage_diagnostics.get("coverage_stage") != "no_gcda":
                    runnable = fallback_runnable
                    hits = fallback_hits
                    diagnostics = {
                        **diagnostics,
                        "pre_fallback_coverage_stage": diagnostics.get("coverage_stage"),
                        **fallback_coverage_diagnostics,
                    }
                    runnable_diagnostics = {**runnable_diagnostics, **fallback_diagnostics, "dynamic_binary_match": "ad_hoc_after_no_gcda"}
        test_isolated = runnable.test_filter is not None or tests_per_file[source_test.file] <= 1
        if hits and test_isolated:
            mapped += 1
        records.append(DynamicProbeRecord(
            schema_version=1,
            project=project,
            source_test_id=mapping.source_test_id,
            source_test_nodeid=mapping.source_test_nodeid,
            source_test_file=mapping.source_test_file,
            source_test_name=mapping.source_test_name,
            source_test_framework=mapping.source_test_framework,
            status="candidate" if hits and test_isolated else "unresolved",
            source_functions=hits if test_isolated else [],
            diagnostics={
                **diagnostics,
                "dynamic_stage": diagnostics.get("coverage_stage") if test_isolated else "coverage_not_test_isolated",
                "dynamic_test_isolated": test_isolated,
                "build_system": build.build_system,
                "executable": str(runnable.executable),
                "framework": runnable.framework,
                "test_filter": runnable.test_filter,
                "dynamic_recipe": recipe.project if recipe is not None else None,
                **(initial_build_failure or {}),
                **runnable_diagnostics,
            },
        ))

    report = DynamicProjectReport(
        project=project,
        build_status="coverage_collected" if mapped else "test_discovered",
        build_system=build.build_system,
        source_dir=str(paths.source_dir),
        build_dir=str(build.build_dir),
        executable_count=len(executables),
        listed_test_count=len(runnable_tests),
        selected_test_count=len(mapping_rows),
        dynamic_mapped_test_count=mapped,
        log_excerpt=(
            f"recipe={recipe.project}; notes={recipe.notes}\n" if recipe is not None else ""
        ) + build.log_excerpt(),
    )
    return records, report


def command_probe(args) -> int:
    from test_mapping.dataset import PairLayout
    from test_mapping.models import LanguagePair

    started = time.perf_counter()
    layout = PairLayout.detect(Path(args.dataset_root), LanguagePair.parse(args.pair))
    selected = _selected_mapping_rows(args)
    all_records: list[DynamicProbeRecord] = []
    project_reports: list[DynamicProjectReport] = []
    for project, rows in selected.items():
        records, report = _probe_project(args, layout, project, rows)
        all_records.extend(records)
        project_reports.append(report)

    output_dir = Path(args.output_dir)
    _write_jsonl(all_records, output_dir / "dynamic_probe.jsonl")
    status_counts = Counter(record.status for record in all_records)
    stage_counts = Counter(record.diagnostics.get("dynamic_stage") for record in all_records)
    report = {
        "schema_version": 1,
        "selection": args.selection,
        "project_count": len(project_reports),
        "selected_test_count": sum(report.selected_test_count for report in project_reports),
        "probe_record_count": len(all_records),
        "status_counts": dict(sorted(status_counts.items())),
        "stage_counts": dict(sorted(stage_counts.items())),
        "dynamic_mapped_test_count": status_counts.get("candidate", 0),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "projects": [item.to_dict() for item in project_reports],
    }
    _json_dump(report, output_dir / "dynamic_probe_report.json")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_merge(args) -> int:
    from test_mapping.source_function_mapping import load_source_function_mapping, write_source_function_mapping

    base_records = load_source_function_mapping(args.mapping)
    dynamic_records = _read_dynamic_probe(Path(args.dynamic_probe))
    merged, report = _merge_dynamic_records(base_records, dynamic_records)
    output = Path(args.output)
    write_source_function_mapping(merged, output)
    report["mapping"] = args.mapping
    report["dynamic_probe"] = args.dynamic_probe
    report["output"] = str(output)
    if args.report:
        _json_dump(report, Path(args.report))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dynamic_test_mapping", description="Coverage-assisted C++ test->function probe")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe", help="Run dynamic coverage probe on selected mapping rows")
    probe.add_argument("--dataset-root", required=True)
    probe.add_argument("--pair", default="C++_to_Python")
    probe.add_argument("--mapping", required=True)
    probe.add_argument("--output-dir", required=True)
    probe.add_argument("--sandbox-root", default="/tmp/code2graph_dynamic")
    probe.add_argument("--project")
    probe.add_argument("--selection", choices=("unresolved", "low", "unresolved_or_low"), default="unresolved")
    probe.add_argument("--max-projects", type=int)
    probe.add_argument("--max-tests-per-project", type=int, default=5)
    probe.add_argument("--configure-timeout", type=int, default=120)
    probe.add_argument("--build-timeout", type=int, default=300)
    probe.add_argument("--list-timeout", type=int, default=20)
    probe.add_argument("--test-timeout", type=int, default=120)
    probe.add_argument("--recipe-dir", help="Directory containing per-project dynamic build recipe JSON files")
    probe.add_argument("--clean", action="store_true")
    probe.set_defaults(handler=command_probe)

    merge = sub.add_parser("merge", help="Merge dynamic coverage candidates into a mapping table")
    merge.add_argument("--mapping", required=True, help="Base source-function mapping JSONL")
    merge.add_argument("--dynamic-probe", required=True, help="dynamic_probe.jsonl produced by probe")
    merge.add_argument("--output", required=True, help="Merged mapping JSONL")
    merge.add_argument("--report", help="Optional merge report JSON")
    merge.set_defaults(handler=command_merge)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
