"""Code2Graph 对外统一入口。

调用流程只有三步：

1. ``initrepo``：为 Source 仓库建立代码图、Chunk、索引和映射；
2. ``get_translation_order``：根据静态顺序表返回下一批文件；
3. ``target_test_to_source_code``：用 Target test 找到 Source code。

本文件只负责检查状态和转发调用，具体解析、排序、Embedding 和映射
仍由仓库中的其他组件完成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from code2graph.api import Code2GraphPipeline
from code2graph.initialization import InitializationResult, initialize_repository
from file_topo_sort import get_translation_order as _get_translation_order


# 初始化索引时每次送入模型的 Chunk 数量；不是翻译文件数量。
DEFAULT_BATCH_SIZE = 16


class RepoAnalyze:
    """面向 Agent 的仓库级统一门面。"""

    def __init__(
        self,
        *,
        embedder_kind: str = "unixcoder",
        model_path: str | Path | None = None,
        device: str = "auto",
    ) -> None:
        """保存可复用运行配置。

        ``initrepo`` 仍负责按仓库生成产物；模型类型、模型路径和设备这类
        可跨仓库复用的配置由入口对象统一持有。
        """
        self.embedder_kind = embedder_kind
        self.model_path = model_path
        self.device = device
        self._initialization: InitializationResult | None = None
        self._source_path: Path | None = None
        self._artifact_dir: Path | None = None
        self._query_pipeline: Code2GraphPipeline | None = None

    def initrepo(
        self,
        source_path: str | Path,
        embedder_kind: str | None = None,
        model_path: str | Path | None = None,
        device: str | None = None,
        source_language: str | None = None,
    ) -> None:
        """初始化 Source 仓库；只有 ``source_path`` 是必须参数。"""
        self.embedder_kind = embedder_kind or self.embedder_kind
        self.model_path = self.model_path if model_path is None else model_path
        self.device = device or self.device
        result = initialize_repository(
            source_path,
            source_language=source_language,
            embedder_kind=self.embedder_kind,
            model_path=self.model_path,
            device=self.device,
            batch_size=DEFAULT_BATCH_SIZE,
        )
        self._remember_repository(result)

    def check(
        self,
        source_path: str | Path,
        *,
        embedder_kind: str | None = None,
        model_path: str | Path | None = None,
        device: str | None = None,
        source_language: str | None = None,
    ) -> None:
        """检查 ``.code2graph``；缺失或中断时自动调用 ``initrepo``。"""
        root = Path(source_path).expanduser().resolve()
        artifact_dir = root / ".code2graph"
        if not (artifact_dir / "manifest.json").is_file():
            self.initrepo(
                root,
                embedder_kind=embedder_kind or self.embedder_kind,
                model_path=self.model_path if model_path is None else model_path,
                device=device or self.device,
                source_language=source_language,
            )
            return

        if self._source_path != root or self._artifact_dir != artifact_dir:
            self._source_path, self._artifact_dir = root, artifact_dir
            self._query_pipeline = None
        self.embedder_kind = embedder_kind or self.embedder_kind
        self.model_path = self.model_path if model_path is None else model_path
        self.device = device or self.device

    def get_translation_order(
        self,
        source_path: str | Path,
        number: int,
        already: Sequence[str | Path] = (),
        *,
        include_tests: bool = False,
    ) -> list[str]:
        """检查仓库后，返回静态顺序中接下来的 ``number`` 个文件。"""
        self.check(source_path)
        return _get_translation_order(
            str(Path(source_path).expanduser().resolve()),
            number=number,
            already=[str(path) for path in already],
            include_tests=include_tests,
        )

    def target_test_to_source_code(
        self,
        source_path: str | Path,
        target_language: str,
        target_test_code: str,
    ) -> str:
        """返回最佳 Source test 对应的全部 Source function 代码。"""
        self.check(source_path)
        if self._query_pipeline is None:
            self._query_pipeline = Code2GraphPipeline.from_artifact_dir(
                self._require_artifact_dir(),
                embedder_kind=self.embedder_kind,
                model_path=self.model_path,
                device=self.device,
                batch_size=DEFAULT_BATCH_SIZE,
            )
        return self._query_pipeline.locate_target_test_to_source_code(
            target_language=target_language,
            target_test_code=target_test_code,
            top_k_source_tests=1,
            top_k_source_functions=None,
        )

    def _remember_repository(self, result: InitializationResult) -> None:
        self._initialization = result
        self._source_path = Path(result.source_root)
        self._artifact_dir = Path(result.artifact_dir)
        self._query_pipeline = None

    def _require_artifact_dir(self) -> Path:
        if self._artifact_dir is None:
            raise RuntimeError("Repository artifacts are not initialized.")
        return self._artifact_dir


__all__ = ["RepoAnalyze"]
