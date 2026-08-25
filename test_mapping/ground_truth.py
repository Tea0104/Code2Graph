from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Literal

from .alignment import normalized_file, normalized_name
from .dataset import PairLayout
from .models import TestChunk
from .repository import load_project


LabelStatus = Literal["matched", "no_match", "unresolved"]

_STRING_LITERAL = re.compile(r'''["']([^"'\\]*(?:\\.[^"'\\]*)*)["']''')
_NUMBER_LITERAL = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?")
_IGNORED_CALLS = {
    "assert",
    "asserteq",
    "assertfalse",
    "asserttrue",
    "dict",
    "expecteq",
    "expectfalse",
    "expectne",
    "expecttrue",
    "len",
    "list",
    "print",
    "size",
    "string",
    "vector",
}


@dataclass(frozen=True)
class AlignmentCandidate:
    source_test_id: str
    score: float
    name_similarity: float
    file_similarity: float
    call_similarity: float
    literal_similarity: float
    same_normalized_file: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestAlignmentRecord:
    schema_version: int
    pair: str
    project: str
    target_test_id: str
    target_test_file: str
    target_test_name: str
    source_test_ids: list[str]
    status: LabelStatus
    annotation_method: str
    confidence: str
    evidence: dict[str, Any] = field(default_factory=dict)
    candidates: list[AlignmentCandidate] = field(default_factory=list)
    reviewed: bool = False
    review_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TestAlignmentRecord":
        value = dict(value)
        value["candidates"] = [
            AlignmentCandidate(**candidate)
            for candidate in value.get("candidates", [])
        ]
        return cls(**value)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _calls(test: TestChunk) -> set[str]:
    return {
        normalized_name(call.split(".")[-1])
        for call in test.calls
        if normalized_name(call.split(".")[-1]) not in _IGNORED_CALLS
    }


def _literals(test: TestChunk) -> set[str]:
    return set(_STRING_LITERAL.findall(test.code)) | set(
        _NUMBER_LITERAL.findall(test.code)
    )


def score_alignment_candidate(
    target: TestChunk, source: TestChunk
) -> AlignmentCandidate:
    name_similarity = SequenceMatcher(
        None, normalized_name(target.name), normalized_name(source.name)
    ).ratio()
    file_similarity = SequenceMatcher(
        None, normalized_file(target.file), normalized_file(source.file)
    ).ratio()
    call_similarity = _jaccard(_calls(target), _calls(source))
    literal_similarity = _jaccard(_literals(target), _literals(source))
    same_file = bool(normalized_file(target.file)) and (
        normalized_file(target.file) == normalized_file(source.file)
    )
    score = (
        0.25 * name_similarity
        + 0.10 * file_similarity
        + 0.25 * call_similarity
        + 0.25 * literal_similarity
        + 0.15 * float(same_file)
    )
    return AlignmentCandidate(
        source.chunk_id,
        round(score, 6),
        round(name_similarity, 6),
        round(file_similarity, 6),
        round(call_similarity, 6),
        round(literal_similarity, 6),
        same_file,
    )


def rank_annotation_candidates(
    target: TestChunk, source_tests: list[TestChunk], *, limit: int = 5
) -> list[AlignmentCandidate]:
    ranked = [score_alignment_candidate(target, source) for source in source_tests]
    ranked.sort(key=lambda item: (-item.score, item.source_test_id))
    return ranked[:limit]


def _base_record(
    layout: PairLayout,
    target: TestChunk,
    *,
    source_test_ids: list[str],
    status: LabelStatus,
    method: str,
    confidence: str,
    evidence: dict[str, Any],
    candidates: list[AlignmentCandidate],
    reviewed: bool = False,
) -> TestAlignmentRecord:
    return TestAlignmentRecord(
        schema_version=1,
        pair=layout.pair.name,
        project=target.project,
        target_test_id=target.chunk_id,
        target_test_file=target.file,
        target_test_name=target.qualified_name,
        source_test_ids=source_test_ids,
        status=status,
        annotation_method=method,
        confidence=confidence,
        evidence=evidence,
        candidates=candidates,
        reviewed=reviewed,
    )


