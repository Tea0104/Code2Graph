import json
from array import array
from pathlib import Path

import faiss
import numpy as np


class FaissQueryStore:
    def __init__(self, base_path=None, dim=128, max_items=10):
        self.dim = dim
        self.max_items = max_items
        self.base_path = Path(base_path or Path(__file__).with_name("faiss_cache"))
        self.index_path = self.base_path.with_suffix(".index")
        self.meta_path = self.base_path.with_suffix(".json")
        self.index = faiss.IndexFlatIP(dim)
        self.meta = []
        if self.index_path.exists() and self.meta_path.exists():
            self.index = faiss.read_index(str(self.index_path))
            self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))

    def _vec(self, text):
        vec = array("f", [0.0]) * self.dim
        for token in text.lower().split():
            vec[hash(token) % self.dim] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return np.asarray([v / norm for v in vec], dtype="float32")

    def get(self, query, kind=None, limit=30, threshold=0.95):
        if not self.meta:
            return None
        q = self._vec(query)[None, :]
        scores, ids = self.index.search(q, 1)
        if ids[0][0] < 0 or scores[0][0] < threshold:
            return None
        item = self.meta[ids[0][0]]
        if item["kind"] != kind or item["limit"] != limit:
            return None
        return item["payload"]

    def put(self, query, payload, kind=None, limit=30):
        self.index.add(self._vec(query)[None, :])
        self.meta.append({"query": query, "kind": kind, "limit": limit, "payload": payload})
        if len(self.meta) > self.max_items:
            self.meta = self.meta[-self.max_items:]
            self.index = faiss.IndexFlatIP(self.dim)
            if self.meta:
                self.index.add(np.vstack([self._vec(item["query"]) for item in self.meta]))
        self.save()

    def save(self):
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.index_path))
        self.meta_path.write_text(json.dumps(self.meta, ensure_ascii=False), encoding="utf-8")