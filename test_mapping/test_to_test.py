from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re
import time
from typing import Any

from .alignment import normalized_file, normalized_name
from .dataset import PairLayout
from .embedding import Embedder
from .ground_truth import TestAlignmentRecord
from .index import VectorIndex
from .models import FunctionChunk, SearchHit, TestChunk
from .parsing import extract_calls_from_code
from .repository import load_project


TEST_TO_TEST_STRATEGIES = ("dense", "structure", "fusion")
FUSION_WEIGHTS = {"structure": 0.70, "dense": 0.30}

_IGNORED_CALLS = {
    "assert",
    "assertequal",
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
_STRING_LITERAL = re.compile(r'''["']([^"'\\]*(?:\\.[^"'\\]*)*)["']''')
_NUMBER_LITERAL = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?")


def build_target_test_query(test: TestChunk, *, mask_names: bool = False) -> str:
    name = "<TEST_NAME>" if mask_names else test.qualified_name
    file_name = "<TEST_FILE>" if mask_names else test.file
    call_text = ""
    if test.calls and not mask_names:
        call_text = f"\nCalls: {', '.join(test.calls)}"
    return (
        f"Target public test: {name}\n"
        f"File: {file_name}{call_text}\n"
        f"Code:\n{test.code}"
    )


def _tokens(values: list[str]) -> set[str]:
    return {
        normalized_name(value.split(".")[-1])
        for value in values
        if normalized_name(value.split(".")[-1])
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def _literals(code: str) -> set[str]:
    strings = {value.lower() for value in _STRING_LITERAL.findall(code) if value}
    return strings | set(_NUMBER_LITERAL.findall(code))


def structural_similarity(
    target: TestChunk, source: TestChunk, *, mask_names: bool = False
) -> tuple[float, dict[str, float]]:
    name_score = 0.0
    file_score = 0.0
    if not mask_names:
        name_score = SequenceMatcher(
            None, normalized_name(target.name), normalized_name(source.name)
        ).ratio()
        file_score = SequenceMatcher(
            None, normalized_file(target.file), normalized_file(source.file)
        ).ratio()
    call_score = _jaccard(_tokens(target.calls), _tokens(source.calls))
    literal_score = _jaccard(_literals(target.code), _literals(source.code))
    components = {
        "name": name_score,
        "file": file_score,
        "calls": call_score,
        "literals": literal_score,
    }
    score = (
        0.50 * name_score
        + 0.20 * file_score
        + 0.20 * call_score
        + 0.10 * literal_score
    )
    return score, components


@dataclass(frozen=True)
class StaticFunctionLink:
    function: FunctionChunk
    called_as: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "called_as": self.called_as,
            "confidence": self.confidence,
            "function": self.function.to_dict(),
        }


@dataclass
class StaticExpansion:
    links: list[StaticFunctionLink]
    unmatched_calls: list[str]
    ambiguous_calls: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "links": [link.to_dict() for link in self.links],
            "unmatched_calls": self.unmatched_calls,
            "ambiguous_calls": self.ambiguous_calls,
        }


@dataclass
class SourceTestMappingHit:
    source_test: TestChunk
    score: float
    rank: int
    strategy: str
    components: dict[str, Any] = field(default_factory=dict)
    static_expansion: StaticExpansion | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.source_test.chunk_id,
            "score": self.score,
            "rank": self.rank,
            "strategy": self.strategy,
            "components": self.components,
            "source_test": self.source_test.to_dict(),
            "source_functions": (
                self.static_expansion.to_dict()
                if self.static_expansion is not None
                else None
            ),
        }


@dataclass
class TargetToSourceTestResult:
    query_id: str
    requested_strategy: str
    used_strategies: list[str]
    confidence: float
    margin: float
    hits: list[SourceTestMappingHit]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "direction": "target_test_to_source_test_to_source_function",
            "requested_strategy": self.requested_strategy,
            "used_strategies": self.used_strategies,
            "confidence": self.confidence,
            "margin": self.margin,
            "hits": [hit.to_dict() for hit in self.hits],
            "diagnostics": self.diagnostics,
        }


