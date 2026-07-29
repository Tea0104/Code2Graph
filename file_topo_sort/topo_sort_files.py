#!/usr/bin/env python3
"""
对源代码文件按功能依赖链排序 —— 从入口文件（不被其他文件依赖的）出发，
追溯完整依赖链，链内按依赖关系拓扑排序。共享依赖出现在第一条用到它的链中，
后续链自动跳过已翻译的文件。

支持的语言：
- Python: import xxx, from xxx import yyy（含相对导入）
- C/C++: #include "local_file.h"

用法：
    python topo_sort_files.py --source ./myproject
    python topo_sort_files.py --source ./myproject --lang cpp
"""

from __future__ import annotations

import argparse
import cmd
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
# 构建依赖图 + 依赖链排序
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


def _transitive_deps(
    adjacency: dict[str, list[str]],
    node: str,
    all_nodes: set[str],
) -> set[str]:
    """返回 node 的所有传递依赖（不含 node 自身）。"""
    result: set[str] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        for dep in adjacency.get(current, []):
            if dep in all_nodes and dep not in result and dep != node:
                result.add(dep)
                stack.append(dep)
    return result


def _subgraph_topo_sort(
    adjacency: dict[str, list[str]],
    nodes: set[str],
    edge_lines: dict[tuple[str, str], int],
    languages: list[str],
    source_root: Path,
) -> list[str]:
    """对子图 nodes 做拓扑排序，仅考虑内部边。遇环自动断弱边。"""
    graph: dict[str, list[str]] = defaultdict(list)
    in_deg: dict[str, int] = {node: 0 for node in nodes}

    for node in nodes:
        graph.setdefault(node, [])

    for node in nodes:
        for dep in adjacency.get(node, []):
            if dep in nodes and dep != node:
                graph[dep].append(node)
                in_deg[node] += 1

    def _kahn(g: dict[str, list[str]], deg: dict[str, int]) -> list[str]:
        d = dict(deg)
        queue = [n for n in nodes if d.get(n, 0) == 0]
        heapq.heapify(queue)
        order: list[str] = []
        while queue:
            n = heapq.heappop(queue)
            order.append(n)
            for neighbor in sorted(g.get(n, [])):
                d[neighbor] -= 1
                if d[neighbor] == 0:
                    heapq.heappush(queue, neighbor)
        return order

    order = _kahn(graph, in_deg)
    remaining = nodes - set(order)

    lang = languages[0] if languages else "python"

    while remaining:
        cycles = _find_cycles(graph, remaining)
        if not cycles:
            break

        for cycle in cycles:
            candidates: list[tuple[int, str, str]] = []
            for i in range(len(cycle) - 1):
                B, A = cycle[i], cycle[i + 1]  # graph edge B→A means A depends on B
                line_no = _edge_import_line(adjacency, edge_lines, A, B)
                last_def = _last_definition_line(source_root, A, lang)
                weakness = line_no - last_def if line_no and last_def else 0
                candidates.append((weakness, A, B))

            candidates.sort(key=lambda x: x[0], reverse=True)
            _, break_A, break_B = candidates[0]

            if break_A in graph.get(break_B, []):
                graph[break_B].remove(break_A)
                in_deg[break_A] -= 1

        order = _kahn(graph, in_deg)
        remaining = nodes - set(order)

        # 防止死循环
        if not any(
            any(v in nodes for v in graph.get(n, [])) for n in remaining
        ):
            break

    if remaining:
        order.extend(sorted(remaining))

    return order


