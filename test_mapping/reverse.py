from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import re
import time

from .alignment import align_tests, normalized_name
from .dataset import PairLayout
from .embedding import Embedder
from .evaluation import build_function_gold
from .index import VectorIndex
from .models import FunctionChunk, LocationResult, SearchHit, TestChunk
from .repository import load_project


REVERSE_STRATEGIES = ("dense", "call_name", "fusion")


def build_target_test_query(test: TestChunk, *, mask_names: bool = False) -> str:
    """Build an online query without Source tests or Target implementation code."""
    name = "<TEST_NAME>" if mask_names else test.qualified_name
    file_name = "<TEST_FILE>" if mask_names else test.file
    calls = [] if mask_names else test.calls
    call_text = f"\nCalls: {', '.join(calls)}" if calls else ""
    return (
        f"Target public test: {name}\n"
        f"File: {file_name}{call_text}\n"
        f"Code:\n{test.code}"
    )


def build_target_test_function_gold(
    source_tests: list[TestChunk],
    target_tests: list[TestChunk],
    source_functions: list[FunctionChunk],
) -> dict[str, set[str]]:
    """Invert strict Source-function -> Target-test links for offline evaluation."""
    strict = [
        alignment
        for alignment in align_tests(source_tests, target_tests)
        if alignment.confidence == "high"
        and len(alignment.target_chunk_ids) == 1
    ]
    forward = build_function_gold(source_tests, source_functions, strict)
    reverse: dict[str, set[str]] = defaultdict(set)
    for function_id, target_ids in forward.items():
        for target_id in target_ids:
            reverse[target_id].add(function_id)
    return dict(reverse)


