#!/usr/bin/env python3
"""
对源代码文件进行拓扑排序 —— 如果文件 A 依赖文件 B 的内容（通过 import/include），
则 B 排在 A 前面。遇到循环依赖时自动断开"弱边"（文件末尾的延迟导入）。

支持的语言：
- Python: import xxx, from xxx import yyy（含相对导入）
- C/C++: #include "local_file.h"

用法：
    python topo_sort_files.py --source ./myproject
    python topo_sort_files.py --source ./myproject --lang cpp
"""

from __future__ import annotations

import argparse
import heapq
import json
import posixpath
import re
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 语言扩展名映射
# ---------------------------------------------------------------------------
LANGUAGE_EXTENSIONS: dict[str, set[str]] = {
    "python": {".py"},
    "cpp": {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"},
}

SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".venv", "venv", ".tox", ".nox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "site-packages", "node_modules", "build", "dist",
    "test", "tests", "public_test", "public_tests", "spec", "specs",
}


# ---------------------------------------------------------------------------
# 依赖提取（优先 tree-sitter，回退到正则）
# ---------------------------------------------------------------------------

def _looks_like_test_file(path: Path) -> bool:
    stem = path.stem.lower()
    return (
        stem.startswith(("test_", "public_test_"))
        or stem.endswith(("_test", "_tests", "_spec"))
        or "_public_test" in stem
    )


def _should_skip(path: Path, include_tests: bool = False) -> bool:
    parts = {part.lower() for part in path.parts}
    skipped_dirs = SKIP_DIR_NAMES
    if include_tests:
        skipped_dirs = skipped_dirs - {
            "test", "tests", "public_test", "public_tests", "spec", "specs",
        }
    if parts & skipped_dirs:
        return True
    if any(part.lower().endswith(".egg-info") for part in path.parts):
        return True
    return not include_tests and _looks_like_test_file(path)