def build_feature_chains(
    adjacency: dict[str, list[str]],
    all_nodes: set[str],
    edge_lines: dict[tuple[str, str], int],
    languages: list[str],
    source_root: Path,
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """
    从入口文件出发构建依赖链。共享文件出现在第一条用到它的链中。
    每条链内部按依赖关系拓扑排序。

    返回:
        chains:     [(入口文件, [链内文件列表]), ...]
        flat_order: 所有文件的扁平翻译顺序
    """
    # 1. 找到入口文件（不被项目内任何文件依赖的）
    depended_on: set[str] = set()
    for node, deps in adjacency.items():
        for dep in deps:
            if dep in all_nodes:
                depended_on.add(dep)

    entries = sorted(all_nodes - depended_on)

    # 边界情况：无明确入口（全在环中）
    if not entries:
        chain = _subgraph_topo_sort(
            adjacency, all_nodes, edge_lines, languages, source_root,
        )
        return [("(all)", chain)], list(chain)

    # 2. 计算每个入口的传递闭包
    entry_closure: dict[str, set[str]] = {}
    for entry in entries:
        closure = _transitive_deps(adjacency, entry, all_nodes)
        closure.add(entry)
        entry_closure[entry] = closure

    # 3. 对入口排序：被其他链依赖的链优先，同样则大链优先
    def entry_priority(entry: str) -> tuple[int, int, str]:
        closure = entry_closure[entry]
        provided_to = 0
        for other_entry, other_closure in entry_closure.items():
            if other_entry == entry:
                continue
            for node in other_closure:
                deps = adjacency.get(node, [])
                if any(dep in closure for dep in deps):
                    provided_to += 1
                    break
        return (-provided_to, -len(closure), entry)

    ordered_entries = sorted(entries, key=entry_priority)

    # 4. 按优先级处理每条链，去重
    seen: set[str] = set()
    chains: list[tuple[str, list[str]]] = []

    for entry in ordered_entries:
        closure = entry_closure[entry]
        new_files = closure - seen
        if not new_files:
            continue
        chain = _subgraph_topo_sort(
            adjacency, new_files, edge_lines, languages, source_root,
        )
        chains.append((entry, chain))
        seen.update(new_files)

    # 5. 未被任何入口可达的孤立节点
    orphans = all_nodes - seen
    if orphans:
        orphan_chain = _subgraph_topo_sort(
            adjacency, orphans, edge_lines, languages, source_root,
        )
        chains.append(("(orphans)", orphan_chain))

    flat_order: list[str] = []
    for _, chain in chains:
        flat_order.extend(chain)

    return chains, flat_order


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
    chains: list[tuple[str, list[str]]],
    flat_order: list[str],
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
        "translation_order": flat_order,
        "chains": [
            {"entry": entry, "files": chain}
            for entry, chain in chains
        ],
        "dependencies": dependencies,
        "external_dependencies": external_dependencies,
    }


# ---------------------------------------------------------------------------
# 交互式翻译进度追踪
# ---------------------------------------------------------------------------

def _compute_ready(
    all_nodes: set[str],
    adjacency: dict[str, list[str]],
    translated: set[str],
) -> list[str]:
    """返回当前可翻译的文件列表：所有项目内依赖已翻译完成的文件。"""
    ready: list[str] = []
    for node in sorted(all_nodes - translated):
        deps = [d for d in adjacency.get(node, []) if d in all_nodes]
        if all(d in translated for d in deps):
            ready.append(node)
    return ready


