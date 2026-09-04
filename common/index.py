"""Source test 向量索引的简单函数。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from common.embedding import encode
from common.models import test_chunk_text


def build_source_test_index(
    source_tests: list[dict],
    embedder: dict[str, object],
) -> dict[str, object]:
    """把 Source test 向量化，并保存向量与 chunk 的对应关系。"""
    vectors = encode(embedder, [test_chunk_text(test) for test in source_tests])
    return {
        "chunks": source_tests,
        "vectors": np.asarray(vectors, dtype=np.float32),
        "embedder": embedder["name"],
    }


def save_source_test_index(
    index: dict[str, object],
    vectors_path: Path,
    chunks_path: Path,
    manifest_path: Path,
) -> None:
    """保存 Source test 索引的三个文件。"""
    chunks = index["chunks"]
    vectors = index["vectors"]
    if not isinstance(chunks, list) or not isinstance(vectors, np.ndarray):
        raise ValueError("Source test 索引格式错误")
    vectors_path.parent.mkdir(parents=True, exist_ok=True)
    chunks_path.write_text(
        "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks),
        encoding="utf-8",
    )
    np.save(vectors_path, vectors)
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "embedder": index["embedder"],
                "chunks": len(chunks),
                "dimension": int(vectors.shape[1]),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_source_test_index(
    vectors_path: Path,
    chunks_path: Path,
    manifest_path: Path,
    embedder: dict[str, object],
) -> dict[str, object]:
    """读取索引文件，并检查模型、chunk 数量和向量维度。"""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("embedder") != embedder["name"]:
        raise ValueError("索引使用的模型与当前查询模型不一致")
    chunks = [
        json.loads(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    vectors = np.load(vectors_path, allow_pickle=False)
    dimension = vectors.shape[1] if vectors.ndim == 2 else None
    if manifest.get("chunks") != len(chunks):
        raise ValueError("索引 manifest 与 Source test 数量不一致")
    if manifest.get("dimension") != dimension:
        raise ValueError("索引 manifest 与向量维度不一致")
    return {"chunks": chunks, "vectors": vectors, "embedder": manifest["embedder"]}


def search_source_tests(
    index: dict[str, object],
    query_text: str,
    embedder: dict[str, object],
    *,
    project: str,
    top_k: int = 1,
) -> list[dict]:
    """将查询向量与当前仓库的 Source test 向量比较并排序。"""
    if top_k <= 0:
        raise ValueError("top_k 必须大于 0")
    if embedder["name"] != index["embedder"]:
        raise ValueError("索引使用的模型与当前查询模型不一致")
    chunks = index["chunks"]
    vectors = index["vectors"]
    if not isinstance(chunks, list) or not isinstance(vectors, np.ndarray):
        raise ValueError("Source test 索引格式错误")
    if not query_text.strip() or not chunks:
        return []
    query_rows = encode(embedder, [query_text])
    if query_rows.ndim != 2 or query_rows.shape[0] != 1:
        raise ValueError("查询向量格式错误")
    if query_rows.shape[1] != vectors.shape[1]:
        raise ValueError("查询向量维度与索引不一致")
    scored = [
        (float(vectors[index] @ query_rows[0]), chunk)
        for index, chunk in enumerate(chunks)
        if chunk.get("project") == project
    ]
    scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
    return [
        {"source_test_id": chunk["chunk_id"], "score": score, "source_test": chunk}
        for score, chunk in scored[:top_k]
    ]
