from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Literal

from .models import FunctionChunk, TestChunk
from .static_resolution import (
    ResolvedFunction,
    direct_call_targets,
    resolve_source_function_links,
)


GoldStatus = Literal["matched", "no_match", "uncertain"]
GoldRelation = Literal[
    "direct_business_api",
    "secondary_business_api",
    "setup_api",
    "inspection_api",
    "helper_internal",
    "coverage_only",
    "not_business_api",
]

EVALUABLE_GOLD_RELATIONS = {
    "direct_business_api",
    "secondary_business_api",
}

ASSERTION_TOKENS = (
    "assert",
    "ASSERT_",
    "EXPECT_",
    "REQUIRE",
    "CHECK",
    "BOOST_CHECK",
    "BOOST_REQUIRE",
    "pytest.raises",
)

BUSINESS_EXCLUDED_PREFIXES = (
    "std::",
    "std.",
    "boost::",
    "boost.",
    "testing::",
    "testing.",
    "absl::",
    "absl.",
    "gtest_",
    "gmock_",
)

BUSINESS_EXCLUDED_NAMES = {
    "main",
    "TestBody",
    "SetUp",
    "TearDown",
    "SetUpTestSuite",
    "TearDownTestSuite",
}

VENDOR_OR_FRAMEWORK_PATH_PARTS = {
    "3rdparty",
    "thirdparty",
    "third_party",
    "external",
    "extern",
    "vendor",
    "vendors",
    "deps",
}

FRAMEWORK_PATH_TOKENS = {
    "gtest",
    "gmock",
    "googletest",
    "googlemock",
}


