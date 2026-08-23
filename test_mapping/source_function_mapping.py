from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable, Literal

from .dataset import PairLayout, iter_language_files, public_test_files
from .models import FunctionChunk, TestChunk
from .source_function_gold import (
    SourceFunctionGoldRecord,
    evaluable_expected_function_ids,
    is_business_function,
    verification_reason,
)
from .static_resolution import (
    ResolvedFunction,
    direct_call_targets,
    resolve_source_function_links,
)


SourceFunctionMappingMethod = Literal[
    "static",
    "verified_static",
    "verified_static_with_medium",
    "verified_static_with_low",
    "recall_static",
]
SourceFunctionMappingStatus = Literal["matched", "candidate", "unresolved", "no_match"]
SourceFunctionTestScope = Literal["public", "all"]
SourceFunctionHitConfidence = Literal["high", "medium", "low"]
DEFAULT_SOURCE_FUNCTION_MAPPING_PATH = Path(
    "outputs/source-function-map/cpp_to_python_verified_static_all_tests.jsonl"
)


@dataclass(frozen=True)
class SourceFunctionMappingHit:
    rank: int
    chunk_id: str
    file: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    matched_call: str
    resolution_reason: str
    verification_reason: str | None = None
    parent: str | None = None
    confidence: SourceFunctionHitConfidence = "high"

    @classmethod
    def from_link(
        cls,
        link: ResolvedFunction,
        *,
        rank: int,
        verification_reason: str | None = None,
        confidence: SourceFunctionHitConfidence = "high",
    ) -> "SourceFunctionMappingHit":
        function = link.function
        return cls(
            rank=rank,
            chunk_id=function.chunk_id,
            file=function.file,
            name=function.name,
            qualified_name=function.qualified_name,
            start_line=function.start_line,
            end_line=function.end_line,
            parent=function.parent,
            matched_call=link.call,
            resolution_reason=link.reason,
            verification_reason=verification_reason,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceFunctionMappingHit":
        value = dict(value)
        value.setdefault("confidence", "high")
        return cls(**value)


@dataclass
class SourceFunctionMappingRecord:
    schema_version: int
    pair: str
    project: str
    source_test_id: str
    source_test_nodeid: str
    source_test_file: str
    source_test_name: str
    source_test_language: str
    source_test_framework: str
    resolver_method: SourceFunctionMappingMethod
    status: SourceFunctionMappingStatus
    direct_calls: list[str]
    source_functions: list[SourceFunctionMappingHit] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_functions"] = [item.to_dict() for item in self.source_functions]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceFunctionMappingRecord":
        value = dict(value)
        value["source_functions"] = [
            SourceFunctionMappingHit.from_dict(item)
            for item in value.get("source_functions", [])
        ]
        return cls(**value)


@dataclass(frozen=True)
class SourceFunctionMappingEvaluationSummary:
    query_unit: str
    mapping: str
    gold: str
    method_counts: dict[str, int]
    mapping_record_count: int
    gold_record_count: int
    gold_status_counts: dict[str, int]
    evaluable_matched_gold_count: int
    evaluable_no_match_gold_count: int
    reviewed_evaluable_gold_count: int
    missing_mapping_record_count: int
    empty_prediction_count: int
    no_match_correct_count: int
    overall_accuracy_at_1_including_no_match: float
    hit_rate_at_1: float
    hit_rate_at_3: float
    hit_rate_at_5: float
    macro_recall_at_1: float
    macro_recall_at_3: float
    macro_recall_at_5: float
    mrr: float
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def source_test_nodeid(test: TestChunk) -> str:
    if test.language == "Python":
        parts = [part for part in test.qualified_name.split(".") if part]
        return "::".join([test.file, *parts])
    return f"{test.file}::{test.qualified_name}"


def source_test_selector_values(record: SourceFunctionMappingRecord) -> set[str]:
    return {
        record.source_test_id,
        record.source_test_nodeid,
        record.source_test_name,
        record.source_test_file,
        f"{record.source_test_file}::{record.source_test_name}",
    }


def _rank_verified_business_links(
    test: TestChunk,
    links: Iterable[ResolvedFunction],
) -> list[tuple[ResolvedFunction, str]]:
    business_links = [link for link in links if is_business_function(link.function)]
    business_count = len(business_links)
    priority = {
        "direct_call_inside_assertion": 0,
        "direct_call_inside_exception_assertion": 0,
        "call_result_assigned_then_asserted": 1,
        "call_effect_or_output_asserted": 2,
        "constructed_object_state_asserted": 3,
        "single_business_call_in_asserting_test": 4,
    }
    setup_like_names = {
        "Start",
        "Stop",
        "close_stdin",
        "get_exit_status",
        "init",
        "initGridMap",
        "shutdown",
    }

    verified: list[tuple[ResolvedFunction, str]] = []
    for link in business_links:
        reason = verification_reason(
            test,
            link.call,
            business_link_count=business_count,
        )
        if reason is not None:
            verified.append((link, reason))

    def key(item: tuple[ResolvedFunction, str]) -> tuple[int, int, str, int, str]:
        link, reason = item
        setup_rank = 1 if link.function.name in setup_like_names else 0
        return (
            priority.get(reason, 9),
            setup_rank,
            link.function.file,
            link.function.start_line,
            link.function.qualified_name,
        )

    return sorted(verified, key=key)


def _test_or_helpers_have_assertion(test: TestChunk) -> bool:
    assertion_tokens = (
        "assert",
        "ASSERT_",
        "EXPECT_",
        "REQUIRE",
        "CHECK",
        "BOOST_CHECK",
        "BOOST_REQUIRE",
        "pytest.raises",
    )
    text = "\n".join([test.code, *test.helpers])
    return any(token in text for token in assertion_tokens)


def _rank_medium_business_links(
    test: TestChunk,
    links: Iterable[ResolvedFunction],
    *,
    excluded_function_ids: set[str],
    include_low: bool = False,
) -> list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]]:
    business_links = [
        link
        for link in links
        if link.function.chunk_id not in excluded_function_ids
        and is_business_function(link.function)
    ]
    business_count = len(business_links)
    has_assertion = _test_or_helpers_have_assertion(test)
    ranked: list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]] = []
    for link in business_links:
        reason = verification_reason(
            test,
            link.call,
            business_link_count=business_count,
            include_helpers=True,
        )
        if link.reason.endswith("_via_helper") and reason is not None:
            ranked.append((link, f"{reason}_via_helper", "medium"))
        elif reason == "single_business_call_in_asserting_test":
            ranked.append((link, reason, "medium"))
        elif include_low and has_assertion:
            ranked.append((link, "business_call_in_asserting_test_unverified", "low"))

    confidence_rank = {"medium": 0, "low": 1}
    reason_rank = {
        "direct_call_inside_assertion_via_helper": 0,
        "direct_call_inside_exception_assertion_via_helper": 0,
        "call_result_assigned_then_asserted_via_helper": 1,
        "call_effect_or_output_asserted_via_helper": 2,
        "constructed_object_state_asserted_via_helper": 3,
        "single_business_call_in_asserting_test": 4,
        "business_call_in_asserting_test_unverified": 9,
    }
    return sorted(
        ranked,
        key=lambda item: (
            confidence_rank[item[2]],
            reason_rank.get(item[1] or "", 99),
            item[0].function.file,
            item[0].function.start_line,
            item[0].function.qualified_name,
        ),
    )