def _identifier_tokens(value: str) -> set[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return {token.lower() for token in re.findall(r"[A-Za-z0-9]+", value)}


class SourceFunctionLocator:
    def __init__(self, index: VectorIndex, embedder: Embedder) -> None:
        if index.chunk_type != "function":
            raise ValueError(
                f"SourceFunctionLocator requires a function index, got {index.chunk_type}"
            )
        self.index = index
        self.embedder = embedder

    def _dense(
        self, test: TestChunk, *, k: int, mask_names: bool
    ) -> list[SearchHit]:
        return self.index.search(
            build_target_test_query(test, mask_names=mask_names),
            self.embedder,
            k=k,
            project=test.project,
            strategy="dense",
        )

    def _call_name(self, test: TestChunk, *, k: int) -> list[SearchHit]:
        calls = {normalized_name(call.split(".")[-1]) for call in test.calls}
        test_tokens = _identifier_tokens(test.name)
        scored: list[tuple[float, FunctionChunk]] = []
        for chunk in self.index.chunks:
            if not isinstance(chunk, FunctionChunk) or chunk.project != test.project:
                continue
            function_name = normalized_name(chunk.name)
            score = 0.0
            if function_name and function_name in calls:
                score = 1.0
            elif _identifier_tokens(chunk.name) and _identifier_tokens(chunk.name) <= test_tokens:
                score = 0.75
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return [
            SearchHit(chunk.chunk_id, score, rank, "call_name", chunk)
            for rank, (score, chunk) in enumerate(scored[:k], start=1)
        ]

    def locate(
        self,
        test: TestChunk,
        *,
        strategy: str = "dense",
        k: int = 5,
        mask_names: bool = False,
    ) -> LocationResult:
        if strategy not in REVERSE_STRATEGIES:
            raise ValueError(f"Unsupported reverse locator strategy: {strategy}")
        if strategy == "dense":
            hits = self._dense(test, k=k, mask_names=mask_names)
            return self._result(test, strategy, ["dense"], hits, mask_names)
        if strategy == "call_name":
            hits = self._call_name(test, k=k)
            return self._result(test, strategy, ["call_name"], hits, mask_names)

        groups = [
            ("dense", self._dense(test, k=max(k, 10), mask_names=mask_names)),
            ("call_name", self._call_name(test, k=max(k, 10))),
        ]
        rrf: dict[str, float] = defaultdict(float)
        chunks: dict[str, FunctionChunk] = {}
        for _, hits in groups:
            for hit in hits:
                rrf[hit.chunk_id] += 1.0 / (60 + hit.rank)
                if isinstance(hit.chunk, FunctionChunk):
                    chunks[hit.chunk_id] = hit.chunk
        ordered = sorted(rrf, key=lambda chunk_id: (-rrf[chunk_id], chunk_id))[:k]
        hits = [
            SearchHit(
                chunk_id, rrf[chunk_id], rank, "fusion", chunks[chunk_id]
            )
            for rank, chunk_id in enumerate(ordered, start=1)
        ]
        used = [name for name, group in groups if group]
        return self._result(test, strategy, used, hits, mask_names)

    @staticmethod
    def _result(
        test: TestChunk,
        strategy: str,
        used: list[str],
        hits: list[SearchHit],
        mask_names: bool,
    ) -> LocationResult:
        score = hits[0].score if hits else 0.0
        margin = (
            hits[0].score - hits[1].score
            if len(hits) > 1
            else score
        )
        return LocationResult(
            test.chunk_id,
            strategy,
            used,
            False,
            score,
            margin,
            hits,
            {
                "direction": "target_test_to_source_function",
                "mask_names": mask_names,
                "target_test_calls": test.calls,
            },
        )


@dataclass
class ReverseEvaluationSummary:
    query_unit: str
    strategy: str
    mask_names: bool
    project_count: int
    target_test_count: int
    source_function_count: int
    evaluable_query_count: int
    gold_source_function_link_count: int
    macro_recall_at_1: float
    macro_recall_at_3: float
    macro_recall_at_5: float
    hit_rate_at_1: float
    hit_rate_at_3: float
    hit_rate_at_5: float
    mrr: float
    gold_coverage: float
    elapsed_seconds: float
    project_status_counts: dict[str, int]
    retrieval_failed_query_count: int

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_target_tests(
    layout: PairLayout,
    locator: SourceFunctionLocator,
    *,
    projects: list[str] | None = None,
    strategy: str = "dense",
    mask_names: bool = False,
) -> tuple[ReverseEvaluationSummary, list[dict]]:
    started = time.perf_counter()
    selected = projects or [item.project for item in layout.projects()]
    statuses: Counter[str] = Counter()
    rows: list[dict] = []
    target_total = function_total = query_total = gold_link_total = 0
    recall_sums = {1: 0.0, 3: 0.0, 5: 0.0}
    hit_counts = {1: 0, 3: 0, 5: 0}
    reciprocal_sum = 0.0
    retrieval_failed = 0

    for project in selected:
        data = load_project(layout, project)
        target_total += len(data.target_tests)
        function_total += len(data.source_functions)
        if data.paths.source_dir is None or data.paths.target_dir is None:
            statuses["dataset_missing"] += 1
            rows.append({"project": project, "status": "dataset_missing", "errors": data.errors})
            continue
        if not data.target_tests or not data.source_functions:
            statuses["chunk_failed"] += 1
            rows.append({"project": project, "status": "chunk_failed", "errors": data.errors})
            continue
        gold = build_target_test_function_gold(
            data.source_tests, data.target_tests, data.source_functions
        )
        if not gold:
            statuses["gold_unknown"] += 1
            rows.append({"project": project, "status": "gold_unknown", "errors": data.errors})
            continue
        target_by_id = {test.chunk_id: test for test in data.target_tests}
        project_rows = []
        for target_id, source_ids in sorted(gold.items()):
            test = target_by_id.get(target_id)
            if test is None:
                continue
            result = locator.locate(
                test, strategy=strategy, k=5, mask_names=mask_names
            )
            retrieved = [hit.chunk_id for hit in result.hits]
            relevant_ranks = [
                rank
                for rank, chunk_id in enumerate(retrieved, start=1)
                if chunk_id in source_ids
            ]
            first_rank = min(relevant_ranks) if relevant_ranks else None
            query_total += 1
            gold_link_total += len(source_ids)
            if first_rank is None:
                retrieval_failed += 1
            else:
                reciprocal_sum += 1.0 / first_rank
            for cutoff in recall_sums:
                relevant = len(set(retrieved[:cutoff]) & source_ids)
                recall_sums[cutoff] += relevant / len(source_ids)
                hit_counts[cutoff] += int(relevant > 0)
            project_rows.append({
                "target_test_id": target_id,
                "gold_source_function_ids": sorted(source_ids),
                "first_relevant_rank": first_rank,
                "result": result.to_dict(),
            })
        statuses["evaluated"] += 1
        rows.append({
            "project": project,
            "status": "evaluated",
            "queries": len(project_rows),
            "results": project_rows,
            "errors": data.errors,
        })

    denominator = max(query_total, 1)
    summary = ReverseEvaluationSummary(
        query_unit="target_test",
        strategy=strategy,
        mask_names=mask_names,
        project_count=len(selected),
        target_test_count=target_total,
        source_function_count=function_total,
        evaluable_query_count=query_total,
        gold_source_function_link_count=gold_link_total,
        macro_recall_at_1=recall_sums[1] / denominator,
        macro_recall_at_3=recall_sums[3] / denominator,
        macro_recall_at_5=recall_sums[5] / denominator,
        hit_rate_at_1=hit_counts[1] / denominator,
        hit_rate_at_3=hit_counts[3] / denominator,
        hit_rate_at_5=hit_counts[5] / denominator,
        mrr=reciprocal_sum / denominator,
        gold_coverage=query_total / max(target_total, 1),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        project_status_counts=dict(sorted(statuses.items())),
        retrieval_failed_query_count=retrieval_failed,
    )
    return summary, rows
