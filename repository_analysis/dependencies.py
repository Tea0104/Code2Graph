from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import posixpath
import re
import sys

from .languages import (
    LANGUAGE_EXTENSIONS,
    LANGUAGE_SPECS,
    normalize_language,
    normalize_languages,
)
from .parsing import node_text, parser_for
from .repository import scan_repository


@dataclass(frozen=True)
class ImportReference:
    value: str
    line: int


@dataclass
class FileDependencyGraph:
    """Repository file dependency graph shared by ordering and future planners."""

    adjacency: dict[str, list[str]]
    nodes: set[str]
    edge_lines: dict[tuple[str, str], int]
    languages: list[str]


class ImportExtractor:
    """Extract language-level import/include evidence from one source file."""

    def __init__(self, source_root: str | Path, language: str) -> None:
        self.source_root = Path(source_root).resolve()
        self.language = normalize_language(language)
        self._try_tree_sitter = True

    def extract(self, file_path: str | Path) -> list[ImportReference]:
        path = Path(file_path)
        if self._try_tree_sitter:
            try:
                return self._extract_with_tree_sitter(path)
            except (ImportError, ModuleNotFoundError):
                self._try_tree_sitter = False
                print(
                    f"Warning: tree-sitter unavailable; using fallback parser for {self.language}.",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(
                    f"Warning: tree-sitter failed for {path}: {exc}; using fallback parser.",
                    file=sys.stderr,
                )
        return self._extract_with_regex(path)

    def extract_imports(self, file_path: str | Path) -> list[tuple[str, int]]:
        """Backward-compatible tuple representation used by file ordering."""
        return [(item.value, item.line) for item in self.extract(file_path)]

    def _extract_with_tree_sitter(self, path: Path) -> list[ImportReference]:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = parser_for(self.language).parse(source.encode("utf-8"))
        raw: list[ImportReference] = []
        seen: set[str] = set()

        def add(value: str, line: int) -> None:
            cleaned = value.strip().strip('"').strip("'").strip("<").strip(">")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                raw.append(ImportReference(cleaned, line))

        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            line = node.start_point.row + 1
            if self.language == "python" and node.type == "import_statement":
                for index, child in enumerate(node.children):
                    if node.field_name_for_child(index) != "name":
                        continue
                    name_node = child.child_by_field_name("name") if child.type == "aliased_import" else child
                    add(node_text(source, name_node), line)
                continue
            if self.language == "python" and node.type == "import_from_statement":
                module_node = node.child_by_field_name("module_name")
                if module_node is not None:
                    module = node_text(source, module_node).strip()
                    add(module, line)
                    for index, child in enumerate(node.children):
                        if node.field_name_for_child(index) != "name":
                            continue
                        name_node = child.child_by_field_name("name") if child.type == "aliased_import" else child
                        imported = node_text(source, name_node).strip()
                        if imported and imported != "*":
                            add(f"{module}.{imported}", line)
                continue
            if self.language in {"c", "cpp"} and node.type == "preproc_include":
                path_node = node.child_by_field_name("path")
                if path_node is not None:
                    add(node_text(source, path_node), line)
                continue
            if self.language == "java" and node.type == "import_declaration":
                value = re.sub(r"^import\s+(?:static\s+)?|;\s*$", "", node_text(source, node).strip())
                add(value, line)
                continue
            if self.language == "csharp" and node.type == "using_directive":
                value = re.sub(
                    r"^(?:global\s+)?using\s+(?:static\s+)?|;\s*$",
                    "",
                    node_text(source, node).strip(),
                )
                if "=" in value:
                    value = value.split("=", 1)[1].strip()
                add(value, line)
                continue
            if self.language == "javascript" and node.type in {"import_statement", "export_statement"}:
                value = javascript_module_from_statement(node_text(source, node))
                if value:
                    add(value, line)
                continue
            if self.language == "javascript" and node.type == "call_expression":
                match = re.match(
                    r"\s*(?:require|import)\s*\(\s*(['\"])(.+?)\1\s*\)",
                    node_text(source, node),
                    re.DOTALL,
                )
                if match:
                    add(match.group(2), line)
                continue
            stack.extend(reversed(node.children))
        return raw

    def _extract_with_regex(self, path: Path) -> list[ImportReference]:
        source = path.read_text(encoding="utf-8", errors="replace")
        if self.language == "python":
            return python_imports_with_regex(source)

        raw: list[ImportReference] = []
        if self.language in {"c", "cpp"}:
            for match in re.finditer(r'#include\s+"([^"]+)"|#include\s+<([^>]+)>', source):
                raw.append(ImportReference(match.group(1) or match.group(2), line_of(source, match.start())))
        elif self.language == "java":
            for match in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", source, re.MULTILINE):
                raw.append(ImportReference(match.group(1), line_of(source, match.start())))
        elif self.language == "csharp":
            pattern = r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:[\w]+\s*=\s*)?([\w.]+)\s*;"
            for match in re.finditer(pattern, source, re.MULTILINE):
                raw.append(ImportReference(match.group(1), line_of(source, match.start())))
        elif self.language == "javascript":
            patterns = (
                r"(?:import|export)\s+(?:[\s\S]*?\s+from\s+)?(['\"])(.+?)\1",
                r"(?:require|import)\s*\(\s*(['\"])(.+?)\1\s*\)",
            )
            for pattern in patterns:
                for match in re.finditer(pattern, source):
                    raw.append(ImportReference(match.group(2), line_of(source, match.start())))
        return raw


