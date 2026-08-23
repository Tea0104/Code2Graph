from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Iterator

from tree_sitter_graph.extractor import node_text, parser_for

from .models import FunctionChunk, TestChunk


CPP_TEST_MACROS = (
    "TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P", "TEST_CASE", "SCENARIO",
    "BOOST_AUTO_TEST_CASE", "DOCTEST_TEST_CASE",
)
CPP_CALL_EXCLUDES = {
    "TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P", "TEST_CASE", "SCENARIO", "DOCTEST_TEST_CASE",
    "EXPECT_EQ", "EXPECT_NE", "EXPECT_TRUE", "EXPECT_FALSE", "EXPECT_THROW", "EXPECT_NO_THROW",
    "ASSERT_EQ", "ASSERT_NE", "ASSERT_TRUE", "ASSERT_FALSE", "ASSERT_THROW", "ASSERT_NO_THROW",
    "REQUIRE", "CHECK", "SECTION", "GIVEN", "WHEN", "THEN", "AND_THEN",
    "BOOST_AUTO_TEST_CASE", "assert",
    "if", "for", "while", "switch", "catch", "return", "throw", "sizeof",
    "static_cast", "reinterpret_cast", "dynamic_cast", "const_cast",
}
JAVA_TEST_ANNOTATIONS = {
    "Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate",
}
JAVA_CALL_EXCLUDES = {
    "if", "for", "while", "switch", "catch", "try", "synchronized", "return", "throw",
    "new", "this", "super",
    "assertEquals", "assertNotEquals", "assertSame", "assertNotSame", "assertTrue",
    "assertFalse", "assertNull", "assertNotNull", "assertThrows", "assertDoesNotThrow",
    "assertArrayEquals", "assertIterableEquals", "assertLinesMatch", "assertAll",
    "assertThat", "fail", "verify", "when", "thenReturn", "thenThrow",
}


def _walk(node) -> Iterator:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_cpp_test_framework_header(relative: str) -> bool:
    normalized = relative.replace("\\", "/").lower()
    filename = Path(normalized).name
    return filename in {"doctest.h", "doctest.hpp", "catch.hpp", "catch2.hpp", "gtest.h", "gmock.h"}


def _qualified(parent: str | None, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _python_callee(call_node, source: str) -> str | None:
    function = call_node.child_by_field_name("function")
    if function is None:
        return None
    value = node_text(source, function).strip()
    if not value:
        return None
    return value.split(".")[-1]


def _python_parent_class(node, source: str) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "class_definition":
            name = parent.child_by_field_name("name")
            return node_text(source, name).strip() if name is not None else None
        parent = parent.parent
    return None


def _python_imports(root, source: str) -> list[str]:
    return [
        node_text(source, node).strip()
        for node in root.named_children
        if node.type in {"import_statement", "import_from_statement"}
    ]


def _python_function_nodes(root) -> list:
    return [node for node in _walk(root) if node.type == "function_definition"]


def _python_name(node, source: str) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = node_text(source, name_node).strip()
    return name or None


def _walk_direct_scope(node, nested_types: set[str]) -> Iterator:
    yield node
    for child in node.named_children:
        if child.type in nested_types:
            continue
        yield from _walk_direct_scope(child, nested_types)


def _python_direct_calls_from_node(node, source: str) -> list[str]:
    nested_types = {"function_definition", "class_definition", "lambda"}
    values = {
        value
        for child in _walk_direct_scope(node, nested_types)
        if child.type == "call"
        if (value := _python_callee(child, source))
    }
    return sorted(values)


def _python_direct_calls(source: str) -> list[str]:
    tree = parser_for("python").parse(source.encode("utf-8"))
    root = tree.root_node
    functions = _python_function_nodes(root)
    test_functions = [node for node in functions if (_python_name(node, source) or "").startswith("test_")]
    scopes = test_functions or (functions if len(functions) == 1 else [])
    if scopes:
        values = {call for node in scopes for call in _python_direct_calls_from_node(node, source)}
        return sorted(values)
    values = {
        value
        for child in _walk(root)
        if child.type == "call"
        if (value := _python_callee(child, source))
    }
    return sorted(values)


def _python_parameters(node, source: str) -> list[str]:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return []
    values = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node_text(source, parameters))
    return [value for value in values if value not in {"self", "cls"}]