class SourceTestLocator:
    def __init__(self, index: VectorIndex, embedder: Embedder) -> None:
        if index.chunk_type != "test":
            raise ValueError(
                f"SourceTestLocator requires a test index, got {index.chunk_type}"
            )
        if index.corpus_role not in {"test", "source_test"}:
            raise ValueError(
                f"SourceTestLocator requires a Source-test index, got {index.corpus_role}"
            )
        self.index = index
        self.embedder = embedder

    def _dense(
        self, target: TestChunk, *, k: int, mask_names: bool
    ) -> list[SearchHit]:
        return self.index.search(
            build_target_test_query(target, mask_names=mask_names),
            self.embedder,
            k=k,
            project=target.project,
            strategy="dense",
        )

    def _structure(
        self, target: TestChunk, *, k: int, mask_names: bool
    ) -> tuple[list[SearchHit], dict[str, dict[str, float]]]:
        scored: list[tuple[float, TestChunk, dict[str, float]]] = []
        for chunk in self.index.chunks:
            if not isinstance(chunk, TestChunk) or chunk.project != target.project:
                continue
            score, components = structural_similarity(
                target, chunk, mask_names=mask_names
            )
            scored.append((score, chunk, components))
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
        selected = scored[:k]
        hits = [
            SearchHit(chunk.chunk_id, score, rank, "structure", chunk)
            for rank, (score, chunk, _) in enumerate(selected, start=1)
        ]
        return hits, {chunk.chunk_id: components for _, chunk, components in selected}

    @staticmethod
    def expand_functions(
        source_test: TestChunk, source_functions: list[FunctionChunk]
    ) -> StaticExpansion:
        by_name: dict[str, list[FunctionChunk]] = defaultdict(list)
        constructors: dict[str, list[FunctionChunk]] = defaultdict(list)
        for function in source_functions:
            key = normalized_name(function.name)
            if key:
                by_name[key].append(function)
            if function.name == "__init__" and function.parent:
                constructors[normalized_name(function.parent)].append(function)
        links: list[StaticFunctionLink] = []
        unmatched: list[str] = []
        ambiguous: dict[str, list[str]] = {}
        seen: set[str] = set()
        calls = list(source_test.calls)
        for helper in source_test.helpers:
            calls.extend(extract_calls_from_code(helper, source_test.language))
        for call in sorted(set(calls)):
            key = normalized_name(call.split(".")[-1])
            if not key or key in _IGNORED_CALLS:
                continue
            candidates = by_name.get(key, [])
            constructor_match = False
            if not candidates:
                candidates = constructors.get(key, [])
                constructor_match = bool(candidates)
            if not candidates:
                unmatched.append(call)
                continue
            if constructor_match:
                confidence = (
                    "unique_constructor"
                    if len(candidates) == 1
                    else "ambiguous_constructor"
                )
            else:
                confidence = (
                    "unique_name" if len(candidates) == 1 else "ambiguous_name"
                )
            if len(candidates) > 1:
                ambiguous[call] = sorted(item.chunk_id for item in candidates)
            for function in sorted(candidates, key=lambda item: item.chunk_id):
                if function.chunk_id in seen:
                    continue
                links.append(StaticFunctionLink(function, call, confidence))
                seen.add(function.chunk_id)
        return StaticExpansion(links, sorted(set(unmatched)), ambiguous)

    def locate(
        self,
        target: TestChunk,
        source_functions: list[FunctionChunk] | None = None,
        *,
        strategy: str = "fusion",
        k: int = 5,
        mask_names: bool = False,
    ) -> TargetToSourceTestResult:
        if strategy not in TEST_TO_TEST_STRATEGIES:
            raise ValueError(f"Unsupported test-to-test strategy: {strategy}")
        if k <= 0:
            raise ValueError("k must be positive")
        components: dict[str, dict[str, Any]] = defaultdict(dict)
        used: list[str]
        if strategy == "dense":
            hits = self._dense(target, k=k, mask_names=mask_names)
            used = ["dense"]
            for hit in hits:
                components[hit.chunk_id]["dense_score"] = hit.score
                components[hit.chunk_id]["dense_rank"] = hit.rank
        elif strategy == "structure":
            hits, details = self._structure(target, k=k, mask_names=mask_names)
            used = ["structure"]
            for hit in hits:
                components[hit.chunk_id].update(details[hit.chunk_id])
                components[hit.chunk_id]["structure_score"] = hit.score
                components[hit.chunk_id]["structure_rank"] = hit.rank
        else:
            pool_size = max(k, 20)
            dense = self._dense(target, k=pool_size, mask_names=mask_names)
            structure, details = self._structure(
                target, k=pool_size, mask_names=mask_names
            )
            chunks: dict[str, TestChunk] = {}
            dense_scores: dict[str, float] = {}
            structure_scores: dict[str, float] = {}
            for hit in dense:
                chunks[hit.chunk_id] = hit.chunk  # type: ignore[assignment]
                dense_scores[hit.chunk_id] = hit.score
                components[hit.chunk_id]["dense_score"] = hit.score
                components[hit.chunk_id]["dense_rank"] = hit.rank
            for hit in structure:
                chunks[hit.chunk_id] = hit.chunk  # type: ignore[assignment]
                structure_scores[hit.chunk_id] = hit.score
                components[hit.chunk_id].update(details[hit.chunk_id])
                components[hit.chunk_id]["structure_score"] = hit.score
                components[hit.chunk_id]["structure_rank"] = hit.rank
            fused = {
                chunk_id: (
                    FUSION_WEIGHTS["dense"] * dense_scores.get(chunk_id, 0.0)
                    + FUSION_WEIGHTS["structure"]
                    * structure_scores.get(chunk_id, 0.0)
                )
                for chunk_id in chunks
            }
            for chunk_id, score in fused.items():
                components[chunk_id]["fusion_score"] = score
            ordered = sorted(
                fused, key=lambda chunk_id: (-fused[chunk_id], chunk_id)
            )[:k]
            hits = [
                SearchHit(chunk_id, fused[chunk_id], rank, "fusion", chunks[chunk_id])
                for rank, chunk_id in enumerate(ordered, start=1)
            ]
            used = ["dense", "structure"]

        functions = source_functions or []
        mapped_hits = [
            SourceTestMappingHit(
                source_test=hit.chunk,  # type: ignore[arg-type]
                score=hit.score,
                rank=hit.rank,
                strategy=hit.strategy,
                components=dict(components.get(hit.chunk_id, {})),
                static_expansion=self.expand_functions(hit.chunk, functions),  # type: ignore[arg-type]
            )
            for hit in hits
        ]
        score = mapped_hits[0].score if mapped_hits else 0.0
        margin = (
            mapped_hits[0].score - mapped_hits[1].score
            if len(mapped_hits) > 1
            else score
        )
        return TargetToSourceTestResult(
            target.chunk_id,
            strategy,
            used,
            score,
            margin,
            mapped_hits,
            {
                "project": target.project,
                "mask_names": mask_names,
                "target_test_calls": target.calls,
                "candidate_scope": "same_project_source_public_tests",
                "fusion_weights": FUSION_WEIGHTS if strategy == "fusion" else None,
            },
        )