class _TranslateShell(cmd.Cmd):
    """翻译进度管理交互终端。"""

    intro = (
        "\n╔══════════════════════════════════════╗\n"
        "║      Translation Progress Tracker   ║\n"
        "╚══════════════════════════════════════╝\n"
        "输入 help 查看命令，quit 退出。\n"
    )
    prompt = "\n> "

    def __init__(self, state: dict, state_path: Path) -> None:
        super().__init__()
        self.state = state
        self.state_path = state_path
        self.all_nodes: set[str] = set(state["all_files"])
        self.adjacency: dict[str, list[str]] = {
            k: [d for d in v if d in self.all_nodes]
            for k, v in state["adjacency"].items()
        }
        self.translated: set[str] = set(state["translated"])
        self._update_ready()
        if self.ready:
            self._print_ready()

    def _save(self) -> None:
        self.state["translated"] = sorted(self.translated)
        self.state["ready"] = self.ready
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _update_ready(self) -> None:
        self.ready = _compute_ready(self.all_nodes, self.adjacency, self.translated)

    def _print_ready(self, limit: int | None = None) -> None:
        if not self.ready:
            remaining = len(self.all_nodes) - len(self.translated)
            if remaining == 0:
                print("✔ 所有文件已翻译完成。")
            else:
                print(
                    f"⚠ 无可翻译文件，但还有 {remaining} 个文件未翻译"
                    "（可能存在循环依赖）。"
                )
            return

        r = self.ready[:limit] if limit else self.ready
        print(f"\n📋 可翻译 ({len(self.ready)} 个，显示前 {len(r)} 个)：")
        for f in r:
            deps = [d for d in self.adjacency.get(f, []) if d in self.all_nodes]
            dep_hint = f"  ← 依赖: {', '.join(deps[-2:])}" if deps else ""
            print(f"   {f}{dep_hint}")

    def _resolve_files(self, args: str) -> list[str]:
        if not args.strip():
            return []
        targets = args.strip().split()
        result: list[str] = []
        for t in targets:
            if t in self.all_nodes:
                result.append(t)
                continue
            matches = [f for f in self.all_nodes if t in f]
            if len(matches) == 1:
                result.append(matches[0])
            elif len(matches) > 1:
                print(f"   '{t}' 匹配多个文件: {matches}")
            else:
                print(f"   '{t}' 未找到匹配文件")
        return result

    def do_ready(self, arg: str) -> None:
        """ready [N]  显示当前可翻译的文件。"""
        limit = None
        if arg.strip():
            try:
                limit = int(arg.strip())
            except ValueError:
                print("用法: ready [数量]")
                return
        self._update_ready()
        self._print_ready(limit=limit)

    def do_done(self, arg: str) -> None:
        """done <文件> [文件2 ...]  标记文件为已翻译。"""
        targets = self._resolve_files(arg)
        if not targets:
            return
        newly_done = [t for t in targets if t not in self.translated]
        already = [t for t in targets if t in self.translated]
        for f in newly_done:
            self.translated.add(f)
        self._update_ready()
        self._save()
        if newly_done:
            print(f"✔ 标记完成 ({len(newly_done)}): {', '.join(newly_done)}")
        if already:
            print(f"  已翻译，跳过 ({len(already)}): {', '.join(already)}")
        total = len(self.all_nodes)
        done = len(self.translated)
        print(f"📊 进度: {done}/{total} ({done * 100 // total}%)")
        if self.ready:
            self._print_ready(limit=10)

    def do_undo(self, arg: str) -> None:
        """undo <文件> [文件2 ...]  撤销翻译标记。"""
        targets = self._resolve_files(arg)
        if not targets:
            return
        for f in targets:
            self.translated.discard(f)
        self._update_ready()
        self._save()
        print(f"↩ 已撤销: {', '.join(targets)}")

    def do_translated(self, arg: str) -> None:
        """translated [关键词]  列出已翻译的文件。"""
        files = sorted(self.translated)
        if arg.strip():
            files = [f for f in files if arg.strip() in f]
        if not files:
            print("(无)")
            return
        print(f"✅ 已翻译 ({len(files)} 个):")
        for f in files:
            print(f"   {f}")

    def do_remaining(self, arg: str) -> None:
        """remaining [关键词]  列出未翻译的文件及阻塞原因。"""
        files = sorted(self.all_nodes - self.translated)
        if arg.strip():
            files = [f for f in files if arg.strip() in f]
        if not files:
            print("(无)")
            return
        print(f"⏳ 未翻译 ({len(files)} 个):")
        for f in files:
            blocked = [
                d for d in self.adjacency.get(f, [])
                if d in self.all_nodes and d not in self.translated
            ]
            hint = f"  ← 等待: {', '.join(blocked[:3])}" if blocked else ""
            print(f"   {f}{hint}")

    def do_status(self, arg: str) -> None:
        """status  显示翻译进度概览。"""
        total = len(self.all_nodes)
        done = len(self.translated)
        pct = done * 100 // total if total else 0
        bar_len = 30
        filled = int(bar_len * done / total) if total else 0
        bar = "█" * filled + "░" * (bar_len - filled)

        print(f"\n📊 翻译进度: {done}/{total} ({pct}%)")
        print(f"   [{bar}]")
        print(f"   ✅ 已翻译: {done}")
        print(f"   📋 可翻译: {len(self.ready)}")
        print(f"   ⏳ 等待中: {total - done - len(self.ready)}")

    def do_next(self, arg: str) -> None:
        """next [N]  按依赖链顺序显示建议翻译的文件。"""
        flat_order = self.state.get("flat_order", [])
        remaining = [f for f in flat_order if f not in self.translated]
        if not remaining:
            print("✔ 全部完成。")
            return
        limit = 10
        if arg.strip():
            try:
                limit = int(arg.strip())
            except ValueError:
                pass
        print(f"📋 建议翻译顺序 (前 {min(limit, len(remaining))} 个):")
        for f in remaining[:limit]:
            deps = [d for d in self.adjacency.get(f, []) if d in self.all_nodes]
            dep_str = f"  ← 依赖: {', '.join(deps[:3])}" if deps else "  ← 无依赖"
            print(f"   {f}{dep_str}")

    def do_search(self, arg: str) -> None:
        """search <关键词>  搜索文件。"""
        if not arg.strip():
            print("用法: search <关键词>")
            return
        kw = arg.strip()
        matches = sorted([
            f for f in self.all_nodes
            if kw.lower() in f.lower()
        ])
        if not matches:
            print(f"未找到包含 '{kw}' 的文件。")
            return
        print(f"🔍 找到 {len(matches)} 个文件:")
        for f in matches:
            status = "✅" if f in self.translated else (
                "📋" if f in self.ready else "⏳"
            )
            deps = [d for d in self.adjacency.get(f, []) if d in self.all_nodes]
            dep_info = f" → {' ,'.join(deps[:2])}" if deps else ""
            print(f"   {status} {f}{dep_info}")

    def do_quit(self, arg: str) -> bool:
        print("退出。状态已保存。")
        return True

    def do_EOF(self, arg: str) -> bool:
        print("\n退出。状态已保存。")
        return True

    do_q = do_quit
    do_ls = do_ready
    do_d = do_done
    do_t = do_translated
    do_r = do_remaining
    do_s = do_status
    do_n = do_next