def _rank_low_business_links(
    links: Iterable[ResolvedFunction],
    *,
    excluded_function_ids: set[str],
) -> list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]]:
    candidates = [
        link
        for link in links
        if link.function.chunk_id not in excluded_function_ids
        and is_business_function(link.function)
    ]
    return [
        (link, "static_business_call_unverified", "low")
        for link in sorted(
            candidates,
            key=lambda item: (
                item.function.file,
                item.function.start_line,
                item.function.qualified_name,
            ),
        )
    ]


def _call_base(value: str) -> str:
    value = value.replace("::", ".")
    value = value.split("<", 1)[0]
    return value.rsplit(".", 1)[-1]


def _receiver_name(value: str) -> str:
    normalized = value.replace("::", ".")
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[0].rsplit(".", 1)[-1]


def _function_noise_rank(function: FunctionChunk) -> int:
    normalized = function.file.replace("\\", "/").lower()
    parts = set(Path(normalized).parts)
    stem = Path(normalized).stem
    return int("bench" in stem or bool(parts & {"example", "examples", "benchmark", "benchmarks"}))


def _fallback_low_links(
    test: TestChunk,
    functions: Iterable[FunctionChunk],
    *,
    excluded_function_ids: set[str],
    max_per_call: int = 3,
) -> list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]]:
    """Return recall-first static candidates when strict resolution failed.

    This intentionally does not claim verification.  It uses exact short-name
    and receiver/name suffix evidence to avoid returning an empty answer for
    tests whose calls cannot be resolved by the stricter C++ resolver.
    """

    business_functions = [
        function
        for function in functions
        if function.chunk_id not in excluded_function_ids
        and is_business_function(function)
    ]
    result: list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]] = []
    seen: set[str] = set()

    for call in direct_call_targets(test):
        base = _call_base(call)
        receiver = _receiver_name(call)
        if not base:
            continue

        scored: list[tuple[tuple[int, int, str, int, str], FunctionChunk]] = []
        for function in business_functions:
            qualified = function.qualified_name.replace("::", ".")
            parent = (function.parent or "").replace("::", ".")
            score = 0
            if function.name == base:
                score += 6
            if qualified.endswith(f".{base}") or qualified == base:
                score += 4
            if receiver:
                if parent == receiver or parent.endswith(f".{receiver}"):
                    score += 4
                if qualified.endswith(f"{receiver}.{base}"):
                    score += 5
                if receiver.lower() in function.file.replace("\\", "/").lower():
                    score += 1
            if score <= 0:
                continue
            scored.append(((-score, _function_noise_rank(function), function.file, function.start_line, function.chunk_id), function))

        for _, function in sorted(scored, key=lambda item: item[0])[:max_per_call]:
            if function.chunk_id in seen:
                continue
            seen.add(function.chunk_id)
            result.append((
                ResolvedFunction(function, call, "recall_name_fallback"),
                "name_or_receiver_static_candidate_unverified",
                "low",
            ))

    return result


