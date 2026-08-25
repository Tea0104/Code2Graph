from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .languages import LANGUAGE_SPECS, normalize_languages


IGNORED_DIR_NAMES = frozenset(
    {
        ".code2graph",
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "venv",
    }
)
TEST_DIR_NAMES = frozenset({"test", "tests", "public_test", "public_tests", "spec", "specs"})


@dataclass(frozen=True)
class RepositoryFile:
    path: Path
    relative_path: str
    language: str
    is_test: bool


def is_ignored_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    lowered = {part.lower() for part in relative.parts}
    return bool(lowered & IGNORED_DIR_NAMES) or any(
        part.lower().endswith(".egg-info") for part in relative.parts
    )


def is_test_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    directories = {part.lower() for part in relative.parts[:-1]}
    stem = path.stem.lower()
    return (
        bool(directories & TEST_DIR_NAMES)
        or stem.startswith(("test_", "public_test_"))
        or stem.endswith(("_test", "_tests", "_spec"))
        or "public_test" in stem
        or "test_public" in stem
    )


def scan_repository(
    source_root: str | Path,
    languages: str | Iterable[str] | None = None,
) -> list[RepositoryFile]:
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source repository does not exist: {root}")
    selected = (
        list(LANGUAGE_SPECS)
        if languages is None
        else normalize_languages(languages if isinstance(languages, str) else list(languages))
    )
    result: list[RepositoryFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or is_ignored_path(path, root):
            continue
        for language in selected:
            if path.suffix.lower() not in LANGUAGE_SPECS[language].extensions:
                continue
            result.append(
                RepositoryFile(
                    path=path,
                    relative_path=path.relative_to(root).as_posix(),
                    language=language,
                    is_test=is_test_path(path, root),
                )
            )
            break
    return result


def detect_languages(source_root: str | Path) -> list[tuple[str, int]]:
    counts = Counter(item.language for item in scan_repository(source_root))
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
