"""仓库路径、语言识别和 Source 文件扫描。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path


LANGUAGE_ALIASES = {
    "py": "python",
    "python": "python",
    "c++": "cpp",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
}
DISPLAY_NAMES = {"python": "Python", "cpp": "C++"}
EXTENSIONS = {
    "python": {".py"},
    "cpp": {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"},
}
IGNORED_DIRS = {
    ".git", ".code2graph", "__pycache__", ".venv", "venv", ".pytest_cache",
    "build", "dist", "coverage", "node_modules", "site-packages",
}
TEST_DIRS = {"test", "tests", "public_test", "public_tests", "spec", "specs"}
AUXILIARY_DIRS = {"bench", "benchmark", "benchmarks", "examples", "demos","example","sample","samples"}


def normalize_language(value: str) -> str:
    """把用户输入的语言名转换成内部名称。"""
    try:
        return LANGUAGE_ALIASES[value.strip().lower()]
    except KeyError as exc:
        raise ValueError("目前支持的 Source 语言只有 Python 和 C++") from exc


def display_language(value: str) -> str:
    return DISPLAY_NAMES[normalize_language(value)]


def normalize_source_path(source_path: str | Path) -> Path:
    root = Path(source_path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source repository does not exist: {root}")
    return root


def _is_test(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    directories = {part.lower() for part in relative.parts[:-1]}
    stem = path.stem.lower()
    return (
        bool(directories & (TEST_DIRS | AUXILIARY_DIRS))
        or stem.startswith(("test_", "public_test_"))
        or stem.endswith(("_test", "_tests", "_spec"))
        or "public_test" in stem
    )


def scan_repository(root: Path, language: str) -> list[dict]:
    canonical = normalize_language(language)
    result: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part.lower() in IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in EXTENSIONS[canonical]:
            result.append({
                "path": path,
                "language": canonical,
                "is_test": _is_test(path, root),
            })
    return result


def detect_source_language(source_root: Path) -> str:
    counts = Counter(item["language"] for language in EXTENSIONS for item in scan_repository(source_root, language))
    if not counts:
        raise ValueError("仓库中没有找到 Python 或 C++ Source 文件")
    return DISPLAY_NAMES[counts.most_common(1)[0][0]]


def scan_source_files(root: Path, language: str) -> tuple[list[Path], list[Path]]:
    files = scan_repository(root, language)
    return (
        [item["path"] for item in files if not item["is_test"]],
        [item["path"] for item in files if item["is_test"]],
    )


def load_source_repository(
    source_path: str | Path,
    source_language: str | None = None,
) -> dict:
    root = normalize_source_path(source_path)
    language = display_language(source_language) if source_language else detect_source_language(root)
    source_files, test_files = scan_source_files(root, language)
    return {
        "root": root,
        "project": root.name,
        "language": language,
        "source_files": source_files,
        "test_files": test_files,
    }
