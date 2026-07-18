from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import LanguagePair, ProjectPaths


LANGUAGE_EXTENSIONS = {
    "Python": {".py"},
    "C++": {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"},
}

IGNORED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "htmlcov",
    "coverage",
    "build",
    "dist",
    "venv",
    ".venv",
    "node_modules",
}


@dataclass(frozen=True)
class PairLayout:
    root: Path
    pair: LanguagePair
    source_root: Path
    target_root: Path
    layout: str

    @classmethod
    def detect(cls, root: Path, pair: LanguagePair) -> "PairLayout":
        root = root.resolve()
        team_pair = root / pair.name
        if (team_pair / "source_projects").is_dir() and (team_pair / "target_projects").is_dir():
            return cls(root, pair, team_pair / "source_projects", team_pair / "target_projects", "team_subset")

        raw_source = root / "source_projects" / pair.source
        raw_target = root / "target_projects" / pair.source / pair.target
        if raw_source.is_dir() and raw_target.is_dir():
            return cls(root, pair, raw_source, raw_target, "raw_repotransbench")

        direct_source = root / "source_projects"
        direct_target = root / "target_projects"
        if direct_source.is_dir() and direct_target.is_dir():
            return cls(root, pair, direct_source, direct_target, "pair_root")

        raise FileNotFoundError(f"Cannot detect dataset layout under {root} for {pair.name}")

    def projects(self) -> list[ProjectPaths]:
        source = {path.name: path for path in self.source_root.iterdir() if path.is_dir()}
        target = {path.name: path for path in self.target_root.iterdir() if path.is_dir()}
        return [ProjectPaths(name, source.get(name), target.get(name)) for name in sorted(source.keys() | target.keys())]

    def project(self, name: str) -> ProjectPaths:
        source = self.source_root / name
        target = self.target_root / name
        return ProjectPaths(name, source if source.is_dir() else None, target if target.is_dir() else None)


def _is_ignored(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in parts)


def iter_language_files(root: Path, language: str, *, include_tests: bool = False) -> Iterable[Path]:
    extensions = LANGUAGE_EXTENSIONS.get(language)
    if extensions is None:
        raise ValueError(f"Unsupported language: {language}")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in extensions or _is_ignored(path, root):
            continue
        relative = path.relative_to(root)
        directory_parts = {part.lower() for part in relative.parts[:-1]}
        filename = path.name.lower()
        is_test = (
            bool(directory_parts & {"test", "tests", "public_test", "public_tests", "spec", "specs"})
            or "public_test" in filename
            or "test_public" in filename
            or filename.startswith("test_")
            or filename.endswith("_test" + path.suffix.lower())
        )
        if include_tests or not is_test:
            yield path


def public_test_files(root: Path, language: str) -> list[Path]:
    extensions = LANGUAGE_EXTENSIONS.get(language)
    if extensions is None:
        raise ValueError(f"Unsupported language: {language}")
    return [
        path for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in extensions
        and not _is_ignored(path, root)
        and (
            "public_tests" in {part.lower() for part in path.relative_to(root).parts}
            or "public_test" in path.name.lower()
            or "test_public" in path.name.lower()
            or "public" in path.stem.lower()
        )
    ]
