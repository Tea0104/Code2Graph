from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
import re
import textwrap
from typing import Iterable

from .models import FunctionChunk, TestChunk


ASSERT_AND_TEST_HELPER_NAMES = {
    "TEST",
    "TEST_F",
    "TEST_P",
    "TYPED_TEST",
    "TYPED_TEST_P",
    "TEST_CASE",
    "SCENARIO",
    "BOOST_AUTO_TEST_CASE",
    "assert",
    "assert_equal",
    "assert_equals",
    "assert_true",
    "assert_false",
    "assertraises",
    "assertRaises",
    "assertEqual",
    "assertEquals",
    "assertTrue",
    "assertFalse",
    "expect_eq",
    "expect_ne",
    "expect_true",
    "expect_false",
    "EXPECT_EQ",
    "EXPECT_NE",
    "EXPECT_TRUE",
    "EXPECT_FALSE",
    "ASSERT_EQ",
    "ASSERT_NE",
    "ASSERT_TRUE",
    "ASSERT_FALSE",
    "SUCCEED",
    "FAIL",
    "ADD_FAILURE",
    "static_assert",
    "REQUIRE",
    "CHECK",
    "BOOST_CHECK",
    "BOOST_REQUIRE",
}

IGNORED_CALL_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "throw",
    "sizeof",
    "static_assert",
    "new",
    "delete",
    "decltype",
    "public",
    "private",
    "protected",
    "override",
    "final",
    "static_cast",
    "reinterpret_cast",
    "dynamic_cast",
    "const_cast",
    "main",
    "print",
    "len",
    "range",
    "enumerate",
    "list",
    "dict",
    "set",
    "tuple",
    "str",
    "int",
    "float",
    "bool",
    "pytest",
    "fixture",
    "mock",
    "patch",
    "MagicMock",
    "Mock",
    "setUp",
    "setup",
    "setup_method",
    "setup_class",
    "tearDown",
    "teardown",
    "teardown_method",
    "teardown_class",
}

CPP_DECLARATION_KEYWORDS = {
    "auto",
    "bool",
    "char",
    "double",
    "float",
    "int",
    "long",
    "short",
    "signed",
    "size_t",
    "std",
    "string",
    "unsigned",
    "void",
    # Standard-library containers / utility types should not turn receiver
    # calls such as v.size() into project business calls named vector.size.
    "array",
    "deque",
    "list",
    "map",
    "optional",
    "pair",
    "queue",
    "set",
    "stringstream",
    "unordered_map",
    "unordered_set",
    "variant",
    "vector",
}

CPP_UNTYPED_CHAIN_ACCESSOR_NOISE = {
    "at",
    "back",
    "begin",
    "cbegin",
    "cend",
    "data",
    "empty",
    "end",
    "find",
    "front",
    "rbegin",
    "rend",
    "size",
}


@dataclass(frozen=True)
class ResolvedFunction:
    function: FunctionChunk
    call: str
    reason: str

    def to_dict(self) -> dict:
        payload = asdict(self.function)
        payload["matched_call"] = self.call
        payload["resolution_reason"] = self.reason
        return payload


def _base_name(call: str) -> str:
    normalized = call.replace("::", ".")
    return normalized.rsplit(".", 1)[-1]


def _normalized_qualified(value: str) -> str:
    return value.replace("::", ".").strip(".")


def _strip_qualified_templates(value: str) -> str:
    return re.sub(r"<[^<>]*>", "", value)


def _path_matches_suffix(candidate: str, expected: str) -> bool:
    candidate = candidate.replace("\\", "/").strip("/")
    expected = expected.replace("\\", "/").strip("/")
    return candidate == expected or candidate.endswith(f"/{expected}") or expected.endswith(f"/{candidate}")


def _module_file_suffixes(module: str) -> set[str]:
    module_path = module.replace(".", "/").strip("/")
    if not module_path:
        return set()
    return {f"{module_path}.py", f"{module_path}/__init__.py"}


