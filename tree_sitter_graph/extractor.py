from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Iterable

from tree_sitter import Language, Parser
import tree_sitter_cpp
import tree_sitter_python


LANGUAGE_EXTENSIONS: dict[str, set[str]] = {
    "python": {".py"},
    "cpp": {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"},
}

SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules", "build"}


@dataclass
class NodeRecord:
    id: str
    kind: str
    name: str
    file: str
    startline: int
    endline: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class EdgeRecord:
    edge_id: str
    source: str
    target: str | None
    kind: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ScopeState:
    node_id: str
    kind: str
    name: str
    file: str
    startline: int
    endline: int
    parent_id: str | None = None
    class_id: str | None = None
    symbols: dict[str, str | None] = field(default_factory=dict)


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, NodeRecord] = {}
        self.edges: dict[str, EdgeRecord] = {}

    def add_node(self, kind: str, name: str, file: str, startline: int, endline: int) -> str:
        node_id = f"{file}:{name}:{startline}"
        self.nodes.setdefault(node_id, NodeRecord(node_id, kind, name, file, startline, endline))
        return node_id

    def add_edge(self, kind: str, source_id: str, target_id: str | None) -> None:
        edge_id = f"{source_id}:{target_id}:{kind}"
        self.edges.setdefault(edge_id, EdgeRecord(edge_id, source_id, target_id, kind))

    def nodes_json(self) -> dict[str, dict[str, object]]:
        return {node_id: node.to_dict() for node_id, node in self.nodes.items()}

    def edges_json(self) -> dict[str, dict[str, object]]:
        return {edge_id: edge.to_dict() for edge_id, edge in self.edges.items()}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _node_text(source: str, node) -> str:
    return source.encode("utf-8")[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


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


def _parser_for(language: str) -> Parser:
    parser = Parser()
    if language == "python":
        parser.language = Language(tree_sitter_python.language())
    elif language == "cpp":
        parser.language = Language(tree_sitter_cpp.language())
    else:
        raise ValueError(f"Unsupported language: {language}")
    return parser


def _extensions_for(language: str) -> set[str]:
    return LANGUAGE_EXTENSIONS[language]


def _module_name(path: Path) -> str:
    return path.stem or path.name


class BaseExtractor:
    def __init__(self, graph: GraphBuilder, language: str, source_root: Path) -> None:
        self.graph = graph
        self.language = language
        self.source_root = source_root
        self.parser = _parser_for(language)
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


class PythonExtractor(BaseExtractor):
    def __init__(self, graph: GraphBuilder, source_root: Path) -> None:
        super().__init__(graph, "python", source_root)

    def _collect_definitions(self, node, source: str, scope: ScopeState) -> None:
        scope_stack = [scope]
        self._collect_python_definitions(node, source, scope_stack)

    def _collect_python_definitions(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]

        if node.type == "class_definition":
            class_name = _safe_name(_identifier_text(node.child_by_field_name("name"), source))
            class_id = self.graph.add_node("Class", class_name, scope.file, _line_start(node), _line_end(node))
            self.graph.add_edge("DEFINES", scope.node_id, class_id)
            scope.symbols[class_name] = class_id

            class_scope = ScopeState(
                class_id,
                "class",
                class_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=class_id,
            )
            self.scope_index[class_id] = class_scope
            scope_stack.append(class_scope)

            superclasses = node.child_by_field_name("superclasses")
            if superclasses is not None:
                for base in superclasses.named_children:
                    if base.type in {"identifier", "attribute", "generic_type", "qualified_identifier"}:
                        base_name = _identifier_text(base, source)
                        if base_name:
                            base_id = self._resolve_name(base_name.split(".")[-1], scope_stack[:-1])
                            if base_id and base_id != class_id:
                                self.graph.add_edge("INHERITS", class_id, base_id)

            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    self._collect_python_definitions(child, source, scope_stack)

            scope_stack.pop()
            return

        if node.type == "function_definition":
            function_name = _safe_name(_identifier_text(node.child_by_field_name("name"), source))
            is_method = scope.kind == "class"
            function_kind = "Method" if is_method else "Function"
            function_id = self.graph.add_node(function_kind, function_name, scope.file, _line_start(node), _line_end(node))
            self.graph.add_edge("DEFINES", scope.node_id, function_id)
            scope.symbols[function_name] = function_id

            function_scope = ScopeState(
                function_id,
                "function",
                function_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=scope.class_id,
            )
            self.scope_index[function_id] = function_scope
            scope_stack.append(function_scope)

            self._collect_python_parameters(node, function_scope, source)

            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    self._collect_python_definitions(child, source, scope_stack)

            scope_stack.pop()
            return

        if node.type in {"import_statement", "import_from_statement"}:
            import_name = _safe_name(_node_text(source, node).replace("\n", " "))
            import_id = self.graph.add_node("Import", import_name, scope.file, _line_start(node), _line_end(node))
            self.graph.add_edge("DEFINES", scope.node_id, import_id)
            scope.symbols[import_name] = import_id
            return

        if node.type in {"expression_statement", "assignment", "annotated_assignment"}:
            self._collect_python_assignment(node, source, scope)

        for child in node.named_children:
            self._collect_python_definitions(child, source, scope_stack)

    def _collect_python_parameters(self, function_node, function_scope: ScopeState, source: str) -> None:
        parameters = function_node.child_by_field_name("parameters")
        if parameters is None:
            return

        for child in parameters.named_children:
            text = _identifier_text(child, source)
            if not text:
                continue
            text = text.lstrip("*")
            if not text:
                continue
            if "=" in text:
                text = text.split("=", 1)[0].strip()
            if ":" in text:
                text = text.split(":", 1)[0].strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", text):
                continue
            function_scope.symbols[text] = None

    def _assignment_target_names(self, assignment_node, source: str) -> list[str]:
        left = assignment_node.child_by_field_name("left") or assignment_node.child_by_field_name("target")
        if left is None:
            return []
        names: list[str] = []

        if left.type == "identifier":
            names.append(_identifier_text(left, source) or "")
        elif left.type == "tuple" or left.type == "list":
            for child in left.named_children:
                if child.type == "identifier":
                    text = _identifier_text(child, source)
                    if text:
                        names.append(text)

        return [name for name in names if name]

    def _collect_python_assignment(self, node, source: str, scope: ScopeState) -> None:
        assignment = node
        if node.type == "expression_statement" and node.named_children:
            assignment = node.named_children[0]

        if assignment.type not in {"assignment", "annotated_assignment"}:
            return

        target_names = self._assignment_target_names(assignment, source)
        if not target_names:
            return

        if scope.kind == "module":
            kind = "GlobalVariable"
        elif scope.kind == "class":
            kind = "ClassAttribute"
        else:
            kind = None

        for name in target_names:
            if kind is None:
                scope.symbols[name] = None
                continue
            if name in scope.symbols:
                continue
            target_id = self.graph.add_node(kind, name, scope.file, _line_start(assignment), _line_end(assignment))
            self.graph.add_edge("DEFINES", scope.node_id, target_id)
            scope.symbols[name] = target_id

    def _collect_references(self, node, source: str, scope: ScopeState) -> None:
        scope_stack = [scope]
        self._collect_python_references(node, source, scope_stack)

    def _is_definition_context(self, node) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if parent.type in {"class_definition", "function_definition"} and parent.child_by_field_name("name") == node:
            return True
        if parent.type in {"assignment", "annotated_assignment"}:
            left = parent.child_by_field_name("left") or parent.child_by_field_name("target")
            return left == node or (left is not None and node in left.named_children)
        if parent.type in {"import_statement", "import_from_statement", "parameters", "typed_parameter", "default_parameter", "list_splat_pattern", "dictionary_splat_pattern"}:
            return True
        return False

    def _collect_python_references(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]

        if node.type == "class_definition":
            class_name = _safe_name(_identifier_text(node.child_by_field_name("name"), source))
            class_id = scope.symbols.get(class_name)
            definition_scope = self.scope_index.get(class_id) if class_id else None
            class_scope = ScopeState(
                class_id or f"{scope.file}:{class_name}:{_line_start(node)}",
                "class",
                class_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=class_id,
                symbols=dict(definition_scope.symbols) if definition_scope is not None else {},
            )
            if class_id:
                class_scope.symbols.setdefault(class_name, class_id)
            body = node.child_by_field_name("body")
            scope_stack.append(class_scope)
            if body is not None:
                for child in body.named_children:
                    self._collect_python_references(child, source, scope_stack)
            scope_stack.pop()
            return

        if node.type == "function_definition":
            function_name = _safe_name(_identifier_text(node.child_by_field_name("name"), source))
            function_id = scope.symbols.get(function_name)
            definition_scope = self.scope_index.get(function_id) if function_id else None
            function_scope = ScopeState(
                function_id or f"{scope.file}:{function_name}:{_line_start(node)}",
                "function",
                function_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=scope.class_id,
                symbols=dict(definition_scope.symbols) if definition_scope is not None else {},
            )
            if function_id:
                function_scope.symbols.setdefault(function_name, function_id)
            body = node.child_by_field_name("body")
            scope_stack.append(function_scope)
            if body is not None:
                for child in body.named_children:
                    self._collect_python_references(child, source, scope_stack)
            scope_stack.pop()
            return

        if node.type == "call":
            current_function = self._current_function_scope(scope_stack)
            if current_function is not None:
                callee = node.child_by_field_name("function")
                target_id = self._resolve_python_callable(callee, source, scope_stack)
                if target_id:
                    self.graph.add_edge("CALLS", current_function.node_id, target_id)

        if node.type == "attribute":
            current_function = self._current_function_scope(scope_stack)
            current_class = self._current_class_scope(scope_stack)
            if current_function is not None and current_class is not None:
                object_node = node.child_by_field_name("object")
                attribute_name = _identifier_text(node.child_by_field_name("attribute") or node.named_children[-1] if node.named_children else None, source)
                if object_node is not None and _node_text(source, object_node).strip() == "self" and attribute_name:
                    target_id = current_class.symbols.get(attribute_name)
                    if target_id:
                        target_kind = self.graph.nodes[target_id].kind if target_id in self.graph.nodes else None
                        if target_kind != "ClassAttribute":
                            target_id = None
                    if target_id:
                        self.graph.add_edge("REFERENCES", current_function.node_id, target_id)

        if node.type == "identifier" and not self._is_definition_context(node):
            current_function = self._current_function_scope(scope_stack)
            if current_function is not None:
                name = _identifier_text(node, source)
                if name:
                    target_id = self._resolve_name(name, scope_stack)
                    if target_id:
                        target_kind = self.graph.nodes[target_id].kind if target_id in self.graph.nodes else None
                        if target_kind not in {"GlobalVariable", "ClassAttribute"}:
                            target_id = None
                    if target_id:
                        self.graph.add_edge("REFERENCES", current_function.node_id, target_id)

        if node.type in {"call", "attribute"}:
            # Avoid double-visiting the callee/object as plain identifiers.
            for child in node.named_children:
                if child.type == "identifier" and self._is_definition_context(child):
                    continue
                self._collect_python_references(child, source, scope_stack)
            return

        for child in node.named_children:
            self._collect_python_references(child, source, scope_stack)

    def _resolve_python_callable(self, callee_node, source: str, scope_stack: list[ScopeState]) -> str | None:
        if callee_node is None:
            return None

        if callee_node.type == "identifier":
            name = _identifier_text(callee_node, source)
            if name:
                target_id = self._resolve_name(name, scope_stack)
                if target_id and self.graph.nodes.get(target_id, None) is not None:
                    kind = self.graph.nodes[target_id].kind
                    if kind in {"Function", "Method"}:
                        return target_id
            return None

        if callee_node.type == "attribute":
            current_class = self._current_class_scope(scope_stack)
            if current_class is None:
                return None
            object_node = callee_node.child_by_field_name("object")
            attribute_node = callee_node.child_by_field_name("attribute")
            attribute_name = _identifier_text(attribute_node, source) if attribute_node is not None else None
            if object_node is not None and _node_text(source, object_node).strip() == "self" and attribute_name:
                target_id = current_class.symbols.get(attribute_name)
                if target_id and self.graph.nodes.get(target_id, None) is not None:
                    kind = self.graph.nodes[target_id].kind
                    if kind in {"Function", "Method"}:
                        return target_id
        return None


class CppExtractor(BaseExtractor):
    def __init__(self, graph: GraphBuilder, source_root: Path) -> None:
        super().__init__(graph, "cpp", source_root)

    def _collect_definitions(self, node, source: str, scope: ScopeState) -> None:
        scope_stack = [scope]
        self._collect_cpp_definitions(node, source, scope_stack)

    def _collect_cpp_definitions(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]

        if node.type in {"class_specifier", "struct_specifier"}:
            class_name_node = node.child_by_field_name("name")
            class_name = _safe_name(_identifier_text(class_name_node, source))
            class_id = self.graph.add_node("Class", class_name, scope.file, _line_start(node), _line_end(node))
            self.graph.add_edge("DEFINES", scope.node_id, class_id)
            scope.symbols[class_name] = class_id

            class_scope = ScopeState(
                class_id,
                "class",
                class_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=class_id,
            )
            self.scope_index[class_id] = class_scope
            scope_stack.append(class_scope)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    self._collect_cpp_definitions(child, source, scope_stack)
            scope_stack.pop()
            return

        if node.type == "function_definition":
            function_name = self._cpp_function_name(node, source)
            function_kind = "Method" if scope.kind == "class" else "Function"
            function_id = self.graph.add_node(function_kind, function_name, scope.file, _line_start(node), _line_end(node))
            self.graph.add_edge("DEFINES", scope.node_id, function_id)
            scope.symbols[function_name] = function_id

            function_scope = ScopeState(
                function_id,
                "function",
                function_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=scope.class_id,
            )
            self.scope_index[function_id] = function_scope
            scope_stack.append(function_scope)
            self._collect_cpp_parameters(node, function_scope, source)
            body = node.child_by_field_name("body") or node.child_by_field_name("body_statement") or node.named_children[-1]
            if body is not None:
                for child in body.named_children:
                    self._collect_cpp_definitions(child, source, scope_stack)
            scope_stack.pop()
            return

        if node.type == "preproc_include":
            import_name = _safe_name(_node_text(source, node).replace("\n", " "))
            import_id = self.graph.add_node("Import", import_name, scope.file, _line_start(node), _line_end(node))
            self.graph.add_edge("DEFINES", scope.node_id, import_id)
            scope.symbols[import_name] = import_id
            return

        if node.type in {"declaration", "field_declaration", "init_declarator"}:
            self._collect_cpp_declaration(node, source, scope)

        for child in node.named_children:
            self._collect_cpp_definitions(child, source, scope_stack)

    def _cpp_function_name(self, node, source: str) -> str:
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            name = declarator.child_by_field_name("declarator") or declarator.child_by_field_name("name")
            if name is None:
                for candidate in declarator.named_children:
                    if candidate.type in {"identifier", "field_identifier", "qualified_identifier"}:
                        name = candidate
                        break
            if name is not None:
                text = _identifier_text(name, source)
                if text:
                    return text.split("::")[-1]
        for candidate in node.named_children:
            if candidate.type in {"identifier", "field_identifier", "qualified_identifier"}:
                text = _identifier_text(candidate, source)
                if text:
                    return text.split("::")[-1]
        return "Function"

    def _collect_cpp_parameters(self, node, function_scope: ScopeState, source: str) -> None:
        declarator = node.child_by_field_name("declarator")
        if declarator is None:
            return
        parameters = declarator.child_by_field_name("parameters")
        if parameters is None:
            return
        for child in parameters.named_children:
            if child.type in {"parameter_declaration", "optional_parameter_declaration"}:
                name = None
                for candidate in child.named_children:
                    if candidate.type in {"identifier", "field_identifier", "type_identifier"}:
                        name = _identifier_text(candidate, source)
                        break
                if name:
                    function_scope.symbols[name] = None

    def _collect_cpp_declaration(self, node, source: str, scope: ScopeState) -> None:
        kind = "ClassAttribute" if scope.kind == "class" else ("LocalVariable" if scope.kind == "function" else "GlobalVariable")
        names = []
        for candidate in node.named_children:
            if candidate.type in {"identifier", "field_identifier"}:
                text = _identifier_text(candidate, source)
                if text:
                    names.append(text)
        for name in names:
            if kind == "LocalVariable":
                scope.symbols[name] = None
                continue
            if name in scope.symbols:
                continue
            target_id = self.graph.add_node(kind, name, scope.file, _line_start(node), _line_end(node))
            self.graph.add_edge("DEFINES", scope.node_id, target_id)
            scope.symbols[name] = target_id

    def _collect_references(self, node, source: str, scope: ScopeState) -> None:
        scope_stack = [scope]
        self._collect_cpp_references(node, source, scope_stack)

    def _collect_cpp_references(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]

        if node.type in {"class_specifier", "struct_specifier"}:
            class_name_node = node.child_by_field_name("name")
            class_name = _safe_name(_identifier_text(class_name_node, source))
            class_id = scope.symbols.get(class_name)
            definition_scope = self.scope_index.get(class_id) if class_id else None
            class_scope = ScopeState(
                class_id or f"{scope.file}:{class_name}:{_line_start(node)}",
                "class",
                class_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=class_id,
                symbols=dict(definition_scope.symbols) if definition_scope is not None else {},
            )
            if class_id:
                class_scope.symbols.setdefault(class_name, class_id)
            body = node.child_by_field_name("body")
            scope_stack.append(class_scope)
            if body is not None:
                for child in body.named_children:
                    self._collect_cpp_references(child, source, scope_stack)
            scope_stack.pop()
            return

        if node.type == "function_definition":
            function_name = self._cpp_function_name(node, source)
            function_id = scope.symbols.get(function_name)
            definition_scope = self.scope_index.get(function_id) if function_id else None
            function_scope = ScopeState(
                function_id or f"{scope.file}:{function_name}:{_line_start(node)}",
                "function",
                function_name,
                scope.file,
                _line_start(node),
                _line_end(node),
                parent_id=scope.node_id,
                class_id=scope.class_id,
                symbols=dict(definition_scope.symbols) if definition_scope is not None else {},
            )
            if function_id:
                function_scope.symbols.setdefault(function_name, function_id)
            declarator = node.child_by_field_name("declarator")
            if declarator is not None:
                parameters = declarator.child_by_field_name("parameters")
                if parameters is not None:
                    for child in parameters.named_children:
                        if child.type == "parameter_declaration":
                            for candidate in child.named_children:
                                if candidate.type in {"identifier", "field_identifier"}:
                                    name = _identifier_text(candidate, source)
                                    if name:
                                        function_scope.symbols[name] = scope.symbols.get(name, function_scope.node_id)
                                    break
            body = node.child_by_field_name("body") or node.child_by_field_name("body_statement") or node.named_children[-1]
            scope_stack.append(function_scope)
            if body is not None:
                for child in body.named_children:
                    self._collect_cpp_references(child, source, scope_stack)
            scope_stack.pop()
            return

        if node.type == "call_expression":
            current_function = self._current_function_scope(scope_stack)
            if current_function is not None:
                target_id = self._resolve_cpp_callable(node, source, scope_stack)
                if target_id:
                    self.graph.add_edge("CALLS", current_function.node_id, target_id)

        if node.type == "identifier" and not self._is_cpp_definition_context(node):
            current_function = self._current_function_scope(scope_stack)
            if current_function is not None:
                name = _identifier_text(node, source)
                if name:
                    target_id = self._resolve_name(name, scope_stack)
                    if target_id:
                        target_kind = self.graph.nodes[target_id].kind if target_id in self.graph.nodes else None
                        if target_kind not in {"GlobalVariable", "ClassAttribute"}:
                            target_id = None
                    if target_id:
                        self.graph.add_edge("REFERENCES", current_function.node_id, target_id)

        if node.type == "field_expression":
            current_function = self._current_function_scope(scope_stack)
            current_class = self._current_class_scope(scope_stack)
            if current_function is not None and current_class is not None:
                field_node = node.child_by_field_name("field")
                object_node = node.child_by_field_name("argument") or node.child_by_field_name("object")
                field_name = _identifier_text(field_node, source) if field_node is not None else None
                if object_node is not None and field_name:
                    object_text = _node_text(source, object_node).strip()
                    if object_text in {"this", "self"}:
                        target_id = current_class.symbols.get(field_name)
                        if target_id:
                            target_kind = self.graph.nodes[target_id].kind if target_id in self.graph.nodes else None
                            if target_kind != "ClassAttribute":
                                target_id = None
                        if target_id:
                            self.graph.add_edge("REFERENCES", current_function.node_id, target_id)

        for child in node.named_children:
            self._collect_cpp_references(child, source, scope_stack)

    def _resolve_cpp_callable(self, node, source: str, scope_stack: list[ScopeState]) -> str | None:
        function_node = node.child_by_field_name("function") or (node.named_children[0] if node.named_children else None)
        if function_node is None:
            return None
        if function_node.type in {"identifier", "field_identifier", "qualified_identifier"}:
            name = _identifier_text(function_node, source)
            if name:
                target_id = self._resolve_name(name.split("::")[-1], scope_stack)
                if target_id and self.graph.nodes.get(target_id, None) is not None:
                    kind = self.graph.nodes[target_id].kind
                    if kind in {"Function", "Method"}:
                        return target_id
        if function_node.type == "field_expression":
            field_node = function_node.child_by_field_name("field")
            object_node = function_node.child_by_field_name("argument") or function_node.child_by_field_name("object")
            field_name = _identifier_text(field_node, source) if field_node is not None else None
            current_class = self._current_class_scope(scope_stack)
            if current_class is not None and object_node is not None and field_name:
                object_text = _node_text(source, object_node).strip()
                if object_text in {"this", "self"}:
                    target_id = current_class.symbols.get(field_name)
                    if target_id and self.graph.nodes.get(target_id, None) is not None:
                        kind = self.graph.nodes[target_id].kind
                        if kind in {"Function", "Method"}:
                            return target_id
        return None

    def _is_cpp_definition_context(self, node) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if parent.type in {"class_specifier", "struct_specifier"} and parent.child_by_field_name("name") == node:
            return True
        if parent.type == "function_declarator":
            return True
        if parent.type == "function_definition":
            declarator = parent.child_by_field_name("declarator")
            if declarator is not None and declarator.child_by_field_name("declarator") == node:
                return True
        if parent.type in {"field_declaration", "init_declarator", "parameter_declaration", "namespace_definition"}:
            return True
        return False


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
