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
import cmd
import heapq
import json
import posixpath
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from repository_analysis.dependencies import ImportExtractor, build_file_dependency_graph
from repository_analysis.dependencies import line_of as _line_of
from repository_analysis.repository import detect_languages, is_test_path
from repository_analysis.languages import normalize_languages

# Compatibility export for existing callers; implementation lives in repository_analysis.
DependencyExtractor = ImportExtractor


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
    elif language == "javascript":
        pattern = r'^\s*(?:export\s+)?(?:async\s+)?(?:class|function)\s+'
    else:
        pattern = r'^\s*(?:(?:public|private|protected|internal|static|final|abstract)\s+)*(?:class|struct|interface|record|enum\s+class|enum)\s+'

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
    """Compatibility wrapper around the shared repository dependency graph."""
    graph = build_file_dependency_graph(
        source_root,
        languages,
        include_tests=include_tests,
    )
    return graph.adjacency, graph.nodes, graph.edge_lines


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


def build_feature_chains(
    adjacency: dict[str, list[str]],
    all_nodes: set[str],
    sorted_order: list[str],
) -> list[tuple[str, list[str]]]:
    """Group the global translation order by entry-file dependency closures."""
    depended_on = {
        dep
        for deps in adjacency.values()
        for dep in deps
        if dep in all_nodes
    }
    entries = sorted(all_nodes - depended_on)
    if not entries:
        return [("(all)", list(sorted_order))]

    def closure(entry: str) -> set[str]:
        result = {entry}
        stack = [entry]
        while stack:
            node = stack.pop()
            for dep in adjacency.get(node, []):
                if dep in all_nodes and dep not in result:
                    result.add(dep)
                    stack.append(dep)
        return result

    closures = {entry: closure(entry) for entry in entries}
    ordered_entries = sorted(
        entries,
        key=lambda entry: (-len(closures[entry]), entry),
    )

    seen: set[str] = set()
    chains: list[tuple[str, list[str]]] = []
    for entry in ordered_entries:
        files = [
            path
            for path in sorted_order
            if path in closures[entry] and path not in seen
        ]
        if files:
            chains.append((entry, files))
            seen.update(files)

    remaining = [path for path in sorted_order if path not in seen]
    if remaining:
        chains.append(("(orphans)", remaining))
    return chains


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
    chains: list[tuple[str, list[str]]],
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
        "schema_version": 1,
        "source_root": str(source_root),
        "languages": languages,
        "translation_order": sorted_order,
        "chains": [
            {"entry": entry, "files": files}
            for entry, files in chains
        ],
        "dependencies": dependencies,
        "external_dependencies": external_dependencies,
        "cycles": cycles,
        "broken_edges": [
            {"file": source, "depends_on": target}
            for source, target in sorted(broken_edges)
        ],
    }


def _effective_adjacency(
    adjacency: dict[str, list[str]],
    broken_edges: set[tuple[str, str]],
) -> dict[str, list[str]]:
    return {
        node: [
            dep for dep in deps
            if (node, dep) not in broken_edges
        ]
        for node, deps in adjacency.items()
    }


def _compute_ready(
    all_nodes: set[str],
    adjacency: dict[str, list[str]],
    translated: set[str],
) -> list[str]:
    ready = []
    for node in sorted(all_nodes - translated):
        internal_deps = [
            dep for dep in adjacency.get(node, []) if dep in all_nodes
        ]
        if all(dep in translated for dep in internal_deps):
            ready.append(node)
    return ready


def _normalize_languages(
    languages: Iterable[str] | str | None = None,
    *,
    source_root: Path | None = None,
) -> list[str]:
    if languages is None:
        if source_root is None:
            result = ["python"]
        else:
            result = [language for language, _count in detect_languages(source_root)]
            if not result:
                raise ValueError(f"No supported source files found in {source_root}.")
    elif isinstance(languages, str):
        result = [lang.strip() for lang in languages.split(",") if lang.strip()]
    else:
        result = [lang.strip() for lang in languages if lang.strip()]

    return normalize_languages(result)