def propose_ground_truth(layout: PairLayout) -> list[TestAlignmentRecord]:
    records: list[TestAlignmentRecord] = []
    for paths in layout.projects():
        data = load_project(layout, paths.project)
        source_by_name: dict[str, list[TestChunk]] = defaultdict(list)
        source_by_file: dict[str, list[TestChunk]] = defaultdict(list)
        target_by_file: dict[str, list[TestChunk]] = defaultdict(list)
        for source in data.source_tests:
            source_by_name[normalized_name(source.name)].append(source)
            source_by_file[normalized_file(source.file)].append(source)
        for target in data.target_tests:
            target_by_file[normalized_file(target.file)].append(target)

        source_positions: dict[str, int] = {}
        target_positions: dict[str, int] = {}
        for file_key, chunks in source_by_file.items():
            for position, chunk in enumerate(
                sorted(chunks, key=lambda item: (item.file, item.start_line, item.chunk_id))
            ):
                source_positions[chunk.chunk_id] = position
        for file_key, chunks in target_by_file.items():
            for position, chunk in enumerate(
                sorted(chunks, key=lambda item: (item.file, item.start_line, item.chunk_id))
            ):
                target_positions[chunk.chunk_id] = position

        for target in data.target_tests:
            candidates = rank_annotation_candidates(target, data.source_tests)
            name_matches = source_by_name[normalized_name(target.name)]
            file_matches = source_by_file[normalized_file(target.file)]
            target_file_group = target_by_file[normalized_file(target.file)]
            if len(name_matches) == 1:
                records.append(_base_record(
                    layout,
                    target,
                    source_test_ids=[name_matches[0].chunk_id],
                    status="matched",
                    method="unique_normalized_test_name",
                    confidence="high",
                    evidence={"normalized_name": normalized_name(target.name)},
                    candidates=candidates,
                ))
                continue
            if len(name_matches) > 1:
                name_file_matches = [
                    source for source in name_matches
                    if normalized_file(source.file) == normalized_file(target.file)
                ]
                if len(name_file_matches) == 1:
                    records.append(_base_record(
                        layout,
                        target,
                        source_test_ids=[name_file_matches[0].chunk_id],
                        status="matched",
                        method="normalized_name_and_file",
                        confidence="high",
                        evidence={
                            "normalized_name": normalized_name(target.name),
                            "normalized_file": normalized_file(target.file),
                        },
                        candidates=candidates,
                    ))
                    continue
                records.append(_base_record(
                    layout,
                    target,
                    source_test_ids=[],
                    status="unresolved",
                    method="ambiguous_normalized_test_name",
                    confidence="unresolved",
                    evidence={
                        "candidate_source_test_ids": [
                            source.chunk_id for source in name_matches
                        ]
                    },
                    candidates=candidates,
                ))
                continue
            if len(file_matches) == 1:
                records.append(_base_record(
                    layout,
                    target,
                    source_test_ids=[file_matches[0].chunk_id],
                    status="matched",
                    method="single_source_test_in_normalized_file",
                    confidence="high",
                    evidence={"normalized_file": normalized_file(target.file)},
                    candidates=candidates,
                ))
                continue
            if file_matches and len(file_matches) == len(target_file_group):
                source = sorted(
                    file_matches,
                    key=lambda item: (item.file, item.start_line, item.chunk_id),
                )[target_positions[target.chunk_id]]
                records.append(_base_record(
                    layout,
                    target,
                    source_test_ids=[source.chunk_id],
                    status="matched",
                    method="same_file_equal_count_order",
                    confidence="medium",
                    evidence={
                        "normalized_file": normalized_file(target.file),
                        "position": target_positions[target.chunk_id],
                    },
                    candidates=candidates,
                ))
                continue
            records.append(_base_record(
                layout,
                target,
                source_test_ids=[],
                status="unresolved",
                method="candidate_review_required",
                confidence="unresolved",
                evidence={
                    "same_file_source_test_ids": [
                        source.chunk_id for source in file_matches
                    ]
                },
                candidates=candidates,
            ))
    return records