def _file_matches_python_module(function: FunctionChunk, module: str) -> bool:
    suffixes = _module_file_suffixes(module)
    return any(function.file == suffix or function.file.endswith(f"/{suffix}") for suffix in suffixes)


def _helper_names_from_code(snippets: Iterable[str], language: str) -> set[str]:
    names: set[str] = set()
    for snippet in snippets:
        if language == "Python":
            names.update(re.findall(r"(?m)^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", snippet))
        elif language == "C++":
            matches = re.findall(r"\b(?:[A-Za-z_][A-Za-z0-9_:<>]*\s+)+([A-Za-z_~][A-Za-z0-9_]*)\s*\(", snippet)
            names.update(match for match in matches if match not in ASSERT_AND_TEST_HELPER_NAMES)
            names.update(re.findall(r"\b(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)", snippet))
    return names


def _test_local_names(test: TestChunk) -> set[str]:
    names = _helper_names_from_code(test.helpers, test.language)
    names.update(test.metadata.get("fixture_parameters") or [])
    if test.parent:
        names.update({test.parent, "setUp", "setup_method", "setup_class"})
    return names


def _is_ignored_call(call: str, local_names: set[str]) -> bool:
    normalized = _normalized_qualified(call)
    if normalized.startswith(("std.", "boost.", "testing.", "absl.")):
        return True
    base = _base_name(call)
    receiver = normalized.rsplit(".", 1)[0] if "." in normalized else ""
    lowered = base.lower()
    if base in local_names:
        if not receiver or receiver in local_names or receiver.split(".")[-1] in local_names:
            return True
    if base in ASSERT_AND_TEST_HELPER_NAMES or base in IGNORED_CALL_NAMES:
        return True
    if lowered.startswith(("assert", "expect", "require", "check", "mock", "patch")):
        return True
    if lowered.startswith(("setup", "teardown")):
        return True
    return False


