"""Data structure skeletons for code graph nodes and edges."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Node:
    """Code graph node."""

    id: str
    type: str
    name: str
    path: str | None = None
    lineno: int | None = None
    end_lineno: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the node to a serializable dictionary."""
        return asdict(self)


@dataclass
class Edge:
    """Code graph edge."""

    source: str
    target: str
    type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the edge to a serializable dictionary."""
        return asdict(self)
