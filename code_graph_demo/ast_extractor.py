"""AST extraction skeleton built on Python's standard library."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def _get_arg_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract simple argument names from a function-like node."""
    args = [arg.arg for arg in node.args.posonlyargs]
    args.extend(arg.arg for arg in node.args.args)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    args.extend(arg.arg for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return args


def _get_call_name(node: ast.AST) -> str | None:
    """Resolve a conservative callee name for a call expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _get_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


class _CallCollector(ast.NodeVisitor):
    """Collect call names while skipping nested function/class scopes."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _get_call_name(node.func)
        if name:
            self.calls.append(name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


def _collect_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Collect call names from a function body."""
    collector = _CallCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.calls


def _statement_type(node: ast.stmt) -> str:
    """Return a compact statement type name for CFG demo nodes."""
    if isinstance(node, ast.Return):
        return "ReturnStatement"
    return f"{type(node).__name__}Statement"


def _statement_info(node: ast.stmt, index: int) -> dict[str, Any]:
    """Build a light statement summary for top-level function body statements."""
    info: dict[str, Any] = {
        "index": index,
        "type": _statement_type(node),
        "ast_type": type(node).__name__,
        "lineno": getattr(node, "lineno", None),
        "end_lineno": getattr(node, "end_lineno", None),
    }
    if isinstance(node, (ast.If, ast.For, ast.While)):
        info["body_first_lineno"] = getattr(node.body[0], "lineno", None) if node.body else None
        if node.body:
            info["body_first"] = {
                "type": _statement_type(node.body[0]),
                "ast_type": type(node.body[0]).__name__,
                "lineno": getattr(node.body[0], "lineno", None),
                "end_lineno": getattr(node.body[0], "end_lineno", None),
            }
    if isinstance(node, ast.If):
        info["orelse_first_lineno"] = (
            getattr(node.orelse[0], "lineno", None) if node.orelse else None
        )
        if node.orelse:
            info["orelse_first"] = {
                "type": _statement_type(node.orelse[0]),
                "ast_type": type(node.orelse[0]).__name__,
                "lineno": getattr(node.orelse[0], "lineno", None),
                "end_lineno": getattr(node.orelse[0], "end_lineno", None),
            }
    return info


def _var_name(node: ast.AST) -> str | None:
    """Return a simple variable name for Name or self.x style targets."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _var_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _collect_load_names(node: ast.AST) -> list[str]:
    """Collect variable names used in Load context inside an expression."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.append(child.id)
        elif isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
            name = _var_name(child)
            if name:
                names.append(name)
    return names


def _collect_data_flow(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    """Collect simple define/use events for DFG demo."""
    events: list[dict[str, Any]] = []
    for index, stmt in enumerate(node.body):
        if isinstance(stmt, ast.Assign):
            for use_name in _collect_load_names(stmt.value):
                events.append(
                    {"kind": "use", "name": use_name, "lineno": stmt.lineno, "statement_index": index}
                )
            for target in stmt.targets:
                target_name = _var_name(target)
                if target_name:
                    events.append(
                        {
                            "kind": "def",
                            "name": target_name,
                            "lineno": stmt.lineno,
                            "statement_index": index,
                        }
                    )
        elif isinstance(stmt, ast.Return) and stmt.value:
            for use_name in _collect_load_names(stmt.value):
                events.append(
                    {"kind": "use", "name": use_name, "lineno": stmt.lineno, "statement_index": index}
                )
    return events


def _build_function_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    """Build function or method info."""
    return {
        "name": node.name,
        "lineno": getattr(node, "lineno", None),
        "end_lineno": getattr(node, "end_lineno", None),
        "args": _get_arg_names(node),
        "calls": _collect_calls(node),
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "statements": [_statement_info(stmt, idx) for idx, stmt in enumerate(node.body)],
        "data_flow": _collect_data_flow(node),
    }


def _build_import_info(node: ast.Import | ast.ImportFrom) -> list[dict[str, Any]]:
    """Build import items from import nodes."""
    items: list[dict[str, Any]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            items.append(
                {
                    "name": alias.name,
                    "module": None,
                    "lineno": getattr(node, "lineno", None),
                    "type": "import",
                }
            )
    else:
        module_name = node.module
        if node.level:
            dots = "." * node.level
            module_name = f"{dots}{module_name or ''}"
        for alias in node.names:
            items.append(
                {
                    "name": alias.name,
                    "module": module_name,
                    "lineno": getattr(node, "lineno", None),
                    "type": "from",
                }
            )
    return items


def extract_python_ast_info(file_path: str | Path) -> dict[str, Any]:
    """Extract a structured AST summary from one Python file."""
    path = Path(file_path)
    result: dict[str, Any] = {
        "file": {"path": path.as_posix(), "name": path.name},
        "classes": [],
        "functions": [],
        "imports": [],
    }

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result["imports"].extend(_build_import_info(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append(_build_function_info(node))
        elif isinstance(node, ast.ClassDef):
            methods: list[dict[str, Any]] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(_build_function_info(item))
            result["classes"].append(
                {
                    "name": node.name,
                    "lineno": getattr(node, "lineno", None),
                    "end_lineno": getattr(node, "end_lineno", None),
                    "methods": methods,
                }
            )

    return result
