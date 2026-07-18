from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import time

from .alignment import align_tests
from .dataset import PairLayout
from .models import Alignment, FunctionChunk, TestChunk
from .pipeline import TestLocator
from .repository import load_project


@dataclass
class EvaluationSummary:
    query_unit: str
    strategy: str
    project_count: int
    source_test_count: int
    target_test_count: int
    strict_gold_count: int
    evaluated_query_count: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    alignment_coverage: float
    end_to_end_recall_at_1: float
    elapsed_seconds: float
    project_status_counts: dict[str, int]
    retrieval_failed_query_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FunctionEvaluationSummary:
    query_unit: str
    strategy: str
    project_count: int
    source_function_count: int
    test_referenced_function_count: int
    evaluable_function_count: int
    gold_target_link_count: int
    macro_recall_at_1: float
    macro_recall_at_3: float
    macro_recall_at_5: float
    hit_rate_at_1: float
    hit_rate_at_3: float
    hit_rate_at_5: float
    mrr: float
    function_coverage: float
    gold_coverage_of_referenced_functions: float
    elapsed_seconds: float
    project_status_counts: dict[str, int]
    retrieval_failed_query_count: int

    def to_dict(self) -> dict:
        return asdict(self)


def build_function_gold(
    source_tests: list[TestChunk],
    source_functions: list[FunctionChunk],
    alignments: list[Alignment],
) -> dict[str, set[str]]:
    """Build strict function -> target-test links without name-ambiguity guesses."""
    targets_by_test = {
        alignment.source_chunk_id: set(alignment.target_chunk_ids)
        for alignment in alignments
        if alignment.confidence == "high" and len(alignment.target_chunk_ids) == 1
    }
    functions_by_name: dict[str, list[FunctionChunk]] = {}
    for function in source_functions:
        functions_by_name.setdefault(function.name, []).append(function)
    gold: dict[str, set[str]] = {}
    for test in source_tests:
        target_ids = targets_by_test.get(test.chunk_id)
        if not target_ids:
            continue
        for call in test.calls:
            candidates = functions_by_name.get(call.split(".")[-1], [])
            if len(candidates) == 1:
                gold.setdefault(candidates[0].chunk_id, set()).update(target_ids)
    return gold


def uniquely_referenced_function_ids(source_tests: list[TestChunk], source_functions: list[FunctionChunk]) -> set[str]:
    functions_by_name: dict[str, list[FunctionChunk]] = {}
    for function in source_functions:
        functions_by_name.setdefault(function.name, []).append(function)
    result: set[str] = set()
    for test in source_tests:
        for call in test.calls:
            candidates = functions_by_name.get(call.split(".")[-1], [])
            if len(candidates) == 1:
                result.add(candidates[0].chunk_id)
    return result


def evaluate(layout: PairLayout, locator: TestLocator, *, strategy: str = "adaptive", projects: list[str] | None = None) -> tuple[EvaluationSummary, list[dict]]:
    started = time.perf_counter()
    selected = projects or [item.project for item in layout.projects()]
    rows: list[dict] = []
    statuses: Counter[str] = Counter()
    retrieval_failed = 0
    source_total = target_total = gold_total = reciprocal_sum = 0
    hits_at = {1: 0, 3: 0, 5: 0}
    for project in selected:
        data = load_project(layout, project)
        source_total += len(data.source_tests)
        target_total += len(data.target_tests)
        if data.paths.source_dir is None or data.paths.target_dir is None:
            statuses["dataset_missing"] += 1
            rows.append({"project": project, "status": "dataset_missing", "errors": data.errors})
            continue
        if not data.source_tests or not data.target_tests:
            statuses["chunk_failed"] += 1
            rows.append({"project": project, "status": "chunk_failed", "source_tests": len(data.source_tests), "target_tests": len(data.target_tests), "errors": data.errors})
            continue
        strict = [alignment for alignment in align_tests(data.source_tests, data.target_tests) if alignment.confidence == "high"]
        if not strict:
            statuses["alignment_unknown"] += 1
            rows.append({"project": project, "status": "alignment_unknown", "source_tests": len(data.source_tests), "target_tests": len(data.target_tests), "errors": data.errors})
            continue
        source_by_id = {chunk.chunk_id: chunk for chunk in data.source_tests}
        statuses["evaluated"] += 1
        project_rows = []
        for alignment in strict:
            gold_total += 1
            result = locator.locate(source_by_id[alignment.source_chunk_id], data.source_functions, strategy=strategy, k=5)
            rank = next((hit.rank for hit in result.hits if hit.chunk_id in alignment.target_chunk_ids), None)
            if rank is None:
                retrieval_failed += 1
            else:
                reciprocal_sum += 1.0 / rank
                for k in hits_at:
                    hits_at[k] += int(rank <= k)
            project_rows.append({
                "source_test_id": alignment.source_chunk_id,
                "gold_target_ids": list(alignment.target_chunk_ids),
                "rank": rank,
                "result": result.to_dict(),
            })
        rows.append({"project": project, "status": "evaluated", "queries": len(project_rows), "results": project_rows, "errors": data.errors})
    denominator = max(gold_total, 1)
    summary = EvaluationSummary(
        query_unit="test",
        strategy=strategy,
        project_count=len(selected),
        source_test_count=source_total,
        target_test_count=target_total,
        strict_gold_count=gold_total,
        evaluated_query_count=gold_total,
        recall_at_1=hits_at[1] / denominator,
        recall_at_3=hits_at[3] / denominator,
        recall_at_5=hits_at[5] / denominator,
        mrr=reciprocal_sum / denominator,
        alignment_coverage=gold_total / max(source_total, 1),
        end_to_end_recall_at_1=hits_at[1] / max(source_total, 1),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        project_status_counts=dict(sorted(statuses.items())),
        retrieval_failed_query_count=retrieval_failed,
    )
    return summary, rows