def _python_is_fixture(node, source: str) -> bool:
    parent = node.parent
    if parent is None or parent.type != "decorated_definition":
        return False
    decorators = [node_text(source, child) for child in parent.named_children if child.type == "decorator"]
    return any("fixture" in decorator for decorator in decorators)


def extract_python_functions(path: Path, project_root: Path, project: str) -> list[FunctionChunk]:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = parser_for("python").parse(source.encode("utf-8"))
    result: list[FunctionChunk] = []
    relative = _relative(path, project_root)
    for node in _python_function_nodes(tree.root_node):
        name = _python_name(node, source)
        if name is None:
            continue
        if name.startswith("test_"):
            continue
        parent = _python_parent_class(node, source)
        calls = _python_direct_calls_from_node(node, source)
        qualified = _qualified(parent, name)
        result.append(FunctionChunk(
            chunk_id=f"{project}:Python:{relative}:{qualified}:{node.start_point.row + 1}",
            project=project,
            language="Python",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=node_text(source, node),
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            parent=parent,
            calls=calls,
        ))
    return result


def extract_python_tests(path: Path, project_root: Path, project: str) -> list[TestChunk]:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = parser_for("python").parse(source.encode("utf-8"))
    root = tree.root_node
    relative = _relative(path, project_root)
    imports = _python_imports(root, source)
    functions = _python_function_nodes(root)
    helper_by_name = {}
    fixture_names: set[str] = set()
    for node in functions:
        name = _python_name(node, source)
        if name is None:
            continue
        if not name.startswith("test_"):
            helper_by_name[name] = node_text(source, node)
            if _python_is_fixture(node, source):
                fixture_names.add(name)

    result: list[TestChunk] = []
    for node in functions:
        name = _python_name(node, source)
        if name is None:
            continue
        if not name.startswith("test_"):
            continue
        parent = _python_parent_class(node, source)
        calls = _python_direct_calls_from_node(node, source)
        parameters = _python_parameters(node, source)
        context_names = set(calls) | (set(parameters) & fixture_names)
        if parent:
            context_names.update({"setUp", "setup_method", "setup_class"})
        helpers = [helper_by_name[value] for value in sorted(context_names) if value in helper_by_name]
        code = node_text(source, node)
        qualified = _qualified(parent, name)
        context = "\n".join(imports + helpers)
        chunk_text = f"Project: {project}\nFile: {relative}\nTest: {qualified}\nCalls: {', '.join(calls)}\nCode:\n{code}"
        if context:
            chunk_text += f"\nContext:\n{context}"
        result.append(TestChunk(
            chunk_id=f"{project}:Python:{relative}:{qualified}:{node.start_point.row + 1}",
            project=project,
            language="Python",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=code,
            chunk_text=chunk_text,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            framework="pytest_or_unittest",
            parent=parent,
            fixture=parent,
            imports=imports,
            calls=calls,
            helpers=helpers,
            metadata={"parameters": parameters, "fixture_parameters": sorted(set(parameters) & fixture_names)},
        ))
    return result


@dataclass(frozen=True)
class _MacroMatch:
    macro: str
    args: str
    start: int
    body_start: int
    end: int


def _balanced_end(source: str, opening: int, left: str, right: str) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    index = opening
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and nxt == "/":
            end = source.find("\n", index + 2)
            if end == -1:
                return None
            index = end + 1
            continue
        if char == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            if end == -1:
                return None
            index = end + 2
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _cpp_macros(source: str) -> Iterator[_MacroMatch]:
    names = "|".join(re.escape(name) for name in sorted(CPP_TEST_MACROS, key=len, reverse=True))
    pattern = re.compile(rf"\b({names})\s*\(")
    for match in pattern.finditer(source):
        args_end = _balanced_end(source, match.end() - 1, "(", ")")
        if args_end is None:
            continue
        body_start = args_end
        while body_start < len(source) and source[body_start].isspace():
            body_start += 1
        if body_start >= len(source) or source[body_start] != "{":
            continue
        end = _balanced_end(source, body_start, "{", "}")
        if end is None:
            continue
        yield _MacroMatch(match.group(1), source[match.end():args_end - 1], match.start(), body_start, end)