def _run_interactive(
    source_root: Path,
    languages: list[str],
    include_tests: bool,
    state_path: Path,
    reset: bool,
) -> None:
    """启动交互式翻译进度追踪。"""
    if state_path.exists() and not reset:
        print(f"加载已有状态: {state_path}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        if reset and state_path.exists():
            print(f"重置状态: {state_path}")
        print(f"扫描 {source_root} 并构建依赖图...")
        adjacency, all_nodes, edge_lines = build_dependency_graph(
            source_root, languages, include_tests,
        )
        chains, flat_order = build_feature_chains(
            adjacency, all_nodes, edge_lines, languages, source_root,
        )

        ready = _compute_ready(all_nodes, adjacency, set())

        state = {
            "source_root": str(source_root),
            "languages": languages,
            "all_files": sorted(all_nodes),
            "adjacency": {k: sorted(v) for k, v in adjacency.items()},
            "translated": [],
            "ready": ready,
            "flat_order": flat_order,
        }
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"状态已保存至: {state_path}")

    print(
        f"共 {len(state['all_files'])} 个文件，"
        f"已翻译 {len(state['translated'])} 个。"
    )

    try:
        _TranslateShell(state, state_path).cmdloop()
    except KeyboardInterrupt:
        print("\n退出。状态已保存。")


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
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Launch interactive translation progress tracker.",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="Path to state file for interactive mode. Default: <source>/.translate_state.json",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Discard existing state and start fresh (interactive mode only).",
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

    # 交互模式
    if args.interactive:
        state_path = (
            Path(args.state) if args.state
            else source_root / ".translate_state.json"
        )
        _run_interactive(
            source_root, languages, args.include_tests, state_path, args.reset,
        )
        return

    # 静态输出模式
    print(f"Scanning {source_root} for {', '.join(languages)} files...", file=sys.stderr)

    adjacency, all_nodes, edge_lines = build_dependency_graph(
        source_root,
        languages,
        include_tests=args.include_tests,
    )
    chains, flat_order = build_feature_chains(
        adjacency, all_nodes, edge_lines, languages, source_root,
    )

    if args.format == "json":
        result = build_json_result(
            source_root,
            languages,
            adjacency,
            all_nodes,
            edge_lines,
            chains,
            flat_order,
        )
        output = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        output = "\n".join(flat_order)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
