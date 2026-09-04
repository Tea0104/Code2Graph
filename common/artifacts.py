"""七个核心产物的路径和简单读写函数。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def get_artifact_paths(source_path: str | Path) -> dict[str, Path]:
    """根据仓库路径，返回七个产物的路径字典。"""
    root = Path(source_path).expanduser().resolve()
    artifact_root = root / ".code2graph"
    index_root = artifact_root / "indexes" / "source_tests"
    return {
        "root": artifact_root,
        "manifest": artifact_root / "manifest.json",
        "translation_order": artifact_root / "translation" / "translation_order.json",
        "source_functions": artifact_root / "chunks" / "source_functions.jsonl",
        "source_test_vectors": index_root / "vectors.npy",
        "source_test_chunks": index_root / "chunks.jsonl",
        "source_test_index_manifest": index_root / "manifest.json",
        "source_test_mapping": artifact_root / "mappings" / "source_test_to_source_function.jsonl",
    }


def check_required_artifacts(paths: dict[str, Path]) -> bool:
    """检查七个文件是否存在，并检查 Source test 索引是否一致。"""
    required = (
        "manifest",
        "translation_order",
        "source_functions",
        "source_test_vectors",
        "source_test_chunks",
        "source_test_index_manifest",
        "source_test_mapping",
    )
    if not all(paths[key].is_file() for key in required):
        return False
    try:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        index_manifest = json.loads(
            paths["source_test_index_manifest"].read_text(encoding="utf-8")
        )
        vectors = np.load(paths["source_test_vectors"], allow_pickle=False)
        chunk_count = sum(
            1
            for line in paths["source_test_chunks"].read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        return (
            manifest.get("schema_version") == 1
            and vectors.ndim == 2
            and index_manifest.get("chunks") == chunk_count == len(vectors)
            and index_manifest.get("dimension") == vectors.shape[1]
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def save_manifest(manifest_path: Path, source_root: Path, source_language: str) -> None:
    """写入总目录 manifest。"""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "repository_id": source_root.name,
        "source_root": str(source_root),
        "source_language": source_language,
        "artifacts": {
            "translation_order": "translation/translation_order.json",
            "source_functions": "chunks/source_functions.jsonl",
            "source_test_vectors": "indexes/source_tests/vectors.npy",
            "source_test_chunks": "indexes/source_tests/chunks.jsonl",
            "source_test_index_manifest": "indexes/source_tests/manifest.json",
            "source_test_mapping": "mappings/source_test_to_source_function.jsonl",
        },
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_manifest(manifest_path: Path) -> dict:
    """读取总目录 manifest。"""
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_source_functions(functions_path: Path, functions: list[dict]) -> None:
    """把 Source function 字典逐行写入 JSONL。"""
    functions_path.parent.mkdir(parents=True, exist_ok=True)
    functions_path.write_text(
        "".join(json.dumps(function, ensure_ascii=False) + "\n" for function in functions),
        encoding="utf-8",
    )