def _split_macro_args(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
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
        elif char in "([{<":
            depth += 1
        elif char in ")]}>" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def _cpp_name(match: _MacroMatch) -> tuple[str, str | None, str]:
    parts = _split_macro_args(match.args)
    if match.macro in {"TEST_CASE", "SCENARIO", "BOOST_AUTO_TEST_CASE", "DOCTEST_TEST_CASE"}:
        name = parts[0].strip('"') if parts else f"case_{match.start}"
        return name, None, name
    suite = parts[0] if parts else "Suite"
    name = parts[1] if len(parts) > 1 else f"case_{match.start}"
    fixture = suite if match.macro in {"TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P"} else None
    return name, fixture, f"{suite}.{name}"


def _cpp_calls(code: str) -> list[str]:
    values = {
        match.group(1).split("::")[-1]
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_:]*)\s*\(", code)
    }
    return sorted(value for value in values if value not in CPP_CALL_EXCLUDES)


def _cpp_direct_calls(source: str) -> list[str]:
    snippets = [source[match.start:match.end] for match in _cpp_macros(source)]
    if not snippets:
        try:
            tree = parser_for("cpp").parse(source.encode("utf-8"))
            for node in _walk(tree.root_node):
                if node.type != "function_definition":
                    continue
                declarator_node = node.child_by_field_name("declarator")
                declarator = node_text(source, declarator_node) if declarator_node is not None else ""
                name = _cpp_function_name(declarator)
                if name and ("test" in name.lower() or name == "main"):
                    snippets.append(node_text(source, node))
        except Exception:
            snippets = []
    if not snippets:
        snippets = [source]
    return sorted({call for snippet in snippets for call in _cpp_calls(snippet)})


def _strip_java_comments_and_literals(source: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index)
            if end == -1:
                break
            result.append("\n")
            index = end + 1
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            comment = source[index:] if end == -1 else source[index:end + 2]
            result.append("\n" * comment.count("\n"))
            index = len(source) if end == -1 else end + 2
        elif source[index] in {'"', "'"}:
            quote = source[index]
            index += 1
            escaped = False
            while index < len(source):
                char = source[index]
                if char == "\n":
                    result.append("\n")
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    index += 1
                    break
                index += 1
            result.append(" ")
        else:
            result.append(source[index])
            index += 1
    return "".join(result)


def _java_test_method_bodies(source: str) -> list[str]:
    annotations = "|".join(sorted(JAVA_TEST_ANNOTATIONS, key=len, reverse=True))
    pattern = re.compile(rf"@\s*(?:[A-Za-z_$][A-Za-z0-9_$]*\.)*({annotations})\b(?:\s*\([^)]*\))?")
    bodies: list[str] = []
    for match in pattern.finditer(source):
        body_start = source.find("{", match.end())
        if body_start == -1:
            continue
        body_end = _balanced_end(source, body_start, "{", "}")
        if body_end is not None:
            bodies.append(source[body_start + 1:body_end - 1])
    return bodies


def _java_plain_test_method_bodies(source: str) -> list[str]:
    pattern = re.compile(
        r"\b(?:public|protected|private|static|final|synchronized|void|[A-Za-z_$][A-Za-z0-9_$<>\[\], ?]*)"
        r"\s+(test[A-Za-z0-9_$]*)\s*\([^;{}]*\)\s*(?:throws\s+[^{]+)?\{"
    )
    bodies: list[str] = []
    for match in pattern.finditer(source):
        body_start = match.end() - 1
        body_end = _balanced_end(source, body_start, "{", "}")
        if body_end is not None:
            bodies.append(source[body_start + 1:body_end - 1])
    return bodies