def _build_translation_state(
    source_path: str,
    include_tests: bool = False,
    languages: Iterable[str] | str | None = None,
) -> tuple[Path, list[str], set[str], dict[str, list[str]], list[str]]:
    source_root = Path(source_path).resolve()
    if not source_root.is_dir():
        raise ValueError(f"{source_root} is not a directory.")

    normalized_languages = _normalize_languages(languages, source_root=source_root)
    adjacency, all_nodes, edge_lines = build_dependency_graph(
        source_root,
        normalized_languages,
        include_tests=include_tests,
    )
    sorted_order, _cycles, broken_edges = topological_sort(
        adjacency,
        all_nodes,
        edge_lines,
        normalized_languages,
        source_root,
    )
    effective_adjacency = _effective_adjacency(adjacency, broken_edges)
    return (
        source_root,
        normalized_languages,
        all_nodes,
        effective_adjacency,
        sorted_order,
    )


def _normalize_translated_files(
    source_root: Path,
    all_nodes: set[str],
    already: Iterable[str],
) -> set[str]:
    translated: set[str] = set()
    unknown: list[str] = []

    for value in already:
        raw = str(value).strip()
        if not raw:
            continue

        normalized = raw.replace("\\", "/")
        path = Path(raw)
        if path.is_absolute():
            try:
                normalized = path.resolve().relative_to(source_root).as_posix()
            except ValueError:
                unknown.append(raw)
                continue

        if normalized in all_nodes:
            translated.add(normalized)
        else:
            unknown.append(raw)

    if unknown:
        raise ValueError(
            "Unknown translated file(s): "
            + ", ".join(unknown)
            + ". Use paths returned by get_order_information()."
        )
    return translated


def get_order_information(
    source_path: str,
    include_tests: bool = False,
    languages: Iterable[str] | str | None = None,
) -> dict[str, object]:
    """
    Return the complete dependency-first translation order for source_path.

    The returned files are repository-relative POSIX paths, matching the CLI
    output from this module.
    """
    _source_root, _languages, _all_nodes, _adjacency, sorted_order = (
        _build_translation_state(
            source_path,
            include_tests=include_tests,
            languages=languages,
        )
    )
    return {
        "number": len(sorted_order),
        "files": sorted_order,
    }


def get_translation_order(
    source_path: str,
    number: int,
    already: list[str],
    include_tests: bool = False,
    languages: Iterable[str] | str | None = None,
) -> list[str]:
    """
    Return the next files in global translation order.

    The list follows the global topological order and excludes files listed in
    already.
    """
    if number < 0:
        raise ValueError("number must be non-negative.")

    source_root, _languages, all_nodes, _adjacency, sorted_order = (
        _build_translation_state(
            source_path,
            include_tests=include_tests,
            languages=languages,
        )
    )
    translated = _normalize_translated_files(source_root, all_nodes, already)
    remaining = [path for path in sorted_order if path not in translated]
    return remaining[:number]


