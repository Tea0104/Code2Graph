from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    display_name: str
    extensions: frozenset[str]
    tree_sitter_module: str
    dependency_resolver: str

    @property
    def resolver(self) -> str:
        """Compatibility name used by the existing ordering resolver."""
        return self.dependency_resolver


LANGUAGE_SPECS: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        "python", "Python", frozenset({".py"}), "tree_sitter_python", "python"
    ),
    "cpp": LanguageSpec(
        "cpp",
        "C++",
        frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}),
        "tree_sitter_cpp",
        "include",
    ),
    "c": LanguageSpec(
        "c", "C", frozenset({".c", ".h"}), "tree_sitter_c", "include"
    ),
    "java": LanguageSpec(
        "java", "Java", frozenset({".java"}), "tree_sitter_java", "java"
    ),
    "javascript": LanguageSpec(
        "javascript",
        "JavaScript",
        frozenset({".js", ".jsx", ".mjs", ".cjs"}),
        "tree_sitter_javascript",
        "javascript",
    ),
    "csharp": LanguageSpec(
        "csharp", "C#", frozenset({".cs"}), "tree_sitter_c_sharp", "csharp"
    ),
}

LANGUAGE_ALIASES = {
    "py": "python",
    "python": "python",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "java": "java",
    "js": "javascript",
    "javascript": "javascript",
    "node": "javascript",
    "cs": "csharp",
    "c#": "csharp",
    "csharp": "csharp",
}

LANGUAGE_EXTENSIONS: dict[str, set[str]] = {
    name: set(spec.extensions) for name, spec in LANGUAGE_SPECS.items()
}


def normalize_language(language: str) -> str:
    canonical = LANGUAGE_ALIASES.get(language.strip().lower())
    if canonical is None:
        supported = ", ".join(LANGUAGE_SPECS)
        raise ValueError(f"Unsupported language '{language}'. Supported: {supported}")
    return canonical


def normalize_languages(languages: str | Sequence[str]) -> list[str]:
    values = languages.split(",") if isinstance(languages, str) else languages
    normalized: list[str] = []
    for value in values:
        if not value.strip():
            continue
        canonical = normalize_language(value)
        if canonical not in normalized:
            normalized.append(canonical)
    if not normalized:
        raise ValueError("At least one language is required")
    return normalized


def display_language(language: str) -> str:
    return LANGUAGE_SPECS[normalize_language(language)].display_name