def _java_direct_calls(source: str) -> list[str]:
    snippets = _java_test_method_bodies(source) or _java_plain_test_method_bodies(source) or [source]
    calls: set[str] = set()
    for snippet in snippets:
        cleaned = _strip_java_comments_and_literals(snippet)
        for match in re.finditer(r"(?<![A-Za-z0-9_$])([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^(){};]*>)?\s*\(", cleaned):
            name = match.group(1)
            if name not in JAVA_CALL_EXCLUDES:
                calls.add(name)
    return sorted(calls)


def _normalized_language(language: str) -> str:
    value = language.strip().lower().replace("_", "-")
    if value in {"python", "py"}:
        return "Python"
    if value in {"c++", "cpp", "cxx", "cc"}:
        return "C++"
    if value == "java":
        return "Java"
    raise ValueError(f"Unsupported language: {language}")


def extract_direct_calls(code: str, language: str) -> list[str]:
    """Return callee names directly invoked by a test-case snippet."""
    normalized = _normalized_language(language)
    if normalized == "Python":
        return _python_direct_calls(code)
    if normalized == "C++":
        return _cpp_direct_calls(code)
    if normalized == "Java":
        return _java_direct_calls(code)
    raise ValueError(f"Unsupported language: {language}")


def _cpp_includes(source: str) -> list[str]:
    return re.findall(r"(?m)^\s*#\s*include\s*[<\"][^>\"]+[>\"]", source)


def _cpp_definitions(source: str) -> tuple[dict[str, str], dict[str, str]]:
    tree = parser_for("cpp").parse(source.encode("utf-8"))
    functions: dict[str, str] = {}
    classes: dict[str, str] = {}
    for node in _walk(tree.root_node):
        if node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            text = node_text(source, declarator) if declarator is not None else ""
            names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
            if names:
                functions[names[-1]] = node_text(source, node)
        elif node.type in {"class_specifier", "struct_specifier"}:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                classes[node_text(source, name_node).strip()] = node_text(source, node)
    for match in re.finditer(r"\b(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)[^;{}]*\{", source):
        name = match.group(1)
        if name in classes:
            continue
        end = _balanced_end(source, match.end() - 1, "{", "}")
        if end is not None:
            classes[name] = source[match.start():end]
    return functions, classes


def extract_cpp_tests(path: Path, project_root: Path, project: str) -> list[TestChunk]:
    source = path.read_text(encoding="utf-8", errors="replace")
    relative = _relative(path, project_root)
    if _is_cpp_test_framework_header(relative):
        return []
    includes = _cpp_includes(source)
    helpers, classes = _cpp_definitions(source)
    result: list[TestChunk] = []
    macro_matches = list(_cpp_macros(source))
    for match in macro_matches:
        name, fixture, qualified = _cpp_name(match)
        code = source[match.start:match.end]
        calls = _cpp_calls(code)
        helper_codes = [helpers[name] for name in calls if name in helpers]
        class_codes = [
            code for class_name, code in sorted(classes.items())
            if class_name != (fixture or "")
        ]
        fixture_code = classes.get(fixture or "")
        context = "\n".join(includes + ([fixture_code] if fixture_code else []) + class_codes + helper_codes)
        chunk_text = f"Project: {project}\nFile: {relative}\nTest: {qualified}\nCalls: {', '.join(calls)}\nCode:\n{code}"
        if context:
            chunk_text += f"\nContext:\n{context}"
        start_line = _line(source, match.start)
        end_line = _line(source, match.end)
        result.append(TestChunk(
            chunk_id=f"{project}:C++:{relative}:{qualified}:{start_line}",
            project=project,
            language="C++",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=code,
            chunk_text=chunk_text,
            start_line=start_line,
            end_line=end_line,
            framework=match.macro,
            parent=qualified.split(".", 1)[0] if "." in qualified else None,
            fixture=fixture,
            imports=includes,
            calls=calls,
            helpers=class_codes + helper_codes,
        ))
    if result:
        return result

    # Some RepoTransBench C++ public tests use plain test_* functions or one main function.
    tree = parser_for("cpp").parse(source.encode("utf-8"))
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        declarator_node = node.child_by_field_name("declarator")
        declarator = node_text(source, declarator_node) if declarator_node is not None else ""
        name = _cpp_function_name(declarator)
        if not name or ("test" not in name.lower() and name != "main"):
            continue
        code = node_text(source, node)
        calls = _cpp_calls(code)
        helper_codes = [helpers[call] for call in calls if call in helpers and call != name]
        class_codes = [code for _, code in sorted(classes.items())]
        context = "\n".join(includes + class_codes + helper_codes)
        chunk_text = f"Project: {project}\nFile: {relative}\nTest: {name}\nCalls: {', '.join(calls)}\nCode:\n{code}"
        if context:
            chunk_text += f"\nContext:\n{context}"
        result.append(TestChunk(
            chunk_id=f"{project}:C++:{relative}:{name}:{node.start_point.row + 1}",
            project=project,
            language="C++",
            file=relative,
            name=name,
            qualified_name=name,
            code=code,
            chunk_text=chunk_text,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            framework="plain_function",
            imports=includes,
            calls=calls,
            helpers=class_codes + helper_codes,
        ))
    return result