def javascript_module_from_statement(statement: str) -> str | None:
    match = re.search(r"\bfrom\s*(['\"])(.+?)\1", statement, re.DOTALL)
    if match is None:
        match = re.search(r"(?:^|\s)(['\"])(.+?)\1", statement, re.DOTALL)
    return match.group(2) if match else None


def line_of(source: str, position: int) -> int:
    return source[:position].count("\n") + 1


def python_imports_with_regex(source: str) -> list[ImportReference]:
    raw: list[ImportReference] = []
    import_pattern = re.compile(r"^\s*import\s+([^#\n]+)", re.MULTILINE)
    from_pattern = re.compile(
        r"^\s*from\s+([.\w]+)\s+import\s+(\([^)]*\)|[^#\n]+)",
        re.MULTILINE,
    )
    for match in import_pattern.finditer(source):
        line = line_of(source, match.start())
        for item in match.group(1).split(","):
            name = item.strip().split(" as ", 1)[0].strip()
            if name:
                raw.append(ImportReference(name, line))
    for match in from_pattern.finditer(source):
        module = match.group(1).strip()
        line = line_of(source, match.start())
        raw.append(ImportReference(module, line))
        for item in match.group(2).strip().strip("()").split(","):
            name = item.strip().split(" as ", 1)[0].strip()
            if name and name != "*":
                raw.append(ImportReference(f"{module}.{name}", line))
    return raw


def _resolve_python_import(
    import_text: str,
    importer_rel_path: str,
    known_files: set[str],
) -> str | None:
    if import_text.startswith("."):
        importer_dir = Path(importer_rel_path).parent
        dots = len(import_text) - len(import_text.lstrip("."))
        module_part = import_text[dots:]
        for _ in range(dots - 1):
            importer_dir = importer_dir.parent
        target = (
            (importer_dir / module_part.replace(".", "/")).as_posix()
            if module_part
            else importer_dir.as_posix()
        )
        for candidate in (f"{target}.py", f"{target}/__init__.py"):
            if candidate in known_files:
                return candidate
        return None

    parts = import_text.split(".")
    importer_dir = Path(importer_rel_path).parent
    for length in range(len(parts), 0, -1):
        target = "/".join(parts[:length])
        candidates = (
            f"{target}.py",
            f"{target}/__init__.py",
            f"{(importer_dir / target).as_posix()}.py",
            f"{(importer_dir / target).as_posix()}/__init__.py",
        )
        for candidate in candidates:
            if candidate in known_files:
                return candidate
    return None


def _resolve_include(
    include_text: str,
    importer_rel_path: str,
    known_files: set[str],
    language: str,
) -> str | None:
    importer_dir = Path(importer_rel_path).parent
    extensions = LANGUAGE_EXTENSIONS[language]

    def candidates(base: str) -> list[str]:
        normalized = posixpath.normpath(base.replace("\\", "/"))
        if any(normalized.endswith(extension) for extension in extensions):
            return [normalized]
        return [f"{normalized}{extension}" for extension in extensions]

    for candidate in candidates((importer_dir / include_text).as_posix()):
        if candidate in known_files:
            return candidate
    for candidate in candidates(include_text):
        if candidate in known_files:
            return candidate
    for candidate in candidates(include_text):
        suffix = "/" + candidate
        matches = [
            path for path in known_files
            if path == candidate or path.endswith(suffix)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _resolve_javascript_import(
    import_text: str,
    importer_rel_path: str,
    known_files: set[str],
) -> list[str]:
    if not import_text.startswith((".", "/")):
        return []
    importer_dir = Path(importer_rel_path).parent
    raw_base = (
        (importer_dir / import_text).as_posix()
        if import_text.startswith(".")
        else import_text.lstrip("/")
    )
    base = posixpath.normpath(raw_base)
    extensions = LANGUAGE_EXTENSIONS["javascript"]
    candidates = [base]
    if Path(base).suffix.lower() not in extensions:
        candidates.extend(f"{base}{extension}" for extension in sorted(extensions))
        candidates.extend(
            f"{base}/index{extension}" for extension in sorted(extensions)
        )
    return [candidate for candidate in candidates if candidate in known_files][:1]


def _build_dotted_indexes(
    source_root: Path,
    known_files: set[str],
) -> dict[str, dict[str, list[str]]]:
    indexes: dict[str, dict[str, list[str]]] = {
        "java": defaultdict(list),
        "csharp": defaultdict(list),
    }
    for rel_path in sorted(known_files):
        path = source_root / rel_path
        suffix = path.suffix.lower()
        if suffix not in {".java", ".cs"}:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".java":
            match = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.MULTILINE)
            namespace = match.group(1) if match else ""
            language = "java"
        else:
            match = re.search(
                r"^\s*namespace\s+([\w.]+)\s*(?:[;{])", source, re.MULTILINE
            )
            namespace = match.group(1) if match else ""
            language = "csharp"
        full_name = f"{namespace}.{path.stem}" if namespace else path.stem
        indexes[language][full_name].append(rel_path)
        if namespace:
            indexes[language][namespace].append(rel_path)
    return indexes


