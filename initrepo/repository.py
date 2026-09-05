"""仓库路径、语言识别和 Source 文件扫描。"""

from __future__ import annotations

from collections import Counter
import json
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
PUBLIC_TEST_SUMMARY = "public_test_summary.json"


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


def load_public_test_files(root: Path) -> set[str]:
    """读取数据集提供的 public test 文件清单。"""
    summary_path = root / PUBLIC_TEST_SUMMARY
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"Source repository is missing {PUBLIC_TEST_SUMMARY}: {summary_path}"
        )

    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        values = payload["public_tests"]["test_files"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            f"{PUBLIC_TEST_SUMMARY} 必须包含 public_tests.test_files 列表"
        ) from exc

    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"{PUBLIC_TEST_SUMMARY} 的 test_files 必须是字符串列表")

    result: set[str] = set()
    for value in values:
        candidate = (root / value).resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"public test 文件不在仓库内: {value}") from exc
        result.add(relative)
    return result


def _looks_like_test(path: Path, root: Path) -> bool:
    """判断路径是否看起来像测试或辅助代码，但不把它认定为正式测试。"""
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
    public_test_files = load_public_test_files(root)
    result: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part.lower() in IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in EXTENSIONS[canonical]:
            relative = path.relative_to(root).as_posix()
            is_test = relative in public_test_files
            result.append({
                "path": path,
                "language": canonical,
                "is_test": is_test,
                "dirty_test": not is_test and _looks_like_test(path, root),
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
        [item["path"] for item in files if not item["is_test"] and not item["dirty_test"]],
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