class _TranslateShell(cmd.Cmd):
    """Interactive translation progress tracker."""

    intro = "Translation Progress Tracker. Type help for commands."
    prompt = "\n> "

    def __init__(self, state: dict[str, object], state_path: Path) -> None:
        super().__init__()
        self.state = state
        self.state_path = state_path
        self.all_nodes = set(state["all_files"])
        self.adjacency = {
            node: [dep for dep in deps if dep in self.all_nodes]
            for node, deps in dict(state["adjacency"]).items()
        }
        self.translated = set(state["translated"])
        self._update_ready()
        self._print_ready(limit=10)

    def _update_ready(self) -> None:
        self.ready = _compute_ready(
            self.all_nodes, self.adjacency, self.translated,
        )

    def _save(self) -> None:
        self.state["translated"] = sorted(self.translated)
        self.state["ready"] = self.ready
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _resolve_files(self, value: str) -> list[str]:
        resolved = []
        for query in value.split():
            if query in self.all_nodes:
                resolved.append(query)
                continue
            matches = sorted(path for path in self.all_nodes if query in path)
            if len(matches) == 1:
                resolved.append(matches[0])
            elif matches:
                print(f"'{query}' matches multiple files: {', '.join(matches)}")
            else:
                print(f"No file matches '{query}'.")
        return resolved

    def _print_ready(self, limit: int | None = None) -> None:
        if not self.ready:
            remaining = len(self.all_nodes - self.translated)
            message = "All files are translated." if not remaining else "No files are ready."
            print(message)
            return
        files = self.ready[:limit] if limit else self.ready
        print(f"Ready ({len(self.ready)}):")
        for path in files:
            print(f"  {path}")

    def do_ready(self, arg: str) -> None:
        """ready [N]: show files whose dependencies are translated."""
        try:
            limit = int(arg) if arg.strip() else None
        except ValueError:
            print("Usage: ready [N]")
            return
        self._update_ready()
        self._print_ready(limit)

    def do_done(self, arg: str) -> None:
        """done FILE [...]: mark files as translated."""
        files = self._resolve_files(arg)
        self.translated.update(files)
        self._update_ready()
        self._save()
        if files:
            print(f"Marked done: {', '.join(files)}")
        self.do_status("")

    def do_undo(self, arg: str) -> None:
        """undo FILE [...]: remove translated marks."""
        files = self._resolve_files(arg)
        self.translated.difference_update(files)
        self._update_ready()
        self._save()
        if files:
            print(f"Undone: {', '.join(files)}")

    def do_next(self, arg: str) -> None:
        """next [N]: show the next files in translation order."""
        try:
            limit = int(arg) if arg.strip() else 10
        except ValueError:
            print("Usage: next [N]")
            return
        remaining = [
            path for path in self.state.get("translation_order", [])
            if path not in self.translated
        ]
        for path in remaining[:limit]:
            status = "ready" if path in self.ready else "blocked"
            print(f"  [{status}] {path}")

    def do_remaining(self, arg: str) -> None:
        """remaining [TEXT]: show untranslated files and blockers."""
        query = arg.strip().lower()
        files = sorted(self.all_nodes - self.translated)
        if query:
            files = [path for path in files if query in path.lower()]
        for path in files:
            blockers = [
                dep for dep in self.adjacency.get(path, [])
                if dep in self.all_nodes and dep not in self.translated
            ]
            suffix = f" <- {', '.join(blockers)}" if blockers else ""
            print(f"  {path}{suffix}")

    def do_translated(self, arg: str) -> None:
        """translated [TEXT]: show translated files."""
        query = arg.strip().lower()
        files = sorted(self.translated)
        if query:
            files = [path for path in files if query in path.lower()]
        for path in files:
            print(f"  {path}")

    def do_search(self, arg: str) -> None:
        """search TEXT: search files and show progress state."""
        query = arg.strip().lower()
        if not query:
            print("Usage: search TEXT")
            return
        for path in sorted(self.all_nodes):
            if query not in path.lower():
                continue
            status = "done" if path in self.translated else (
                "ready" if path in self.ready else "blocked"
            )
            print(f"  [{status}] {path}")

    def do_status(self, arg: str) -> None:
        """status: show translation progress."""
        total = len(self.all_nodes)
        done = len(self.translated)
        percent = int(done * 100 / total) if total else 100
        print(
            f"Progress: {done}/{total} ({percent}%), "
            f"ready={len(self.ready)}, blocked={total - done - len(self.ready)}"
        )

    def do_quit(self, arg: str) -> bool:
        """quit: save and exit."""
        self._save()
        return True

    def do_EOF(self, arg: str) -> bool:
        print()
        return self.do_quit(arg)

    do_q = do_quit
    do_ls = do_ready
    do_d = do_done
    do_n = do_next
    do_r = do_remaining
    do_t = do_translated
    do_s = do_status


