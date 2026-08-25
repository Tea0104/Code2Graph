from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class NodeRecord:
    id: str
    kind: str
    name: str
    file: str
    startline: int
    endline: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EdgeRecord:
    edge_id: str
    source: str
    target: str | None
    kind: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ScopeState:
    node_id: str
    kind: str
    name: str
    file: str
    startline: int
    endline: int
    parent_id: str | None = None
    class_id: str | None = None
    symbols: dict[str, str | None] = field(default_factory=dict)


class GraphBuilder:
    """Language-neutral graph container shared by repository extractors."""

    def __init__(self) -> None:
        self.nodes: dict[str, NodeRecord] = {}
        self.edges: dict[str, EdgeRecord] = {}

    def add_node(
        self, kind: str, name: str, file: str, startline: int, endline: int
    ) -> str:
        node_id = f"{file}:{name}:{startline}"
        self.nodes.setdefault(
            node_id, NodeRecord(node_id, kind, name, file, startline, endline)
        )
        return node_id

    def add_edge(self, kind: str, source_id: str, target_id: str | None) -> None:
        edge_id = f"{source_id}:{target_id}:{kind}"
        self.edges.setdefault(
            edge_id, EdgeRecord(edge_id, source_id, target_id, kind)
        )

    def nodes_json(self) -> dict[str, dict[str, object]]:
        return {node_id: node.to_dict() for node_id, node in self.nodes.items()}

    def edges_json(self) -> dict[str, dict[str, object]]:
        return {edge_id: edge.to_dict() for edge_id, edge in self.edges.items()}