def load_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        target_id = value.get("target_test_id")
        if not target_id:
            raise ValueError(f"Override line {line_number} has no target_test_id")
        if target_id in overrides:
            raise ValueError(f"Duplicate override for {target_id}")
        overrides[target_id] = value
    return overrides


def apply_overrides(
    records: list[TestAlignmentRecord], overrides: dict[str, dict[str, Any]]
) -> list[TestAlignmentRecord]:
    known = {record.target_test_id for record in records}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError(f"Overrides reference unknown Target tests: {unknown[:5]}")
    for record in records:
        override = overrides.get(record.target_test_id)
        if override is None:
            continue
        status = override.get("status", "matched")
        if status not in {"matched", "no_match", "unresolved"}:
            raise ValueError(f"Invalid override status for {record.target_test_id}: {status}")
        source_ids = list(override.get("source_test_ids", []))
        if status == "matched" and not source_ids:
            raise ValueError(f"Matched override has no Source tests: {record.target_test_id}")
        if status != "matched" and source_ids:
            raise ValueError(
                f"Non-matched override has Source tests: {record.target_test_id}"
            )
        record.source_test_ids = source_ids
        record.status = status
        record.annotation_method = override.get("annotation_method", "manual_code_review")
        record.confidence = override.get("confidence", "verified")
        record.reviewed = bool(override.get("reviewed", True))
        record.review_notes = override.get("review_notes", "")
        record.evidence = {
            **record.evidence,
            **override.get("evidence", {}),
            "overridden": True,
        }
    return records


def validate_ground_truth(
    layout: PairLayout, records: list[TestAlignmentRecord]
) -> dict[str, Any]:
    expected_targets: dict[str, TestChunk] = {}
    source_tests: dict[str, TestChunk] = {}
    for paths in layout.projects():
        data = load_project(layout, paths.project)
        expected_targets.update({test.chunk_id: test for test in data.target_tests})
        source_tests.update({test.chunk_id: test for test in data.source_tests})
    counts = Counter(record.status for record in records)
    record_ids = [record.target_test_id for record in records]
    duplicates = sorted(
        target_id for target_id, count in Counter(record_ids).items() if count > 1
    )
    missing = sorted(set(expected_targets) - set(record_ids))
    extra = sorted(set(record_ids) - set(expected_targets))
    invalid_source_ids = sorted({
        source_id
        for record in records
        for source_id in record.source_test_ids
        if source_id not in source_tests
    })
    cross_project_links = []
    for record in records:
        for source_id in record.source_test_ids:
            source = source_tests.get(source_id)
            if source is not None and source.project != record.project:
                cross_project_links.append((record.target_test_id, source_id))
    errors = {
        "duplicates": duplicates,
        "missing_target_tests": missing,
        "extra_target_tests": extra,
        "invalid_source_test_ids": invalid_source_ids,
        "cross_project_links": cross_project_links,
    }
    valid = not any(errors.values())
    return {
        "pair": layout.pair.name,
        "valid": valid,
        "expected_target_tests": len(expected_targets),
        "records": len(records),
        "status_counts": dict(sorted(counts.items())),
        "matched_links": sum(len(record.source_test_ids) for record in records),
        "reviewed_records": sum(record.reviewed for record in records),
        "errors": errors,
    }


def write_ground_truth(
    records: list[TestAlignmentRecord], path: Path
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in sorted(records, key=lambda item: item.target_test_id):
            stream.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def load_ground_truth(path: Path) -> list[TestAlignmentRecord]:
    return [
        TestAlignmentRecord.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
