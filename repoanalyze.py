"""Code2Graph 的唯一对外入口。

外部程序只需要导入本文件中的 ``RepoAnalyze``。三个公开能力分别由
``initrepo``、``translation_order`` 和 ``target_test_to_source`` 实现，
这里负责把它们组合成一个清晰的使用入口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from common.artifacts import check_required_artifacts, get_artifact_paths, load_manifest
from common.embedding import make_embedder
from common.index import load_source_test_index, search_source_tests
from initrepo.pipeline import initialize_repository
from target_test_to_source.mapping import (
    join_source_function_code,
    load_source_functions,
    load_source_test_mapping,
    lookup_source_function_ids,
)


class RepoAnalyze:
    """面向外部调用的仓库分析接口。"""

    def __init__(
        self,
        *,
        embedder_kind: str = "unixcoder",
        model_path: str | Path | None = None,
        device: str = "auto",
        batch_size: int = 16,
        source_language: str | None = None,
    ) -> None:
        # 保存一次配置，后面的仓库初始化和查询都复用它。
        self.embedder_kind = embedder_kind
        self.model_path = model_path
        self.device = device
        self.batch_size = batch_size
        self.source_language = source_language
        self._state: dict | None = None
        self._embedder = None

    def initrepo(self, source_path: str | Path) -> None:
        """扫描仓库，并生成后续接口需要的七个核心产物。"""
        initialize_repository(
            source_path,
            source_language=self.source_language,
            embedder_kind=self.embedder_kind,
            model_path=self.model_path,
            device=self.device,
            batch_size=self.batch_size,
        )
        # 初始化可能覆盖旧文件，下一次查询必须重新读取新结果。
        self._state = None

    def check(self, source_path: str | Path) -> None:
        """检查产物；缺少或损坏时自动初始化。"""
        paths = get_artifact_paths(source_path)
        if not check_required_artifacts(paths):
            self.initrepo(source_path)

    def get_all_translation_files(self, source_path: str | Path) -> list[str]:
        """返回初始化时保存的全部文件翻译顺序。"""
        self.check(source_path)
        order_path = get_artifact_paths(source_path)["translation_order"]
        payload = json.loads(order_path.read_text(encoding="utf-8"))
        return payload["translation_order"]

    def get_translation_order(
        self,
        source_path: str | Path,
        number: int,
        already: Sequence[str | Path] = (),
        *,
        include_tests: bool = False,
    ) -> list[str]:
        """从固定顺序表中排除已翻译文件，并返回下一批文件。"""
        if number < 0:
            raise ValueError("number 不能是负数")
        self.check(source_path)
        order_path = get_artifact_paths(source_path)["translation_order"]
        payload = json.loads(order_path.read_text(encoding="utf-8"))
        order = payload["translation_order"]

        # 把 already 统一成顺序表使用的仓库相对路径。
        root = Path(source_path).expanduser().resolve()
        translated: set[str] = set()
        for value in already:
            path = Path(value).expanduser()
            if path.is_absolute():
                path = path.resolve().relative_to(root)
            translated.add(path.as_posix())

        return [path for path in order if path not in translated][:number]

    def target_test_to_source_code(
        self,
        source_path: str | Path,
        target_language: str,
        target_test_code: str,
    ) -> str:
        """检索最相近的 Source test，并返回它对应的 Source function 代码。"""
        self.check(source_path)
        artifact_dir = get_artifact_paths(source_path)["root"]
        state = self._load_repository_state(artifact_dir)
        query_text = f"Language: {target_language}\nCode:\n{target_test_code}"
        matches = search_source_tests(
            state["source_test_index"],
            query_text,
            self._embedder,
            project=state["repository_id"],
            top_k=1,
        )
        if not matches:
            return ""

        function_ids = lookup_source_function_ids(
            matches[0]["source_test_id"],
            state["source_test_to_source_function"],
        )
        return join_source_function_code(function_ids, state["source_functions"])

    def _load_repository_state(self, artifact_dir: Path) -> dict:
        # 同一个 RepoAnalyze 实例重复查询时，复用已经加载的索引和模型。
        if self._state is not None and self._state["artifact_root"] == artifact_dir:
            return self._state

        paths = get_artifact_paths(artifact_dir.parent)
        manifest = load_manifest(paths["manifest"])
        self._embedder = make_embedder(
            self.embedder_kind,
            model_path=self.model_path,
            device=self.device,
            batch_size=self.batch_size,
        )
        self._state = {
            "artifact_root": artifact_dir,
            "repository_id": manifest["repository_id"],
            "source_language": manifest["source_language"],
            "source_test_index": load_source_test_index(
                paths["source_test_vectors"],
                paths["source_test_chunks"],
                paths["source_test_index_manifest"],
                self._embedder,
            ),
            "source_functions": load_source_functions(paths["source_functions"]),
            "source_test_to_source_function": load_source_test_mapping(
                paths["source_test_mapping"]
            ),
        }
        return self._state

__all__ = ["RepoAnalyze"]
