from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import re
import shutil

from test_mapping.models import FunctionChunk, TestChunk
from test_mapping.source_function_gold import is_business_function

from .models import DynamicFunctionHit
from .runner import run_command
from .test_discovery import RunnableTest, command_for_runnable, env_for_runnable


CPP_PSEUDO_FUNCTION_NAMES = {
    "if",
    "else",
    "for",
    "while",
    "switch",
    "catch",
    "case",
    "return",
}


def _covered_lines_from_llvm_export(payload: dict) -> dict[str, set[int]]:
    """Convert llvm-cov export JSON into filename -> covered lines."""

    covered: dict[str, set[int]] = defaultdict(set)
    for data in payload.get("data", []):
        for file_payload in data.get("files", []):
            filename = file_payload.get("filename")
            if not filename:
                continue
            # segments: [line, col, count, has_count, is_region_entry, ...]
            segments = sorted(file_payload.get("segments", []), key=lambda item: (item[0], item[1]))
            active_count = 0
            last_line: int | None = None
            for segment in segments:
                if len(segment) < 3:
                    continue
                line = int(segment[0])
                count = int(segment[2] or 0)
                if last_line is not None and active_count > 0:
                    for covered_line in range(last_line, max(last_line, line)):
                        covered[filename].add(covered_line)
                active_count = count
                last_line = line
            if last_line is not None and active_count > 0:
                covered[filename].add(last_line)
    return covered


def _path_matches(function_file: str, coverage_file: str, source_dir: Path) -> bool:
    function_norm = function_file.replace("\\", "/").strip("/")
    coverage_path = Path(coverage_file)
    coverage_norm = str(coverage_path).replace("\\", "/")
    if coverage_norm.endswith(function_norm):
        return True
    try:
        rel = coverage_path.resolve().relative_to(source_dir.resolve())
        return str(rel).replace("\\", "/") == function_norm
    except Exception:
        return False


def rank_covered_functions(
    *,
    covered_lines: dict[str, set[int]],
    functions: list[FunctionChunk],
    source_test: TestChunk,
    source_dir: Path,
    executable: Path,
    test_filter: str | None,
    limit: int = 10,
) -> list[DynamicFunctionHit]:
    suite_hint = ""
    if "." in source_test.qualified_name:
        suite_hint = source_test.qualified_name.split(".", 1)[0]
    elif "::" in source_test.qualified_name:
        suite_hint = source_test.qualified_name.rsplit("::", 1)[0].split("::")[-1]
    suite_hint = re.sub(r"(Test|Tests|Fixture|Suite)$", "", suite_hint, flags=re.I).lower()

    scored: list[tuple[tuple[float, str, int, str], FunctionChunk, int]] = []
    for function in functions:
        if not is_business_function(function):
            continue
        if function.name in CPP_PSEUDO_FUNCTION_NAMES:
            continue
        if function.file == source_test.file or "/test" in function.file.lower():
            continue
        count = 0
        for filename, lines in covered_lines.items():
            if _path_matches(function.file, filename, source_dir):
                count += sum(1 for line in lines if function.start_line <= line <= function.end_line)
        if count <= 0:
            continue
        direct_name_bonus = 0.5 if function.name in source_test.code else 0.0
        same_stem_bonus = 0.25 if Path(function.file).stem.lower() in source_test.file.lower() else 0.0
        function_names = {
            function.name.lower(),
            function.qualified_name.split("::")[-1].lower(),
            Path(function.file).stem.lower(),
        }
        suite_match_bonus = 2.0 if suite_hint and suite_hint in function_names else 0.0
        score = count + direct_name_bonus + same_stem_bonus + suite_match_bonus
        scored.append(((-score, function.file, function.start_line, function.chunk_id), function, count))

    hits: list[DynamicFunctionHit] = []
    for rank, (_, function, count) in enumerate(sorted(scored, key=lambda item: item[0])[:limit], start=1):
        hits.append(DynamicFunctionHit(
            rank=rank,
            chunk_id=function.chunk_id,
            file=function.file,
            name=function.name,
            qualified_name=function.qualified_name,
            start_line=function.start_line,
            end_line=function.end_line,
            covered_lines=count,
            executable=str(executable),
            test_filter=test_filter,
            score=float(count),
        ))
    return hits