@dataclass(frozen=True)
class GoldFunctionRef:
    chunk_id: str
    file: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    matched_call: str
    resolution_reason: str
    verification_reason: str
    relation: GoldRelation = "direct_business_api"
    evidence: str = ""

    @classmethod
    def from_link(
        cls,
        link: ResolvedFunction,
        *,
        verification_reason: str,
        relation: GoldRelation = "direct_business_api",
        evidence: str = "",
    ) -> "GoldFunctionRef":
        function = link.function
        return cls(
            chunk_id=function.chunk_id,
            file=function.file,
            name=function.name,
            qualified_name=function.qualified_name,
            start_line=function.start_line,
            end_line=function.end_line,
            matched_call=link.call,
            resolution_reason=link.reason,
            verification_reason=verification_reason,
            relation=relation,
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceFunctionGoldRecord:
    schema_version: int
    pair: str
    project: str
    source_test_id: str
    source_test_file: str
    source_test_name: str
    status: GoldStatus
    expected_function_ids: list[str]
    expected_functions: list[GoldFunctionRef] = field(default_factory=list)
    confidence: str = "none"
    annotation_method: str = "direct_verified_public_api"
    evidence: dict[str, Any] = field(default_factory=dict)
    excluded_calls: list[dict[str, str]] = field(default_factory=list)
    reviewed: bool = False
    review_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["expected_functions"] = [item.to_dict() for item in self.expected_functions]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceFunctionGoldRecord":
        value = dict(value)
        value["expected_functions"] = [
            GoldFunctionRef(**item)
            for item in value.get("expected_functions", [])
        ]
        return cls(**value)


def evaluable_expected_function_ids(record: SourceFunctionGoldRecord) -> list[str]:
    """Return function ids that count toward default accuracy metrics.

    Manual gold may keep setup/inspection/helper calls for auditability, but
    only direct or secondary public/business APIs should be considered correct
    answers for source-test -> business-function evaluation.
    """

    if record.expected_functions:
        return [
            function.chunk_id
            for function in record.expected_functions
            if function.relation in EVALUABLE_GOLD_RELATIONS
        ]
    return list(record.expected_function_ids)


def _path_is_test(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = set(PurePosixPath(normalized).parts)
    filename = PurePosixPath(normalized).name
    return (
        bool(parts & {"test", "tests", "public_test", "public_tests", "spec", "specs"})
        or "public_test" in filename
        or "test_public" in filename
        or filename.startswith("test_")
        or filename.endswith("_test.cpp")
        or filename.endswith("_test.cxx")
        or filename.endswith("_test.py")
    )


def is_business_function(function: FunctionChunk) -> bool:
    qualified = function.qualified_name.replace(".", "::")
    if function.name in BUSINESS_EXCLUDED_NAMES or function.name.startswith("~"):
        return False
    if qualified.startswith(BUSINESS_EXCLUDED_PREFIXES):
        return False
    normalized_file = function.file.replace("\\", "/").lower()
    path_parts = set(PurePosixPath(normalized_file).parts)
    if path_parts & VENDOR_OR_FRAMEWORK_PATH_PARTS:
        return False
    if any(token in normalized_file for token in FRAMEWORK_PATH_TOKENS):
        return False
    if _path_is_test(function.file):
        return False
    if normalized_file.endswith(("json.hpp", "json.h")) and function.name in {"size", "empty", "begin", "end"}:
        return False
    return True


def _base_call_name(value: str) -> str:
    value = re.sub(r"<[^<>]*>", "", value)
    value = value.replace("::", ".")
    value = re.sub(r"[^A-Za-z0-9_.~]", "", value).strip(".")
    return value.rsplit(".", 1)[-1]


def _has_assertion(line: str) -> bool:
    stripped = line.strip()
    return any(token in stripped for token in ASSERTION_TOKENS)


def _call_inside_assertion_line(line: str, call: str) -> bool:
    if not _line_has_call(line, call):
        return False
    call_match = re.search(rf"(?<![A-Za-z0-9_~]){re.escape(_base_call_name(call))}\s*(?:<[^;{{}}]*>)?\s*\(", line)
    if call_match is None:
        return False
    assertion_positions = [line.find(token) for token in ASSERTION_TOKENS if token in line]
    assertion_positions = [position for position in assertion_positions if position >= 0]
    if not assertion_positions:
        return False
    return min(assertion_positions) <= call_match.start()


def _line_has_call(line: str, call: str) -> bool:
    base = _base_call_name(call)
    if not base:
        return False
    return re.search(rf"(?<![A-Za-z0-9_~]){re.escape(base)}\s*(?:<[^;{{}}]*>)?\s*\(", line) is not None


def _assigned_variables(lines: list[str], call: str) -> set[str]:
    variables: set[str] = set()
    base = re.escape(_base_call_name(call))
    if not base:
        return variables
    pattern = re.compile(
        rf"(?:^\s*|[;{{]\s*)"
        rf"(?:[A-Za-z_][A-Za-z0-9_:<>,\s]*(?:\s*[*&])*\s+)?"
        rf"(?:[*&]\s*)?"
        rf"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*[^;]*"
        rf"(?<![A-Za-z0-9_~]){base}\s*(?:<[^;{{}}]*>)?\s*\("
    )
    for line in lines:
        match = pattern.search(line)
        if match:
            variables.add(match.group("var"))
    return variables


def _constructed_variables(lines: list[str], call: str) -> set[str]:
    variables: set[str] = set()
    base = re.escape(_base_call_name(call))
    if not base:
        return variables
    pattern = re.compile(
        rf"(?:^\s*|[;{{]\s*)"
        rf"(?:[A-Za-z_][A-Za-z0-9_:<>]*::)*{base}(?:\s*<[^;{{}}=()]*>)?"
        rf"\s*(?:[*&]\s*)?"
        rf"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?=\(|\{{|=)"
    )
    for line in lines:
        match = pattern.search(line)
        if match:
            variables.add(match.group("var"))
    return variables


def _variable_asserted(lines: list[str], variable: str) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(variable)}(?![A-Za-z0-9_])")
    return any(_has_assertion(line) and pattern.search(line) for line in lines)


def _derived_variables(lines: list[str], variable: str) -> set[str]:
    """Return local variables whose value is derived from ``variable``.

    This catches common C++ test patterns such as:

    ``std::string str(array); EXPECT_EQ(str.length(), 26);``

    and:

    ``auto it = table.begin(); ASSERT_TRUE(!it->first.empty());``
    """

    escaped = re.escape(variable)
    derived: set[str] = set()
    for line in lines:
        assignment = re.search(
            rf"(?:^\s*|[;{{]\s*)(?:[A-Za-z_][A-Za-z0-9_:<>,\s]*\s+)?(?:[*&]\s*)?"
            rf"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*[^;]*"
            rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])",
            line,
        )
        if assignment and assignment.group("var") != variable:
            derived.add(assignment.group("var"))
        construction = re.search(
            rf"(?:^\s*|[;{{]\s*)(?:[A-Za-z_][A-Za-z0-9_:<>,\s]*\s+)"
            rf"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*"
            rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])",
            line,
        )
        if construction and construction.group("var") != variable:
            derived.add(construction.group("var"))
    return derived


