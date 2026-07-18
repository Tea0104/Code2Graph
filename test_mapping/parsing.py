from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Iterator

from tree_sitter_graph.extractor import node_text, parser_for

from .models import FunctionChunk, TestChunk


CPP_TEST_MACROS = (
    "TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P", "TEST_CASE", "SCENARIO",
    "BOOST_AUTO_TEST_CASE",
)
CPP_CALL_EXCLUDES = {
    "TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P", "TEST_CASE", "SCENARIO",
    "EXPECT_EQ", "EXPECT_NE", "EXPECT_TRUE", "EXPECT_FALSE", "EXPECT_THROW", "EXPECT_NO_THROW",
    "ASSERT_EQ", "ASSERT_NE", "ASSERT_TRUE", "ASSERT_FALSE", "ASSERT_THROW", "ASSERT_NO_THROW",
    "REQUIRE", "CHECK", "SECTION", "GIVEN", "WHEN", "THEN", "AND_THEN",
    "BOOST_AUTO_TEST_CASE",
}


def _walk(node) -> Iterator:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


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
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = node_text(source, name_node).strip()
        if name.startswith("test_"):
            continue
        parent = _python_parent_class(node, source)
        calls = sorted({value for child in _walk(node) if child.type == "call" if (value := _python_callee(child, source))})
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
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = node_text(source, name_node).strip()
        if not name.startswith("test_"):
            helper_by_name[name] = node_text(source, node)
            if _python_is_fixture(node, source):
                fixture_names.add(name)

    result: list[TestChunk] = []
    for node in functions:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = node_text(source, name_node).strip()
        if not name.startswith("test_"):
            continue
        parent = _python_parent_class(node, source)
        calls = sorted({value for child in _walk(node) if child.type == "call" if (value := _python_callee(child, source))})
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
    for index in range(opening, len(source)):
        char = source[index]
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
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index + 1
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
    if match.macro in {"TEST_CASE", "SCENARIO", "BOOST_AUTO_TEST_CASE"}:
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
    return functions, classes


def extract_cpp_tests(path: Path, project_root: Path, project: str) -> list[TestChunk]:
    source = path.read_text(encoding="utf-8", errors="replace")
    relative = _relative(path, project_root)
    includes = _cpp_includes(source)
    helpers, classes = _cpp_definitions(source)
    result: list[TestChunk] = []
    macro_matches = list(_cpp_macros(source))
    for match in macro_matches:
        name, fixture, qualified = _cpp_name(match)
        code = source[match.start:match.end]
        calls = _cpp_calls(code)
        helper_codes = [helpers[name] for name in calls if name in helpers]
        fixture_code = classes.get(fixture or "")
        context = "\n".join(includes + ([fixture_code] if fixture_code else []) + helper_codes)
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
            helpers=helper_codes,
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
        context = "\n".join(includes + helper_codes)
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
            helpers=helper_codes,
        ))
    return result


def _cpp_function_name(declarator: str) -> str | None:
    names = re.findall(r"(?:[A-Za-z_][A-Za-z0-9_]*::)*([A-Za-z_~][A-Za-z0-9_]*)\s*\(", declarator)
    return names[-1] if names else None


def _cpp_parent(declarator: str) -> str | None:
    matches = re.findall(r"([A-Za-z_][A-Za-z0-9_:]*)::[A-Za-z_~][A-Za-z0-9_]*\s*\(", declarator)
    return matches[-1] if matches else None


def extract_cpp_functions(path: Path, project_root: Path, project: str) -> list[FunctionChunk]:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = parser_for("cpp").parse(source.encode("utf-8"))
    relative = _relative(path, project_root)
    result: list[FunctionChunk] = []
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
        parent = _cpp_parent(declarator)
        qualified = _qualified(parent, name)
        code = node_text(source, node)
        result.append(FunctionChunk(
            chunk_id=f"{project}:C++:{relative}:{qualified}:{node.start_point.row + 1}",
            project=project,
            language="C++",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=code,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            parent=parent,
            calls=_cpp_calls(code),
        ))
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