def _covered_lines_from_gcov_file(path: Path) -> dict[str, set[int]]:
    source: str | None = None
    covered: set[int] = set()
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw_line.startswith("        -:    0:Source:"):
            source = raw_line.split("Source:", 1)[1].strip()
            continue
        match = re.match(r"\s*(?P<count>#####+|=====|[-0-9]+):\s*(?P<line>[0-9]+):", raw_line)
        if not match:
            continue
        count = match.group("count").strip()
        if count in {"-", "#####", "====="}:
            continue
        try:
            if int(count) > 0:
                covered.add(int(match.group("line")))
        except ValueError:
            continue
    if source and covered:
        return {source: covered}
    return {}


def _collect_gcov_lines(
    *,
    build_dir: Path,
    coverage_dir: Path,
    extra_roots: list[Path] | None = None,
    gcda_files: list[Path] | None = None,
) -> tuple[dict[str, set[int]], dict]:
    gcov_tool = shutil.which("gcov")
    if not gcov_tool:
        return {}, {"coverage_stage": "missing_gcov"}
    if gcda_files is None:
        roots = [build_dir, *(extra_roots or [])]
        seen_gcda: set[Path] = set()
        gcda_files = []
        for root in roots:
            if root.exists():
                for item in root.rglob("*.gcda"):
                    if item not in seen_gcda:
                        seen_gcda.add(item)
                        gcda_files.append(item)
    gcda_files = sorted(path for path in gcda_files if path.is_file())
    if not gcda_files:
        return {}, {"coverage_stage": "no_gcda"}
    coverage_dir.mkdir(parents=True, exist_ok=True)
    for stale_gcov in coverage_dir.glob("*.gcov"):
        stale_gcov.unlink()
    diagnostics = {"gcda_file_count": len(gcda_files), "gcov_failures": 0}
    for gcda in gcda_files[:1000]:
        result = run_command(
            [gcov_tool, "-b", "-c", "-o", str(gcda.parent), str(gcda)],
            cwd=coverage_dir,
            timeout=30,
        )
        if not result.ok:
            diagnostics["gcov_failures"] += 1
    covered: dict[str, set[int]] = defaultdict(set)
    for gcov_file in coverage_dir.glob("*.gcov"):
        for filename, lines in _covered_lines_from_gcov_file(gcov_file).items():
            covered[filename].update(lines)
    diagnostics["covered_file_count"] = len(covered)
    diagnostics["covered_line_count"] = sum(len(lines) for lines in covered.values())
    return covered, diagnostics


def _is_safe_coverage_root(path: Path) -> bool:
    """Return whether generated coverage files can be removed before a test run.

    gcc/gcov accumulates counters in `.gcda` files across executions.  If we do
    not remove them before each selected source test, a later test inherits
    coverage from earlier tests and the dynamic mapping becomes project-level
    rather than test-level.  Deletion is intentionally limited to sandbox-like
    roots so an in-tree Makefile build cannot accidentally clean user data.
    """

    try:
        resolved = path.resolve()
    except OSError:
        return False
    parts = set(resolved.parts)
    if resolved.is_relative_to(Path("/tmp")):
        return True
    return any(part.startswith("code2graph_dynamic") for part in parts)


def _clear_gcov_counters(*roots: Path) -> dict:
    cleared = 0
    skipped: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen or not root.exists():
            continue
        seen.add(root)
        if not _is_safe_coverage_root(root):
            skipped.append(str(root))
            continue
        for gcda in root.rglob("*.gcda"):
            try:
                gcda.unlink()
                cleared += 1
            except OSError:
                skipped.append(str(gcda))
    return {
        "cleared_gcda_count": cleared,
        "skipped_gcda_roots": skipped[:20],
    }