class DependencyExtractor:
    """从源文件中提取它依赖的其他文件（模块）。"""

    def __init__(self, source_root: Path, language: str) -> None:
        self.source_root = source_root
        self.language = language
        self._try_tree_sitter = True

    def extract_imports(self, file_path: Path) -> list[tuple[str, int]]:
        """返回该文件中所有 import/include 的 (文本, 行号) 列表。"""
        if self._try_tree_sitter:
            try:
                return self._extract_with_tree_sitter(file_path)
            except (ImportError, ModuleNotFoundError):
                self._try_tree_sitter = False
                print(
                    f"Warning: tree-sitter unavailable, using fallback parser for {self.language} files.",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(
                    f"Warning: tree-sitter failed for {file_path}: {exc}; using fallback parser.",
                    file=sys.stderr,
                )
        return self._extract_without_tree_sitter(file_path)

    def _extract_with_tree_sitter(self, file_path: Path) -> list[tuple[str, int]]:
        """使用 tree-sitter 精确解析 import/include，返回 (文本, 行号)。"""
        from tree_sitter import Language, Parser

        source = file_path.read_text(encoding="utf-8", errors="replace")
        parser = Parser()
        if self.language == "python":
            import tree_sitter_python  # type: ignore

            language = Language(tree_sitter_python.language())
        elif self.language == "cpp":
            import tree_sitter_cpp  # type: ignore

            language = Language(tree_sitter_cpp.language())
        else:
            return []
        parser.language = language

        tree = parser.parse(source.encode("utf-8"))
        raw: list[tuple[str, int]] = []
        seen: set[str] = set()
        source_bytes = source.encode("utf-8")

        def node_text(node) -> str:
            return source_bytes[node.start_byte:node.end_byte].decode(
                "utf-8", errors="replace"
            )

        def add(value: str, line: int) -> None:
            value = value.strip().strip('"').strip("'").strip("<").strip(">")
            if value and value not in seen:
                seen.add(value)
                raw.append((value, line))

        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if self.language == "python" and node.type == "import_statement":
                for index, child in enumerate(node.children):
                    if node.field_name_for_child(index) != "name":
                        continue
                    name_node = (
                        child.child_by_field_name("name")
                        if child.type == "aliased_import"
                        else child
                    )
                    add(node_text(name_node), node.start_point.row + 1)
                continue
            if self.language == "python" and node.type == "import_from_statement":
                module_node = node.child_by_field_name("module_name")
                if module_node is not None:
                    module = node_text(module_node).strip()
                    add(module, node.start_point.row + 1)
                    for index, child in enumerate(node.children):
                        if node.field_name_for_child(index) != "name":
                            continue
                        name_node = (
                            child.child_by_field_name("name")
                            if child.type == "aliased_import"
                            else child
                        )
                        imported = node_text(name_node).strip()
                        if imported and imported != "*":
                            add(f"{module}.{imported}", node.start_point.row + 1)
                continue
            if self.language == "cpp" and node.type == "preproc_include":
                path_node = node.child_by_field_name("path")
                if path_node is not None:
                    add(node_text(path_node), node.start_point.row + 1)
                continue
            stack.extend(reversed(node.children))
        return raw

    def _extract_without_tree_sitter(self, file_path: Path) -> list[tuple[str, int]]:
        """Use best-effort regex extraction when tree-sitter is unavailable."""
        source = file_path.read_text(encoding="utf-8", errors="replace")

        if self.language == "python":
            return _extract_python_imports_with_regex(source)

        raw: list[tuple[str, int]] = []
        if self.language == "cpp":
            for m in re.finditer(r'#include\s+"([^"]+)"', source):
                raw.append((m.group(1), _line_of(source, m.start())))
            for m in re.finditer(r'#include\s+<([^>]+)>', source):
                raw.append((m.group(1), _line_of(source, m.start())))

        return raw


def _line_of(source: str, pos: int) -> int:
    """返回 source 中位置 pos 的行号（1-based）。"""
    return source[:pos].count("\n") + 1


def _extract_python_imports_with_regex(source: str) -> list[tuple[str, int]]:
    """Best-effort import extraction for Python 2 or damaged source files."""
    raw: list[tuple[str, int]] = []
    import_pattern = re.compile(r'^\s*import\s+([^#\n]+)', re.MULTILINE)
    from_pattern = re.compile(
        r'^\s*from\s+([.\w]+)\s+import\s+(\([^)]*\)|[^#\n]+)',
        re.MULTILINE,
    )
    for match in import_pattern.finditer(source):
        line = _line_of(source, match.start())
        for item in match.group(1).split(","):
            name = item.strip().split(" as ", 1)[0].strip()
            if name:
                raw.append((name, line))
    for match in from_pattern.finditer(source):
        module = match.group(1).strip()
        line = _line_of(source, match.start())
        raw.append((module, line))
        names = match.group(2).strip().strip("()")
        for item in names.split(","):
            name = item.strip().split(" as ", 1)[0].strip()
            if name and name != "*":
                raw.append((f"{module}.{name}", line))
    return raw


# ---------------------------------------------------------------------------
# 导入路径解析
# ---------------------------------------------------------------------------

def _resolve_python_import(
    import_text: str,
    importer_rel_path: str,
    known_files: set[str],
) -> str | None:
    if import_text.startswith("."):
        importer_dir = Path(importer_rel_path).parent
        dots = 0
        for ch in import_text:
            if ch == ".":
                dots += 1
            else:
                break
        module_part = import_text[dots:]
        for _ in range(dots - 1):
            importer_dir = importer_dir.parent
        if module_part:
            target = (importer_dir / module_part.replace(".", "/")).as_posix()
        else:
            target = importer_dir.as_posix()
        for candidate in (f"{target}.py", f"{target}/__init__.py"):
            if candidate in known_files:
                return candidate
        return None

    # 绝对导入，从 source_root 搜索，最长匹配优先
    parts = import_text.split(".")
    importer_dir = Path(importer_rel_path).parent

    for i in range(len(parts), 0, -1):
        target = "/".join(parts[:i])
        # 1) 标准 Python 3：从 source_root 搜索
        for candidate in (f"{target}.py", f"{target}/__init__.py"):
            if candidate in known_files:
                return candidate
        # 2) 回退：导入者同目录（sys.path[0] = 脚本所在目录）
        for candidate in (
            f"{(importer_dir / target).as_posix()}.py",
            f"{(importer_dir / target).as_posix()}/__init__.py",
        ):
            if candidate in known_files:
                return candidate
    return None


def _resolve_cpp_include(
    include_text: str,
    importer_rel_path: str,
    known_files: set[str],
) -> str | None:
    importer_dir = Path(importer_rel_path).parent
    cpp_exts = LANGUAGE_EXTENSIONS["cpp"]

    def candidates(base: str) -> list[str]:
        base = posixpath.normpath(base.replace("\\", "/"))
        if any(base.endswith(ext) for ext in cpp_exts):
            return [base]
        return [f"{base}{ext}" for ext in cpp_exts]

    # 1) 引号形式：相对导入文件所在目录
    for c in candidates((importer_dir / include_text).as_posix()):
        if c in known_files:
            return c

    # 2) 相对于 source_root 精确匹配
    for c in candidates(include_text):
        if c in known_files:
            return c

    # 3) 后缀匹配（-I include/path 导致的路径偏移）
    #    #include <beast/server.hpp> 实际文件在 include/beast/server.hpp
    for c in candidates(include_text):
        suffix = "/" + c
        matches = [k for k in known_files if k.endswith(suffix) or k == c]
        if len(matches) == 1:
            return matches[0]

    return None


# ---------------------------------------------------------------------------
# 弱边检测（用于断开循环依赖）
# ---------------------------------------------------------------------------

def _last_definition_line(source_root: Path, rel_path: str, language: str) -> int:
    """返回文件中最后一个顶层定义的行号（class/function/struct），无则返回 0。"""
    path = source_root / rel_path
    if not path.is_file():
        return 0
    source = path.read_text(encoding="utf-8", errors="replace")

    if language == "python":
        pattern = r'^\s*(?:class|def|async def)\s+'
    else:
        pattern = r'^\s*(?:class|struct|enum\s+class|enum)\s+'

    last = 0
    for m in re.finditer(pattern, source, re.MULTILINE):
        last = _line_of(source, m.start())
    return last


# ---------------------------------------------------------------------------
# 构建依赖图 + 拓扑排序
# ---------------------------------------------------------------------------

def build_dependency_graph(
    source_root: Path,
    languages: list[str],
    include_tests: bool = False,
) -> tuple[dict[str, list[str]], set[str], dict[tuple[str, str], int]]:
    """
    返回:
        adjacency:   { rel_path → [依赖的 rel_path] }
        all_nodes:   所有 rel_path 的集合
        edge_lines:  {(source, target) → import_line} 用于环路断开
    """
    # 收集节点
    all_nodes: set[str] = set()
    for language in languages:
        node_exts = LANGUAGE_EXTENSIONS[language]
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or _should_skip(
                path.relative_to(source_root), include_tests
            ):
                continue
            if path.suffix.lower() in node_exts:
                all_nodes.add(path.relative_to(source_root).as_posix())

    adjacency: dict[str, list[str]] = {}
    edge_lines: dict[tuple[str, str], int] = {}

    for language in languages:
        extractor = DependencyExtractor(source_root, language)
        scan_exts = LANGUAGE_EXTENSIONS[language]
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or _should_skip(
                path.relative_to(source_root), include_tests
            ):
                continue
            if path.suffix.lower() not in scan_exts:
                continue

            importer_rel = path.relative_to(source_root).as_posix()
            raw_imports = extractor.extract_imports(path)
            seen: set[str] = set()
            resolved: list[str] = []
            for raw, line_no in raw_imports:
                if language == "python":
                    target = _resolve_python_import(raw, importer_rel, all_nodes)
                else:
                    target = _resolve_cpp_include(raw, importer_rel, all_nodes)
                if target and target != importer_rel:
                    if target not in seen:
                        seen.add(target)
                        resolved.append(target)
                        edge_lines.setdefault((importer_rel, target), line_no)
                elif target is None:
                    label = f"ext:{raw}"
                    if label not in seen:
                        seen.add(label)
                        resolved.append(label)

            adjacency[importer_rel] = resolved

    for node in all_nodes:
        adjacency.setdefault(node, [])

    return adjacency, all_nodes, edge_lines


def topological_sort(
    adjacency: dict[str, list[str]],
    all_nodes: set[str],
    edge_lines: dict[tuple[str, str], int],
    languages: list[str],
    source_root: Path,
) -> tuple[list[str], list[list[str]], set[tuple[str, str]]]:
    """
    Kahn 算法拓扑排序。遇到环路时自动断开弱边（文件末尾的延迟导入）。

    返回:
        sorted_order: 拓扑排序后的节点列表
        cycles:       检测到的原始环路
        broken_edges: 被断开的边集合
    """
    graph: dict[str, list[str]] = defaultdict(list)
    in_deg: dict[str, int] = {node: 0 for node in all_nodes}

    for node in all_nodes:
        graph.setdefault(node, [])

    for node, deps in adjacency.items():
        for dep in deps:
            if dep in all_nodes and dep != node:
                graph[dep].append(node)
                in_deg[node] += 1

    def _kahn(graph: dict[str, list[str]], in_deg: dict[str, int]) -> list[str]:
        deg = dict(in_deg)
        queue = [node for node in all_nodes if deg.get(node, 0) == 0]
        heapq.heapify(queue)
        order: list[str] = []
        while queue:
            node = heapq.heappop(queue)
            order.append(node)
            for neighbor in sorted(graph.get(node, [])):
                deg[neighbor] -= 1
                if deg[neighbor] == 0:
                    heapq.heappush(queue, neighbor)
        return order

    sorted_order = _kahn(graph, in_deg)
    remaining = all_nodes - set(sorted_order)
    all_cycles: list[list[str]] = []
    broken_edges: set[tuple[str, str]] = set()

    while remaining:
        cycles = _find_cycles(graph, remaining)
        all_cycles.extend(cycles)
        if not cycles:
            break

        # 对每个环，找最弱的边断开
        lang = languages[0] if languages else "python"
        for cycle in cycles:
            # 遍历环中每条边 A → B（在图中是 B→A，因为是反向边）
            # 找出 A（doing the import）→ B（being imported）
            candidates: list[tuple[int, str, str]] = []  # (weakness_score, A, B)
            for i in range(len(cycle) - 1):
                B, A = cycle[i], cycle[i + 1]  # graph edge B→A means A depends on B
                # 在 adjacency 中查找 A 依赖 B 的行号
                line_no = _edge_import_line(adjacency, edge_lines, A, B)
                last_def = _last_definition_line(source_root, A, lang)
                # weakness: line_no - last_def，正值 = 导入在定义之后 = 弱边
                weakness = line_no - last_def if line_no and last_def else 0
                candidates.append((weakness, A, B))

            # 选 weakness 最大的边（导入最靠后）
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, break_A, break_B = candidates[0]

            # 断开 A→B：从 graph 中移除边 B→A，从 adjacency 中移除 A→B
            if break_A in graph.get(break_B, []):
                graph[break_B].remove(break_A)
                in_deg[break_A] -= 1
                broken_edges.add((break_A, break_B))

        sorted_order = _kahn(graph, in_deg)
        remaining = all_nodes - set(sorted_order)

        # 防止死循环（理论上不会）
        if not broken_edges:
            break

    # 如果还有剩余节点（理论上不应该），直接追加
    if remaining and broken_edges:
        sorted_order.extend(sorted(remaining))
        remaining = set()

    return sorted_order, all_cycles, broken_edges


def _edge_import_line(
    adjacency: dict[str, list[str]],
    edge_lines: dict[tuple[str, str], int],
    importer: str,
    imported: str,
) -> int:
    """返回 importer 导入 imported 的行号，未知则返回 0。"""
    if imported not in adjacency.get(importer, []):
        return 0
    # 在 edge_lines 中查找，处理扩展名变体
    for (src, tgt), line in edge_lines.items():
        if src == importer and tgt == imported:
            return line
    return 0


def _find_cycles(graph: dict[str, list[str]], nodes: set[str]) -> list[list[str]]:
    """在剩余节点中用 DFS 找出所有简单环路。"""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    stack: list[str] = []
    in_stack: set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        stack.append(node)
        in_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in nodes:
                continue
            if neighbor in in_stack:
                cycle_start = stack.index(neighbor)
                cycles.append(stack[cycle_start:] + [neighbor])
            elif neighbor not in visited:
                dfs(neighbor)
        stack.pop()
        in_stack.discard(node)

    for node in sorted(nodes):
        if node not in visited:
            dfs(node)

    return cycles


def build_json_result(
    source_root: Path,
    languages: list[str],
    adjacency: dict[str, list[str]],
    all_nodes: set[str],
    edge_lines: dict[tuple[str, str], int],
    sorted_order: list[str],
    cycles: list[list[str]],
    broken_edges: set[tuple[str, str]],
) -> dict[str, object]:
    dependencies = []
    external_dependencies = []

    for source_file in sorted(all_nodes):
        for target in sorted(adjacency.get(source_file, [])):
            if target in all_nodes:
                dependencies.append({
                    "file": source_file,
                    "depends_on": target,
                    "line": edge_lines.get((source_file, target)),
                })
            elif target.startswith("ext:"):
                external_dependencies.append({
                    "file": source_file,
                    "dependency": target.removeprefix("ext:"),
                })

    return {
        "source_root": str(source_root),
        "languages": languages,
        "translation_order": sorted_order,
        "dependencies": dependencies,
        "external_dependencies": external_dependencies,
        "cycles": cycles,
        "broken_edges": [
            {"file": source, "depends_on": target}
            for source, target in sorted(broken_edges)
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Topological sort of source files based on import/include dependencies.",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Path to the source repository to analyze.",
    )
    parser.add_argument(
        "--lang",
        default="python",
        help="Language: python, cpp, or comma-separated list. Default: python",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Write output to file instead of stdout.",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test files and test directories in the ordering.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. Default: text",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    source_root = Path(args.source).resolve()

    if not source_root.is_dir():
        print(f"Error: {source_root} is not a directory.", file=sys.stderr)
        sys.exit(1)

    languages = [lang.strip() for lang in args.lang.split(",") if lang.strip()]
    for lang in languages:
        if lang not in LANGUAGE_EXTENSIONS:
            print(f"Error: unsupported language '{lang}'. Supported: {list(LANGUAGE_EXTENSIONS)}", file=sys.stderr)
            sys.exit(1)

    print(f"Scanning {source_root} for {', '.join(languages)} files...", file=sys.stderr)

    adjacency, all_nodes, edge_lines = build_dependency_graph(
        source_root,
        languages,
        include_tests=args.include_tests,
    )
    sorted_order, cycles, broken_edges = topological_sort(
        adjacency, all_nodes, edge_lines, languages, source_root,
    )

    if args.format == "json":
        result = build_json_result(
            source_root,
            languages,
            adjacency,
            all_nodes,
            edge_lines,
            sorted_order,
            cycles,
            broken_edges,
        )
        output = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        output = "\n".join(sorted_order)
        if cycles:
            output += "\n# Detected cycles (auto-resolved by breaking late-import edges):"
            for cycle in cycles:
                output += "\n#   " + " → ".join(cycle)
        if broken_edges:
            output += "\n# Broken edges (import at end of file, after definitions):"
            for src, tgt in sorted(broken_edges):
                output += f"\n#   {src}  imports  {tgt}"

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