def _tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", value.replace("_", " "))
        if len(token) >= 3
        and token.lower() not in {"test", "tests", "public", "main", "src", "include", "cpp", "hpp", "hxx"}
    }


def _last_resort_project_links(
    test: TestChunk,
    functions: Iterable[FunctionChunk],
    *,
    excluded_function_ids: set[str],
    max_candidates: int = 3,
) -> list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]]:
    """Return low-confidence project-local candidates when no call can be resolved.

    This is the recall-maximizing final fallback. It is deliberately labeled
    as low confidence and unverified: it uses only file/name proximity, then a
    deterministic first-business-function fallback if proximity has no signal.
    """

    business_functions = [
        function
        for function in functions
        if function.chunk_id not in excluded_function_ids
        and is_business_function(function)
    ]
    calls = direct_call_targets(test)
    if not business_functions or not calls:
        return []

    test_path = Path(test.file.replace("\\", "/"))
    test_tokens = _tokens(f"{test.file} {test.qualified_name} {' '.join(calls)}")
    scored: list[tuple[tuple[int, int, str, int, str], FunctionChunk]] = []
    for function in business_functions:
        function_path = Path(function.file.replace("\\", "/"))
        function_tokens = _tokens(f"{function.file} {function.qualified_name} {function.name}")
        overlap = len(test_tokens & function_tokens)
        same_stem = int(test_path.stem.lower().replace("test", "") in function_path.stem.lower())
        same_parent_dir = int(bool(test_path.parent.name) and test_path.parent.name == function_path.parent.name)
        score = overlap * 3 + same_stem * 2 + same_parent_dir
        scored.append(((-score, _function_noise_rank(function), function.file, function.start_line, function.chunk_id), function))

    result: list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]] = []
    for _, function in sorted(scored, key=lambda item: item[0])[:max_candidates]:
        result.append((
            ResolvedFunction(function, test.qualified_name, "recall_project_fallback"),
            "project_file_or_name_static_candidate_unverified",
            "low",
        ))
    return result


def _has_assertion_text(test: TestChunk) -> bool:
    return _test_or_helpers_have_assertion(test)


def _business_function_count(functions: Iterable[FunctionChunk]) -> int:
    return sum(1 for function in functions if is_business_function(function))


NO_FUNCTION_DESCRIPTIONS = {
    "no_source_business_functions_available": (
        "No parsed project-local public/business FunctionChunk is available for this project, "
        "so the resolver cannot return a source function."
    ),
    "no_business_call_detected_after_filter": (
        "The test body does not contain a remaining project-business call after filtering "
        "assertion macros, framework calls, local helpers, declarations, and common library noise."
    ),
    "calls_not_resolved_to_source_function_chunks": (
        "The test contains call-like expressions, but static resolution cannot bind them to "
        "parsed source FunctionChunks. Typical causes are macros, templates, receiver-type gaps, "
        "missing headers, or parse gaps."
    ),
    "resolved_calls_are_not_business_functions": (
        "The resolver found calls, but they resolve only to test helpers, framework/vendor code, "
        "or otherwise non-business functions."
    ),
    "business_calls_not_directly_verified": (
        "The resolver found project-business calls, but the current high/medium-confidence rules "
        "cannot prove that the test directly verifies their return value, exception, state, or output."
    ),
    "only_low_confidence_candidates_suppressed": (
        "Only weak static candidates are available. They are suppressed by the selected high-precision "
        "method; build or query a recall_static table if low-confidence candidates are acceptable."
    ),
}


