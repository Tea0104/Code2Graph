from __future__ import annotations

from collections import defaultdict

from .embedding import Embedder
from .index import VectorIndex
from .models import FunctionChunk, LocationResult, SearchHit, TestChunk
from .query import build_query


BASE_STRATEGIES = ("function_test", "test", "function")


class TestLocator:
    def __init__(self, index: VectorIndex, embedder: Embedder, *, confidence_threshold: float = 0.55, margin_threshold: float = 0.03) -> None:
        self.index = index
        self.embedder = embedder
        self.confidence_threshold = confidence_threshold
        self.margin_threshold = margin_threshold

    def _search(self, test: TestChunk, functions: list[FunctionChunk], strategy: str, k: int) -> list[SearchHit]:
        query = build_query(test, functions, strategy)
        if query is None:
            return []
        return self.index.search(query.text, self.embedder, k=k, project=test.project, strategy=strategy)

    @staticmethod
    def _confidence(hits: list[SearchHit]) -> tuple[float, float]:
        if not hits:
            return 0.0, 0.0
        margin = hits[0].score - hits[1].score if len(hits) > 1 else hits[0].score
        return hits[0].score, margin

    def locate(self, test: TestChunk, functions: list[FunctionChunk], *, strategy: str = "adaptive", k: int = 5) -> LocationResult:
        if strategy in BASE_STRATEGIES:
            hits = self._search(test, functions, strategy, k)
            score, margin = self._confidence(hits)
            return LocationResult(test.chunk_id, strategy, [strategy], False, score, margin, hits)
        if strategy == "fusion":
            hits, used = self._fusion(test, functions, k)
            score, margin = self._confidence(hits)
            return LocationResult(test.chunk_id, strategy, used, False, score, margin, hits)
        if strategy != "adaptive":
            raise ValueError(f"Unsupported locator strategy: {strategy}")

        used: list[str] = []
        best_hits: list[SearchHit] = []
        for current in BASE_STRATEGIES:
            hits = self._search(test, functions, current, k)
            if not hits:
                continue
            used.append(current)
            if not best_hits or self._confidence(hits) > self._confidence(best_hits):
                best_hits = hits
            score, margin = self._confidence(hits)
            if score >= self.confidence_threshold and margin >= self.margin_threshold:
                break
        score, margin = self._confidence(best_hits)
        return LocationResult(
            test.chunk_id, strategy, used, len(used) > 1, score, margin, best_hits,
            {"threshold": self.confidence_threshold, "margin_threshold": self.margin_threshold},
        )

    def locate_function(self, function: FunctionChunk, *, k: int = 5) -> LocationResult:
        return self.locate_function_with_tests(function, [], strategy="function", k=k)

    def _aggregate_queries(self, texts: list[str], *, project: str, strategy: str, k: int) -> list[SearchHit]:
        best: dict[str, SearchHit] = {}
        for text in texts:
            for hit in self.index.search(text, self.embedder, k=max(k, 10), project=project, strategy=strategy):
                current = best.get(hit.chunk_id)
                if current is None or hit.score > current.score:
                    best[hit.chunk_id] = hit
        ordered = sorted(best.values(), key=lambda hit: (-hit.score, hit.chunk_id))[:k]
        return [SearchHit(hit.chunk_id, hit.score, rank, strategy, hit.chunk) for rank, hit in enumerate(ordered, 1)]

    def locate_function_with_tests(
        self,
        function: FunctionChunk,
        source_tests: list[TestChunk],
        *,
        strategy: str = "adaptive",
        k: int = 5,
    ) -> LocationResult:
        function_text = f"Source function: {function.qualified_name}\nFile: {function.file}\n{function.code}"
        matching_tests = [
            test for test in source_tests
            if function.name in {call.split(".")[-1] for call in test.calls}
        ]
        test_texts = [f"Source public test: {test.qualified_name}\n{test.chunk_text}" for test in matching_tests]
        variants = {
            "function": [function_text],
            "test": test_texts,
            "function_test": [f"{test_text}\n\n{function_text}" for test_text in test_texts],
        }

        def search(current: str) -> list[SearchHit]:
            return self._aggregate_queries(variants[current], project=function.project, strategy=current, k=k)

        if strategy in BASE_STRATEGIES:
            hits = search(strategy)
            score, margin = self._confidence(hits)
            return LocationResult(function.chunk_id, strategy, [strategy], False, score, margin, hits)
        if strategy == "adaptive":
            used: list[str] = []
            best_hits: list[SearchHit] = []
            for current in BASE_STRATEGIES:
                hits = search(current)
                if not hits:
                    continue
                used.append(current)
                if not best_hits or self._confidence(hits) > self._confidence(best_hits):
                    best_hits = hits
                score, margin = self._confidence(hits)
                if score >= self.confidence_threshold and margin >= self.margin_threshold:
                    break
            score, margin = self._confidence(best_hits)
            return LocationResult(
                function.chunk_id,
                strategy,
                used,
                len(used) > 1,
                score,
                margin,
                best_hits,
                {"source_test_count": len(matching_tests)},
            )
        if strategy != "fusion":
            raise ValueError(f"Unsupported locator strategy: {strategy}")
        ranked_groups = [(current, search(current)) for current in BASE_STRATEGIES]
        ranked_groups = [(current, hits) for current, hits in ranked_groups if hits]
        rrf: dict[str, float] = defaultdict(float)
        chunks = {}
        for _, hits in ranked_groups:
            for hit in hits:
                rrf[hit.chunk_id] += 1.0 / (60 + hit.rank)
                chunks[hit.chunk_id] = hit.chunk
        ordered = sorted(rrf, key=lambda chunk_id: (-rrf[chunk_id], chunk_id))[:k]
        hits = [SearchHit(chunk_id, rrf[chunk_id], rank, "fusion", chunks[chunk_id]) for rank, chunk_id in enumerate(ordered, 1)]
        score, margin = self._confidence(hits)
        return LocationResult(
            function.chunk_id,
            strategy,
            [current for current, _ in ranked_groups],
            False,
            score,
            margin,
            hits,
            {"source_test_count": len(matching_tests)},
        )

    def _fusion(self, test: TestChunk, functions: list[FunctionChunk], k: int) -> tuple[list[SearchHit], list[str]]:
        rrf: dict[str, float] = defaultdict(float)
        chunks = {}
        used = []
        for strategy in BASE_STRATEGIES:
            hits = self._search(test, functions, strategy, max(k, 10))
            if not hits:
                continue
            used.append(strategy)
            for hit in hits:
                rrf[hit.chunk_id] += 1.0 / (60 + hit.rank)
                chunks[hit.chunk_id] = hit.chunk
        ordered = sorted(rrf, key=lambda chunk_id: (-rrf[chunk_id], chunk_id))[:k]
        hits = [SearchHit(chunk_id, rrf[chunk_id], rank, "fusion", chunks[chunk_id]) for rank, chunk_id in enumerate(ordered, 1)]
        return hits, used
