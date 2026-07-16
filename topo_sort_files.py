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
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

# ---------------------------------------------------------------------------
# 语言扩展名映射
# ---------------------------------------------------------------------------
LANGUAGE_EXTENSIONS: dict[str, set[str]] = {
    "python": {".py"},
    "cpp": {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"},
}

# C++ 中只有头文件参与拓扑排序
CPP_HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx"}

SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist"}


# ---------------------------------------------------------------------------
# 依赖提取（优先 tree-sitter，回退到正则）
# ---------------------------------------------------------------------------

def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


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
            except Exception:
                self._try_tree_sitter = False
                print(
                    f"Warning: tree-sitter unavailable, falling back to regex for {self.language} files.",
                    file=sys.stderr,
                )
        return self._extract_with_regex(file_path)

    def _extract_with_tree_sitter(self, file_path: Path) -> list[tuple[str, int]]:
        """使用 tree-sitter 精确解析 import/include，返回 (文本, 行号)。"""
        import tree_sitter_python  # type: ignore
        import tree_sitter_cpp     # type: ignore
        from tree_sitter import Language, Parser, Query, QueryCursor

        source = file_path.read_text(encoding="utf-8")
        parser = Parser()

        if self.language == "python":
            parser.language = Language(tree_sitter_python.language())
            # 同时捕获 module_name 和 imported name，拼接出完整路径
            # from EvoloPy import benchmarks → EvoloPy + EvoloPy.benchmarks
            query = Query(parser.language, """
                (import_statement
                    name: (dotted_name) @name)
                (import_from_statement
                    module_name: (dotted_name) @mod
                    name: (dotted_name) @imp)
                (import_from_statement
                    module_name: (relative_import) @mod
                    name: (dotted_name) @imp)
            """)
        elif self.language == "cpp":
            parser.language = Language(tree_sitter_cpp.language())
            query = Query(parser.language, """
                (preproc_include
                    path: (string_literal) @path)
            """)
        else:
            return []

        tree = parser.parse(source.encode("utf-8"))
        cursor = QueryCursor(query)
        raw: list[tuple[str, int]] = []
        seen: set[str] = set()

        def _clean(node, text: str) -> str:
            """清洗节点文本，兼容旧版 relative_import。"""
            text = text.strip().strip('"').strip("'")
            if node.type == "relative_import" and text.lstrip(".") == "":
                name_child = node.child_by_field_name("name") or next(
                    (c for c in node.named_children if c.type == "dotted_name"), None
                )
                if name_child is not None:
                    name_text = source.encode("utf-8")[name_child.start_byte:name_child.end_byte].decode("utf-8", errors="replace").strip()
                    if name_text:
                        text = text + name_text
            return text

        for _pattern_idx, captures in cursor.matches(tree.root_node):
            mod_nodes = captures.get("mod", [])
            imp_nodes = captures.get("imp", [])
            name_nodes = captures.get("name", [])
            path_nodes = captures.get("path", [])

            # import X → 直接取 name
            for node in name_nodes:
                text = _clean(node, source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="replace"))
                if text and text not in seen:
                    seen.add(text)
                    raw.append((text, node.start_point.row + 1))

            # from MOD import IMP → MOD 和 MOD.IMP 都加入
            if mod_nodes and imp_nodes:
                for mod_node in mod_nodes:
                    mod_text = _clean(mod_node, source.encode("utf-8")[mod_node.start_byte:mod_node.end_byte].decode("utf-8", errors="replace"))
                    if not mod_text:
                        continue
                    if mod_text not in seen:
                        seen.add(mod_text)
                        raw.append((mod_text, mod_node.start_point.row + 1))
                    for imp_node in imp_nodes:
                        imp_text = _clean(imp_node, source.encode("utf-8")[imp_node.start_byte:imp_node.end_byte].decode("utf-8", errors="replace"))
                        if imp_text:
                            combined = f"{mod_text}.{imp_text}"
                            if combined not in seen:
                                seen.add(combined)
                                raw.append((combined, imp_node.start_point.row + 1))

            # #include "..." / #include <...> → strip 引号和尖括号
            for node in path_nodes:
                text = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                text = text.strip().strip('"').strip("'").strip("<").strip(">")
                if text and text not in seen:
                    seen.add(text)
                    raw.append((text, node.start_point.row + 1))
        return raw

    def _extract_with_regex(self, file_path: Path) -> list[tuple[str, int]]:
        """使用正则表达式回退方案提取 import/include，返回 (文本, 行号)。"""
        source = file_path.read_text(encoding="utf-8")
        raw: list[tuple[str, int]] = []

        if self.language == "python":
            # import X
            for m in re.finditer(
                r'^\s*import\s+([\w.]+)',
                source, re.MULTILINE,
            ):
                raw.append((m.group(1).strip(), _line_of(source, m.start())))
            # from MOD import a, b, c → MOD + MOD.a + MOD.b + MOD.c
            for m in re.finditer(
                r'^\s*from\s+([.\w][.\w]*)\s+import\s+(.+)',
                source, re.MULTILINE,
            ):
                mod = m.group(1).strip()
                line = _line_of(source, m.start())
                raw.append((mod, line))
                names_str = re.sub(r'[\(\)]', '', m.group(2))
                for name in re.split(r'\s*,\s*', names_str):
                    name = name.strip().split()[0]  # 去 as xxx
                    if name and name != '*':
                        raw.append((f"{mod}.{name}", line))
        elif self.language == "cpp":
            for m in re.finditer(r'#include\s+"([^"]+)"', source):
                raw.append((m.group(1), _line_of(source, m.start())))
            for m in re.finditer(r'#include\s+<([^>]+)>', source):
                raw.append((m.group(1), _line_of(source, m.start())))

        return raw


def _line_of(source: str, pos: int) -> int:
    """返回 source 中位置 pos 的行号（1-based）。"""
    return source[:pos].count("\n") + 1


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
    cpp_exts = CPP_HEADER_EXTS

    def candidates(base: str) -> list[str]:
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
    source = path.read_text(encoding="utf-8")

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
        node_exts = CPP_HEADER_EXTS if language == "cpp" else LANGUAGE_EXTENSIONS[language]
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or _should_skip(path):
                continue
            if path.suffix.lower() in node_exts:
                all_nodes.add(path.relative_to(source_root).as_posix())

    adjacency: dict[str, list[str]] = {}
    edge_lines: dict[tuple[str, str], int] = {}

    for language in languages:
        extractor = DependencyExtractor(source_root, language)
        scan_exts = CPP_HEADER_EXTS if language == "cpp" else LANGUAGE_EXTENSIONS[language]
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or _should_skip(path):
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
        queue: deque[str] = deque(node for node in all_nodes if deg.get(node, 0) == 0)
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in graph.get(node, []):
                deg[neighbor] -= 1
                if deg[neighbor] == 0:
                    queue.append(neighbor)
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

    adjacency, all_nodes, edge_lines = build_dependency_graph(source_root, languages)
    sorted_order, cycles, broken_edges = topological_sort(
        adjacency, all_nodes, edge_lines, languages, source_root,
    )

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