def _no_function_classification(
    test: TestChunk,
    functions: Iterable[FunctionChunk],
    *,
    direct_calls: list[str],
    raw_links: list[ResolvedFunction],
    helper_links: list[ResolvedFunction],
    method: SourceFunctionMappingMethod,
) -> dict[str, Any]:
    raw_business_link_count = sum(1 for link in raw_links if is_business_function(link.function))
    helper_business_link_count = sum(1 for link in helper_links if is_business_function(link.function))
    business_count = _business_function_count(functions)
    has_assertion = _has_assertion_text(test)
    if business_count == 0:
        reason = "no_source_business_functions_available"
        kind = "source_function_parse_or_header_only_gap"
    elif not direct_calls:
        reason = "no_business_call_detected_after_filter"
        kind = "assertion_only_or_compile_time_test" if has_assertion else "runner_or_smoke_test_without_assertion"
    elif not raw_links and not helper_links:
        reason = "calls_not_resolved_to_source_function_chunks"
        kind = "static_resolution_gap"
    elif raw_business_link_count == 0 and helper_business_link_count == 0:
        reason = "resolved_calls_are_not_business_functions"
        kind = "framework_helper_or_dependency_test"
    elif method != "recall_static":
        reason = "business_calls_not_directly_verified"
        kind = "business_call_without_strong_verification_signal"
    else:
        reason = "only_low_confidence_candidates_suppressed"
        kind = "low_confidence_candidate_suppressed"
    return {
        "no_function_reason": reason,
        "no_function_type": kind,
        "no_function_description": NO_FUNCTION_DESCRIPTIONS[reason],
        "unresolved_reason": reason,
        "test_kind": kind,
        "source_business_function_count": business_count,
        "has_assertion": has_assertion,
    }


def _links_for_method(
    test: TestChunk,
    functions: list[FunctionChunk],
    method: SourceFunctionMappingMethod,
) -> list[tuple[ResolvedFunction, str | None, SourceFunctionHitConfidence]]:
    links = resolve_source_function_links(test, functions)
    if method == "static":
        return [(link, None, "high") for link in links]
    if method == "verified_static":
        return [(link, reason, "high") for link, reason in _rank_verified_business_links(test, links)]
    if method in {"verified_static_with_medium", "verified_static_with_low"}:
        high = [(link, reason, "high") for link, reason in _rank_verified_business_links(test, links)]
        high_ids = {link.function.chunk_id for link, _, _ in high}
        expanded_links = resolve_source_function_links(test, functions, include_helpers=True)
        medium = _rank_medium_business_links(
            test,
            expanded_links,
            excluded_function_ids=high_ids,
            include_low=method == "verified_static_with_low",
        )
        return [*high, *medium]
    if method == "recall_static":
        high = [(link, reason, "high") for link, reason in _rank_verified_business_links(test, links)]
        high_ids = {link.function.chunk_id for link, _, _ in high}
        expanded_links = resolve_source_function_links(test, functions, include_helpers=True)
        medium = _rank_medium_business_links(
            test,
            expanded_links,
            excluded_function_ids=high_ids,
            include_low=False,
        )
        medium_ids = {link.function.chunk_id for link, _, _ in medium}
        excluded_ids = high_ids | medium_ids
        low = _rank_medium_business_links(
            test,
            expanded_links,
            excluded_function_ids=excluded_ids,
            include_low=True,
        )
        low_ids = {link.function.chunk_id for link, _, _ in low}
        low.extend(_rank_low_business_links(expanded_links, excluded_function_ids=excluded_ids | low_ids))
        low_ids.update(link.function.chunk_id for link, _, _ in low)
        low.extend(_fallback_low_links(test, functions, excluded_function_ids=excluded_ids | low_ids))
        low_ids.update(link.function.chunk_id for link, _, _ in low)
        low.extend(_last_resort_project_links(test, functions, excluded_function_ids=excluded_ids | low_ids))
        return [*high, *medium, *low]
    raise ValueError(f"Unsupported source-function mapping method: {method}")


def _mapping_status_for_hits(
    hits: list[SourceFunctionMappingHit],
    *,
    method: SourceFunctionMappingMethod,
) -> SourceFunctionMappingStatus:
    if hits:
        if method == "recall_static" and hits[0].confidence == "low":
            return "candidate"
        return "matched"
    if method == "recall_static":
        return "unresolved"
    return "no_match"


def _looks_like_test_file(path: Path) -> bool:
    normalized = path.as_posix().lower()
    parts = set(path.parts[:-1])
    filename = path.name.lower()
    stem = path.stem.lower()
    if parts & {"3rdparty", "thirdparty", "third_party", "external", "extern", "vendor", "vendors", "deps"}:
        return False
    if any(token in normalized for token in ("/gtest/", "/gmock/", "googletest", "googlemock")):
        return False
    framework_or_runner_files = {
        "catch.hpp",
        "catch2.hpp",
        "gtest.h",
        "gtest-all.cc",
        "gtest_main.cc",
        "gmock.h",
        "gmock-all.cc",
        "gmock_main.cc",
    }
    if filename in framework_or_runner_files:
        return False
    if normalized.endswith(("/catch.hpp", "/catch2/catch.hpp", "/gtest/gtest.h", "/gmock/gmock.h")):
        return False
    return (
        bool(parts & {"test", "tests", "public_test", "public_tests", "spec", "specs"})
        or "test" in filename
        or stem in {"main_test", "test", "tests"}
        or normalized.endswith(("/run_tests.cpp", "/run_tests.cc", "/run_tests.cxx"))
    )