def _run_interactive(
    source_root: Path,
    languages: list[str],
    include_tests: bool,
    state_path: Path,
    reset: bool,
) -> None:
    if state_path.exists() and not reset:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        print(f"Loaded state: {state_path}")
    else:
        adjacency, all_nodes, edge_lines = build_dependency_graph(
            source_root, languages, include_tests=include_tests,
        )
        order, cycles, broken_edges = topological_sort(
            adjacency, all_nodes, edge_lines, languages, source_root,
        )
        effective = _effective_adjacency(adjacency, broken_edges)
        chains = build_feature_chains(effective, all_nodes, order)
        state = {
            "source_root": str(source_root),
            "languages": languages,
            "all_files": sorted(all_nodes),
            "adjacency": effective,
            "translation_order": order,
            "chains": [
                {"entry": entry, "files": files}
                for entry, files in chains
            ],
            "cycles": cycles,
            "broken_edges": [
                {"file": source, "depends_on": target}
                for source, target in sorted(broken_edges)
            ],
            "translated": [],
            "ready": _compute_ready(all_nodes, effective, set()),
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"Created state: {state_path}")

    try:
        _TranslateShell(state, state_path).cmdloop()
    except KeyboardInterrupt:
        print("\nExited; progress already saved.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def analyze_project(
    source_root: str | Path,
    languages: str | Sequence[str],
    *,
    include_tests: bool = False,
) -> dict[str, object]:
    """Analyze one repository and return the stable JSON-compatible result."""
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {root}")
    normalized = normalize_languages(languages)
    adjacency, all_nodes, edge_lines = build_dependency_graph(
        root, normalized, include_tests=include_tests,
    )
    order, cycles, broken_edges = topological_sort(
        adjacency, all_nodes, edge_lines, normalized, root,
    )
    effective = _effective_adjacency(adjacency, broken_edges)
    chains = build_feature_chains(effective, all_nodes, order)
    return build_json_result(
        root,
        normalized,
        adjacency,
        all_nodes,
        edge_lines,
        order,
        cycles,
        broken_edges,
        chains,
    )


