from __future__ import annotations

from pathlib import Path

from .extractor import BaseExtractor, GraphBuilder, ScopeState, _identifier_text, _line_end, _line_start, _node_text, _safe_name


class CppExtractor(BaseExtractor):
    def __init__(self, graph: GraphBuilder, source_root: Path) -> None:
        super().__init__(graph, "cpp", source_root)
        self._definition_handlers = {
            "class_specifier": self._handle_class_definition,
            "struct_specifier": self._handle_class_definition,
            "function_definition": self._handle_function_definition,
            "preproc_include": self._handle_import_definition,
            "declaration": self._handle_declaration_definition,
            "field_declaration": self._handle_declaration_definition,
            "init_declarator": self._handle_declaration_definition,
        }
        self._reference_handlers = {
            "class_specifier": self._handle_class_reference,
            "struct_specifier": self._handle_class_reference,
            "function_definition": self._handle_function_reference,
            "call_expression": self._handle_call_reference,
            "identifier": self._handle_identifier_reference,
            "field_expression": self._handle_field_reference,
        }

    def _collect_definitions(self, node, source: str, scope: ScopeState) -> None:
        scope_stack = [scope]
        self._collect_cpp_definitions(node, source, scope_stack)

    def _collect_cpp_definitions(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        handler = self._definition_handlers.get(node.type)
        if handler is not None:
            handler(node, source, scope_stack)
            return

        for child in node.named_children:
            self._collect_cpp_definitions(child, source, scope_stack)

    def _handle_class_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]
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

        for child in node.named_children:
            if child.type == "base_class_clause":
                for base in child.named_children:
                    if base.type in {"type_identifier", "qualified_identifier"}:
                        base_name = _identifier_text(base, source)
                        if base_name:
                            base_id = self._resolve_name(base_name, scope_stack[:-1])
                            if base_id and base_id != class_id:
                                self.graph.add_edge("INHERITS", class_id, base_id)

        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                self._collect_cpp_definitions(child, source, scope_stack)

        scope_stack.pop()

    def _handle_function_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]
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

    def _handle_import_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]
        import_name = _safe_name(_node_text(source, node).replace("\n", " "))
        import_id = self.graph.add_node("Import", import_name, scope.file, _line_start(node), _line_end(node))
        self.graph.add_edge("DEFINES", scope.node_id, import_id)
        scope.symbols[import_name] = import_id

    def _handle_declaration_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        self._collect_cpp_declaration(node, source, scope_stack[-1])

    def _cpp_function_name(self, node, source: str) -> str:
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            # Walk through pointer/reference declarators to reach function_declarator
            while declarator.type in {"pointer_declarator", "reference_declarator",
                                       "array_declarator", "rvalue_reference_declarator"}:
                inner = declarator.child_by_field_name("declarator")
                if inner is not None:
                    declarator = inner
                else:
                    break
            if declarator.type == "function_declarator":
                name_node = declarator.child_by_field_name("declarator")
                if name_node is not None:
                    text = _identifier_text(name_node, source)
                    if text:
                        # Only split :: for bare identifiers, not for text containing parens
                        return text if "(" in text else text.split("::")[-1]
            # Fallback: search declarator's named children
            for candidate in declarator.named_children:
                if candidate.type in {"identifier", "field_identifier", "qualified_identifier"}:
                    text = _identifier_text(candidate, source)
                    if text:
                        return text if "(" in text else text.split("::")[-1]
        # Last resort: search function_definition's direct named children
        for candidate in node.named_children:
            if candidate.type in {"identifier", "field_identifier", "qualified_identifier"}:
                text = _identifier_text(candidate, source)
                if text:
                    return text if "(" in text else text.split("::")[-1]
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

    @staticmethod
    def _declarator_names(node, source: str) -> list[str]:
        """Recursively collect identifier names from a declarator chain."""
        names: list[str] = []
        for child in node.named_children:
            if child.type in {"identifier", "field_identifier", "type_identifier"}:
                text = _identifier_text(child, source)
                if text:
                    names.append(text)
            elif child.type in {"init_declarator", "pointer_declarator", "reference_declarator",
                                "array_declarator", "rvalue_reference_declarator"}:
                names.extend(CppExtractor._declarator_names(child, source))
        return names

    def _collect_cpp_declaration(self, node, source: str, scope: ScopeState) -> None:
        kind = "ClassAttribute" if scope.kind == "class" else ("LocalVariable" if scope.kind == "function" else "GlobalVariable")
        names = self._declarator_names(node, source)
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
        handler = self._reference_handlers.get(node.type)
        if handler is not None and handler(node, source, scope_stack):
            return

        for child in node.named_children:
            self._collect_cpp_references(child, source, scope_stack)

    def _handle_class_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        scope = scope_stack[-1]
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
        return True

    def _handle_function_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        scope = scope_stack[-1]
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
        return True

    def _handle_call_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        current_function = self._current_function_scope(scope_stack)
        if current_function is not None:
            target_id = self._resolve_cpp_callable(node, source, scope_stack)
            if target_id:
                self.graph.add_edge("CALLS", current_function.node_id, target_id)

        for child in node.named_children:
            if child.type in {"identifier", "field_identifier"} and self._is_cpp_definition_context(child):
                continue
            self._collect_cpp_references(child, source, scope_stack)
        return True

    def _handle_identifier_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        if self._is_cpp_definition_context(node):
            return False

        current_function = self._current_function_scope(scope_stack)
        if current_function is None:
            return False

        name = _identifier_text(node, source)
        if not name:
            return False

        target_id = self._resolve_name(name, scope_stack)
        if target_id:
            target_kind = self.graph.nodes[target_id].kind if target_id in self.graph.nodes else None
            if target_kind not in {"GlobalVariable", "ClassAttribute"}:
                target_id = None
        if target_id:
            self.graph.add_edge("REFERENCES", current_function.node_id, target_id)
        return False

    def _handle_field_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
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
        return True

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