def collect_coverage(
    *,
    runnable: RunnableTest,
    source_test: TestChunk,
    functions: list[FunctionChunk],
    source_dir: Path,
    build_dir: Path,
    coverage_dir: Path,
    timeout: int = 120,
) -> tuple[list[DynamicFunctionHit], dict]:
    coverage_dir.mkdir(parents=True, exist_ok=True)
    for path in coverage_dir.glob("*.profraw"):
        path.unlink()
    gcov_clear_diagnostics = _clear_gcov_counters(build_dir, runnable.executable.parent)
    profraw = coverage_dir / "%p.profraw"
    env = {
        **dict(os.environ),
        **env_for_runnable(runnable),
        "LLVM_PROFILE_FILE": str(profraw),
    }
    command = command_for_runnable(runnable)
    run = run_command(command, cwd=runnable.executable.parent, timeout=timeout, env=env)
    diagnostics = {
        "run_command": command,
        "run_returncode": run.returncode,
        "run_timed_out": run.timed_out,
        "run_log_excerpt": run.excerpt(),
        **gcov_clear_diagnostics,
    }
    if not run.ok:
        return [], {**diagnostics, "coverage_stage": "test_run_failed"}
    if runnable.framework == "gtest" and re.search(r"\[\s*=+\s*\]\s+Running 0 tests from 0 test suites", run.excerpt(4000)):
        return [], {**diagnostics, "coverage_stage": "gtest_filter_matched_zero_tests"}

    profraw_files = sorted(coverage_dir.glob("*.profraw"))
    if not profraw_files:
        focused_gcda = [
            path
            for path in [
                Path(str(runnable.executable) + ".gcda"),
                runnable.executable.with_suffix(runnable.executable.suffix + ".gcda") if runnable.executable.suffix else Path(str(runnable.executable) + ".gcda"),
            ]
            if path.is_file()
        ]
        covered_lines, gcov_diagnostics = _collect_gcov_lines(
            build_dir=build_dir,
            coverage_dir=coverage_dir,
            extra_roots=[runnable.executable.parent],
            gcda_files=focused_gcda or None,
        )
        if not covered_lines:
            return [], {**diagnostics, **gcov_diagnostics}
        hits = rank_covered_functions(
            covered_lines=covered_lines,
            functions=functions,
            source_test=source_test,
            source_dir=source_dir,
            executable=runnable.executable,
            test_filter=runnable.test_filter,
        )
        return hits, {
            **diagnostics,
            **gcov_diagnostics,
            "coverage_stage": "coverage_mapped" if hits else "coverage_no_business_function_hit",
            "coverage_tool": "gcov",
        }
    profdata = coverage_dir / "merged.profdata"
    profdata_tool = shutil.which("llvm-profdata")
    cov_tool = shutil.which("llvm-cov")
    if not profdata_tool or not cov_tool:
        return [], {**diagnostics, "coverage_stage": "missing_llvm_tools"}

    merge = run_command(
        [profdata_tool, "merge", "-sparse", *map(str, profraw_files), "-o", str(profdata)],
        cwd=coverage_dir,
        timeout=60,
    )
    if not merge.ok:
        return [], {**diagnostics, "coverage_stage": "profdata_merge_failed", "merge_log_excerpt": merge.excerpt()}

    export = run_command(
        [cov_tool, "export", str(runnable.executable), f"-instr-profile={profdata}", "-format=text"],
        cwd=runnable.executable.parent,
        timeout=90,
    )
    if not export.ok:
        return [], {**diagnostics, "coverage_stage": "llvm_cov_export_failed", "export_log_excerpt": export.excerpt()}
    try:
        payload = json.loads(export.stdout)
    except json.JSONDecodeError as exc:
        return [], {**diagnostics, "coverage_stage": "coverage_json_parse_failed", "error": str(exc)}

    covered_lines = _covered_lines_from_llvm_export(payload)
    hits = rank_covered_functions(
        covered_lines=covered_lines,
        functions=functions,
        source_test=source_test,
        source_dir=source_dir,
        executable=runnable.executable,
        test_filter=runnable.test_filter,
    )
    return hits, {
        **diagnostics,
        "coverage_stage": "coverage_mapped" if hits else "coverage_no_business_function_hit",
        "coverage_tool": "llvm-cov",
        "covered_file_count": len(covered_lines),
        "covered_line_count": sum(len(lines) for lines in covered_lines.values()),
    }


collect_llvm_coverage = collect_coverage