def _progress_file_path(
    value: str | Path,
    source_root: Path,
    all_nodes: set[str],
) -> str:
    """Normalize a caller-provided translated-file name to a repository path."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("translated_files cannot contain an empty path")

    candidate_path = Path(raw).expanduser()
    if candidate_path.is_absolute():
        try:
            candidate = candidate_path.resolve().relative_to(source_root).as_posix()
        except ValueError as exc:
            raise ValueError(
                f"Translated file is outside the source repository: {raw}"
            ) from exc
    else:
        candidate = raw.replace("\\", "/").lstrip("./")
        candidate = posixpath.normpath(candidate)

    if candidate in all_nodes:
        return candidate

    matches = sorted(
        path for path in all_nodes
        if path.endswith("/" + candidate) or path == candidate
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Translated file does not belong to the repository: {raw}")
    matches_text = ", ".join(matches)
    raise ValueError(
        f"Translated file is ambiguous: {raw}; matches: {matches_text}"
    )


def _transitive_dependencies(
    start: str,
    adjacency: dict[str, list[str]],
    all_nodes: set[str],
) -> set[str]:
    """Return all repository files reachable from a file through dependencies."""
    result: set[str] = set()
    stack = list(adjacency.get(start, []))
    while stack:
        dependency = stack.pop()
        if dependency not in all_nodes or dependency in result:
            continue
        result.add(dependency)
        stack.extend(adjacency.get(dependency, []))
    return result


def _verification_files(
    source_root: Path,
    languages: list[str],
    source_files: set[str],
) -> list[dict[str, object]]:
    """Find test files whose static import closure touches a planned chain."""
    test_adjacency, all_nodes, _ = build_dependency_graph(
        source_root, languages, include_tests=True,
    )
    tests = []
    for path in sorted(all_nodes):
        relative = source_root / path
        if not is_test_path(relative, source_root):
            continue
        covered = sorted(
            _transitive_dependencies(path, test_adjacency, all_nodes) & source_files
        )
        if covered:
            tests.append({"file": path, "covered_files": covered})
    return tests


def plan_translation_batch(
    source_root: str | Path,
    languages: str | Sequence[str],
    translated_files: Sequence[str | Path] = (),
    requested_count: int = 1,
    *,
    include_tests: bool = False,
) -> dict[str, object]:
    """Plan the next translation batch that completes one or more feature chains.

    requested_count is a lower bound. If the next feature chain needs more
    files, the returned batch is expanded so the translated result can reach a
    testable boundary instead of stopping at an arbitrary file count.
    """
    if requested_count < 1:
        raise ValueError("requested_count must be at least 1")

    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {root}")
    normalized = normalize_languages(languages)
    analysis = analyze_project(root, normalized, include_tests=include_tests)
    all_nodes = set(analysis["translation_order"])
    translated = {
        _progress_file_path(value, root, all_nodes)
        for value in translated_files
    }
    order = list(analysis["translation_order"])
    order_index = {path: index for index, path in enumerate(order)}

    chain_records = list(analysis["chains"])
    incomplete = [
        chain for chain in chain_records
        if any(path not in translated for path in chain["files"])
    ]
    incomplete.sort(
        key=lambda chain: (
            0 if any(path in translated for path in chain["files"]) else 1,
            min(order_index[path] for path in chain["files"]),
        )
    )

    selected_chains: list[dict[str, object]] = []
    recommended: list[str] = []
    translated_after = set(translated)
    for chain in incomplete:
        remaining = [
            path for path in chain["files"] if path not in translated_after
        ]
        if not remaining:
            continue
        selected_chains.append({
            "entry": chain["entry"],
            "files": remaining,
            "already_translated": [
                path for path in chain["files"] if path in translated
            ],
        })
        recommended.extend(remaining)
        translated_after.update(remaining)
        if len(recommended) >= requested_count:
            break

    recommended.sort(key=order_index.__getitem__)
    selected_set = set(recommended)
    dependencies = analysis["dependencies"]
    blockers = sorted({
        item["depends_on"]
        for item in dependencies
        if item["file"] in selected_set
        and item["depends_on"] in all_nodes
        and item["depends_on"] not in translated_after
        and item["depends_on"] not in selected_set
    })

    verification_tests = _verification_files(root, normalized, selected_set)
    expanded = len(recommended) > requested_count
    if not recommended:
        status = "repository_complete"
    elif blockers:
        status = "blocked_by_dependencies"
    elif not verification_tests:
        status = "no_static_verification_test"
    else:
        status = "ready_for_realtime_test"

    reasons: list[str] = []
    if expanded:
        reasons.append("expanded_to_complete_feature_chain")
    if len(selected_chains) > 1:
        reasons.append("included_additional_chain_to_reach_requested_count")
    if blockers:
        reasons.append("selected_chain_has_untranslated_dependencies")
    if not verification_tests and recommended:
        reasons.append("no_test_file_imports_the_selected_source_chain")

    return {
        "schema_version": 2,
        "source_root": str(root),
        "languages": normalized,
        "translated_files": sorted(translated),
        "requested_count": requested_count,
        "recommended_files": recommended,
        "recommended_count": len(recommended),
        "expanded": expanded,
        "expansion_count": max(0, len(recommended) - requested_count),
        "reasons": reasons,
        "status": status,
        "selected_chains": selected_chains,
        "verification_tests": verification_tests,
        "realtime_test_ready": status == "ready_for_realtime_test",
        "untranslated_dependencies": blockers,
        "translation_order": order,
    }

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
        help=(
            "Language: python, c, cpp, java, javascript, csharp, aliases, "
            "or a comma-separated list. Default: python"
        ),
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
        help="Launch the interactive translation progress tracker.",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="Interactive state file. Default: <source>/.translate_state.json",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Discard existing interactive progress and rescan the project.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    source_root = Path(args.source).resolve()

    if not source_root.is_dir():
        print(f"Error: {source_root} is not a directory.", file=sys.stderr)
        sys.exit(1)

    try:
        languages = normalize_languages(args.lang)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.interactive:
        state_path = (
            Path(args.state).resolve()
            if args.state
            else source_root / ".translate_state.json"
        )
        _run_interactive(
            source_root,
            languages,
            args.include_tests,
            state_path,
            args.reset,
        )
        return

    print(f"Scanning {source_root} for {', '.join(languages)} files...", file=sys.stderr)

    result = analyze_project(
        source_root, languages, include_tests=args.include_tests,
    )
    sorted_order = result["translation_order"]
    cycles = result["cycles"]
    broken_edges = {
        (item["file"], item["depends_on"])
        for item in result["broken_edges"]
    }

    if args.format == "json":
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
