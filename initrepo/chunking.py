"""Source function 和 Source test 的提取接口。"""

from __future__ import annotations

from pathlib import Path

from common.models import Chunk
from .parsing import extract_functions, extract_tests


def extract_source_functions(
    source_files: list[Path],
    source_root: Path,
    project: str,
    language: str,
) -> list[Chunk]:
    # 使用对应语言适配器和 Tree-sitter，生成函数粒度的字典。
    return extract_functions(source_files, source_root, project, language)


def extract_source_tests(
    test_files: list[Path],
    source_root: Path,
    project: str,
    language: str,
) -> list[Chunk]:
    # 使用对应语言适配器和 Tree-sitter，生成测试函数粒度的字典。
    chunks: list[Chunk] = []
    for path in test_files:
        parsed = extract_tests(path, source_root, project, language)
        chunks.extend(parsed)
    return chunks


def extract_source_chunks(
    source_files: list[Path],
    test_files: list[Path],
    source_root: Path,
    project: str,
    language: str,
) -> tuple[list[Chunk], list[Chunk]]:
    # 统一协调函数和测试提取，同时保持两类结果相互独立。
    return (
        extract_source_functions(source_files, source_root, project, language),
        extract_source_tests(test_files, source_root, project, language),
    )
