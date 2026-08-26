from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_mapping.api import SourceTestMappingAPI
from test_mapping.models import FunctionChunk
from test_mapping.source_function_mapping import SourceFunctionMappingAPI


def _join_function_codes(functions: list[dict[str, Any]]) -> str:
    codes = [
        function["code"].strip()
        for function in functions
        if isinstance(function.get("code"), str) and function["code"].strip()
    ]
    return "\n\n".join(codes)


class TargetToSourceCodeAPI:
    """Reusable Target-test-code to Source-function-code pipeline."""

    def __init__(
        self,
        source_tests: SourceTestMappingAPI,
        source_functions: SourceFunctionMappingAPI,
        function_chunks: list[FunctionChunk],
        *,
        repository_id: str,
        source_language: str,
    ) -> None:
        self.source_tests = source_tests
        self.source_functions = source_functions
        self.function_by_id = {chunk.chunk_id: chunk for chunk in function_chunks}
        self.repository_id = repository_id
        self.source_language = source_language

    @classmethod
    def from_artifact_dir(
        cls,
        artifact_dir: str | Path,
        *,
        embedder_kind: str = "unixcoder",
        model_path: str | Path | None = None,
        device: str = "auto",
        batch_size: int = 16,
    ) -> "TargetToSourceCodeAPI":
        root = Path(artifact_dir).expanduser().resolve()
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        artifacts = manifest["artifacts"]
        index_path = artifacts.get("source_test_index")
        if not index_path:
            raise RuntimeError(
                "The repository was initialized without a Source-test index"
            )
        test_api = SourceTestMappingAPI.from_index(
            index_path,
            embedder_kind=embedder_kind,
            model_path=model_path,
            device=device,
            batch_size=batch_size,
        )
        function_api = SourceFunctionMappingAPI.from_jsonl(
            artifacts["source_test_to_source_function"]
        )
        chunks = [
            FunctionChunk.from_dict(json.loads(line))
            for line in Path(artifacts["source_functions"])
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        return cls(
            test_api,
            function_api,
            chunks,
            repository_id=manifest["repository_id"],
            source_language=manifest["source_language"],
        )

    def locate_source_code(
        self,
        *,
        target_language: str,
        target_test_code: str,
        target_test_name: str | None = None,
        target_test_file: str | None = None,
        strategy: str = "fusion",
        top_k_source_tests: int = 5,
        top_k_source_functions: int | None = 5,
        mask_names: bool = False,
    ) -> str:
        retrieval = self.source_tests.locate_source_tests(
            project=self.repository_id,
            target_language=target_language,
            target_test_code=target_test_code,
            target_test_name=target_test_name,
            target_test_file=target_test_file,
            strategy=strategy,
            top_k=top_k_source_tests,
            mask_names=mask_names,
        )

        ranked_functions: list[dict[str, Any]] = []
        seen_function_ids: set[str] = set()
        for test_hit in retrieval["hits"]:
            mapping = self.source_functions.lookup_result(
                test_hit["source_test_id"], project=self.repository_id
            )
            for static_hit in mapping["source_functions"]:
                chunk = self.function_by_id.get(static_hit["chunk_id"])
                enriched = {
                    **static_hit,
                    "code": chunk.code if chunk else None,
                    "source_test_rank": test_hit["rank"],
                    "source_test_score": test_hit["score"],
                }
                if static_hit["chunk_id"] in seen_function_ids:
                    continue
                seen_function_ids.add(static_hit["chunk_id"])
                ranked_functions.append(enriched)

        selected_functions = (
            ranked_functions
            if top_k_source_functions is None
            else ranked_functions[:top_k_source_functions]
        )
        return _join_function_codes(selected_functions)


def locate_source_code(
    artifact_dir: str | Path,
    *,
    target_language: str,
    target_test_code: str,
    target_test_name: str | None = None,
    target_test_file: str | None = None,
    strategy: str = "fusion",
    top_k_source_tests: int = 5,
    top_k_source_functions: int | None = 5,
    mask_names: bool = False,
    embedder_kind: str = "unixcoder",
    model_path: str | Path | None = None,
    device: str = "auto",
    batch_size: int = 16,
) -> str:
    """One-shot wrapper; reuse TargetToSourceCodeAPI for repeated queries."""
    api = TargetToSourceCodeAPI.from_artifact_dir(
        artifact_dir,
        embedder_kind=embedder_kind,
        model_path=model_path,
        device=device,
        batch_size=batch_size,
    )
    return api.locate_source_code(
        target_language=target_language,
        target_test_code=target_test_code,
        target_test_name=target_test_name,
        target_test_file=target_test_file,
        strategy=strategy,
        top_k_source_tests=top_k_source_tests,
        top_k_source_functions=top_k_source_functions,
        mask_names=mask_names,
    )