def _resolve_dotted_import(
    import_text: str,
    language: str,
    source_root: Path,
    importer_rel_path: str,
    known_files: set[str],
    dotted_indexes: dict[str, dict[str, list[str]]],
) -> list[str]:
    value = import_text.strip().removesuffix(".*")
    index = dotted_indexes[language]
    direct = index.get(value, [])
    if direct:
        if len(direct) == 1 or value.endswith(Path(direct[0]).stem):
            return direct
        source = (source_root / importer_rel_path).read_text(
            encoding="utf-8", errors="replace"
        )
        return [
            candidate
            for candidate in direct
            if re.search(rf"\b{re.escape(Path(candidate).stem)}\b", source)
        ]

    extension = ".java" if language == "java" else ".cs"
    parts = value.split(".")
    for length in range(len(parts), 0, -1):
        type_name = ".".join(parts[:length])
        matches = index.get(type_name, [])
        if matches:
            return matches
        suffix = "/" + "/".join(parts[:length]) + extension
        path_matches = [path for path in known_files if path.endswith(suffix)]
        if len(path_matches) == 1:
            return path_matches
    return []


def _resolve_import(
    language: str,
    import_text: str,
    importer_rel_path: str,
    source_root: Path,
    known_files: set[str],
    dotted_indexes: dict[str, dict[str, list[str]]],
) -> list[str]:
    resolver = LANGUAGE_SPECS[language].resolver
    if resolver == "python":
        target = _resolve_python_import(import_text, importer_rel_path, known_files)
        return [target] if target else []
    if resolver == "include":
        target = _resolve_include(
            import_text, importer_rel_path, known_files, language
        )
        return [target] if target else []
    if resolver == "javascript":
        return _resolve_javascript_import(
            import_text, importer_rel_path, known_files
        )
    if resolver in {"java", "csharp"}:
        return _resolve_dotted_import(
            import_text,
            resolver,
            source_root,
            importer_rel_path,
            known_files,
            dotted_indexes,
        )
    raise ValueError(f"No resolver configured for {language}")


def _is_known_dotted_namespace(
    language: str,
    import_text: str,
    dotted_indexes: dict[str, dict[str, list[str]]],
) -> bool:
    resolver = LANGUAGE_SPECS[language].resolver
    if resolver not in {"java", "csharp"}:
        return False
    return import_text.strip().removesuffix(".*") in dotted_indexes[resolver]


def build_file_dependency_graph(
    source_root: str | Path,
    languages: str | list[str],
    *,
    include_tests: bool = False,
) -> FileDependencyGraph:
    """Scan a repository and resolve imports/includes to file-level edges.

    This is the shared front half of translation planning. Ordering, cycle
    breaking, and feature-chain policy intentionally remain in ``file_topo_sort``.
    """
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source repository does not exist: {root}")
    normalized = normalize_languages(languages)
    files = [
        item
        for item in scan_repository(root, normalized)
        if include_tests or not item.is_test
    ]
    nodes = {item.relative_path for item in files}
    dotted_indexes = _build_dotted_indexes(root, nodes)
    extractors = {
        language: ImportExtractor(root, language) for language in normalized
    }
    adjacency: dict[str, list[str]] = {}
    edge_lines: dict[tuple[str, str], int] = {}

    for item in files:
        seen: set[str] = set()
        resolved: list[str] = []
        for reference in extractors[item.language].extract(item.path):
            targets = _resolve_import(
                item.language,
                reference.value,
                item.relative_path,
                root,
                nodes,
                dotted_indexes,
            )
            internal_targets = [
                target for target in targets if target != item.relative_path
            ]
            for target in internal_targets:
                if target in seen:
                    continue
                seen.add(target)
                resolved.append(target)
                edge_lines.setdefault(
                    (item.relative_path, target), reference.line
                )
            if not internal_targets and not _is_known_dotted_namespace(
                item.language, reference.value, dotted_indexes
            ):
                external = f"ext:{reference.value}"
                if external not in seen:
                    seen.add(external)
                    resolved.append(external)
        adjacency[item.relative_path] = resolved

    for node in nodes:
        adjacency.setdefault(node, [])
    return FileDependencyGraph(adjacency, nodes, edge_lines, normalized)
