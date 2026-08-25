from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

from tree_sitter import Parser

from repository_analysis.graph import EdgeRecord, GraphBuilder, NodeRecord, ScopeState
from repository_analysis.languages import LANGUAGE_EXTENSIONS as SHARED_LANGUAGE_EXTENSIONS
from repository_analysis.parsing import node_text as shared_node_text
from repository_analysis.parsing import parser_for as shared_parser_for

FULL_GRAPH_LANGUAGES = ("python", "cpp")
LANGUAGE_EXTENSIONS: dict[str, set[str]] = {
    language: set(SHARED_LANGUAGE_EXTENSIONS[language])
    for language in FULL_GRAPH_LANGUAGES
}

SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules", "build"}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _node_text(source: str, node) -> str:
    return shared_node_text(source, node)


def _line_start(node) -> int:
    return node.start_point.row + 1


def _line_end(node) -> int:
    return node.end_point.row + 1


def _safe_name(text: str | None) -> str:
    normalized = (text or "").strip()
    return normalized if normalized else "Entity"


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def _identifier_text(node, source: str) -> str | None:
    if node is None:
        return None
    text = _node_text(source, node).strip()
    if not text:
        return None
    return text.replace("*", "").replace("&", "").replace("::", "::").strip()


def parser_for(language: str) -> Parser:
    """Backward-compatible access to the shared Tree-sitter parser."""
    if language not in FULL_GRAPH_LANGUAGES:
        raise ValueError(f"Unsupported full-graph language: {language}")
    return shared_parser_for(language)


def node_text(source: str, node) -> str:
    """Return source text covered by a tree-sitter node."""
    return _node_text(source, node)


def _extensions_for(language: str) -> set[str]:
    return LANGUAGE_EXTENSIONS[language]


def _module_name(path: Path) -> str:
    return path.stem or path.name


class BaseExtractor:
    def __init__(self, graph: GraphBuilder, language: str, source_root: Path) -> None:
        self.graph = graph
        self.language = language
        self.source_root = source_root
        self.parser = parser_for(language)
        self.scope_index: dict[str, ScopeState] = {}

    def extract_file(self, path: Path) -> None:
        source = _read_text(path)
        tree = self.parser.parse(source.encode("utf-8"))
        relative_path = path.relative_to(self.source_root).as_posix()
        module_name = _module_name(path)
        module_id = self.graph.add_node("Module", module_name, relative_path, 1, max(1, source.count("\n") + 1))
        module_scope = ScopeState(module_id, "module", module_name, relative_path, 1, max(1, source.count("\n") + 1))
        self.scope_index[module_id] = module_scope
        self._collect_definitions(tree.root_node, source, module_scope)
        self._collect_references(tree.root_node, source, module_scope)

    def _collect_definitions(self, node, source: str, scope: ScopeState) -> None:
        raise NotImplementedError

    def _collect_references(self, node, source: str, scope: ScopeState) -> None:
        raise NotImplementedError

    def _resolve_name(self, name: str, scope_stack: list[ScopeState]) -> str | None:
        for scope in reversed(scope_stack):
            if name in scope.symbols:
                return scope.symbols[name]
        return None

    def _current_function_scope(self, scope_stack: list[ScopeState]) -> ScopeState | None:
        for scope in reversed(scope_stack):
            if scope.kind == "function":
                return scope
        return None

    def _current_class_scope(self, scope_stack: list[ScopeState]) -> ScopeState | None:
        for scope in reversed(scope_stack):
            if scope.kind == "class":
                return scope
        return None


from .cpp_extractor import CppExtractor
from .python_extractor import PythonExtractor


def _select_extractor(language: str, graph: GraphBuilder, source_root: Path) -> BaseExtractor:
    if language == "python":
        return PythonExtractor(graph, source_root)
    if language == "cpp":
        return CppExtractor(graph, source_root)
    raise ValueError(f"Unsupported language: {language}")


def _iter_source_files(source_root: Path, languages: Iterable[str]) -> Iterable[tuple[str, Path]]:
    language_set = list(languages)
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or _should_skip(path):
            continue
        suffix = path.suffix.lower()
        for language in language_set:
            if suffix in LANGUAGE_EXTENSIONS[language]:
                yield language, path
                break


def extract_repository(source_root: Path, languages: Iterable[str]) -> GraphBuilder:
    graph = GraphBuilder()
    for language in languages:
        if language not in LANGUAGE_EXTENSIONS:
            raise ValueError(f"Unsupported language: {language}")

    extractor_cache: dict[str, BaseExtractor] = {}
    for language, path in _iter_source_files(source_root, languages):
        extractor = extractor_cache.get(language)
        if extractor is None:
            extractor = _select_extractor(language, graph, source_root)
            extractor_cache[language] = extractor
        extractor.extract_file(path)
    return graph
