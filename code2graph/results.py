from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class InitializationResult:
    schema_version: int
    repository_id: str
    source_root: str
    source_language: str
    artifact_dir: str
    source_file_count: int
    source_test_file_count: int
    source_function_count: int
    source_test_count: int
    artifacts: dict[str, str | None]
    reports: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
