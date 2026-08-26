"""面向论文主程序的 Code2Graph 三个统一接口。

这个模块只做适配和参数整理，底层实现仍然由现有组件负责：

* ``code2graph.initialization`` 负责 Source 仓库初始化；
* ``file_topo_sort`` 负责文件依赖分析和翻译顺序；
* ``code2graph.mapping`` 负责 Target test -> Source test -> Source code。

这样论文主程序只需要依赖本模块，不需要直接了解底层目录结构。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .initialization import InitializationResult, initialize_repository
from .mapping import TargetToSourceCodeAPI
from file_topo_sort import analyze_project, plan_translation_batch


class Code2GraphPipeline:
    """论文主程序使用的统一门面。

    初始化和翻译顺序是仓库级操作；定位接口可以在同一个对象上重复调用，
    这样不会为每一次实时测试请求重复加载向量索引和 UniXcoder 模型。
    """

    def __init__(self, mapping_api: TargetToSourceCodeAPI) -> None:
        self._mapping_api = mapping_api

    @classmethod
    def from_artifact_dir(
        cls,
        artifact_dir: str | Path,
        *,
        embedder_kind: str = "unixcoder",
        model_path: str | Path | None = None,
        device: str = "auto",
        batch_size: int = 16,
    ) -> "Code2GraphPipeline":
        """从 ``initialize`` 生成的 ``.code2graph`` 目录加载可查询 Pipeline。"""
        return cls(
            TargetToSourceCodeAPI.from_artifact_dir(
                artifact_dir,
                embedder_kind=embedder_kind,
                model_path=model_path,
                device=device,
                batch_size=batch_size,
            )
        )

    def locate_target_test_to_source_code(
        self,
        *,
        target_language: str,
        target_test_code: str,
        target_test_name: str | None = None,
        target_test_file: str | None = None,
        strategy: str = "fusion",
        top_k_source_tests: int = 5,
        top_k_source_functions: int | None = 5,
        mask_names: bool = False,
    ) -> str:
        """将一个 Target test 函数定位到 Source function 代码字符串。"""
        return self._mapping_api.locate_source_code(
            target_language=target_language,
            target_test_code=target_test_code,
            target_test_name=target_test_name,
            target_test_file=target_test_file,
            strategy=strategy,
            top_k_source_tests=top_k_source_tests,
            top_k_source_functions=top_k_source_functions,
            mask_names=mask_names,
        )


def initialize(
    source_repository: str | Path,
    **kwargs: Any,
) -> InitializationResult:
    """接口一：从 Source 仓库建立后续流程所需的全部基础设施。

    该过程会扫描仓库、解析代码图、提取 Source function 和 Source test、
    建立 Source test 向量索引、生成 Source test -> Source function 静态映射，
    并保存文件级翻译顺序。Target 仓库不是初始化的必需输入。
    """
    return initialize_repository(source_repository, **kwargs)


def get_translation_order(
    source_repository: str | Path,
    languages: str | list[str],
    *,
    include_tests: bool = False,
) -> dict[str, Any]:
    """接口二：分析 Source 仓库并返回文件级翻译顺序。

    返回值不仅包含 ``translation_order``，还包含依赖边、功能链、循环和
    自动断开的边，方便主程序展示原因。当前接口返回完整分析结果；
    如果需要结合 Agent 已翻译进度规划下一批闭环文件，请使用
    get_translation_batch。
    """
    return analyze_project(
        source_repository,
        languages,
        include_tests=include_tests,
    )


def get_translation_batch(
    source_repository: str | Path,
    languages: str | list[str],
    translated_files: list[str] | tuple[str, ...] = (),
    requested_count: int = 1,
    *,
    include_tests: bool = False,
) -> dict[str, Any]:
    """接口二的闭环版本：规划下一批可实时验证的翻译文件。"""
    return plan_translation_batch(
        source_repository,
        languages,
        translated_files,
        requested_count,
        include_tests=include_tests,
    )


def locate_target_test_to_source_code(
    artifact_dir: str | Path,
    *,
    target_language: str,
    target_test_code: str,
    target_test_name: str | None = None,
    target_test_file: str | None = None,
    strategy: str = "fusion",
    top_k_source_tests: int = 5,
    top_k_source_functions: int | None = 5,
    mask_names: bool = False,
    embedder_kind: str = "unixcoder",
    model_path: str | Path | None = None,
    device: str = "auto",
    batch_size: int = 16,
) -> str:
    """接口三：一次性执行 Target test -> Source code 查询。

    如果需要连续处理多个实时测试，应优先使用
    ``Code2GraphPipeline.from_artifact_dir``，避免每次重复加载模型。
    返回值是匹配到的 Source function 代码字符串；多个函数按排名用空行拼接。
    """
    pipeline = Code2GraphPipeline.from_artifact_dir(
        artifact_dir,
        embedder_kind=embedder_kind,
        model_path=model_path,
        device=device,
        batch_size=batch_size,
    )
    return pipeline.locate_target_test_to_source_code(
        target_language=target_language,
        target_test_code=target_test_code,
        target_test_name=target_test_name,
        target_test_file=target_test_file,
        strategy=strategy,
        top_k_source_tests=top_k_source_tests,
        top_k_source_functions=top_k_source_functions,
        mask_names=mask_names,
    )
