"""初始化流程编排接口。"""

from __future__ import annotations

from pathlib import Path

from common.artifacts import (
    get_artifact_paths,
    save_manifest,
    save_source_functions,
)
from initrepo.chunking import extract_source_chunks
from common.embedding import make_embedder
from common.index import build_source_test_index, save_source_test_index
from target_test_to_source.mapping import (
    build_source_test_mapping,
    save_source_test_mapping,
)
from translation_order.order import (
    build_translation_order,
    save_translation_order,
)
from initrepo.repository import load_source_repository


def initialize_repository(
    source_path: str | Path,
    *,
    source_language: str | None = None,
    embedder_kind: str = "unixcoder",
    model_path: str | Path | None = None,
    device: str = "auto",
    batch_size: int = 16,
) -> None:
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")
    # 1. 读取仓库并确定 Source language。
    repository = load_source_repository(source_path, source_language)

    # 2. 使用 Tree-sitter 提取 Source function 和 Source test。
    source_functions, source_tests = extract_source_chunks(
        repository["source_files"],
        repository["test_files"],
        repository["root"],
        repository["project"],
        repository["language"],
    )

    # 3. 初始化时只计算一次固定的文件翻译顺序。
    translation_order = build_translation_order(
        repository["root"],
        repository["language"],
    )

    # 4. 建立 Source test 到 Source function 的静态映射。
    mapping = build_source_test_mapping(source_tests, source_functions)

    # 5. 准备七个产物的标准路径。
    paths = get_artifact_paths(repository["root"])

    # 6. 保存函数、顺序和静态映射。
    save_source_functions(paths["source_functions"], source_functions)
    save_translation_order(paths["translation_order"], translation_order)
    save_source_test_mapping(paths["source_test_mapping"], mapping)

    # 7. 对 Source test 向量化，并保存索引的三个文件。
    embedder = make_embedder(
        embedder_kind,
        model_path=model_path,
        device=device,
        batch_size=batch_size,
    )
    index = build_source_test_index(source_tests, embedder)
    save_source_test_index(
        index,
        paths["source_test_vectors"],
        paths["source_test_chunks"],
        paths["source_test_index_manifest"],
    )

    # 8. 最后写 manifest，表示本次初始化已经完成。
    save_manifest(
        paths["manifest"],
        source_root=repository["root"],
        source_language=repository["language"],
    )