@dataclass
class TestToTestEvaluationSummary:
    query_unit: str
    strategy: str
    mask_names: bool
    project_count: int
    ground_truth_record_count: int
    matched_query_count: int
    no_match_query_count: int
    gold_source_test_link_count: int
    macro_recall_at_1: float
    macro_recall_at_3: float
    macro_recall_at_5: float
    hit_rate_at_1: float
    hit_rate_at_3: float
    hit_rate_at_5: float
    mrr: float
    retrieval_failed_query_count: int
    gold_source_tests_with_static_function_links: int
    gold_source_test_static_link_coverage: float
    gold_source_function_link_count: int
    matched_top1_score_mean: float | None
    no_match_top1_score_mean: float | None
    match_score_auroc: float | None
    elapsed_seconds: float
    project_status_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _auc(positive: list[float], negative: list[float]) -> float | None:
    if not positive or not negative:
        return None
    wins = 0.0
    for left in positive:
        for right in negative:
            wins += float(left > right) + 0.5 * float(left == right)
    return wins / (len(positive) * len(negative))


def evaluate_target_test_to_source_test(
    layout: PairLayout,
    locator: SourceTestLocator,
    records: list[TestAlignmentRecord],
    *,
    projects: list[str] | None = None,
    strategy: str = "fusion",
    mask_names: bool = False,
) -> tuple[TestToTestEvaluationSummary, list[dict[str, Any]]]:
    started = time.perf_counter()
    selected = projects or [item.project for item in layout.projects()]
    selected_set = set(selected)
    selected_records = [record for record in records if record.project in selected_set]
    record_groups: dict[str, list[TestAlignmentRecord]] = defaultdict(list)
    for record in selected_records:
        record_groups[record.project].append(record)

    statuses: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    matched = no_match = gold_links = failures = 0
    recall_sums = {1: 0.0, 3: 0.0, 5: 0.0}
    hit_counts = {1: 0, 3: 0, 5: 0}
    reciprocal_sum = 0.0
    gold_source_tests = gold_source_tests_with_links = function_links = 0
    matched_scores: list[float] = []
    no_match_scores: list[float] = []

    for project in selected:
        project_records = record_groups.get(project, [])
        if not project_records:
            statuses["ground_truth_missing"] += 1
            rows.append({"project": project, "status": "ground_truth_missing"})
            continue
        data = load_project(layout, project)
        target_by_id = {test.chunk_id: test for test in data.target_tests}
        source_by_id = {test.chunk_id: test for test in data.source_tests}
        project_rows: list[dict[str, Any]] = []
        for record in sorted(project_records, key=lambda item: item.target_test_id):
            target = target_by_id.get(record.target_test_id)
            if target is None:
                raise ValueError(
                    f"Ground-truth Target test is missing from dataset: {record.target_test_id}"
                )
            result = locator.locate(
                target,
                data.source_functions,
                strategy=strategy,
                k=5,
                mask_names=mask_names,
            )
            retrieved = [hit.source_test.chunk_id for hit in result.hits]
            row: dict[str, Any] = {
                "target_test_id": record.target_test_id,
                "gold_status": record.status,
                "gold_source_test_ids": record.source_test_ids,
                "result": result.to_dict(),
            }
            if record.status == "no_match":
                no_match += 1
                no_match_scores.append(result.confidence)
                row["first_relevant_rank"] = None
                project_rows.append(row)
                continue
            if record.status != "matched":
                raise ValueError(
                    f"Evaluation requires resolved ground truth: {record.target_test_id}"
                )
            gold = set(record.source_test_ids)
            matched += 1
            gold_links += len(gold)
            matched_scores.append(result.confidence)
            ranks = [
                rank
                for rank, chunk_id in enumerate(retrieved, start=1)
                if chunk_id in gold
            ]
            first_rank = min(ranks) if ranks else None
            row["first_relevant_rank"] = first_rank
            if first_rank is None:
                failures += 1
            else:
                reciprocal_sum += 1.0 / first_rank
            for cutoff in recall_sums:
                relevant = len(set(retrieved[:cutoff]) & gold)
                recall_sums[cutoff] += relevant / len(gold)
                hit_counts[cutoff] += int(relevant > 0)
            for source_id in gold:
                source_test = source_by_id.get(source_id)
                if source_test is None:
                    raise ValueError(f"Ground-truth Source test is missing: {source_id}")
                expansion = locator.expand_functions(source_test, data.source_functions)
                gold_source_tests += 1
                if expansion.links:
                    gold_source_tests_with_links += 1
                    function_links += len(expansion.links)
            project_rows.append(row)
        statuses["evaluated"] += 1
        rows.append({
            "project": project,
            "status": "evaluated",
            "queries": len(project_rows),
            "results": project_rows,
            "errors": data.errors,
        })

    denominator = max(matched, 1)
    summary = TestToTestEvaluationSummary(
        query_unit="target_test_to_source_test",
        strategy=strategy,
        mask_names=mask_names,
        project_count=len(selected),
        ground_truth_record_count=len(selected_records),
        matched_query_count=matched,
        no_match_query_count=no_match,
        gold_source_test_link_count=gold_links,
        macro_recall_at_1=recall_sums[1] / denominator,
        macro_recall_at_3=recall_sums[3] / denominator,
        macro_recall_at_5=recall_sums[5] / denominator,
        hit_rate_at_1=hit_counts[1] / denominator,
        hit_rate_at_3=hit_counts[3] / denominator,
        hit_rate_at_5=hit_counts[5] / denominator,
        mrr=reciprocal_sum / denominator,
        retrieval_failed_query_count=failures,
        gold_source_tests_with_static_function_links=gold_source_tests_with_links,
        gold_source_test_static_link_coverage=(
            gold_source_tests_with_links / max(gold_source_tests, 1)
        ),
        gold_source_function_link_count=function_links,
        matched_top1_score_mean=_mean(matched_scores),
        no_match_top1_score_mean=_mean(no_match_scores),
        match_score_auroc=_auc(matched_scores, no_match_scores),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        project_status_counts=dict(sorted(statuses.items())),
    )
    return summary, rows
