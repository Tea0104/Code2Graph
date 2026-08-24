from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DynamicBuildRecipe:
    """Project-specific build hints for dynamic test mapping.

    Recipes are intentionally data-only JSON so they can be reviewed and
    extended without changing the runner code.  They should describe how to
    build/test a project in a sandbox, not what function a test maps to.
    """

    project: str
    cmake_source_subdirs: list[str] | None = None
    cmake_args: list[str] = field(default_factory=list)
    build_args: list[str] = field(default_factory=list)
    build_targets: list[str] = field(default_factory=list)
    force_gtest: bool = False
    gtest_provider: str | None = None
    conan_requires: list[str] = field(default_factory=list)
    extra_include_subdirs: list[str] = field(default_factory=list)
    executable_globs: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DynamicBuildRecipe":
        return cls(
            project=str(payload["project"]),
            cmake_source_subdirs=list(payload["cmake_source_subdirs"]) if payload.get("cmake_source_subdirs") is not None else None,
            cmake_args=list(payload.get("cmake_args") or []),
            build_args=list(payload.get("build_args") or []),
            build_targets=list(payload.get("build_targets") or []),
            force_gtest=bool(payload.get("force_gtest", False)),
            gtest_provider=str(payload["gtest_provider"]) if payload.get("gtest_provider") else None,
            conan_requires=list(payload.get("conan_requires") or []),
            extra_include_subdirs=list(payload.get("extra_include_subdirs") or []),
            executable_globs=list(payload.get("executable_globs") or []),
            notes=str(payload.get("notes") or ""),
        )

    @property
    def schema_version(self) -> int:
        return 1

    def extra_include_dirs(self, source_dir: Path, build_dir: Path | None) -> list[Path]:
        result: list[Path] = []
        for value in self.extra_include_subdirs:
            if value == "$build_dir" and build_dir is not None:
                result.append(build_dir)
                continue
            path = Path(value)
            result.append(path if path.is_absolute() else source_dir / path)
        return result


def default_recipe_dir() -> Path:
    return Path(__file__).resolve().parent / "recipes"


def load_recipe(project: str, recipe_dir: Path | None = None) -> DynamicBuildRecipe | None:
    root = recipe_dir or default_recipe_dir()
    path = root / f"{project}.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    recipe = DynamicBuildRecipe.from_dict(payload)
    if recipe.project != project:
        raise ValueError(f"Recipe project mismatch in {path}: expected {project}, got {recipe.project}")
    return recipe