def _cpp_strip_trailing_return(declarator: str) -> str:
    """Drop a C++ trailing return type from a function declarator."""
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(declarator):
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
        elif char in "([{<":
            depth += 1
        elif char in ")]}>" and depth:
            depth -= 1
        elif char == "-" and depth == 0 and declarator[index:index + 2] == "->":
            return declarator[:index].rstrip()
    return declarator


def _cpp_function_name(declarator: str) -> str | None:
    declarator = _cpp_strip_trailing_return(declarator)
    declarator = re.sub(r"\b(?:const|noexcept|override|final)\b", " ", declarator)
    if match := re.search(r"\boperator\s*([=+\-*/%<>!&|^\[\]]+)\s*\(", declarator):
        return f"operator{match.group(1)}"
    if re.search(r"\boperator\s*\(\s*\)\s*\(", declarator):
        return "operator()"
    names = re.findall(r"(?:[A-Za-z_][A-Za-z0-9_]*::)*([A-Za-z_~][A-Za-z0-9_]*)\s*\(", declarator)
    if not names:
        return None
    ignored = {"decltype", "declval", "enable_if", "enable_if_t", "forward", "move"}
    for name in reversed(names):
        if name not in ignored and name not in {"void", "storage_type"}:
            return name
    return None


def _cpp_parent(declarator: str) -> str | None:
    matches = re.findall(r"([A-Za-z_][A-Za-z0-9_:]*)::(?:~?[A-Za-z_][A-Za-z0-9_]*|operator\s*[=+\-*/%<>!&|^\[\]]+|operator\s*\(\s*\))\s*\(", declarator)
    return matches[-1].split("::")[-1] if matches else None


def _cpp_parent_class(node, source: str) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type in {"class_specifier", "struct_specifier"}:
            name_node = parent.child_by_field_name("name")
            if name_node is not None:
                name = node_text(source, name_node).strip()
                return re.split(r"\s*<", name, 1)[0] if name else None
        parent = parent.parent
    return None


CPP_PSEUDO_FUNCTION_NAMES = {
    "decltype",
    "storage_type",
    "void",
    "operator",
    "declval",
    "forward",
    "move",
}


def _cpp_should_keep_function(name: str, qualified: str) -> bool:
    if name in CPP_PSEUDO_FUNCTION_NAMES:
        return False
    if qualified in CPP_PSEUDO_FUNCTION_NAMES:
        return False
    return True


