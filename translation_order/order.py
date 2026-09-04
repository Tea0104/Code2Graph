"""生成翻译顺序，并读取已经保存的翻译顺序。"""

from __future__ import annotations

import heapq
import json
from pathlib import Path
import re

from initrepo.repository import normalize_language, normalize_source_path, scan_repository


def _python_target(root: Path, importer: Path, module: str) -> str | None:
    base = root / Path(module.replace(".", "/"))
    candidates = [base.with_suffix(".py"), base / "__init__.py"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def _cpp_target(root: Path, importer: Path, include: str) -> str | None:
    candidates = [importer.parent / include, root / include]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve().relative_to(root.resolve()).as_posix()
    return None


def _dependencies(root: Path, language: str, files: list[str]) -> dict[str, set[str]]:
    nodes = set(files)
    result = {path: set() for path in files}
    for relative in files:
        path = root / relative
        source = path.read_text(encoding="utf-8", errors="replace")
        targets: list[str | None]
        if language == "Python":
            modules = [item[0] or item[1] for item in re.findall(
                r"^\s*(?:from\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+import|import\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*))",
                source,
                re.MULTILINE,
            )]
            targets = [_python_target(root, path, module) for module in modules]
        else:
            includes = re.findall(r"^\s*#\s*include\s*\"([^\"]+)\"", source, re.MULTILINE)
            targets = [_cpp_target(root, path, include) for include in includes]
        result[relative].update(target for target in targets if target in nodes and target != relative)
    return result


def build_translation_order(source_root: Path, source_language: str, *, include_tests: bool = False) -> list[str]:
    """按依赖优先生成稳定的文件顺序。"""
    root = normalize_source_path(source_root)
    language = normalize_language(source_language)
    files = [
        item["path"].relative_to(root).as_posix()
        for item in scan_repository(root, language)
        if include_tests or not item["is_test"]
    ]
    dependencies = _dependencies(root, language, files)
    remaining = {path: set(values) for path, values in dependencies.items()}
    ready = [path for path, values in remaining.items() if not values]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        current = heapq.heappop(ready)
        order.append(current)
        for path in sorted(remaining):
            if current in remaining[path]:
                remaining[path].remove(current)
                if not remaining[path]:
                    heapq.heappush(ready, path)
    # 循环依赖无法严格排序，但仍然稳定地保留所有文件。
    order.extend(sorted(path for path in remaining if path not in order))
    return order


def save_translation_order(order_path: Path, translation_order: list[str]) -> None:
    order_path.parent.mkdir(parents=True, exist_ok=True)
    order_path.write_text(
        json.dumps({"translation_order": translation_order}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
