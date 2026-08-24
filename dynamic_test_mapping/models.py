from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BuildStatus = Literal[
    "not_attempted",
    "configured",
    "built",
    "test_discovered",
    "coverage_collected",
    "failed",
]


@dataclass(frozen=True)
class DynamicFunctionHit:
    rank: int
    chunk_id: str
    file: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    covered_lines: int
    executable: str
    test_filter: str | None
    score: float
    confidence: str = "dynamic"
    resolution_reason: str = "dynamic_coverage"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DynamicProbeRecord:
    schema_version: int
    project: str
    source_test_id: str
    source_test_nodeid: str
    source_test_file: str
    source_test_name: str
    source_test_framework: str
    status: str
    source_functions: list[DynamicFunctionHit] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_functions"] = [item.to_dict() for item in self.source_functions]
        return value


@dataclass
class DynamicProjectReport:
    project: str
    build_status: BuildStatus
    build_system: str | None = None
    source_dir: str | None = None
    build_dir: str | None = None
    executable_count: int = 0
    listed_test_count: int = 0
    selected_test_count: int = 0
    dynamic_mapped_test_count: int = 0
    error_stage: str | None = None
    error: str | None = None
    log_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
