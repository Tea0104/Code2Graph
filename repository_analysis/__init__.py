"""Shared repository parsing and dependency primitives."""

from .dependencies import (
    FileDependencyGraph,
    ImportExtractor,
    ImportReference,
    build_file_dependency_graph,
)
from .graph import EdgeRecord, GraphBuilder, NodeRecord, ScopeState
from .languages import (
    LANGUAGE_ALIASES,
    LANGUAGE_EXTENSIONS,
    LANGUAGE_SPECS,
    LanguageSpec,
    display_language,
    normalize_language,
    normalize_languages,
)
from .parsing import node_text, parser_for
from .repository import RepositoryFile, detect_languages, scan_repository

__all__ = [
    "ImportExtractor",
    "ImportReference",
    "FileDependencyGraph",
    "EdgeRecord",
    "GraphBuilder",
    "LANGUAGE_ALIASES",
    "LANGUAGE_EXTENSIONS",
    "LANGUAGE_SPECS",
    "LanguageSpec",
    "NodeRecord",
    "RepositoryFile",
    "ScopeState",
    "detect_languages",
    "build_file_dependency_graph",
    "display_language",
    "node_text",
    "normalize_language",
    "normalize_languages",
    "parser_for",
    "scan_repository",
]