def _variable_or_derived_asserted(lines: list[str], variable: str) -> bool:
    queue = [variable]
    seen = {variable}
    while queue:
        current = queue.pop(0)
        if _variable_asserted(lines, current):
            return True
        for derived in _derived_variables(lines, current):
            if derived not in seen:
                seen.add(derived)
                queue.append(derived)
    return False


def _call_line_indexes(lines: list[str], call: str) -> list[int]:
    return [index for index, line in enumerate(lines) if _line_has_call(line, call)]


def _call_receiver_variable(line: str, call: str) -> str | None:
    base = re.escape(_base_call_name(call))
    match = re.search(
        rf"(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\s*(?:->|\.)\s*{base}\s*(?:<[^;{{}}]*>)?\s*\(",
        line,
    )
    return match.group("receiver") if match else None


def _call_argument_variables(line: str, call: str) -> set[str]:
    base = re.escape(_base_call_name(call))
    match = re.search(rf"{base}\s*(?:<[^;{{}}]*>)?\s*\((?P<args>[^;]*)\)", line)
    if not match:
        return set()
    args = match.group("args")
    # If the argument list contains a lambda with an internal semicolon, the
    # conservative regex above stops early.  Include the rest of the physical
    # line as a fallback so calls like ``submit(lambda, std::ref(output))`` can
    # still associate the output variable with the call.
    args += " " + line[match.end():]
    return {
        value
        for value in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", args)
        if value
        not in {
            "true",
            "false",
            "nullptr",
            "NULL",
            "std",
            "string",
            "vector",
            "move",
            "make_unique",
        }
    }


def _constructor_argument_variables(line: str, call: str) -> set[str]:
    base = re.escape(_base_call_name(call))
    match = re.search(
        rf"(?:[A-Za-z_][A-Za-z0-9_:<>]*::)*{base}(?:\s*<[^;{{}}=()]*>)?"
        rf"\s*(?:[*&]\s*)?[A-Za-z_][A-Za-z0-9_]*\s*\((?P<args>[^;]*)\)",
        line,
    )
    if not match:
        return set()
    return {
        value
        for value in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", match.group("args"))
        if value not in {"true", "false", "nullptr", "NULL", "std", "string", "vector", "move"}
    }


def _call_effect_asserted(lines: list[str], call: str) -> bool:
    """Return true when a call's receiver or output argument is later asserted.

    Many C/C++ APIs are verified through side effects rather than by asserting
    the call expression itself, for example:

    ``base64_encode(out, src, n); EXPECT_STREQ(out, "eHk=");``

    or:

    ``cache.put(k, v); EXPECT_EQ(cache.get(k), v);``
    """

    for index in _call_line_indexes(lines, call):
        line = lines[index]
        variables = _call_argument_variables(line, call)
        receiver = _call_receiver_variable(line, call)
        if receiver:
            variables.add(receiver)
        if _exception_flag_asserted(lines, index):
            return True
        if not variables:
            continue
        later_lines = lines[index + 1:]
        for variable in variables:
            if _variable_or_derived_asserted(later_lines, variable):
                return True
        if receiver and _receiver_callback_output_asserted(lines, index, receiver):
            return True
    return False


