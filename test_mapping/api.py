from __future__ import annotations

from pathlib import Path
from typing import Any

from .embedding import Embedder, make_embedder
from .index import VectorIndex
from .models import TestChunk
from .parsing import build_test_chunk_from_code
from .test_to_test import SourceTestLocator, TEST_TO_TEST_STRATEGIES


_LANGUAGE_ALIASES = {
    "c++": "C++",
    "cpp": "C++",
    "cxx": "C++",
    "py": "Python",
    "python": "Python",
}


def _normalize_language(value: str) -> str:
    normalized = _LANGUAGE_ALIASES.get(value.strip().lower())
    if normalized is None:
        raise ValueError(f"Unsupported target language: {value}")
    return normalized


class SourceTestMappingAPI:
    """Reusable raw Target-test-code to Source-test-code retrieval API."""

    def __init__(self, index: VectorIndex, embedder: Embedder) -> None:
        if index.embedder_name != embedder.name:
            raise ValueError(
                f"Index uses {index.embedder_name}, query embedder uses {embedder.name}"
            )
        self.locator = SourceTestLocator(index, embedder)
        self.projects = frozenset(
            chunk.project
            for chunk in index.chunks
            if isinstance(chunk, TestChunk)
        )

    @classmethod
    def from_index(
        cls,
        index_dir: str | Path,
        *,
        embedder_kind: str = "unixcoder",
        model_path: str | Path | None = None,
        device: str = "auto",
        batch_size: int = 16,
    ) -> "SourceTestMappingAPI":
        embedder = make_embedder(
            embedder_kind,
            model_path=str(model_path) if model_path is not None else None,
            device=device,
            batch_size=batch_size,
        )
        return cls(VectorIndex.load(Path(index_dir)), embedder)

    def locate_source_tests(
        self,
        *,
        project: str,
        target_language: str,
        target_test_code: str,
        target_test_name: str | None = None,
        target_test_file: str | None = None,
        strategy: str = "fusion",
        top_k: int = 5,
        mask_names: bool = False,
    ) -> dict[str, Any]:
        if project not in self.projects:
            raise ValueError(f"Project is not present in the Source-test index: {project}")
        if strategy not in TEST_TO_TEST_STRATEGIES:
            raise ValueError(f"Unsupported test-to-test strategy: {strategy}")
        language = _normalize_language(target_language)
        target = build_test_chunk_from_code(
            project=project,
            language=language,
            code=target_test_code,
            name=target_test_name,
            file=target_test_file,
        )
        result = self.locator.locate(
            target,
            strategy=strategy,
            k=top_k,
            mask_names=mask_names,
        )
        return {
            "schema_version": 1,
            "direction": "target_test_code_to_source_test_code",
            "query_id": result.query_id,
            "project": project,
            "target_language": language,
            "strategy": result.requested_strategy,
            "used_strategies": result.used_strategies,
            "confidence": result.confidence,
            "margin": result.margin,
            "target_test": {
                "name": target.qualified_name,
                "file": target.file,
                "code": target.code,
                "calls": target.calls,
            },
            "hits": [
                {
                    "rank": hit.rank,
                    "score": hit.score,
                    "source_test_id": hit.source_test.chunk_id,
                    "source_language": hit.source_test.language,
                    "source_test_name": hit.source_test.qualified_name,
                    "source_test_file": hit.source_test.file,
                    "source_test_code": hit.source_test.code,
                    "source_test_calls": hit.source_test.calls,
                    "score_components": hit.components,
                }
                for hit in result.hits
            ],
        }