def _load_source_tests_for_scope(
    *,
    source_dir: Path | None,
    project: str,
    language: str,
    scope: SourceFunctionTestScope,
) -> tuple[list[TestChunk], list[str]]:
    if source_dir is None:
        return [], ["missing_source_project"]

    from .parsing import extract_tests

    if scope == "public":
        files = public_test_files(source_dir, language)
    elif scope == "all":
        files = [
            path
            for path in iter_language_files(source_dir, language, include_tests=True)
            if _looks_like_test_file(path.relative_to(source_dir))
        ]
    else:
        raise ValueError(f"Unsupported source-test scope: {scope}")

    tests: list[TestChunk] = []
    errors: list[str] = []
    seen: set[str] = set()
    for path in files:
        try:
            for test in extract_tests(path, source_dir, project, language):
                if test.chunk_id in seen:
                    continue
                tests.append(test)
                seen.add(test.chunk_id)
        except Exception as exc:
            errors.append(f"source_test_parse_error:{path.name}:{type(exc).__name__}:{exc}")
    return tests, errors


def resolve_source_function_mapping(
    *,
    pair: str,
    test: TestChunk,
    functions: list[FunctionChunk],
    method: SourceFunctionMappingMethod = "verified_static",
) -> SourceFunctionMappingRecord:
    ranked = _links_for_method(test, functions, method)
    hits = [
        SourceFunctionMappingHit.from_link(
            link,
            rank=rank,
            verification_reason=reason,
            confidence=confidence,
        )
        for rank, (link, reason, confidence) in enumerate(ranked, start=1)
    ]
    raw_links = resolve_source_function_links(test, functions)
    helper_links = resolve_source_function_links(test, functions, include_helpers=True)
    calls = direct_call_targets(test)
    matched_calls = {hit.matched_call for hit in hits}
    raw_business_link_count = sum(
        1 for link in raw_links if is_business_function(link.function)
    )
    status = _mapping_status_for_hits(hits, method=method)
    no_function = (
        _no_function_classification(
            test,
            functions,
            direct_calls=calls,
            raw_links=raw_links,
            helper_links=helper_links,
            method=method,
        )
        if not hits
        else {}
    )
    return SourceFunctionMappingRecord(
        schema_version=1,
        pair=pair,
        project=test.project,
        source_test_id=test.chunk_id,
        source_test_nodeid=source_test_nodeid(test),
        source_test_file=test.file,
        source_test_name=test.qualified_name,
        source_test_language=test.language,
        source_test_framework=test.framework,
        resolver_method=method,
        status=status,
        direct_calls=calls,
        source_functions=hits,
        diagnostics={
            "resolver_chain": (
                ["static_direct_call_resolution"]
                if method == "static"
                else [
                    "static_direct_call_resolution",
                    "business_function_filter",
                    "test_verification_filter",
                    "verified_function_ranking",
                    *(["recall_first_low_candidate_fallback"] if method == "recall_static" else []),
                ]
            ),
            "raw_resolved_link_count": len(raw_links),
            "raw_business_link_count": raw_business_link_count,
            "expanded_resolved_link_count": len(helper_links),
            "confidence_counts": dict(sorted(Counter(hit.confidence for hit in hits).items())),
            "matched_call_count": len(matched_calls),
            **no_function,
        },
    )


def build_source_function_mapping_records(
    layout: PairLayout,
    *,
    projects: list[str] | None = None,
    method: SourceFunctionMappingMethod = "verified_static",
    test_scope: SourceFunctionTestScope = "all",
    project_limit: int | None = None,
    limit_per_project: int | None = None,
) -> tuple[list[SourceFunctionMappingRecord], dict[str, Any]]:
    from .repository import load_project

    started = time.perf_counter()
    selected = projects or [item.project for item in layout.projects()]
    if project_limit is not None:
        selected = selected[:project_limit]

    records: list[SourceFunctionMappingRecord] = []
    project_rows: list[dict[str, Any]] = []
    for project in selected:
        data = load_project(layout, project)
        tests, test_errors = _load_source_tests_for_scope(
            source_dir=data.paths.source_dir,
            project=project,
            language=layout.pair.source,
            scope=test_scope,
        )
        tests = [test for test in tests if test.language == layout.pair.source]
        if limit_per_project is not None:
            tests = tests[:limit_per_project]
        before = len(records)
        for test in tests:
            records.append(
                resolve_source_function_mapping(
                    pair=layout.pair.name,
                    test=test,
                    functions=data.source_functions,
                    method=method,
                )
            )
        project_rows.append(
            {
                "project": project,
                "source_tests": len(tests),
                "mapping_records": len(records) - before,
                "source_functions": len(data.source_functions),
                "test_scope": test_scope,
                "errors": data.errors + test_errors,
            }
        )

    report = summarize_source_function_mapping(records)
    report.update(
        {
            "pair": layout.pair.name,
            "resolver_method": method,
            "test_scope": test_scope,
            "project_count": len(selected),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "projects": project_rows,
        }
    )
    return records, report


