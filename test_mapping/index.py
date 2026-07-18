from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .embedding import Embedder, normalize
from .models import SearchHit, TestChunk


class VectorIndex:
    VERSION = 1

    def __init__(self, chunks: list[TestChunk], vectors: np.ndarray, embedder_name: str, backend: str = "numpy") -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Chunk and vector counts differ")
        self.chunks = chunks
        self.vectors = normalize(np.asarray(vectors, dtype=np.float32))
        self.embedder_name = embedder_name
        self.backend = backend
        self._faiss = None
        if backend not in {"numpy", "faiss", "auto"}:
            raise ValueError(f"Unsupported index backend: {backend}")
        if backend in {"faiss", "auto"}:
            try:
                import faiss
                self._faiss = faiss.IndexFlatIP(self.vectors.shape[1])
                self._faiss.add(self.vectors)
                self.backend = "faiss"
            except ImportError:
                if backend == "faiss":
                    raise RuntimeError("FAISS backend requested but faiss is not installed")
                self.backend = "numpy"

    @classmethod
    def build(cls, chunks: list[TestChunk], embedder: Embedder, *, backend: str = "numpy") -> "VectorIndex":
        return cls(chunks, embedder.encode([chunk.chunk_text for chunk in chunks]), embedder.name, backend)

    def search(self, text: str, embedder: Embedder, *, k: int = 5, project: str | None = None, strategy: str = "test") -> list[SearchHit]:
        if embedder.name != self.embedder_name:
            raise ValueError(f"Index uses {self.embedder_name}, query uses {embedder.name}")
        candidates = [index for index, chunk in enumerate(self.chunks) if project is None or chunk.project == project]
        if not candidates:
            return []
        query = embedder.encode([text])[0]
        # Project filters usually make a small exact NumPy scan faster than rebuilding a FAISS sub-index.
        if self._faiss is not None and project is None:
            searched_scores, searched_indices = self._faiss.search(query.reshape(1, -1), min(k, len(candidates)))
            return [
                SearchHit(self.chunks[index].chunk_id, float(score), rank, strategy, self.chunks[index])
                for rank, (score, index) in enumerate(zip(searched_scores[0], searched_indices[0]), start=1)
                if index >= 0
            ]
        scores = self.vectors[candidates] @ query
        order = np.argsort(-scores, kind="stable")[:k]
        return [
            SearchHit(self.chunks[candidates[position]].chunk_id, float(scores[position]), rank, strategy, self.chunks[candidates[position]])
            for rank, position in enumerate(order, start=1)
        ]

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        (directory / "chunks.jsonl").write_text(
            "".join(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n" for chunk in self.chunks), encoding="utf-8"
        )
        manifest = {"version": self.VERSION, "embedder": self.embedder_name, "backend": self.backend, "dimension": int(self.vectors.shape[1]), "chunks": len(self.chunks)}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "VectorIndex":
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["version"] != cls.VERSION:
            raise ValueError(f"Unsupported index version: {manifest['version']}")
        chunks = [TestChunk.from_dict(json.loads(line)) for line in (directory / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line]
        return cls(chunks, np.load(directory / "vectors.npy"), manifest["embedder"], manifest.get("backend", "numpy"))
