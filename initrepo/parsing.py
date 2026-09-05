"""使用 Tree-sitter 提取函数、测试和测试中的调用名。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from tree_sitter import Language, Parser
import tree_sitter_cpp
import tree_sitter_python

from common.models import Chunk


_PARSERS = {
    "Python": Parser(Language(tree_sitter_python.language())),
    "C++": Parser(Language(tree_sitter_cpp.language())),
}
_IGNORED_CALLS = {
    "assert", "assert_equal", "assertEqual", "assertTrue", "assertFalse",
    "ASSERT_EQ", "ASSERT_TRUE", "ASSERT_FALSE", "EXPECT_EQ", "EXPECT_TRUE",
    "EXPECT_FALSE", "REQUIRE", "CHECK", "print", "len", "range", "str",
    "int", "float", "bool", "list", "dict", "set", "tuple", "sizeof",
    "if", "for", "while", "switch", "return", "throw", "new", "delete",
}
_CPP_TEST_MACROS = {
    "TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P",
    "TEST_CASE", "SCENARIO", "BOOST_AUTO_TEST_CASE", "DOCTEST_TEST_CASE",
}


def _walk(node) -> Iterator:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _text(source: str, node) -> str:
    return source[node.start_byte:node.end_byte]


def _parser(language: str) -> Parser:
    try:
        return _PARSERS[language]
    except KeyError as exc:
        raise ValueError(f"暂不支持 {language} 的 Tree-sitter 解析") from exc


def _parse(path: Path, language: str):
    source = path.read_text(encoding="utf-8", errors="replace")
    return source, _parser(language).parse(source.encode("utf-8"))


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _name(node, source: str) -> str | None:
    value = node.child_by_field_name("name")
    if value is None:
        return None
    result = _text(source, value).strip()
    return result or None


def _python_parent(node, source: str) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "class_definition":
            return _name(parent, source)
        parent = parent.parent
    return None


def _python_calls(node, source: str) -> list[str]:
    calls: set[str] = set()
    for item in _walk(node):
        if item.type != "call":
            continue
        function = item.child_by_field_name("function")
        if function is not None:
            value = _text(source, function).strip().split(".")[-1]
            if value and value not in _IGNORED_CALLS:
                calls.add(value)
    return sorted(calls)


def _qualified(parent: str | None, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _function_chunk(
    *, chunk_id: str, project: str, language: str, file: str, name: str,
    qualified_name: str, code: str, start_line: int, end_line: int,
    parent: str | None, calls: list[str],
) -> Chunk:
    return {
        "chunk_id": chunk_id, "project": project, "language": language,
        "file": file, "name": name, "qualified_name": qualified_name,
        "code": code, "start_line": start_line, "end_line": end_line,
        "parent": parent, "calls": calls,
    }


def _test_chunk(
    *, chunk_id: str, project: str, language: str, file: str, name: str,
    qualified_name: str, code: str, start_line: int, end_line: int,
    framework: str, parent: str | None = None, fixture: str | None = None,
    imports: list[str] | None = None, calls: list[str] | None = None,
    helpers: list[str] | None = None, helper_calls: list[str] | None = None,
) -> Chunk:
    return {
        "chunk_id": chunk_id, "project": project, "language": language,
        "file": file, "name": name, "qualified_name": qualified_name,
        "code": code, "start_line": start_line, "end_line": end_line,
        "framework": framework, "parent": parent, "fixture": fixture,
        "imports": imports or [], "calls": calls or [], "helpers": helpers or [],
        "helper_calls": helper_calls or [],
        "metadata": {},
    }


def extract_python_functions(path: Path, root: Path, project: str) -> list[Chunk]:
    source, tree = _parse(path, "Python")
    relative = _relative(path, root)
    result: list[Chunk] = []
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        name = _name(node, source)
        if not name or name.startswith("test_"):
            continue
        parent = _python_parent(node, source)
        qualified = _qualified(parent, name)
        result.append(_function_chunk(
            chunk_id=f"{project}:Python:{relative}:{qualified}:{node.start_point.row + 1}",
            project=project,
            language="Python",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=_text(source, node),
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            parent=parent,
            calls=_python_calls(node, source),
        ))
    return result


def extract_python_tests(path: Path, root: Path, project: str) -> list[Chunk]:
    source, tree = _parse(path, "Python")
    relative = _relative(path, root)
    imports = [_text(source, node).strip() for node in tree.root_node.named_children
               if node.type in {"import_statement", "import_from_statement"}]
    helper_code: dict[str, str] = {}
    helper_calls: dict[str, list[str]] = {}
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        name = _name(node, source)
        if not name or name.startswith("test_"):
            continue
        helper_code[name] = _text(source, node)
        helper_calls[name] = _python_calls(node, source)
    result: list[Chunk] = []
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        name = _name(node, source)
        if not name or not name.startswith("test_"):
            continue
        parent = _python_parent(node, source)
        qualified = _qualified(parent, name)
        calls = _python_calls(node, source)
        helper_codes = [helper_code[value] for value in calls if value in helper_code]
        nested_calls = [
            call
            for value in calls
            if value in helper_calls
            for call in helper_calls[value]
        ]
        result.append(_test_chunk(
            chunk_id=f"{project}:Python:{relative}:{qualified}:{node.start_point.row + 1}",
            project=project,
            language="Python",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=_text(source, node),
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            framework="pytest_or_unittest",
            parent=parent,
            fixture=parent,
            imports=imports,
            calls=calls,
            helpers=helper_codes,
            helper_calls=nested_calls,
        ))
    return result


def _cpp_function_name(declarator: str) -> tuple[str, str | None]:
    names = re.findall(r"((?:[A-Za-z_]\w*::)*~?[A-Za-z_]\w*)\s*\(", declarator)
    if not names:
        return "", None
    full = names[-1]
    parts = full.split("::")
    return parts[-1], parts[-2] if len(parts) > 1 else None


def _cpp_calls(code: str) -> list[str]:
    values = re.findall(r"\b((?:[A-Za-z_]\w*(?:::\w+|\.\w+|->\w*)*))\s*\(", code)
    result = set()
    for value in values:
        base = re.split(r"::|\.|->", value)[-1]
        if base not in _IGNORED_CALLS and base not in _CPP_TEST_MACROS:
            result.add(base)
    return sorted(result)


def _cpp_parent(node, source: str) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type in {"class_specifier", "struct_specifier"}:
            return _name(parent, source)
        parent = parent.parent
    return None


def extract_cpp_functions(path: Path, root: Path, project: str) -> list[Chunk]:
    source, tree = _parse(path, "C++")
    relative = _relative(path, root)
    result: list[Chunk] = []
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        declarator = node.child_by_field_name("declarator")
        if declarator is None:
            continue
        name, declared_parent = _cpp_function_name(_text(source, declarator))
        parent = declared_parent or _cpp_parent(node, source)
        if not name or name in _CPP_TEST_MACROS:
            continue
        qualified = _qualified(parent, name)
        result.append(_function_chunk(
            chunk_id=f"{project}:C++:{relative}:{qualified}:{node.start_point.row + 1}",
            project=project,
            language="C++",
            file=relative,
            name=name,
            qualified_name=qualified,
            code=_text(source, node),
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            parent=parent,
            calls=_cpp_calls(_text(source, node)),
        ))
    return result


def _balanced_end(source: str, opening: int) -> int | None:
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
        if char in {"\"", "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def extract_cpp_tests(path: Path, root: Path, project: str) -> list[Chunk]:
    source, tree = _parse(path, "C++")
    relative = _relative(path, root)
    includes = re.findall(r"(?m)^\s*#\s*include\s*[<\"][^>\"]+[>\"]", source)
    helper_code: dict[str, str] = {}
    helper_calls: dict[str, list[str]] = {}
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        declarator = node.child_by_field_name("declarator")
        fn_name, _ = _cpp_function_name(_text(source, declarator) if declarator else "")
        if not fn_name or fn_name in _CPP_TEST_MACROS or "test" in fn_name.lower() or fn_name == "main":
            continue
        helper_code[fn_name] = _text(source, node)
        helper_calls[fn_name] = _cpp_calls(_text(source, node))

    result: list[Chunk] = []
    macro_pattern = re.compile(r"\b([A-Z][A-Z_]*)\s*\(([^()]*)\)\s*\{")
    for match in macro_pattern.finditer(source):
        if match.group(1) not in _CPP_TEST_MACROS:
            continue
        end = _balanced_end(source, match.end() - 1)
        if end is None:
            continue
        args = [item.strip() for item in match.group(2).split(",") if item.strip()]
        name = ".".join(args) if args else match.group(1)
        code = source[match.start():end]
        start = source.count("\n", 0, match.start()) + 1
        calls = _cpp_calls(code)
        helper_codes = [helper_code[value] for value in calls if value in helper_code]
        nested_calls = [
            call
            for value in calls
            if value in helper_calls
            for call in helper_calls[value]
        ]
        result.append(_test_chunk(
            chunk_id=f"{project}:C++:{relative}:{name}:{start}",
            project=project,
            language="C++",
            file=relative,
            name=name,
            qualified_name=name,
            code=code,
            start_line=start,
            end_line=source.count("\n", 0, end) + 1,
            framework=match.group(1),
            imports=includes,
            calls=calls,
            helpers=helper_codes,
            helper_calls=nested_calls,
        ))
    if result:
        return result

    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        declarator = node.child_by_field_name("declarator")
        name, _ = _cpp_function_name(_text(source, declarator) if declarator else "")
        if name and ("test" in name.lower() or name == "main"):
            calls = _cpp_calls(_text(source, node))
            helper_codes = [helper_code[value] for value in calls if value in helper_code]
            nested_calls = [
                call
                for value in calls
                if value in helper_calls
                for call in helper_calls[value]
            ]
            result.append(_test_chunk(
                chunk_id=f"{project}:C++:{relative}:{name}:{node.start_point.row + 1}",
                project=project,
                language="C++",
                file=relative,
                name=name,
                qualified_name=name,
                code=_text(source, node),
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                framework="plain_function",
                imports=includes,
                calls=calls,
                helpers=helper_codes,
                helper_calls=nested_calls,
            ))
    return result


def extract_functions(paths: list[Path], root: Path, project: str, language: str) -> list[Chunk]:
    result: list[Chunk] = []
    for path in paths:
        if language == "Python":
            result.extend(extract_python_functions(path, root, project))
        elif language == "C++":
            result.extend(extract_cpp_functions(path, root, project))
        else:
            raise ValueError(f"暂不支持 {language}")
    return result


def extract_tests(path: Path, root: Path, project: str, language: str) -> list[Chunk]:
    if language == "Python":
        return extract_python_tests(path, root, project)
    if language == "C++":
        return extract_cpp_tests(path, root, project)
    raise ValueError(f"暂不支持 {language}")
