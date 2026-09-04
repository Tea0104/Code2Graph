"""初始化和检索阶段使用的向量化函数。"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

import numpy as np


def make_embedder(
    kind: str,
    *,
    model_path: str | Path | None = None,
    device: str = "auto",
    batch_size: int = 16,
) -> dict[str, object]:
    """返回一个包含名称、维度和 encode 函数的普通字典。"""
    if kind == "hashing":
        return {"name": "hashing-512", "dimension": 512, "encode": hashing_encode}
    if kind == "unixcoder":
        if model_path is None:
            return {"name": "hashing-512", "dimension": 512, "encode": hashing_encode}
        return make_unixcoder(model_path, device=device, batch_size=batch_size)
    raise ValueError(f"不支持的向量化方式: {kind}")


def encode(embedder: dict[str, object], texts: list[str]) -> np.ndarray:
    """调用向量化字典中保存的 encode 函数。"""
    function = embedder["encode"]
    if not callable(function):
        raise ValueError("向量化器缺少 encode 函数")
    return function(texts)


def hashing_encode(texts: list[str]) -> np.ndarray:
    """用稳定的词元哈希生成向量，供没有模型时的本地测试使用。"""
    dimension = 512
    matrix = np.zeros((len(texts), dimension), dtype=np.float32)
    for row, text in enumerate(texts):
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text.lower())
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            matrix[row, value % dimension] += 1.0 if value & 1 else -1.0
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def make_unixcoder(
    model_path: str | Path,
    *,
    device: str,
    batch_size: int,
) -> dict[str, object]:
    """加载 UniXcoder，并把模型状态放在闭包中。"""
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")
    import torch
    from transformers import AutoModel, AutoTokenizer

    actual_device = (
        "cuda"
        if device == "auto" and torch.cuda.is_available()
        else ("cpu" if device == "auto" else device)
    )
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
    model = AutoModel.from_pretrained(str(model_path), local_files_only=True)
    model = model.to(actual_device).eval()
    dimension = int(model.config.hidden_size)

    def encode_unixcoder(texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, dimension), dtype=np.float32)
        rows: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            encoded = tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            encoded = {key: value.to(actual_device) for key, value in encoded.items()}
            with torch.inference_mode():
                output = model(**encoded).last_hidden_state
                mask = encoded["attention_mask"].unsqueeze(-1)
                pooled = (output * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            rows.append(pooled.cpu().numpy().astype(np.float32))
        return np.concatenate(rows, axis=0)

    return {
        "name": f"unixcoder:{Path(model_path).name}",
        "dimension": dimension,
        "encode": encode_unixcoder,
    }