def _receiver_callback_output_asserted(lines: list[str], call_index: int, receiver: str) -> bool:
    """Detect callback-output tests around an object receiver.

    Example:

    ``Process process(..., [&](...) { output.append(...); });``
    ``process.write(input); ... ASSERT_EQ(output, expected);``
    """

    before = lines[: call_index + 1]
    if not any(re.search(rf"\b{re.escape(receiver)}\b", line) for line in before):
        return False
    mutated: set[str] = set()
    for line in before:
        mutated.update(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(?:append|push_back|assign)\s*\(", line))
        for capture in re.findall(r"\[([^\]]+)\]", line):
            mutated.update(
                item
                for item in re.findall(r"&\s*([A-Za-z_][A-Za-z0-9_]*)", capture)
                if item != receiver
            )
    later_lines = lines[call_index + 1:]
    return any(_variable_or_derived_asserted(later_lines, variable) for variable in mutated)


def _exception_flag_asserted(lines: list[str], call_index: int) -> bool:
    """Detect tests that assert a flag set in the catch block after a call."""

    flags: set[str] = set()
    in_catch = False
    for line in lines[call_index:]:
        if "catch" in line:
            in_catch = True
        if in_catch:
            flags.update(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*true\b", line))
    later_lines = lines[call_index:]
    return any(_variable_asserted(later_lines, flag) for flag in flags)


def _return_value_variable_asserted(lines: list[str], call: str) -> bool:
    for variable in _assigned_variables(lines, call):
        if _variable_or_derived_asserted(lines, variable):
            return True
    return False


def _constructed_object_asserted(lines: list[str], call: str) -> bool:
    for variable in _constructed_variables(lines, call):
        if _variable_or_derived_asserted(lines, variable):
            return True
    for index, line in enumerate(lines):
        for variable in _constructor_argument_variables(line, call):
            if _variable_or_derived_asserted(lines[index + 1:], variable):
                return True
    return False


def _raises_context_verifies(lines: list[str], call: str) -> bool:
    for index, line in enumerate(lines):
        if not _line_has_call(line, call):
            continue
        window = "\n".join(lines[max(0, index - 2): index + 1])
        if "raises" in window or "EXPECT_THROW" in window or "ASSERT_THROW" in window:
            return True
    return False


def verification_reason(
    test: TestChunk,
    call: str,
    *,
    business_link_count: int,
    include_helpers: bool = False,
) -> str | None:
    lines = test.code.splitlines()
    if include_helpers:
        for helper in test.helpers:
            lines.extend(helper.splitlines())
    for line in lines:
        if _call_inside_assertion_line(line, call):
            return "direct_call_inside_assertion"
    if _raises_context_verifies(lines, call):
        return "direct_call_inside_exception_assertion"
    assigned = _assigned_variables(lines, call)
    if any(_variable_or_derived_asserted(lines, variable) for variable in assigned):
        return "call_result_assigned_then_asserted"
    if _constructed_object_asserted(lines, call):
        return "constructed_object_state_asserted"
    if _call_effect_asserted(lines, call):
        return "call_effect_or_output_asserted"
    if business_link_count == 1 and _call_line_indexes(lines, call) and any(_has_assertion(line) for line in lines):
        return "single_business_call_in_asserting_test"
    return None


def _resolved_links(
    test: TestChunk,
    functions: Iterable[FunctionChunk],
) -> tuple[list[ResolvedFunction], str]:
    return resolve_source_function_links(test, list(functions)), "static_direct_call"


def propose_source_function_gold(
    *,
    pair: str,
    test: TestChunk,
    functions: Iterable[FunctionChunk],
) -> SourceFunctionGoldRecord:
    links, source = _resolved_links(
        test,
        functions,
    )
    business_links: list[ResolvedFunction] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in links:
        if link.function.chunk_id in seen:
            continue
        seen.add(link.function.chunk_id)
        if is_business_function(link.function):
            business_links.append(link)
        else:
            excluded.append({"call": link.call, "reason": "non_business_or_test_function"})

    if not business_links:
        return SourceFunctionGoldRecord(
            schema_version=1,
            pair=pair,
            project=test.project,
            source_test_id=test.chunk_id,
            source_test_file=test.file,
            source_test_name=test.qualified_name,
            status="no_match",
            expected_function_ids=[],
            confidence="none",
            evidence={
                "resolver_source": source,
                "direct_calls": direct_call_targets(test),
                "reason": "no_resolved_business_function",
            },
            excluded_calls=excluded,
        )

    expected: list[GoldFunctionRef] = []
    for link in business_links:
        reason = verification_reason(test, link.call, business_link_count=len(business_links))
        if reason:
            expected.append(GoldFunctionRef.from_link(link, verification_reason=reason))
        else:
            excluded.append({"call": link.call, "reason": "not_directly_verified_by_test_assertion"})

    if not expected:
        if not any(_has_assertion(line) for line in test.code.splitlines()):
            return SourceFunctionGoldRecord(
                schema_version=1,
                pair=pair,
                project=test.project,
                source_test_id=test.chunk_id,
                source_test_file=test.file,
                source_test_name=test.qualified_name,
                status="no_match",
                expected_function_ids=[],
                confidence="none",
                evidence={
                    "resolver_source": source,
                    "direct_calls": direct_call_targets(test),
                    "resolved_business_function_ids": [link.function.chunk_id for link in business_links],
                    "reason": "business_calls_without_verifying_assertion",
                },
                excluded_calls=excluded,
            )
        return SourceFunctionGoldRecord(
            schema_version=1,
            pair=pair,
            project=test.project,
            source_test_id=test.chunk_id,
            source_test_file=test.file,
            source_test_name=test.qualified_name,
            status="uncertain",
            expected_function_ids=[],
            confidence="low",
            evidence={
                "resolver_source": source,
                "direct_calls": direct_call_targets(test),
                "resolved_business_function_ids": [link.function.chunk_id for link in business_links],
                "reason": "business_calls_resolved_but_not_directly_verified",
            },
            excluded_calls=excluded,
        )

    confidence = "high"
    if any(item.verification_reason == "single_business_call_in_asserting_test" for item in expected):
        confidence = "medium"
    return SourceFunctionGoldRecord(
        schema_version=1,
        pair=pair,
        project=test.project,
        source_test_id=test.chunk_id,
        source_test_file=test.file,
        source_test_name=test.qualified_name,
        status="matched",
        expected_function_ids=[item.chunk_id for item in expected],
        expected_functions=expected,
        confidence=confidence,
        evidence={
            "resolver_source": source,
            "direct_calls": direct_call_targets(test),
            "criterion": "directly_verified_public_or_business_api",
        },
        excluded_calls=excluded,
    )


def write_source_function_gold(records: Iterable[SourceFunctionGoldRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record.to_dict(), ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def load_source_function_gold(path: Path | str) -> list[SourceFunctionGoldRecord]:
    records: list[SourceFunctionGoldRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(SourceFunctionGoldRecord.from_dict(json.loads(line)))
    return records


def summarize_source_function_gold(records: Iterable[SourceFunctionGoldRecord]) -> dict[str, Any]:
    rows = list(records)
    status_counts = Counter(record.status for record in rows)
    confidence_counts = Counter(record.confidence for record in rows)
    return {
        "records": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "matched_records": status_counts.get("matched", 0),
        "expected_function_links": sum(len(record.expected_function_ids) for record in rows),
    }
