"""CLI skeleton for the code graph demo."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from code_graph_demo.ast_extractor import extract_python_ast_info
    from code_graph_demo.exporter import export_graph_to_json
    from code_graph_demo.graph_builder import build_code_graph
    from code_graph_demo.repo_scanner import scan_python_files
except ImportError:
    from ast_extractor import extract_python_ast_info
    from exporter import export_graph_to_json
    from graph_builder import build_code_graph
    from repo_scanner import scan_python_files


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description="Code graph demo skeleton")
    parser.add_argument("--src", default="RepoTransAgent", help="Source directory to analyze")
    parser.add_argument(
        "--out",
        default="code_graph_demo/output/code_graph.json",
        help="Output graph JSON path",
    )
    parser.add_argument(
        "--show-ast-summary",
        action="store_true",
        help="Show a minimal AST summary for scanned Python files",
    )
    parser.add_argument("--with-cfg", action="store_true", help="Include lightweight CFG")
    parser.add_argument("--with-dfg", action="store_true", help="Include lightweight DFG")
    return parser


def _print_ast_summary(files: list[Path]) -> None:
    """Print aggregate AST stats for scanned files."""
    class_count = 0
    function_count = 0
    import_count = 0
    call_count = 0
    examples: list[str] = []

    for path in files:
        info = extract_python_ast_info(path)
        classes = info.get("classes", [])
        functions = info.get("functions", [])
        imports = info.get("imports", [])
        class_count += len(classes)
        function_count += len(functions)
        import_count += len(imports)

        for function in functions:
            call_count += len(function.get("calls", []))
            if len(examples) < 3:
                examples.append(f"{function['name']} ({path.name})")

        for cls in classes:
            methods = cls.get("methods", [])
            function_count += len(methods)
            for method in methods:
                call_count += len(method.get("calls", []))
                if len(examples) < 3:
                    examples.append(f"{cls['name']}.{method['name']} ({path.name})")

    print(f"scanned python files count: {len(files)}")
    print(f"class count: {class_count}")
    print(f"function count: {function_count}")
    print(f"import count: {import_count}")
    print(f"call count: {call_count}")
    for example in examples:
        print(example)


def main() -> int:
    """Parse CLI arguments and print scan or AST summary results."""
    parser = build_parser()
    args = parser.parse_args()
    src = Path(args.src)
    files = scan_python_files(src)
    if args.show_ast_summary:
        _print_ast_summary(files)
    else:
        ast_infos = [extract_python_ast_info(path) for path in files]
        graph = build_code_graph(
            ast_infos,
            source_root=args.src,
            features={"cfg": args.with_cfg, "dfg": args.with_dfg},
        )
        export_graph_to_json(graph, args.out)
        print(f"scanned python files count: {len(files)}")
        print(f"nodes count: {len(graph['nodes'])}")
        print(f"edges count: {len(graph['edges'])}")
        print(f"output path: {Path(args.out).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