def write_source_function_mapping(
    records: Iterable[SourceFunctionMappingRecord],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def load_source_function_mapping(
    path: str | Path,
) -> list[SourceFunctionMappingRecord]:
    records: list[SourceFunctionMappingRecord] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(SourceFunctionMappingRecord.from_dict(json.loads(line)))
    return records


def summarize_source_function_mapping(
    records: Iterable[SourceFunctionMappingRecord],
) -> dict[str, Any]:
    records = list(records)
    status_counts = Counter(record.status for record in records)
    method_counts = Counter(record.resolver_method for record in records)
    return {
        "schema_version": 1,
        "query_unit": "source_test_to_business_function",
        "mapping_record_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "method_counts": dict(sorted(method_counts.items())),
        "resolved_source_function_link_count": sum(
            len(record.source_functions) for record in records
        ),
        "matched_source_test_count": status_counts.get("matched", 0),
        "candidate_source_test_count": status_counts.get("candidate", 0),
        "unresolved_source_test_count": status_counts.get("unresolved", 0),
        "no_match_source_test_count": status_counts.get("no_match", 0),
        "query_success_source_test_count": status_counts.get("matched", 0) + status_counts.get("candidate", 0),
        "query_success_rate": (
            (status_counts.get("matched", 0) + status_counts.get("candidate", 0)) / len(records)
            if records
            else 0.0
        ),
        "top_confidence_counts": dict(sorted(Counter(
            record.source_functions[0].confidence
            for record in records
            if record.source_functions
        ).items())),
        "unresolved_reason_counts": dict(sorted(Counter(
            record.diagnostics.get("unresolved_reason", "")
            for record in records
            if record.status == "unresolved"
        ).items())),
        "unresolved_test_kind_counts": dict(sorted(Counter(
            record.diagnostics.get("test_kind", "")
            for record in records
            if record.status == "unresolved"
        ).items())),
        "no_function_reason_counts": dict(sorted(Counter(
            record.diagnostics.get("no_function_reason", "")
            for record in records
            if not record.source_functions
        ).items())),
        "no_function_type_counts": dict(sorted(Counter(
            record.diagnostics.get("no_function_type", "")
            for record in records
            if not record.source_functions
        ).items())),
    }


def query_source_function_mapping(
    records: Iterable[SourceFunctionMappingRecord],
    selector: str,
    *,
    project: str | None = None,
) -> list[SourceFunctionMappingRecord]:
    selected: list[SourceFunctionMappingRecord] = []
    for record in records:
        if project is not None and record.project != project:
            continue
        if selector in source_test_selector_values(record):
            selected.append(record)
    return selected


def lookup_mapped_source_functions(
    source_test: str,
    *,
    mapping: str | Path | Iterable[SourceFunctionMappingRecord] = DEFAULT_SOURCE_FUNCTION_MAPPING_PATH,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Return mapped source functions for one Source test.

    Parameters
    ----------
    source_test:
        Source test selector. Supported forms are the same as the CLI:
        ``source_test_id``, ``source_test_nodeid``, ``source_test_name``,
        ``source_test_file``, or ``source_test_file::source_test_name``.
    mapping:
        JSONL mapping table path, or already-loaded mapping records.
    project:
        Optional project name used to disambiguate duplicate test names.

    Returns
    -------
    list[dict]
        Ranked function records. Returns an empty list when the test is present
        in the table but has ``status == "no_match"``.
    """

    records = (
        load_source_function_mapping(mapping)
        if isinstance(mapping, (str, Path))
        else list(mapping)
    )
    matches = query_source_function_mapping(records, source_test, project=project)
    if not matches:
        raise KeyError(f"Source test not found in mapping table: {source_test}")
    if len(matches) > 1:
        choices = ", ".join(
            f"{record.project}:{record.source_test_nodeid}" for record in matches
        )
        raise ValueError(f"Ambiguous source test; pass project: {choices}")
    return [function.to_dict() for function in matches[0].source_functions]


def lookup_best_mapped_source_function(
    source_test: str,
    *,
    mapping: str | Path | Iterable[SourceFunctionMappingRecord] = DEFAULT_SOURCE_FUNCTION_MAPPING_PATH,
    project: str | None = None,
) -> dict[str, Any] | None:
    """Return the top mapped source function for one Source test.

    Returns ``None`` only when the test exists in the table but remains
    unresolved.  In a recall-first table this may be a low-confidence candidate;
    callers should inspect the returned ``confidence`` field.
    """

    functions = lookup_mapped_source_functions(
        source_test,
        mapping=mapping,
        project=project,
    )
    return functions[0] if functions else None


def lookup_source_function_mapping_record(
    source_test: str,
    *,
    mapping: str | Path | Iterable[SourceFunctionMappingRecord] = DEFAULT_SOURCE_FUNCTION_MAPPING_PATH,
    project: str | None = None,
) -> dict[str, Any]:
    """Return the complete mapping record for one Source test."""

    records = (
        load_source_function_mapping(mapping)
        if isinstance(mapping, (str, Path))
        else list(mapping)
    )
    matches = query_source_function_mapping(records, source_test, project=project)
    if not matches:
        raise KeyError(f"Source test not found in mapping table: {source_test}")
    if len(matches) > 1:
        choices = ", ".join(
            f"{record.project}:{record.source_test_nodeid}" for record in matches
        )
        raise ValueError(f"Ambiguous source test; pass project: {choices}")
    return matches[0].to_dict()


def _no_function_payload(record: SourceFunctionMappingRecord) -> dict[str, Any] | None:
    if record.source_functions:
        return None
    reason = record.diagnostics.get("no_function_reason") or record.diagnostics.get("unresolved_reason") or "unknown"
    test_type = record.diagnostics.get("no_function_type") or record.diagnostics.get("test_kind") or "unknown"
    return {
        "type": test_type,
        "reason": reason,
        "description": record.diagnostics.get("no_function_description", ""),
        "has_assertion": record.diagnostics.get("has_assertion"),
        "direct_calls": list(record.direct_calls),
        "raw_resolved_link_count": record.diagnostics.get("raw_resolved_link_count"),
        "raw_business_link_count": record.diagnostics.get("raw_business_link_count"),
        "expanded_resolved_link_count": record.diagnostics.get("expanded_resolved_link_count"),
        "source_business_function_count": record.diagnostics.get("source_business_function_count"),
    }


def lookup_source_function_mapping_result(
    source_test: str,
    *,
    mapping: str | Path | Iterable[SourceFunctionMappingRecord] = DEFAULT_SOURCE_FUNCTION_MAPPING_PATH,
    project: str | None = None,
) -> dict[str, Any]:
    """Return a complete function-or-no-function query result for one Source test.

    This is the safest user-facing API for table lookup.  It always returns a
    structured result when the Source test exists in the mapping table:

    * ``has_function == True``: inspect ``source_functions``.
    * ``has_function == False``: inspect ``no_function`` for test type and reason.
    """

    record = lookup_source_function_mapping_record(
        source_test,
        mapping=mapping,
        project=project,
    )
    source_functions = list(record.get("source_functions") or [])
    return {
        "project": record["project"],
        "source_test_id": record["source_test_id"],
        "source_test_nodeid": record["source_test_nodeid"],
        "source_test_file": record["source_test_file"],
        "source_test_name": record["source_test_name"],
        "source_test_framework": record["source_test_framework"],
        "resolver_method": record["resolver_method"],
        "status": record["status"],
        "has_function": bool(source_functions),
        "source_functions": source_functions,
        "no_function": _no_function_payload(SourceFunctionMappingRecord.from_dict(record)),
        "diagnostics": record.get("diagnostics", {}),
    }


def _mapping_key(record: SourceFunctionMappingRecord) -> tuple[str, str]:
    return (record.project, record.source_test_id)


def evaluate_source_function_mapping(
    *,
    mapping_records: list[SourceFunctionMappingRecord],
    gold_records: list[SourceFunctionGoldRecord],
    mapping_path: str,
    gold_path: str,
    project: str | None = None,
) -> tuple[SourceFunctionMappingEvaluationSummary, list[dict[str, Any]]]:
    started = time.perf_counter()
    if project is not None:
        mapping_records = [
            record for record in mapping_records if record.project == project
        ]
        gold_records = [
            record for record in gold_records if record.project == project
        ]
    mapping_by_key = {_mapping_key(record): record for record in mapping_records}
    gold_status_counts = Counter(record.status for record in gold_records)
    method_counts = Counter(record.resolver_method for record in mapping_records)

    matched_records = [
        record
        for record in gold_records
        if record.status == "matched" and evaluable_expected_function_ids(record)
    ]
    no_match_records = [
        record for record in gold_records if record.status == "no_match"
    ]

    hit_counts = {1: 0, 3: 0, 5: 0}
    recall_sums = {1: 0.0, 3: 0.0, 5: 0.0}
    reciprocal_sum = 0.0
    empty_predictions = 0
    missing_mapping = 0
    rows: list[dict[str, Any]] = []

    def retrieved_ids(record: SourceFunctionGoldRecord) -> tuple[list[str], SourceFunctionMappingRecord | None]:
        mapping = mapping_by_key.get((record.project, record.source_test_id))
        if mapping is None:
            return [], None
        return [hit.chunk_id for hit in mapping.source_functions], mapping

    for record in matched_records:
        retrieved, mapping = retrieved_ids(record)
        if mapping is None:
            missing_mapping += 1
        if not retrieved:
            empty_predictions += 1
        gold = set(evaluable_expected_function_ids(record))
        ranks = [
            rank
            for rank, chunk_id in enumerate(retrieved, start=1)
            if chunk_id in gold
        ]
        first_rank = min(ranks) if ranks else None
        if first_rank is not None:
            reciprocal_sum += 1.0 / first_rank
        for cutoff in (1, 3, 5):
            relevant = len(set(retrieved[:cutoff]) & gold)
            hit_counts[cutoff] += int(relevant > 0)
            recall_sums[cutoff] += relevant / max(1, len(gold))
        rows.append(
            {
                "project": record.project,
                "source_test_id": record.source_test_id,
                "source_test_name": record.source_test_name,
                "gold_status": record.status,
                "mapping_status": mapping.status if mapping else "missing_mapping",
                "gold_function_ids": sorted(gold),
                "retrieved_function_ids": retrieved,
                "first_relevant_rank": first_rank,
            }
        )

    no_match_correct = 0
    for record in no_match_records:
        retrieved, mapping = retrieved_ids(record)
        if mapping is None:
            missing_mapping += 1
        is_correct_empty = mapping is not None and len(retrieved) == 0
        no_match_correct += int(is_correct_empty)
        rows.append(
            {
                "project": record.project,
                "source_test_id": record.source_test_id,
                "source_test_name": record.source_test_name,
                "gold_status": record.status,
                "mapping_status": mapping.status if mapping else "missing_mapping",
                "gold_function_ids": [],
                "retrieved_function_ids": retrieved,
                "first_relevant_rank": None,
                "no_match_correct": is_correct_empty,
            }
        )

    matched_denominator = max(1, len(matched_records))
    reviewed_denominator = len(matched_records) + len(no_match_records)
    reviewed_correct = hit_counts[1] + no_match_correct
    summary = SourceFunctionMappingEvaluationSummary(
        query_unit="source_test_to_business_function",
        mapping=mapping_path,
        gold=gold_path,
        method_counts=dict(sorted(method_counts.items())),
        mapping_record_count=len(mapping_records),
        gold_record_count=len(gold_records),
        gold_status_counts=dict(sorted(gold_status_counts.items())),
        evaluable_matched_gold_count=len(matched_records),
        evaluable_no_match_gold_count=len(no_match_records),
        reviewed_evaluable_gold_count=reviewed_denominator,
        missing_mapping_record_count=missing_mapping,
        empty_prediction_count=empty_predictions,
        no_match_correct_count=no_match_correct,
        overall_accuracy_at_1_including_no_match=(
            reviewed_correct / reviewed_denominator
            if reviewed_denominator
            else 0.0
        ),
        hit_rate_at_1=hit_counts[1] / matched_denominator,
        hit_rate_at_3=hit_counts[3] / matched_denominator,
        hit_rate_at_5=hit_counts[5] / matched_denominator,
        macro_recall_at_1=recall_sums[1] / matched_denominator,
        macro_recall_at_3=recall_sums[3] / matched_denominator,
        macro_recall_at_5=recall_sums[5] / matched_denominator,
        mrr=reciprocal_sum / matched_denominator,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )
    return summary, rows


class SourceFunctionMappingAPI:
    """Read-only query API over a generated source-test -> source-function table."""

    def __init__(self, records: list[SourceFunctionMappingRecord]) -> None:
        self.records = records

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SourceFunctionMappingAPI":
        return cls(load_source_function_mapping(path))

    def lookup(
        self,
        source_test: str,
        *,
        project: str | None = None,
        allow_many: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        matches = query_source_function_mapping(
            self.records,
            source_test,
            project=project,
        )
        if not matches:
            raise KeyError(f"Source test not found in mapping table: {source_test}")
        if len(matches) > 1 and not allow_many:
            choices = ", ".join(
                f"{record.project}:{record.source_test_nodeid}"
                for record in matches
            )
            raise ValueError(f"Ambiguous source test; pass project or allow_many: {choices}")
        payload = [record.to_dict() for record in matches]
        return payload if allow_many else payload[0]

    def lookup_functions(
        self,
        source_test: str,
        *,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return only the mapped function list for one Source test."""

        return lookup_mapped_source_functions(
            source_test,
            mapping=self.records,
            project=project,
        )

    def lookup_best_function(
        self,
        source_test: str,
        *,
        project: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the top mapped function, or None for unresolved rows."""

        return lookup_best_mapped_source_function(
            source_test,
            mapping=self.records,
            project=project,
        )

    def lookup_result(
        self,
        source_test: str,
        *,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Return functions when available, otherwise a no-function classification."""

        return lookup_source_function_mapping_result(
            source_test,
            mapping=self.records,
            project=project,
        )
