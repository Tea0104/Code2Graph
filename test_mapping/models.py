from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


Confidence = Literal["high", "medium", "low", "ambiguous"]


@dataclass(frozen=True)
class LanguagePair:
    source: str
    target: str

    @property
    def name(self) -> str:
        return f"{self.source}_to_{self.target}"

    @classmethod
    def parse(cls, value: str) -> "LanguagePair":
        if "_to_" not in value:
            raise ValueError(f"Language pair must look like Python_to_C++: {value}")
        source, target = value.split("_to_", 1)
        if not source or not target:
            raise ValueError(f"Invalid language pair: {value}")
        return cls(source, target)


@dataclass(frozen=True)
class ProjectPaths:
    project: str
    source_dir: Path | None
    target_dir: Path | None


@dataclass
class FunctionChunk:
    chunk_id: str
    project: str
    language: str
    file: str
    name: str
    qualified_name: str
    code: str
    start_line: int
    end_line: int
    parent: str | None = None
    calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestChunk:
    chunk_id: str
    project: str
    language: str
    file: str
    name: str
    qualified_name: str
    code: str
    chunk_text: str
    start_line: int
    end_line: int
    framework: str
    parent: str | None = None
    fixture: str | None = None
    imports: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TestChunk":
        return cls(**value)


@dataclass(frozen=True)
class Alignment:
    source_chunk_id: str
    target_chunk_ids: tuple[str, ...]
    method: str
    confidence: Confidence
    score: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["target_chunk_ids"] = list(self.target_chunk_ids)
        return value


@dataclass(frozen=True)
class QueryVariant:
    strategy: str
    text: str
    source_test_id: str
    source_function_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float
    rank: int
    strategy: str
    chunk: TestChunk

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "score": self.score,
            "rank": self.rank,
            "strategy": self.strategy,
            "chunk": self.chunk.to_dict(),
        }


@dataclass
class LocationResult:
    query_id: str
    requested_strategy: str
    used_strategies: list[str]
    fallback_triggered: bool
    confidence: float
    margin: float
    hits: list[SearchHit]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "requested_strategy": self.requested_strategy,
            "used_strategies": self.used_strategies,
            "fallback_triggered": self.fallback_triggered,
            "confidence": self.confidence,
            "margin": self.margin,
            "hits": [hit.to_dict() for hit in self.hits],
            "diagnostics": self.diagnostics,
        }