def _cpp_regex_function_chunks(source: str, relative: str, project: str) -> list[FunctionChunk]:
    """Best-effort fallback for C++ inline/free functions missed by tree-sitter.

    Some dataset headers contain simple inline namespace-level functions that
    tree-sitter does not always surface as function_definition nodes in our
    current grammar setup.  This fallback is conservative: it only accepts a
    definition with an immediate body and ignores known test/pseudo names.
    """

    result: list[FunctionChunk] = []
    pattern = re.compile(
        r"(?m)(?:^|[;{}]\s*)"
        r"(?P<decl>"
        r"(?:template\s*<[^;{}]+>\s*)?"
        r"(?:inline\s+|static\s+|constexpr\s+|virtual\s+|friend\s+|explicit\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:<>~,\s*&]+\s+)?"
        r"(?P<name>(?:[A-Za-z_][A-Za-z0-9_]*::)*~?[A-Za-z_][A-Za-z0-9_]*|operator\s*\(\)|operator\s*[=+\-*/%<>!&|^\[\]]+)"
        r"\s*\([^;{}]*\)"
        r"(?:\s*(?:const|noexcept|override|final|volatile))*"
        r")\s*\{"
    )
    for match in pattern.finditer(source):
        declarator = match.group("decl")
        raw_name = match.group("name").replace(" ", "")
        parent = _cpp_parent(declarator)
        if "::" in raw_name:
            raw_parent, raw_base = raw_name.rsplit("::", 1)
            parent = parent or raw_parent.rsplit("::", 1)[-1]
            name = raw_base
        else:
            name = raw_name
        if parent and name in {parent, f"~{parent}"}:
            qualified = name
        else:
            qualified = _qualified(parent, name)
        if not _cpp_should_keep_function(name, qualified):
            continue
        body_end = _balanced_end(source, match.end() - 1, "{", "}")
        if body_end is None:
            continue
        start_line = _line(source, match.start("decl"))
        end_line = _line(source, body_end)
        code = source[match.start("decl"):body_end]
        result.append(FunctionChunk(
            chunk_id=f"{project}:C++:{relative}:{qualified}:{start_line}",
            project=project,
            language="C++",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=code,
            start_line=start_line,
            end_line=end_line,
            parent=parent,
            calls=_cpp_calls(code),
        ))
    return result


def extract_cpp_functions(path: Path, project_root: Path, project: str) -> list[FunctionChunk]:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = parser_for("cpp").parse(source.encode("utf-8"))
    relative = _relative(path, project_root)
    result: list[FunctionChunk] = []
    seen_locations: set[tuple[str, int, str]] = set()
    seen_simple_locations: set[tuple[str, int, str]] = set()
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        declarator_node = node.child_by_field_name("declarator")
        if declarator_node is None:
            continue
        declarator = node_text(source, declarator_node)
        name = _cpp_function_name(declarator)
        if not name:
            continue
        parent = _cpp_parent(declarator) or _cpp_parent_class(node, source)
        if parent and name in {parent, f"~{parent}"}:
            qualified = name
        else:
            qualified = _qualified(parent, name)
        if not _cpp_should_keep_function(name, qualified):
            continue
        code = node_text(source, node)
        start_line = node.start_point.row + 1
        result.append(FunctionChunk(
            chunk_id=f"{project}:C++:{relative}:{qualified}:{start_line}",
            project=project,
            language="C++",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=code,
            start_line=start_line,
            end_line=node.end_point.row + 1,
            parent=parent,
            calls=_cpp_calls(code),
        ))
        seen_locations.add((relative, start_line, qualified))
        seen_simple_locations.add((relative, start_line, name))
    for chunk in _cpp_regex_function_chunks(source, relative, project):
        key = (relative, chunk.start_line, chunk.qualified_name)
        simple_key = (relative, chunk.start_line, chunk.name)
        if key not in seen_locations and simple_key not in seen_simple_locations:
            result.append(chunk)
            seen_locations.add(key)
            seen_simple_locations.add(simple_key)
    return result


def extract_tests(path: Path, project_root: Path, project: str, language: str) -> list[TestChunk]:
    if language == "Python":
        return extract_python_tests(path, project_root, project)
    if language == "C++":
        return extract_cpp_tests(path, project_root, project)
    raise ValueError(f"Unsupported language: {language}")


def extract_functions(paths: Iterable[Path], project_root: Path, project: str, language: str) -> list[FunctionChunk]:
    if language == "Python":
        result: list[FunctionChunk] = []
        for path in paths:
            result.extend(extract_python_functions(path, project_root, project))
        return result
    # C++ function extraction can reuse the repository graph and is added when C++->Python is evaluated.
    if language == "C++":
        result = []
        for path in paths:
            result.extend(extract_cpp_functions(path, project_root, project))
        return result
    raise ValueError(f"Unsupported language: {language}")
