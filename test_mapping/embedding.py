from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
from pathlib import Path
import re

import numpy as np


class Embedder(ABC):
    name: str
    dimension: int

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError


class HashingEmbedder(Embedder):
    """Deterministic dependency-light embedder for tests and lexical baselines."""

    def __init__(self, dimension: int = 512) -> None:
        self.dimension = dimension
        self.name = f"hashing-{dimension}"

    def encode(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text.lower())
            for token in tokens:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "little")
                matrix[row, value % self.dimension] += 1.0 if value & 1 else -1.0
        return normalize(matrix)


class UniXcoderEmbedder(Embedder):
    def __init__(self, model_path: str | Path, *, device: str = "auto", max_length: int = 512, batch_size: int = 16) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("UniXcoder requires torch and transformers") from exc
        self.torch = torch
        self.device = "cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device)
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
        self.model = AutoModel.from_pretrained(str(model_path), local_files_only=True).to(self.device).eval()
        self.dimension = int(self.model.config.hidden_size)
        self.name = f"unixcoder:{Path(model_path).name}"
        self.max_length = max_length
        self.batch_size = batch_size

    def encode(self, texts: list[str]) -> np.ndarray:
        rows: list[np.ndarray] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            encoded = self.tokenizer(batch, padding=True, truncation=True, max_length=self.max_length, return_tensors="pt")
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            with self.torch.inference_mode():
                output = self.model(**encoded).last_hidden_state
                mask = encoded["attention_mask"].unsqueeze(-1)
                pooled = (output * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                pooled = self.torch.nn.functional.normalize(pooled, p=2, dim=1)
            rows.append(pooled.cpu().numpy().astype(np.float32))
        return np.concatenate(rows, axis=0) if rows else np.empty((0, self.dimension), dtype=np.float32)


def normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def make_embedder(kind: str, *, model_path: str | None = None, device: str = "auto", batch_size: int = 16) -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "unixcoder":
        if not model_path:
            raise ValueError("--model-path is required for unixcoder")
        return UniXcoderEmbedder(model_path, device=device, batch_size=batch_size)
    raise ValueError(f"Unsupported embedder: {kind}")