def _dedupe_calls(calls: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for call in calls:
        normalized = _normalized_qualified(call)
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def _ast_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ast_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


class _DirectPythonCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _ast_call_name(node.func)
        if name:
            self.calls.append(name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _python_call_targets(code: str, fallback_calls: Iterable[str]) -> list[str]:
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return _dedupe_calls(fallback_calls)

    roots: list[ast.AST] = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not roots:
        roots = tree.body

    visitor = _DirectPythonCallVisitor()
    for root in roots:
        if isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for item in root.body:
                visitor.visit(item)
        else:
            visitor.visit(root)
    return _dedupe_calls(visitor.calls or fallback_calls)


def _cpp_call_targets(code: str, fallback_calls: Iterable[str]) -> list[str]:
    var_types = _cpp_variable_types(code)
    ignored_receivers = _cpp_ignored_receiver_names(code)
    calls: list[tuple[int, str]] = []

    for position, constructor in _cpp_constructor_initializer_calls(code):
        calls.append((position, constructor))

    member_spans: list[tuple[int, int]] = []
    member_pattern = re.compile(
        r"\b(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\s*(?:->|\.)\s*"
        r"(?P<member>[A-Za-z_~][A-Za-z0-9_]*)\s*(?:<[^;{}()]*>)?\s*\("
    )
    for match in member_pattern.finditer(code):
        receiver = match.group("receiver")
        member = match.group("member")
        member_spans.append(match.span())
        if receiver in ignored_receivers:
            continue
        receiver_type = var_types.get(receiver)
        if receiver_type is None and member in CPP_UNTYPED_CHAIN_ACCESSOR_NOISE:
            continue
        calls.append((match.start(), f"{receiver_type}.{member}" if receiver_type else f"{receiver}.{member}"))

    pattern = re.compile(
        r"\b([A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)*)"
        r"\s*(?:<[^;{}()]*>)?\s*\("
    )
    for match in pattern.finditer(code):
        if any(start <= match.start() < end for start, end in member_spans):
            continue
        prefix = code[:match.start()].rstrip()
        call = match.group(1)
        if prefix.endswith(".") or prefix.endswith("->"):
            if _base_name(call) in CPP_UNTYPED_CHAIN_ACCESSOR_NOISE:
                continue
        if call.count(".") >= 2 and _base_name(call) in CPP_UNTYPED_CHAIN_ACCESSOR_NOISE:
            continue
        if call in var_types:
            calls.append((match.start(), var_types[call]))
        elif "." in call:
            receiver, member = call.rsplit(".", 1)
            if receiver in var_types:
                calls.append((match.start(), f"{var_types[receiver]}.{member}"))
            else:
                calls.append((match.start(), call))
        elif "::" in call:
            receiver, member = call.rsplit("::", 1)
            if receiver in var_types:
                calls.append((match.start(), f"{var_types[receiver]}.{member}"))
            else:
                calls.append((match.start(), call))
        else:
            calls.append((match.start(), call))
    ordered_calls = [call for _, call in sorted(calls, key=lambda item: item[0])]
    return _dedupe_calls(ordered_calls or fallback_calls)


def _strip_cpp_template_args(value: str) -> str:
    result: list[str] = []
    depth = 0
    for char in value:
        if char == "<":
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif depth == 0:
            result.append(char)
    return "".join(result)


def _cpp_base_type(value: str) -> str:
    cleaned = _strip_cpp_template_args(value)
    cleaned = re.sub(r"\b(?:const|volatile|typename|class|struct)\b", " ", cleaned)
    cleaned = cleaned.replace("*", " ").replace("&", " ")
    parts = re.findall(r"[A-Za-z_][A-Za-z0-9_:]*", cleaned)
    if not parts:
        return ""
    return parts[-1].split("::")[-1]


def _cpp_declared_variable_names(code: str) -> set[str]:
    names: set[str] = set()
    type_pattern = (
        r"(?:(?:const|volatile|typename|class|struct)\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:]*)(?:\s*<[^;{}=]+>)?"
    )
    declaration = re.compile(
        rf"(?<![A-Za-z0-9_:])(?P<type>{type_pattern})(?:\s*[*&]\s*|\s+)"
        r"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?=\(|=|\{|;|\[)"
    )
    for match in declaration.finditer(code):
        var_name = match.group("var")
        if _cpp_declaration_match_is_function_definition(code, match):
            continue
        if var_name not in ASSERT_AND_TEST_HELPER_NAMES and var_name not in IGNORED_CALL_NAMES:
            names.add(var_name)
    return names


def _cpp_ignored_receiver_names(code: str) -> set[str]:
    """Return local variables whose member calls are standard-library noise."""
    ignored: set[str] = set()
    type_pattern = (
        r"(?:(?:const|volatile|typename|class|struct)\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:]*)(?:\s*<[^;(){}=]+>)?"
    )
    declaration = re.compile(
        rf"(?<![A-Za-z0-9_:])(?P<type>{type_pattern})(?:\s*[*&]\s*|\s+)"
        r"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?=\(|=|\{|;|\[)"
    )
    for match in declaration.finditer(code):
        if _cpp_declaration_match_is_function_definition(code, match):
            continue
        type_name = _cpp_base_type(match.group("type"))
        if type_name in CPP_DECLARATION_KEYWORDS:
            ignored.add(match.group("var"))
    return ignored


def _cpp_declaration_match_is_function_definition(code: str, match: re.Match[str]) -> bool:
    if code[match.end():match.end() + 1] != "(":
        return False
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(match.end(), len(code)):
        char = code[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                rest = code[index + 1:]
                next_match = re.search(r"\S", rest)
                return bool(next_match and rest[next_match.start()] == "{")
        elif char == ";" and depth == 0:
            return False
    return False


def _cpp_variable_types(code: str) -> dict[str, str]:
    """Infer simple local C++ variable declarations in a test body."""
    variable_types: dict[str, str] = {}
    type_pattern = (
        r"(?:(?:const|volatile|typename|class|struct)\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:]*)(?:\s*<[^;(){}=]+>)?"
    )
    declaration = re.compile(
        rf"(?<![A-Za-z0-9_:])(?P<type>{type_pattern})(?:\s*[*&]\s*|\s+)"
        r"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?=\(|=|\{|;|\[)"
    )
    for match in declaration.finditer(code):
        if _cpp_declaration_match_is_function_definition(code, match):
            continue
        type_name = _cpp_base_type(match.group("type"))
        var_name = match.group("var")
        if not type_name or type_name in CPP_DECLARATION_KEYWORDS:
            continue
        if var_name in ASSERT_AND_TEST_HELPER_NAMES or var_name in IGNORED_CALL_NAMES:
            continue
        # Avoid treating function definitions such as `int main()` as objects.
        if type_name in CPP_DECLARATION_KEYWORDS and code[match.end():match.end() + 1] == ")":
            continue
        variable_types[var_name] = type_name
    return variable_types


def _cpp_constructor_initializer_calls(code: str) -> list[tuple[int, str]]:
    """Infer constructor/type calls from local object initialization.

    Regex call extraction sees ``Widget w(1)`` as a call to ``w(...)`` and the
    later declared-variable filter removes it.  For test->function mapping the
    semantically useful call is the constructed type ``Widget``.  Keep this
    conservative: only local declarations with ``(...)`` or ``{...}``
    initializers are emitted, and standard-library/primitive types are ignored.
    """

    result: list[tuple[int, str]] = []
    type_pattern = (
        r"(?:(?:const|volatile|typename|class|struct)\s+)*"
        r"(?P<type>[A-Za-z_][A-Za-z0-9_:]*)(?:\s*<[^;(){}=]+>)?"
    )
    declaration = re.compile(
        rf"(?<![A-Za-z0-9_:]){type_pattern}(?:\s*[*&]\s*|\s+)"
        r"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<init>\(|\{)"
    )
    for match in declaration.finditer(code):
        if _cpp_declaration_match_is_function_definition(code, match):
            continue
        type_name = _cpp_base_type(match.group("type"))
        var_name = match.group("var")
        if not type_name or type_name in CPP_DECLARATION_KEYWORDS:
            continue
        if type_name in ASSERT_AND_TEST_HELPER_NAMES or type_name in IGNORED_CALL_NAMES:
            continue
        if var_name in ASSERT_AND_TEST_HELPER_NAMES or var_name in IGNORED_CALL_NAMES:
            continue
        result.append((match.start(), type_name))
    return result


def _cpp_variable_type_hints(code: str) -> dict[str, str]:
    """Infer rough local variable type strings, including primitive arrays.

    This is intentionally broader than _cpp_variable_types(): it keeps hints
    such as ``char arg[]`` for overload disambiguation, but those primitive
    hints should not drive receiver/member resolution.
    """

    hints: dict[str, str] = {}
    declaration = re.compile(
        r"(?<![A-Za-z0-9_:])"
        r"(?P<type>[A-Za-z_][A-Za-z0-9_:]*(?:\s*<[^;(){}=]+>)?)\s*(?P<ptr>[*&])?\s*"
        r"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<array>\[[^\]]*\])?\s*(?=\(|=|\{|;)"
    )
    for match in declaration.finditer(code):
        if _cpp_declaration_match_is_function_definition(code, match):
            continue
        type_name = _strip_cpp_template_args(match.group("type")).strip()
        var_name = match.group("var")
        if not type_name or var_name in ASSERT_AND_TEST_HELPER_NAMES or var_name in IGNORED_CALL_NAMES:
            continue
        hints[var_name] = f"{type_name}{match.group('ptr') or ''}{match.group('array') or ''}"
    return hints


def _cpp_call_argument_variables(code: str, call: str) -> list[str]:
    base = re.escape(_base_name(call))
    match = re.search(rf"{base}\s*(?:<[^;{{}}]*>)?\s*\((?P<args>[^;]*)\)", code)
    if not match:
        return []
    return [
        value
        for value in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", match.group("args"))
        if value not in {"true", "false", "nullptr", "NULL", "std"}
    ]


def _resolve_cpp_overload_by_argument_hints(
    call: str,
    test: TestChunk,
    candidates: Iterable[FunctionChunk],
) -> FunctionChunk | None:
    hints = _cpp_variable_type_hints(test.code)
    arg_hints = [hints[arg] for arg in _cpp_call_argument_variables(test.code, call) if arg in hints]
    if not arg_hints:
        return None

    def score(candidate: FunctionChunk) -> int:
        signature = (candidate.code.split("{", 1)[0] if candidate.code else candidate.qualified_name)
        normalized = re.sub(r"\s+", " ", signature)
        total = 0
        for hint in arg_hints:
            compact_hint = hint.replace(" ", "")
            compact_signature = normalized.replace(" ", "")
            if "char" in compact_hint and ("char*" in compact_signature or "char[]" in compact_signature):
                total += 3
            elif "vector" in compact_hint and "vector" in compact_signature:
                total += 2
            elif _cpp_base_type(hint) and _cpp_base_type(hint) in normalized:
                total += 1
        return total

    scored = [(score(candidate), candidate) for candidate in candidates]
    best_score = max((item[0] for item in scored), default=0)
    if best_score <= 0:
        return None
    best = [candidate for item_score, candidate in scored if item_score == best_score]
    return _unique(best)


def direct_call_targets(test: TestChunk) -> list[str]:
    local_names = _test_local_names(test)
    declared_variables: set[str] = set()
    if test.language == "Python":
        calls = _python_call_targets(test.code, test.calls)
    elif test.language == "C++":
        calls = _cpp_call_targets(test.code, test.calls)
        declared_variables = _cpp_declared_variable_names(test.code)
    else:
        calls = _dedupe_calls(test.calls)
    return [
        call for call in calls
        if not _is_ignored_call(call, local_names)
        and _base_name(call) not in declared_variables
    ]


def _raw_call_targets(test: TestChunk) -> list[str]:
    if test.language == "Python":
        return _python_call_targets(test.code, test.calls)
    if test.language == "C++":
        return _cpp_call_targets(test.code, test.calls)
    return _dedupe_calls(test.calls)


def _helper_call_targets(test: TestChunk) -> list[str]:
    """Return one-hop business calls made by helpers invoked from a test.

    Public tests often wrap assertions in file-local helpers.  The direct test
    body then calls only the helper, while the actual business API is called
    inside that helper.  Keep direct_call_targets() strict, and use this helper
    only in source-function resolution.
    """

    if not test.helpers:
        return []
    called_helpers = {_base_name(call) for call in _raw_call_targets(test)}
    if not called_helpers:
        return []

    local_names = _test_local_names(test)
    calls: list[str] = []
    for helper in test.helpers:
        helper_names = _helper_names_from_code([helper], test.language)
        if helper_names and not (helper_names & called_helpers):
            continue
        if test.language == "Python":
            helper_calls = _python_call_targets(helper, [])
        elif test.language == "C++":
            helper_calls = _cpp_call_targets(helper, [])
        else:
            helper_calls = []
        calls.extend(
            call for call in helper_calls
            if not _is_ignored_call(call, local_names)
        )
    return _dedupe_calls(calls)


def _python_import_maps(imports: Iterable[str]) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    module_aliases: dict[str, str] = {}
    symbol_imports: dict[str, tuple[str, str]] = {}
    try:
        tree = ast.parse("\n".join(imports))
    except SyntaxError:
        return module_aliases, symbol_imports
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                key = alias.asname or alias.name.split(".", 1)[0]
                module_aliases[key] = alias.name
                module_aliases[alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                key = alias.asname or alias.name
                symbol_imports[key] = (node.module, alias.name)
    return module_aliases, symbol_imports


def _indexed_functions(functions: Iterable[FunctionChunk]) -> tuple[dict[str, list[FunctionChunk]], list[FunctionChunk]]:
    by_name: dict[str, list[FunctionChunk]] = {}
    all_functions = list(functions)
    for function in all_functions:
        by_name.setdefault(function.name, []).append(function)
        if function.parent:
            by_name.setdefault(function.parent, []).append(function)
    return by_name, all_functions


def _constructor_candidates(name: str, functions: Iterable[FunctionChunk]) -> list[FunctionChunk]:
    return [
        function for function in functions
        if function.parent == name and function.name in {"__init__", name, f"~{name}"}
    ]


def _unique(candidates: Iterable[FunctionChunk]) -> FunctionChunk | None:
    by_id = {candidate.chunk_id: candidate for candidate in candidates}
    return next(iter(by_id.values())) if len(by_id) == 1 else None


def _cpp_prefer_duplicate_declaration(
    candidates: Iterable[FunctionChunk],
    *,
    include_keys: set[str],
) -> FunctionChunk | None:
    """Pick one C++ chunk when duplicates represent the same API symbol.

    C++ projects often expose both a header declaration/inline wrapper and a
    source implementation with the same qualified name.  Returning no result in
    that case loses obvious public API calls such as ``json11::Json::parse``.
    Keep this intentionally narrow: only collapse candidates that share one
    normalized qualified name, and do not guess among constructor overloads.
    """

    by_id = {candidate.chunk_id: candidate for candidate in candidates}
    unique = list(by_id.values())
    if len(unique) <= 1:
        return unique[0] if unique else None
    if len({_normalized_qualified(candidate.qualified_name) for candidate in unique}) != 1:
        return None
    if all(candidate.parent and candidate.name == candidate.parent for candidate in unique):
        return None

    include_matches = [candidate for candidate in unique if _cpp_file_matches_include(candidate, include_keys)]
    if not include_matches:
        return None
    pool = include_matches or unique
    prefer_header = bool(include_matches)
    return sorted(
        pool,
        key=lambda candidate: (
            0 if _cpp_file_matches_include(candidate, include_keys) else 1,
            0 if (_cpp_is_header_file(candidate) == prefer_header) else 1,
            candidate.file.replace("\\", "/"),
            candidate.start_line,
            candidate.end_line,
            candidate.chunk_id,
        ),
    )[0]


def _filter_python_module(candidates: Iterable[FunctionChunk], module: str) -> list[FunctionChunk]:
    return [candidate for candidate in candidates if _file_matches_python_module(candidate, module)]


def _resolve_python_call(call: str, test: TestChunk, functions: list[FunctionChunk]) -> ResolvedFunction | None:
    module_aliases, symbol_imports = _python_import_maps(test.imports)
    by_name, all_functions = _indexed_functions(functions)
    parts = call.split(".")
    base = parts[-1]

    if len(parts) >= 2:
        prefix = parts[0]
        member = parts[-1]
        module = module_aliases.get(prefix)
        if module:
            candidates = _filter_python_module(by_name.get(member, []), module)
            resolved = _unique(candidates) or _unique(_filter_python_module(_constructor_candidates(member, all_functions), module))
            if resolved:
                return ResolvedFunction(resolved, call, "python_module_alias")

    imported = symbol_imports.get(base)
    if imported:
        module, original = imported
        candidates = _filter_python_module(by_name.get(original, []), module)
        resolved = _unique(candidates) or _unique(_filter_python_module(_constructor_candidates(original, all_functions), module))
        if resolved:
            return ResolvedFunction(resolved, call, "python_from_import")

    qualified = _normalized_qualified(call)
    exact = _unique(function for function in all_functions if _normalized_qualified(function.qualified_name) == qualified)
    if exact:
        return ResolvedFunction(exact, call, "python_qualified_name")

    resolved = _unique(by_name.get(base, [])) or _unique(_constructor_candidates(base, all_functions))
    if resolved:
        return ResolvedFunction(resolved, call, "python_unique_name")
    return None


def _cpp_include_keys(imports: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    for item in imports:
        match = re.search(r"#\s*include\s*[<\"]([^>\"]+)[>\"]", item)
        if not match:
            continue
        include = PurePosixPath(match.group(1))
        without_suffix = str(include.with_suffix(""))
        keys.add(without_suffix)
        keys.add(include.stem)
    return keys


def _cpp_file_matches_include(function: FunctionChunk, include_keys: set[str]) -> bool:
    if not include_keys:
        return False
    file_path = PurePosixPath(function.file)
    without_suffix = str(file_path.with_suffix(""))
    stem = file_path.stem
    return any(
        stem == key or without_suffix.endswith(key) or key.endswith(stem)
        for key in include_keys
    )


def _cpp_is_header_file(function: FunctionChunk) -> bool:
    return PurePosixPath(function.file).suffix.lower() in {".h", ".hh", ".hpp", ".hxx"}


def _cpp_duplicate_preference_key(function: FunctionChunk, *, include_keys: set[str]) -> tuple:
    normalized_file = function.file.replace("\\", "/").lower()
    path_parts = set(PurePosixPath(normalized_file).parts)
    include_rank = 0 if _cpp_file_matches_include(function, include_keys) else 1
    noise_rank = 1 if path_parts & {"example", "examples", "benchmark", "benchmarks"} or "bench" in PurePosixPath(normalized_file).stem else 0
    if "ulid_uint128" in normalized_file:
        variant_rank = 0
    elif "ulid_struct" in normalized_file:
        variant_rank = 1
    else:
        variant_rank = 0
    if any(token in normalized_file for token in ("_unix", "/unix", "_linux", "/linux", "posix")):
        platform_rank = 0
    elif any(token in normalized_file for token in ("_win", "/win", "windows")):
        platform_rank = 2
    else:
        platform_rank = 1
    extension_rank = 1 if _cpp_is_header_file(function) else 0
    return (
        include_rank,
        noise_rank,
        variant_rank,
        platform_rank,
        extension_rank,
        function.file.replace("\\", "/"),
        function.start_line,
        function.end_line,
        function.chunk_id,
    )



def _cpp_prefer_by_qualified_receiver(
    candidates: Iterable[FunctionChunk],
    *,
    qualified_call: str,
) -> FunctionChunk | None:
    """Prefer receiver-specific C++ chunks for calls like ``JsonObject.is``.

    Some template/member functions are parsed without a parent because the
    tree-sitter declarator is deeply nested.  When the test receiver type is
    known, the containing file is still a strong signal: ``JsonObject.is``
    should not resolve to ``JsonArray.hpp`` merely because both define ``is``.
    """

    if "." not in qualified_call:
        return None
    receiver = qualified_call.rsplit(".", 1)[0].rsplit(".", 1)[-1].lower()
    if not receiver:
        return None
    unique = list({candidate.chunk_id: candidate for candidate in candidates}.values())
    if any(candidate.parent for candidate in unique):
        return None
    matches = [
        candidate
        for candidate in unique
        if receiver in candidate.file.replace("\\", "/").lower()
        or receiver in _normalized_qualified(candidate.qualified_name).lower().split(".")
    ]
    if not matches:
        return None

    def noise_rank(candidate: FunctionChunk) -> int:
        normalized_file = candidate.file.replace("\\", "/").lower()
        path_parts = set(PurePosixPath(normalized_file).parts)
        return int(
            bool(path_parts & {"example", "examples", "benchmark", "benchmarks"})
            or "bench" in PurePosixPath(normalized_file).stem
        )

    return sorted(
        matches,
        key=lambda candidate: (
            noise_rank(candidate),
            0 if PurePosixPath(candidate.file).stem.lower() == receiver else 1,
            0 if _cpp_is_header_file(candidate) else 1,
            candidate.file.replace("\\", "/"),
            candidate.start_line,
            candidate.end_line,
            candidate.chunk_id,
        ),
    )[0]


def _cpp_prefer_duplicate_by_platform_or_source(
    candidates: Iterable[FunctionChunk],
    *,
    include_keys: set[str],
) -> FunctionChunk | None:
    """Resolve duplicate C++ APIs with identical qualified names.

    RepoTransBench projects often expose both platform-specific implementations
    for the same public API, e.g. ``Process::get_exit_status`` in
    ``process_unix.cpp`` and ``process_win.cpp``.  The evaluation environment is
    Linux, so preferring Unix/Linux implementations is a more useful deterministic
    tie-break than dropping the otherwise obvious call as ambiguous.
    """

    by_id = {candidate.chunk_id: candidate for candidate in candidates}
    unique = list(by_id.values())
    if len(unique) <= 1:
        return unique[0] if unique else None
    if len({_normalized_qualified(candidate.qualified_name) for candidate in unique}) != 1:
        return None
    if all(candidate.parent and candidate.name == candidate.parent for candidate in unique):
        return None
    return sorted(unique, key=lambda candidate: _cpp_duplicate_preference_key(candidate, include_keys=include_keys))[0]


def _resolve_cpp_call(call: str, test: TestChunk, functions: list[FunctionChunk]) -> ResolvedFunction | None:
    by_name, all_functions = _indexed_functions(functions)
    include_keys = _cpp_include_keys(test.imports)
    qualified = _normalized_qualified(call)
    base = _base_name(call)

    exact_candidates = [function for function in all_functions if _normalized_qualified(function.qualified_name) == qualified]
    exact = _unique(exact_candidates)
    if exact is None and "." in qualified:
        exact = (
            _cpp_prefer_duplicate_declaration(exact_candidates, include_keys=include_keys)
            or _cpp_prefer_duplicate_by_platform_or_source(exact_candidates, include_keys=include_keys)
        )
    if exact:
        return ResolvedFunction(exact, call, "cpp_qualified_name")

    if "." in qualified:
        suffix_candidates = [
            function
            for function in all_functions
            if _normalized_qualified(function.qualified_name).endswith(f".{qualified}")
            or qualified.endswith(f".{_normalized_qualified(function.qualified_name)}")
        ]
        resolved = (
            _unique(suffix_candidates)
            or _cpp_prefer_by_qualified_receiver(suffix_candidates, qualified_call=qualified)
            or _cpp_prefer_duplicate_declaration(suffix_candidates, include_keys=include_keys)
        )
        if resolved is None:
            resolved = _cpp_prefer_duplicate_by_platform_or_source(suffix_candidates, include_keys=include_keys)
        if resolved:
            return ResolvedFunction(resolved, call, "cpp_qualified_suffix")

    candidates = by_name.get(base, []) + _constructor_candidates(base, all_functions)
    include_candidates = [candidate for candidate in candidates if _cpp_file_matches_include(candidate, include_keys)]
    resolved = _unique(include_candidates) or _cpp_prefer_duplicate_declaration(include_candidates, include_keys=include_keys)
    if resolved is None:
        resolved = _cpp_prefer_duplicate_by_platform_or_source(include_candidates, include_keys=include_keys)
    if resolved:
        return ResolvedFunction(resolved, call, "cpp_include")

    resolved = _resolve_cpp_overload_by_argument_hints(call, test, candidates)
    if resolved:
        return ResolvedFunction(resolved, call, "cpp_overload_argument_hint")

    resolved = _unique(candidates)
    if resolved:
        return ResolvedFunction(resolved, call, "cpp_unique_name")
    return None



def resolve_source_function_links(
    test: TestChunk,
    functions: Iterable[FunctionChunk],
    *,
    include_helpers: bool = False,
) -> list[ResolvedFunction]:
    function_list = list(functions)
    links: list[ResolvedFunction] = []
    seen_functions: set[str] = set()
    calls_with_source = [(call, "direct") for call in direct_call_targets(test)]
    if include_helpers:
        calls_with_source.extend((call, "helper") for call in _helper_call_targets(test))
    for call, source in calls_with_source:
        if test.language == "Python":
            link = _resolve_python_call(call, test, function_list)
        elif test.language == "C++":
            link = _resolve_cpp_call(call, test, function_list)
        else:
            link = None
        if link and link.function.chunk_id not in seen_functions:
            if source == "helper":
                link = ResolvedFunction(link.function, link.call, f"{link.reason}_via_helper")
            links.append(link)
            seen_functions.add(link.function.chunk_id)
    return links


def related_functions(test: TestChunk, functions: Iterable[FunctionChunk]) -> list[FunctionChunk]:
    return [link.function for link in resolve_source_function_links(test, functions)]