def evaluate_functions(
    layout: PairLayout,
    locator: TestLocator,
    *,
    projects: list[str] | None = None,
    strategy: str = "adaptive",
) -> tuple[FunctionEvaluationSummary, list[dict]]:
    started = time.perf_counter()
    selected = projects or [item.project for item in layout.projects()]
    rows: list[dict] = []
    statuses: Counter[str] = Counter()
    source_function_total = referenced_function_total = query_total = gold_link_total = 0
    recall_sums = {1: 0.0, 3: 0.0, 5: 0.0}
    hit_counts = {1: 0, 3: 0, 5: 0}
    reciprocal_sum = 0.0
    retrieval_failed = 0
    for project in selected:
        data = load_project(layout, project)
        source_function_total += len(data.source_functions)
        referenced_function_total += len(uniquely_referenced_function_ids(data.source_tests, data.source_functions))
        if data.paths.source_dir is None or data.paths.target_dir is None:
            statuses["dataset_missing"] += 1
            rows.append({"project": project, "status": "dataset_missing", "errors": data.errors})
            continue
        if not data.source_tests or not data.target_tests or not data.source_functions:
            statuses["chunk_failed"] += 1
            rows.append({
                "project": project,
                "status": "chunk_failed",
                "source_functions": len(data.source_functions),
                "source_tests": len(data.source_tests),
                "target_tests": len(data.target_tests),
                "errors": data.errors,
            })
            continue
        strict = [alignment for alignment in align_tests(data.source_tests, data.target_tests) if alignment.confidence == "high"]
        if not strict:
            statuses["alignment_unknown"] += 1
            rows.append({"project": project, "status": "alignment_unknown", "errors": data.errors})
            continue
        function_gold = build_function_gold(data.source_tests, data.source_functions, strict)
        if not function_gold:
            statuses["function_link_unknown"] += 1
            rows.append({"project": project, "status": "function_link_unknown", "errors": data.errors})
            continue
        functions_by_id = {function.chunk_id: function for function in data.source_functions}
        statuses["evaluated"] += 1
        project_rows = []
        for function_id, gold_targets in sorted(function_gold.items()):
            function = functions_by_id[function_id]
            result = locator.locate_function_with_tests(function, data.source_tests, strategy=strategy, k=5)
            retrieved = [hit.chunk_id for hit in result.hits]
            relevant_ranks = [rank for rank, chunk_id in enumerate(retrieved, start=1) if chunk_id in gold_targets]
            first_rank = min(relevant_ranks) if relevant_ranks else None
            query_total += 1
            gold_link_total += len(gold_targets)
            if first_rank is None:
                retrieval_failed += 1
            else:
                reciprocal_sum += 1.0 / first_rank
            for k in recall_sums:
                relevant_at_k = len(set(retrieved[:k]) & gold_targets)
                recall_sums[k] += relevant_at_k / len(gold_targets)
                hit_counts[k] += int(relevant_at_k > 0)
            project_rows.append({
                "source_function_id": function_id,
                "gold_target_ids": sorted(gold_targets),
                "first_relevant_rank": first_rank,
                "result": result.to_dict(),
            })
        rows.append({"project": project, "status": "evaluated", "queries": len(project_rows), "results": project_rows, "errors": data.errors})
    denominator = max(query_total, 1)
    summary = FunctionEvaluationSummary(
        query_unit="function",
        strategy=strategy,
        project_count=len(selected),
        source_function_count=source_function_total,
        test_referenced_function_count=referenced_function_total,
        evaluable_function_count=query_total,
        gold_target_link_count=gold_link_total,
        macro_recall_at_1=recall_sums[1] / denominator,
        macro_recall_at_3=recall_sums[3] / denominator,
        macro_recall_at_5=recall_sums[5] / denominator,
        hit_rate_at_1=hit_counts[1] / denominator,
        hit_rate_at_3=hit_counts[3] / denominator,
        hit_rate_at_5=hit_counts[5] / denominator,
        mrr=reciprocal_sum / denominator,
        function_coverage=query_total / max(source_function_total, 1),
        gold_coverage_of_referenced_functions=query_total / max(referenced_function_total, 1),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        project_status_counts=dict(sorted(statuses.items())),
        retrieval_failed_query_count=retrieval_failed,
    )
    return summary, rows
