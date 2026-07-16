from __future__ import annotations

import re
from pathlib import Path

from .extractor import BaseExtractor, GraphBuilder, ScopeState, _identifier_text, _line_end, _line_start, _node_text, _safe_name


class PythonExtractor(BaseExtractor):
    def __init__(self, graph: GraphBuilder, source_root: Path) -> None:
        super().__init__(graph, "python", source_root)
        self._definition_handlers = {
            "class_definition": self._handle_class_definition,
            "function_definition": self._handle_function_definition,
            "import_statement": self._handle_import_definition,
            "import_from_statement": self._handle_import_definition,
            "expression_statement": self._handle_assignment_definition,
            "assignment": self._handle_assignment_definition,
            "annotated_assignment": self._handle_assignment_definition,
        }
        self._reference_handlers = {
            "class_definition": self._handle_class_reference,
            "function_definition": self._handle_function_reference,
            "call": self._handle_call_reference,
            "attribute": self._handle_attribute_reference,
            "identifier": self._handle_identifier_reference,
        }

    def _collect_definitions(self, node, source: str, scope: ScopeState) -> None:
        scope_stack = [scope]
        self._collect_python_definitions(node, source, scope_stack)

    def _collect_python_definitions(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        handler = self._definition_handlers.get(node.type)
        if handler is not None:
            handler(node, source, scope_stack)
            return

        for child in node.named_children:
            self._collect_python_definitions(child, source, scope_stack)

    def _handle_class_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]
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

    def _handle_function_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]
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

    def _handle_import_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]
        import_name = _safe_name(_node_text(source, node).replace("\n", " "))
        import_id = self.graph.add_node("Import", import_name, scope.file, _line_start(node), _line_end(node))
        self.graph.add_edge("DEFINES", scope.node_id, import_id)
        scope.symbols[import_name] = import_id

    def _handle_assignment_definition(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        self._collect_python_assignment(node, source, scope_stack)

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

    def _collect_python_assignment(self, node, source: str, scope_stack: list[ScopeState]) -> None:
        scope = scope_stack[-1]
        assignment = node
        if node.type == "expression_statement" and node.named_children:
            assignment = node.named_children[0]

        if assignment.type not in {"assignment", "annotated_assignment"}:
            return

        target_names = self._assignment_target_names(assignment, source)

        if target_names:
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

        # Handle self.attr = value assignments (attribute left side)
        left = assignment.child_by_field_name("left") or assignment.child_by_field_name("target")
        if left is not None and left.type == "attribute":
            object_node = left.child_by_field_name("object")
            attr_node = left.child_by_field_name("attribute")
            if object_node is not None and attr_node is not None:
                obj_text = _node_text(source, object_node).strip()
                if obj_text == "self":
                    attr_name = _identifier_text(attr_node, source)
                    if attr_name:
                        current_class = self._current_class_scope(scope_stack)
                        if current_class is not None and attr_name not in current_class.symbols:
                            target_id = self.graph.add_node(
                                "ClassAttribute", attr_name, scope.file,
                                _line_start(assignment), _line_end(assignment)
                            )
                            self.graph.add_edge("DEFINES", current_class.node_id, target_id)
                            current_class.symbols[attr_name] = target_id

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
        handler = self._reference_handlers.get(node.type)
        if handler is not None and handler(node, source, scope_stack):
            return

        for child in node.named_children:
            self._collect_python_references(child, source, scope_stack)

    def _handle_class_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        scope = scope_stack[-1]
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
        return True

    def _handle_function_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        scope = scope_stack[-1]
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
        return True

    def _handle_call_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        current_function = self._current_function_scope(scope_stack)
        if current_function is not None:
            callee = node.child_by_field_name("function")
            target_id = self._resolve_python_callable(callee, source, scope_stack)
            if target_id:
                self.graph.add_edge("CALLS", current_function.node_id, target_id)

        for child in node.named_children:
            if child.type == "identifier" and self._is_definition_context(child):
                continue
            self._collect_python_references(child, source, scope_stack)
        return True

    def _handle_attribute_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
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

        for child in node.named_children:
            if child.type == "identifier" and self._is_definition_context(child):
                continue
            self._collect_python_references(child, source, scope_stack)
        return True

    def _handle_identifier_reference(self, node, source: str, scope_stack: list[ScopeState]) -> bool:
        if self._is_definition_context(node):
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
